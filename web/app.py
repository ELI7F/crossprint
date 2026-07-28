"""Web UI for Crossprint: drag in a .3mf, get the converted one back.

Runs locally by default (127.0.0.1, opens a browser). Set PORT -- as every
PaaS does -- and it binds publicly instead and skips the browser, so the same
module serves both cases.

Both endpoints are thin wrappers around convert/pipeline.py; all the actual
conversion logic and its test coverage live there, same as cli.py.

Size limits exist because a .3mf is a zip and conversion holds the whole thing
uncompressed in memory. Measured on the largest real sample: a 20 MB upload
expands to 131 MB of geometry and peaks at 197 MB RSS, about 1.5x the
uncompressed size. A 512 MB container -- the usual free tier -- cannot survive
an unbounded upload, so the uncompressed size is checked up front from the zip
directory rather than discovered by running out of memory.
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
import zipfile
from io import BytesIO
from pathlib import Path
from threading import Semaphore, Timer

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

# Compressed upload cap. Generous locally; hosted, it's a first-pass filter
# and the uncompressed ceiling below is what actually protects the container.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "80" if _HOSTED else "300")) * 1024 * 1024

# Uncompressed ceiling, set from measurement rather than guesswork: converting
# a 131 MB project peaks at 197 MB RSS, i.e. ~1.5x the uncompressed size plus
# ~25 MB of interpreter and preset library. At 220 MB that predicts a ~355 MB
# peak, leaving useful headroom on a 512 MB instance -- and _CONVERSION_SLOT
# below guarantees only one conversion is in flight to spend it.
MAX_UNCOMPRESSED_BYTES = int(os.environ.get("MAX_UNCOMPRESSED_MB", "220" if _HOSTED else "1024")) * 1024 * 1024

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

    buf = BytesIO(file.read())
    _reject_if_too_large_uncompressed(buf)
    return buf, file.filename


def _reject_if_too_large_uncompressed(buf: BytesIO) -> None:
    """Check the zip directory before loading anything.

    A .3mf's compressed size says little about what conversion will cost: the
    geometry part compresses ~7x, so a modest upload can still exhaust the
    container. The directory gives the real figure without reading the data.
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
    """Liveness probe, and what a platform's uptime check should poll."""
    return jsonify(status="ok", models=len(MODEL_REGISTRY))


@app.get("/")
def index():
    # `hosted` drives the privacy wording: the local build can honestly promise
    # files never leave the machine, and the hosted one must not.
    return render_template("index.html", hosted=_HOSTED, donate_url=DONATE_URL)


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
        output_buf = BytesIO()
        archive.write(output_buf)  # assembling the zip is itself memory-heavy
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
