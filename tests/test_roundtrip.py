"""Phase 1 acceptance criterion: read a real .3mf, write it back unmodified,
and confirm the result is semantically identical -- same parts, same parsed
project_settings.config, same parsed model_settings.config structure. Not
byte-identical (zip compression/timestamps differ), but every part and every
key/value survives.
"""
from __future__ import annotations

import zipfile
from io import BytesIO

from core.archive import ThreeMFArchive
from core.model_settings import ModelSettings
from core.project_settings import ProjectSettings

from .conftest import SAMPLES, sample_path


def _rewrite_in_memory(archive: ThreeMFArchive) -> BytesIO:
    """Round-trip through a buffer rather than a temp file. Writing scratch
    .3mf files next to the samples (in Downloads) made these tests flaky on
    Windows -- an indexer or AV scanner would still hold the freshly written
    file when cleanup ran, raising PermissionError. Nothing here needs a real
    file, and ThreeMFArchive reads and writes streams."""
    buf = BytesIO()
    archive.write(buf)
    buf.seek(0)
    return buf


def test_archive_roundtrip_preserves_all_parts():
    for name in SAMPLES:
        original = ThreeMFArchive.open(sample_path(name))
        reopened = ThreeMFArchive.open(_rewrite_in_memory(original))

        assert set(reopened.names()) == set(original.names()), name
        for part_name in original.names():
            assert reopened.get_bytes(part_name) == original.get_bytes(part_name), (name, part_name)


def test_archive_roundtrip_is_a_valid_zip():
    original = ThreeMFArchive.open(sample_path("u1_majorasmask"))

    with zipfile.ZipFile(_rewrite_in_memory(original)) as zf:
        assert zf.testzip() is None
        assert zf.namelist()[0] == "[Content_Types].xml"


def test_roundtrip_preserves_per_part_compression_method():
    """Real Bambu Studio output stores the PNG thumbnails uncompressed and
    deflates everything else. Rewriting every part with deflate would still be
    a valid zip, but it makes the container differ from every reference file
    for no reason -- an unnecessary variable when a converted file won't
    load."""
    path = sample_path("u1_toucan_plus")
    with zipfile.ZipFile(path) as zf:
        source_methods = {i.filename: i.compress_type for i in zf.infolist() if not i.is_dir()}
    assert len(set(source_methods.values())) > 1, "sample should mix stored and deflated parts"

    with zipfile.ZipFile(_rewrite_in_memory(ThreeMFArchive.open(path))) as zf:
        rewritten = {i.filename: i.compress_type for i in zf.infolist() if not i.is_dir()}

    assert rewritten == source_methods


def test_project_settings_roundtrip():
    for name in SAMPLES:
        path = sample_path(name)
        archive = ThreeMFArchive.open(path)
        text = archive.get_text("Metadata/project_settings.config")
        assert text is not None, name
        parsed = ProjectSettings.parse(text)
        reparsed = ProjectSettings.parse(parsed.to_json())
        assert reparsed.data == parsed.data, name


def test_model_settings_roundtrip():
    for name in SAMPLES:
        path = sample_path(name)
        archive = ThreeMFArchive.open(path)
        text = archive.get_text("Metadata/model_settings.config")
        assert text is not None, name
        parsed = ModelSettings.parse(text)
        reparsed = ModelSettings.parse(parsed.to_xml())

        def as_tuples(ms: ModelSettings):
            return [
                (obj.id, obj.get_metadata("name"), obj.extruder, [(p.id, p.subtype) for p in obj.parts])
                for obj in ms.objects
            ]

        assert as_tuples(reparsed) == as_tuples(parsed), name


def test_u1_sample_has_stray_p1s_machine_snapshot():
    """Ground-truth regression check for the discovery documented in
    profiles/SOURCES.md: this U1 project's machine_settings_1.config is a
    leftover Bambu Lab P1S preset, not a Snapmaker U1 one. If this ever stops
    being true (e.g. someone re-exports a cleaned-up fixture), the
    "numbered snapshots can be irrelevant" assumption in project_settings.py's
    docstring needs re-checking against a fresh example instead."""
    path = sample_path("u1_majorasmask")
    archive = ThreeMFArchive.open(path)
    text = archive.get_text("Metadata/machine_settings_1.config")
    assert text is not None
    cfg = ProjectSettings.parse(text)
    assert cfg.printer_model == "Bambu Lab P1S"

    project = ProjectSettings.parse(archive.get_text("Metadata/project_settings.config"))
    assert project.printer_model == "Snapmaker U1"
