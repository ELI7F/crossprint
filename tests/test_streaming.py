"""Guards on the memory behaviour of conversion.

Conversion used to materialise every part of the container. On the largest real
sample that meant holding 726 MB to rewrite about 200 KB, which put the hosted
converter out of reach of exactly the multi-plate projects people most want
converted. core/archive.py now reads lazily and streams untouched parts from
source to output.

That is a performance property, and performance properties rot silently -- a
stray `get_bytes` in a loop over geometry restores the old behaviour without
failing anything else. These tests fail instead.
"""
from __future__ import annotations

import hashlib
import threading
import time
import zipfile
from io import BytesIO
from tempfile import SpooledTemporaryFile

import pytest

from .conftest import sample_path
from convert import paint_transfer
from convert.paint_transfer import _PAINT_COLOR_RE, _iter_paint_codes, geometry_part_names
from convert.pipeline import convert
from core.archive import ThreeMFArchive

psutil = pytest.importorskip("psutil")


def _uncompressed_size(path) -> int:
    with zipfile.ZipFile(path) as zf:
        return sum(info.file_size for info in zf.infolist())


def _peak_rss(work):
    """Run `work`, sampling RSS, and return (result, peak_bytes)."""
    process = psutil.Process()
    peak = process.memory_info().rss
    stop = threading.Event()

    def sample():
        nonlocal peak
        while not stop.is_set():
            peak = max(peak, process.memory_info().rss)
            time.sleep(0.005)

    watcher = threading.Thread(target=sample)
    watcher.start()
    try:
        result = work()
    finally:
        stop.set()
        watcher.join()
    return result, peak


def test_conversion_does_not_load_the_whole_project():
    """Peak memory must not scale with project size.

    The 11-plate sample is ~726 MB uncompressed. Holding even half of it would
    mean the eager path is back; the streaming path peaks around 75 MB, so a
    quarter is a generous ceiling that still catches the regression.

    The output is spooled to disk exactly as web/app.py does. Collecting it in
    a BytesIO instead would add the 123 MB result to the measurement and blame
    the converter for the test's own buffer.
    """
    source = sample_path("a1mini_woody")
    uncompressed = _uncompressed_size(source)

    def run():
        archive, _ = convert(source, "h2c")
        with SpooledTemporaryFile(max_size=8 << 20) as out:
            try:
                archive.write(out)
            finally:
                archive.close()
            return out.tell()

    written, peak = _peak_rss(run)

    assert written > 0
    assert peak < uncompressed / 4, (
        f"peak RSS {peak / 1024**2:.0f} MB while converting a "
        f"{uncompressed / 1024**2:.0f} MB project -- parts are being held in memory again"
    )


def test_untouched_meshes_are_passed_through_byte_identically():
    """Streaming a part means decompressing and recompressing it. The bytes
    that come out the other side must be the ones that went in -- a mesh that
    is subtly altered in transit would be far worse than one that is too big
    to convert."""
    source = sample_path("a1mini_woody")

    archive, _ = convert(source, "h2c")
    out = BytesIO()
    try:
        archive.write(out)
    finally:
        archive.close()
    out.seek(0)

    with zipfile.ZipFile(source) as before, zipfile.ZipFile(out) as after:
        after_names = set(after.namelist())
        meshes = [n for n in before.namelist() if n.startswith("3D/Objects/") and n.endswith(".model")]
        assert meshes, "sample has no separate object meshes -- this test would prove nothing"
        for name in meshes:
            assert name in after_names, f"{name} vanished from the output"
            expected = hashlib.sha256(before.read(name)).hexdigest()
            assert hashlib.sha256(after.read(name)).hexdigest() == expected, name


def test_streamed_paint_scan_matches_a_whole_buffer_scan(monkeypatch):
    """Chunked scanning must find exactly the codes a single-buffer regex
    finds -- no misses at a chunk boundary, and no double counting of the
    overlap that exists to prevent those misses."""
    source = sample_path("u1_majorasmask")
    with ThreeMFArchive.open(source) as archive:
        part = next(
            (n for n in geometry_part_names(archive) if b"paint_color=" in (archive.get_bytes(n) or b"")),
            None,
        )
        assert part is not None, "sample carries no painted triangles"
        expected = [m.group(1) for m in _PAINT_COLOR_RE.finditer(archive.get_bytes(part))]
        assert len(expected) > 100, "sample too small to straddle a chunk boundary"

        longest = max(len(code) for code in expected)
        assert longest > 100, "sample has no long split-triangle codes -- the boundary case is untested"

        # A chunk far smaller than the data, and smaller than the longest code
        # in it, so boundaries land mid-attribute and mid-value. A fixed-size
        # retained tail silently drops codes here; cutting at the last
        # unterminated attribute does not.
        monkeypatch.setattr(paint_transfer, "_SCAN_CHUNK", 97)
        assert list(_iter_paint_codes(archive, part)) == expected


def test_reading_a_part_after_close_is_an_error_not_silent_corruption():
    """A closed archive has no source to fall back on. Callers that read too
    late should hear about it rather than get None and write a truncated
    project."""
    archive = ThreeMFArchive.open(sample_path("h2c_antiwarp"))
    mesh = next(n for n in archive.names() if n.endswith(".model"))
    archive.close()
    with pytest.raises(ValueError, match="closed"):
        archive.get_bytes(mesh)
