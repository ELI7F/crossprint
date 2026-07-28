from __future__ import annotations

import json

from core.archive import ThreeMFArchive
from core.field_policy import FieldPolicy

from .conftest import sample_path


def test_field_map_loads():
    policy = FieldPolicy.load()
    assert policy.default_policy == "passthrough"
    assert "master_extruder_id" in policy.regenerate
    assert "different_settings_to_system" in policy.regenerate


def test_split_real_h2c_project_settings():
    archive = ThreeMFArchive.open(sample_path("h2c_antiwarp"))
    config = json.loads(archive.get_text("Metadata/project_settings.config"))
    policy = FieldPolicy.load()

    passthrough, regenerate, drop = policy.split(config)

    # every key accounted for exactly once
    assert passthrough.keys() | regenerate.keys() | drop.keys() == config.keys()
    assert not (passthrough.keys() & regenerate.keys())
    assert not (passthrough.keys() & drop.keys())

    # known machine-identity / AMS-routing fields must land in regenerate
    for key in ("printer_model", "printer_settings_id", "master_extruder_id", "physical_extruder_map"):
        assert key in regenerate, key

    # print-recipe / material fields must land in passthrough
    for key in ("sparse_infill_density", "filament_type", "filament_colour", "wall_loops"):
        assert key in passthrough, key


def test_different_settings_to_system_is_regenerated_not_dropped():
    """u1_majorasmask carries this key; h2c_antiwarp happens not to. It must
    land in `regenerate` either way -- convert/pipeline.py recomputes it for
    the target rather than copying or discarding it. Dropping it was a real
    bug: it's the only thing telling the slicer that a project naming a stock
    preset doesn't hold that preset's values, so without it the user's layer
    height / infill / wall count were silently replaced by the target's
    defaults (see convert/settings_diff.py)."""
    archive = ThreeMFArchive.open(sample_path("u1_majorasmask"))
    config = json.loads(archive.get_text("Metadata/project_settings.config"))
    assert "different_settings_to_system" in config

    _, regenerate, drop = FieldPolicy.load().split(config)
    assert "different_settings_to_system" in regenerate
    assert "different_settings_to_system" not in drop
