"""Web UI for Crossprint: drag in a .3mf, get the converted one back.

Runs locally by default (127.0.0.1, opens a browser). Set PORT -- as every
PaaS does -- and it binds publicly instead and skips the browser, so the same
module serves both cases.

Both endpoints are thin wrappers around convert/pipeline.py; all the actual
conversion logic and its test coverage live there, same as cli.py.

Size limits still exist, but they no longer follow from the project's size.
core/archive.py streams every part conversion doesn't modify straight from the
source container to the output, and uploads and results are spooled rather than
buffered, so cost is flat: an 11-plate, 726 MB project peaks at 74 MB, a 66 MB
one at 70 MB. That is the difference between working on a small container and
not.

The remaining ceilings are guards, not predictions, and the uncompressed one is
checked up front from the zip directory rather than discovered by running out
of memory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
import zipfile
from pathlib import Path
from tempfile import SpooledTemporaryFile
from threading import Semaphore, Timer
from typing import BinaryIO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, render_template, request, send_file  # noqa: E402

from convert.pipeline import (  # noqa: E402
    MODEL_REGISTRY,
    _WELL_VERIFIED_MODELS,
    ConversionResult,
    SourceInfo,
    UnsupportedSourceError,
    convert,
    inspect_source,
)

# A PaaS always supplies PORT; its presence is what distinguishes "hosted"
# from "someone ran run_web.ps1 on their own machine".
_HOSTED = "PORT" in os.environ
HOST = "0.0.0.0" if _HOSTED else "127.0.0.1"  # noqa: S104 -- binding publicly is the point when hosted
PORT = int(os.environ.get("PORT", 5000))

# Compressed upload cap. Uploads spool to disk rather than RAM, so this bounds
# request time and disk use, not memory.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "300" if _HOSTED else "2048")) * 1024 * 1024

# Uncompressed ceiling. Since parts stream through, total project size no longer
# predicts peak memory -- the largest single part does, and _CONVERSION_SLOT
# guarantees one conversion at a time. This stays as a backstop against a zip
# bomb and against a project whose individual meshes are pathologically large.
MAX_UNCOMPRESSED_BYTES = int(os.environ.get("MAX_UNCOMPRESSED_MB", "3072" if _HOSTED else "16384")) * 1024 * 1024

# Above this, a converted result goes to a temp file instead of staying in RAM.
_SPOOL_TO_DISK_BYTES = 8 * 1024 * 1024

# Bump on a change worth telling users about. The commit beside it is what
# actually identifies the build.
VERSION = "1.1"


def _build_id() -> str:
    """A short, visible identity for the running build.

    This exists because of a real failure: a fix was deployed, the live site
    kept serving a build three commits older, and nothing on the page or in the
    health check said so. Two days were spent re-debugging code that was
    already correct, against a server that never received it. A version anyone
    can read at a glance turns "is my fix live?" from a guess into a look.

    Render exposes the deployed commit as RENDER_GIT_COMMIT. Locally there is
    no such variable, so the commit is read from git; if that fails too (a
    source download with no .git, say) the version alone still shows.
    """
    commit = os.environ.get("RENDER_GIT_COMMIT", "")
    if not commit:
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip()
        except Exception:
            commit = ""
    where = "hosted" if _HOSTED else "local"
    return f"v{VERSION} · {commit[:7]} · {where}" if commit else f"v{VERSION} · {where}"


BUILD_ID = _build_id()

# Conversions are memory-heavy and short. Serialising them keeps peak usage at
# one conversion's worth no matter how many people click at once, while the
# server's other threads still answer page loads and health checks.
_CONVERSION_SLOT = Semaphore(1)
_CONVERSION_WAIT_SECONDS = 120

# A tip jar, shown only when a link is configured -- no placeholder account is
# invented, and a self-hoster who sets nothing simply gets no button.
DONATE_URL = os.environ.get("DONATE_URL", "").strip()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def _model_label(slug: str) -> str:
    return MODEL_REGISTRY[slug]


def _source_info_json(info: SourceInfo) -> dict:
    return {
        "vendor": info.vendor,
        "vendorLabel": _model_label(info.vendor),
        "printerModel": info.printer_model,
        "filamentCount": info.filament_count,
        "availableTargets": [
            {"slug": s, "label": _model_label(s), "verified": s in _WELL_VERIFIED_MODELS} for s in info.available_targets
        ],
    }


def _result_json(result: ConversionResult) -> dict:
    return {
        "sourceVendor": result.source_vendor,
        "targetVendor": result.target_vendor,
        "filamentCount": result.filament_count,
        "warnings": result.warnings,
    }


class UploadRejected(Exception):
    """A request we decline before doing any conversion work."""


def _uploaded_file():
    """The uploaded .3mf as a buffer, or UploadRejected with a reason to show."""
    file = request.files.get("file")
    if file is None or file.filename == "":
        raise UploadRejected("No .3mf file received.")
    if not file.filename.lower().endswith(".3mf"):
        raise UploadRejected("That doesn't look like a .3mf file.")

    # Werkzeug has already spooled the upload -- to disk once it outgrows a few
    # hundred KB -- so its stream is handed straight to the converter. Calling
    # .read() here would copy the entire compressed project into RAM for no
    # reason; on the largest real sample that alone was over 100 MB.
    stream = file.stream
    stream.seek(0)
    _reject_if_too_large_uncompressed(stream)
    return stream, file.filename


def _reject_if_too_large_uncompressed(buf: BinaryIO) -> None:
    """Check the zip directory before loading anything.

    Geometry compresses about 6x, so the upload size says little about what is
    inside -- the largest real sample is 122 MB on disk and 726 MB expanded.
    Conversion streams rather than buffers, so that expansion is no longer a
    memory problem, but the directory still gives the real figure for free and
    is what a zip bomb would have to lie about.
    """
    try:
        with zipfile.ZipFile(buf) as zf:
            total = sum(info.file_size for info in zf.infolist())
    except zipfile.BadZipFile:
        raise UploadRejected("That file isn't a valid .3mf archive.") from None
    finally:
        buf.seek(0)

    if total > MAX_UNCOMPRESSED_BYTES:
        raise UploadRejected(
            f"This project holds {total // (1024 * 1024)} MB of model data, over the "
            f"{MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB this server can handle. "
            "Run the tool locally for a project this size -- it has no such limit."
        )


@app.errorhandler(413)
def _too_large(_error):
    return jsonify(
        error=f"File is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit. "
        "Run the tool locally for a project this size."
    ), 413


@app.get("/healthz")
def healthz():
    """Liveness probe, and what a platform's uptime check should poll.

    `build` is here so the probe answers the question that actually matters
    when a deploy misbehaves: not "is something running" but "which build".
    """
    return jsonify(status="ok", models=len(MODEL_REGISTRY), build=BUILD_ID,
                   uploadLimitMb=MAX_UPLOAD_BYTES // (1024 * 1024))


@app.get("/")
def index():
    # `hosted` drives the privacy wording: the local build can honestly promise
    # files never leave the machine, and the hosted one must not.
    return render_template("index.html", hosted=_HOSTED, donate_url=DONATE_URL,
                           build_id=BUILD_ID,
                           upload_limit_mb=MAX_UPLOAD_BYTES // (1024 * 1024))


@app.post("/api/inspect")
def api_inspect():
    try:
        buf, filename = _uploaded_file()
    except UploadRejected as exc:
        return jsonify(error=str(exc)), 400

    try:
        info = inspect_source(buf)
    except UnsupportedSourceError as exc:
        return jsonify(error=str(exc)), 400
    except (ValueError, zipfile.BadZipFile) as exc:
        return jsonify(error=f"Couldn't read {filename}: {exc}"), 400

    return jsonify(_source_info_json(info))


@app.post("/api/convert")
def api_convert():
    try:
        buf, filename = _uploaded_file()
    except UploadRejected as exc:
        return jsonify(error=str(exc)), 400

    target = request.form.get("to")
    if target not in MODEL_REGISTRY:
        return jsonify(error=f"Invalid target: {target!r}"), 400

    if not _CONVERSION_SLOT.acquire(timeout=_CONVERSION_WAIT_SECONDS):
        return jsonify(error="The converter is busy right now. Please try again in a moment."), 503

    try:
        archive, result = convert(buf, target)
    except UnsupportedSourceError as exc:
        return jsonify(error=str(exc)), 400
    except (ValueError, zipfile.BadZipFile) as exc:
        return jsonify(error=f"Couldn't convert {filename}: {exc}"), 400
    except MemoryError:
        return jsonify(
            error="This project is too large for the hosted converter to process. "
            "Run the tool locally -- it has no such limit."
        ), 507
    else:
        # Spooled, not BytesIO: a converted project can be well over a hundred
        # megabytes and there is no reason to hold it in RAM while the client
        # downloads it. Small results never touch the disk at all.
        output_buf = SpooledTemporaryFile(max_size=_SPOOL_TO_DISK_BYTES)
        try:
            archive.write(output_buf)
        finally:
            archive.close()  # releases the source container's file handle
        output_buf.seek(0)
        del archive
    finally:
        _CONVERSION_SLOT.release()

    stem = Path(filename).stem
    download_name = f"{stem}.{target}.3mf"

    response = send_file(
        output_buf,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/octet-stream",
    )
    # Result metadata rides along as a header (all ASCII -- warnings are
    # deliberately English, see color_mapping.py) so the page can show
    # warnings without a second round trip; same-origin, so no CORS needed.
    response.headers["X-Conversion-Result"] = json.dumps(_result_json(result))
    return response


def _open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    # Hosted, a WSGI server runs `app` directly and never reaches this; this
    # path is the local convenience one.
    if not _HOSTED and not os.environ.get("THREEMF_BRIDGE_NO_BROWSER"):
        Timer(1.0, _open_browser).start()
    app.run(host=HOST, port=PORT, debug=False)
