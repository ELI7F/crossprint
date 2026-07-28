from __future__ import annotations

import json
from pathlib import Path

import pytest

from convert.color_mapping import (
    CAPACITY,
    CapacityExceededError,
    hotend_class,
    map_colors_to_bambu,
    map_colors_to_u1,
    remap_object_extruders,
)
from core.archive import ThreeMFArchive
from core.model_settings import ModelSettings
from core.preset_resolver import PresetLibrary, flatten

from .conftest import sample_path

PROFILES = Path(__file__).parent.parent / "profiles"
BBL = PresetLibrary(PROFILES / "bambu_h2c")


def test_map_colors_to_h2c_matches_the_verified_reference_case():
    """h2c_antiwarp is the real sample the AMS-routing constants in
    color_mapping.py were copied from -- converting *to* H2C with its own
    6 colors should reproduce those exact values with no warnings."""
    archive = ThreeMFArchive.open(sample_path("h2c_antiwarp"))
    config = json.loads(archive.get_text("Metadata/project_settings.config"))

    result = map_colors_to_bambu(
        "Bambu Lab H2C", config["filament_colour"], config["filament_type"], config["filament_settings_id"],
        BBL, "Bambu Lab H2C 0.4 nozzle",
    )

    assert result.warnings == []
    assert result.target_fields["master_extruder_id"] == config["master_extruder_id"]
    assert result.target_fields["physical_extruder_map"] == config["physical_extruder_map"]
    assert result.target_fields["extruder_ams_count"] == config["extruder_ams_count"]
    assert result.target_fields["extruder_nozzle_stats"] == config["extruder_nozzle_stats"]
    assert result.target_fields["filament_map"] == config["filament_map"]

    # Regression: these must be the *narrowed* real-project values, not the
    # full nozzle-variant superset from the system machine preset (that
    # mismatch is what made a real converted file unopenable in Bambu Studio).
    for key in (
        "printer_extruder_variant", "printer_extruder_id", "nozzle_volume", "nozzle_type",
        "machine_max_jerk_z", "machine_max_acceleration_x", "retraction_distances_when_cut",
    ):
        assert result.target_fields[key] == config[key], key


def test_map_colors_to_h2d_matches_a_second_independent_real_sample():
    """Cross-check against a *different* real Vortek printer (H2D) with a
    different real filament count (4) -- confirms the shared kinematics
    constants aren't an H2C-only coincidence."""
    path = Path.home() / "Downloads" / "תלת מימד" / "ArticulatedCuteHedgehog_Multicolor4ColorBambuStudioH2D.3mf"
    if not path.exists():
        pytest.skip("H2D sample not present on this machine")
    config = json.loads(ThreeMFArchive.open(path).get_text("Metadata/project_settings.config"))

    result = map_colors_to_bambu(
        "Bambu Lab H2D", config["filament_colour"], config["filament_type"], config["filament_settings_id"],
        BBL, "Bambu Lab H2D 0.4 nozzle",
    )

    assert result.warnings == []
    assert result.target_fields["extruder_ams_count"] == config["extruder_ams_count"]
    assert "extruder_nozzle_stats" not in result.target_fields  # real H2D sample doesn't carry this key
    for key in ("printer_extruder_variant", "printer_extruder_id", "nozzle_volume", "machine_max_jerk_z"):
        assert result.target_fields[key] == config[key], key


def test_map_colors_to_a1_mini_matches_real_single_hotend_sample():
    """benchy_ams is a real single-hotend (A1 mini) project -- confirms the
    'single' hotend class produces scalar nozzle_type/nozzle_volume (not
    the system preset's list[1]) and excludes every Vortek-only field."""
    archive = ThreeMFArchive.open(sample_path("benchy_ams"))
    config = json.loads(archive.get_text("Metadata/project_settings.config"))

    machine = BBL.get("machine", "Bambu Lab A1 mini 0.4 nozzle")
    assert hotend_class(machine) == "single"

    result = map_colors_to_bambu(
        "Bambu Lab A1 mini", config["filament_colour"], config["filament_type"], config["filament_settings_id"],
        BBL, "Bambu Lab A1 mini 0.4 nozzle",
    )

    # Shape is the thing under test, and the thing that was broken: a real
    # saved project stores these as bare scalars, while the system preset
    # stores them as list[1]. Values are deliberately NOT compared against
    # this sample -- it was saved by Bambu Studio 01.08.02.54 (Jan 2024) and
    # nozzle_volume has since changed from "32" to "92" in the vendor preset.
    # Emitting the *current* preset's value is correct; matching a two-year-
    # old file would not be.
    assert isinstance(result.target_fields["nozzle_type"], str)
    assert isinstance(result.target_fields["nozzle_volume"], str)
    assert isinstance(config["nozzle_type"], str)  # real file confirms scalar is the right shape
    assert isinstance(config["nozzle_volume"], str)
    assert result.target_fields["nozzle_type"] == config["nozzle_type"]  # this one didn't drift

    for key in ("master_extruder_id", "physical_extruder_map", "filament_map", "printer_extruder_id"):
        assert key in result.exclude_fields, key
        assert key not in config, key  # confirm the real file agrees these don't belong


def test_hotend_class_needs_the_unflattened_preset():
    """Regression: `master_extruder_id` is also set on the shared root
    preset every Bambu machine inherits from, so classifying a *flattened*
    preset would call the entire lineup Vortek. Verified split across the
    real vendored library."""
    for name, expected in [
        ("Bambu Lab H2C 0.4 nozzle", "vortek"),
        ("Bambu Lab H2D 0.4 nozzle", "vortek"),
        ("Bambu Lab H2D Pro 0.4 nozzle", "vortek"),
        ("Bambu Lab H2S 0.4 nozzle", "single"),
        ("Bambu Lab X2D 0.4 nozzle", "single"),
        ("Bambu Lab A1 mini 0.4 nozzle", "single"),
        ("Bambu Lab P1S 0.4 nozzle", "single"),
        ("Bambu Lab X1 Carbon 0.4 nozzle", "single"),
    ]:
        machine = BBL.get("machine", name)
        assert machine is not None, name
        assert hotend_class(machine) == expected, name
        # the flattened form is exactly the trap this guards against
        assert hotend_class(flatten("machine", machine, BBL)) == "vortek", name


def test_map_colors_to_h2c_raises_past_ams_capacity():
    colours = [f"#{i:06X}" for i in range(25)]
    with pytest.raises(CapacityExceededError) as exc_info:
        map_colors_to_bambu("Bambu Lab H2C", colours, ["PLA"] * 25, ["Generic PLA"] * 25, BBL, "Bambu Lab H2C 0.4 nozzle")
    assert exc_info.value.needed == 25
    assert exc_info.value.capacity == CAPACITY["vortek_with_ams"]


def test_map_colors_to_u1_warns_but_does_not_raise_for_real_8color_project():
    """majorasmask_8color_snapmakeru1.3mf is a real, working U1 project with
    8 colors and zero physical-routing metadata -- U1 must not hard-error
    here the way H2C does."""
    archive = ThreeMFArchive.open(sample_path("u1_majorasmask"))
    config = json.loads(archive.get_text("Metadata/project_settings.config"))
    assert len(config["filament_colour"]) == 8

    result = map_colors_to_u1(config["filament_colour"], config["filament_type"], config["filament_settings_id"])

    assert result.target_fields == {}
    assert len(result.warnings) == 1
    assert "8 colors" in result.warnings[0]


def test_map_colors_to_u1_no_warning_within_toolhead_count():
    result = map_colors_to_u1(["#000000", "#FFFFFF"], ["PLA", "PLA"], ["A", "B"])
    assert result.warnings == []


def test_remap_object_extruders_identity_leaves_real_file_unchanged():
    archive = ThreeMFArchive.open(sample_path("u1_majorasmask"))
    ms = ModelSettings.parse(archive.get_text("Metadata/model_settings.config"))
    before = [(o.id, o.extruder) for o in ms.objects]

    identity = {i: i for i in range(1, 9)}
    remap_object_extruders(ms, identity)

    after = [(o.id, o.extruder) for o in ms.objects]
    assert before == after


def test_remap_object_extruders_actually_rewrites_values():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>'
        '<object id="1"><metadata key="extruder" value="2"/></object>'
        "</config>"
    )
    ms = ModelSettings.parse(xml)
    remap_object_extruders(ms, {2: 5})
    assert ms.objects[0].extruder == "5"
