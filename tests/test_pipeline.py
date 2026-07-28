from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

import cli
from convert.pipeline import MODEL_REGISTRY, UnsupportedSourceError, convert, inspect_source
from core.archive import ThreeMFArchive
from core.field_policy import FieldPolicy
from core.model_settings import ModelSettings

from .conftest import DOWNLOADS, sample_path

# Machine-shaped routing settings. Conversion leaves every one of these out so
# the target slicer derives them from its own presets -- writing them is what
# made Bambu Studio reject converted projects (see core/slicer_owned.py).
_VORTEK_ONLY_KEYS = (
    "master_extruder_id",
    "physical_extruder_map",
    "extruder_ams_count",
    "extruder_nozzle_stats",
)
_DROPPED_BASENAMES_PREFIXES = ("machine_settings_", "process_settings_", "filament_settings_")

# Regenerate-bucket fields whose length tracks the project's filament count
# rather than the printer's hardware, so comparing them against a reference
# file with a different number of colors proves nothing.
_FILAMENT_COUNT_SIZED = {"filament_map", "different_settings_to_system"}


def _project_settings(archive: ThreeMFArchive) -> dict:
    return json.loads(archive.get_text("Metadata/project_settings.config"))


def _assert_no_dropped_files_remain(archive: ThreeMFArchive) -> None:
    for name in archive.names():
        base = name.rsplit("/", 1)[-1]
        assert not base.startswith(_DROPPED_BASENAMES_PREFIXES), name
        assert base not in ("slice_info.config", "filament_sequence.json"), name
        assert not (base.startswith("plate_") and base.endswith(".json")), name


def _make_minimal_3mf(printer_model: str, filament_count: int = 1) -> BytesIO:
    """A synthetic, minimal-but-valid .3mf for testing the vendor-detection
    reject path in isolation -- doesn't depend on finding a real file for an
    unsupported printer, which got harder to guarantee once the registry
    grew to cover most of the Bambu lineup."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types '
            'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel-1" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>',
        )
        zf.writestr(
            "3D/3dmodel.model",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
            "<resources></resources><build></build></model>",
        )
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps({"printer_model": printer_model, "filament_colour": ["#FFFFFF"] * filament_count}),
        )
    buf.seek(0)
    return buf


def test_u1_to_h2c_real_8color_project():
    archive, result = convert(sample_path("u1_majorasmask"), "h2c")

    assert result.source_vendor == "u1"
    assert result.target_vendor == "h2c"
    assert result.filament_count == 8
    # AMS routing reference-count mismatch (8 != 6). Asserted by content rather
    # than by warning count, so adding an unrelated warning elsewhere doesn't
    # fail this test for the wrong reason.
    assert any("8 colors" in w and "extruder_ams_count" in w for w in result.warnings), result.warnings

    config = _project_settings(archive)
    assert config["printer_model"] == "Bambu Lab H2C"
    assert config["printer_settings_id"] == "Bambu Lab H2C 0.4 nozzle"
    # Machine-shaped settings are deliberately absent -- the slicer derives them
    # from its own presets for the printer we name. Writing them ourselves is
    # what made Bambu Studio reject the project; see core/slicer_owned.py.
    for key in _VORTEK_ONLY_KEYS:
        assert key not in config, key
    assert config["filament_map"] == ["1"] * 8
    assert config["filament_colour"] == json.loads(
        ThreeMFArchive.open(sample_path("u1_majorasmask")).get_text("Metadata/project_settings.config")
    )["filament_colour"]

    _assert_no_dropped_files_remain(archive)

    ms = ModelSettings.parse(archive.get_text("Metadata/model_settings.config"))
    assert len(ms.objects) > 0


def test_h2c_to_u1_real_6color_project():
    archive, result = convert(sample_path("h2c_antiwarp"), "u1")

    assert result.source_vendor == "h2c"
    assert result.target_vendor == "u1"
    assert result.filament_count == 6

    config = _project_settings(archive)
    assert config["printer_model"] == "Snapmaker U1"
    assert config["printer_settings_id"] == "Snapmaker U1 (0.4 nozzle)"
    for key in _VORTEK_ONLY_KEYS:
        assert key not in config, key  # must not leak H2C-only routing fields into a U1 project

    _assert_no_dropped_files_remain(archive)


def test_h2c_output_field_shapes_match_a_real_working_h2c_file():
    """Regression test for a real bug: converting u1_toucan_plus (4 colors)
    to H2C once produced machine-kinematic arrays sized for the *full*
    nozzle-variant superset the system preset enumerates (10/5 entries)
    instead of what a real saved project actually contains (8/4 entries) --
    Bambu Studio rejected the file outright as "Invalid configuration file".
    This checks every regenerate-bucket field's *shape* against a real,
    known-working H2C file for every U1 source we have, not just the one
    filament count (6) the AMS-routing constants were originally verified
    against."""
    reference = _project_settings(ThreeMFArchive.open(sample_path("h2c_antiwarp")))
    policy = FieldPolicy.load()

    for source_name in ("u1_majorasmask", "u1_toucan_plus", "voronoi_u1"):
        path = sample_path(source_name)
        archive, _ = convert(path, "h2c")
        config = _project_settings(archive)

        for key in policy.regenerate:
            if key not in reference or key not in config or key in _FILAMENT_COUNT_SIZED:
                continue
            ref_shape = len(reference[key]) if isinstance(reference[key], list) else None
            got_shape = len(config[key]) if isinstance(config[key], list) else None
            assert got_shape == ref_shape, (source_name, key, reference[key], config[key])


def test_h2d_output_field_shapes_match_a_real_working_h2d_file():
    """Same regression class as above, checked against H2D specifically --
    the shared _VORTEK_VERIFIED_MACHINE_FIELDS constants must actually match
    H2D's own real shapes, not just H2C's."""
    path = DOWNLOADS / "תלת מימד" / "ArticulatedCuteHedgehog_Multicolor4ColorBambuStudioH2D.3mf"
    if not path.exists():
        pytest.skip("H2D sample not present")
    reference = _project_settings(ThreeMFArchive.open(path))
    policy = FieldPolicy.load()

    archive, result = convert(sample_path("u1_majorasmask"), "h2d")
    config = _project_settings(archive)
    assert config["printer_model"] == "Bambu Lab H2D"

    for key in policy.regenerate:
        if key not in reference or key not in config or key in _FILAMENT_COUNT_SIZED:
            continue
        ref_shape = len(reference[key]) if isinstance(reference[key], list) else None
        got_shape = len(config[key]) if isinstance(config[key], list) else None
        assert got_shape == ref_shape, (key, reference[key], config[key])


def test_a1_mini_output_matches_real_single_hotend_file():
    """benchy_ams (A1 mini) round-tripped through the *source* side, then
    used as the *target*-shape reference for a U1 source converted to A1
    mini -- confirms the single-hotend class works end to end through the
    real pipeline, not just in convert.color_mapping unit tests."""
    reference = _project_settings(ThreeMFArchive.open(sample_path("benchy_ams")))

    archive, result = convert(sample_path("h2c_antiwarp"), "a1-mini")
    config = _project_settings(archive)

    assert config["printer_model"] == "Bambu Lab A1 mini"
    # The real single-hotend reference stores nozzle_type as a bare scalar; we
    # now omit the key entirely and let the slicer supply it, which sidesteps
    # the shape question and every version-dependent width along with it.
    assert isinstance(reference["nozzle_type"], str)
    for key in ("nozzle_type", "nozzle_volume"):
        assert key not in config, key
    for key in _VORTEK_ONLY_KEYS:
        assert key not in config, key
    assert not any("isn't independently verified" in w for w in result.warnings)  # a1-mini is well-verified


def test_h2d_pro_conversion_warns_as_unverified():
    """H2D Pro has no real sample to check against -- it must still work
    (reuses H2D's verified AMS routing as a same-generation best-effort
    default) but must say so."""
    archive, result = convert(sample_path("h2c_antiwarp"), "h2d-pro")
    config = _project_settings(archive)
    assert config["printer_model"] == "Bambu Lab H2D Pro"
    assert any("isn't independently verified" in w for w in result.warnings)


def test_unsupported_source_printer_rejected_cleanly():
    with pytest.raises(UnsupportedSourceError):
        convert(_make_minimal_3mf("Some Random 3D Printer"), "h2c")


def test_converting_to_same_vendor_is_rejected():
    with pytest.raises(ValueError, match="already h2c"):
        convert(sample_path("h2c_antiwarp"), "h2c")


def test_voronoi_u1_to_h2c_resembles_the_real_paired_h2c_export():
    """voronoi_u1 and voronoi_nonu1 are the same underlying model, one
    exported for U1 and one for H2C -- converting the U1 one to H2C should
    land on the same target machine identity as the real H2C export."""
    archive, result = convert(sample_path("voronoi_u1"), "h2c")
    config = _project_settings(archive)

    real_h2c = _project_settings(ThreeMFArchive.open(sample_path("voronoi_nonu1")))
    assert config["printer_model"] == real_h2c["printer_model"] == "Bambu Lab H2C"
    assert result.filament_count == len(real_h2c["filament_colour"])


def test_output_is_a_valid_reopenable_archive(tmp_path):
    archive, _ = convert(sample_path("u1_majorasmask"), "h2c")
    out = tmp_path / "converted.3mf"
    archive.write(out)

    reopened = ThreeMFArchive.open(out)
    config = json.loads(reopened.get_text("Metadata/project_settings.config"))
    assert config["printer_model"] == "Bambu Lab H2C"


def test_inspect_source_lists_every_other_registered_model_as_a_target():
    info = inspect_source(sample_path("h2c_antiwarp"))
    assert info.vendor == "h2c"
    assert set(info.available_targets) == set(MODEL_REGISTRY) - {"h2c"}


def test_cli_end_to_end(tmp_path, capsys):
    src = sample_path("h2c_antiwarp")
    out = tmp_path / "out.3mf"

    exit_code = cli.main(["convert", str(src), "--to", "u1", "-o", str(out)])

    assert exit_code == 0
    assert out.exists()
    captured = capsys.readouterr()
    assert "wrote" in captured.out
    config = json.loads(ThreeMFArchive.open(out).get_text("Metadata/project_settings.config"))
    assert config["printer_model"] == "Snapmaker U1"


def test_cli_reports_error_and_nonzero_exit_for_unsupported_source(tmp_path, capsys):
    src = tmp_path / "unsupported.3mf"
    src.write_bytes(_make_minimal_3mf("Some Random 3D Printer").read())

    exit_code = cli.main(["convert", str(src), "--to", "h2c", "-o", str(tmp_path / "out.3mf")])

    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_cli_models_command_lists_every_registered_slug(capsys):
    exit_code = cli.main(["models"])
    assert exit_code == 0
    out = capsys.readouterr().out
    for slug in MODEL_REGISTRY:
        assert slug in out
