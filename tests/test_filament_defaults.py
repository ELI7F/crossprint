"""Filling in the target's filament preset values the project has no answer for.

Reported by converting a Snapmaker U1 project to an H2C: Bambu Studio opened
it with "Partial purging volume set to 0. Multi-color printing may cause color
mixing in models." `filament_prime_volume` comes from the filament preset, the
U1 side never sets it, and nothing filled the gap -- so the slicer read the
partial purge volume as zero.
"""
from __future__ import annotations

import json

import pytest

from convert.filament_defaults import fill_missing_filament_defaults
from convert.pipeline import _vendor_dir, convert
from core.preset_resolver import PresetLibrary

from .conftest import sample_path

BAMBU = PresetLibrary(_vendor_dir("h2c"))


def test_a_missing_key_is_taken_from_the_preset_each_slot_names():
    result = fill_missing_filament_defaults(
        {}, ["Bambu PLA Basic @BBL H2C"] * 3, BAMBU, filament_count=3
    )

    assert result.config["filament_prime_volume"] == ["30", "30", "30"]
    assert "filament_prime_volume" in result.filled


def test_each_slot_gets_its_own_materials_value():
    """Which is the whole reason the slots name different presets."""
    result = fill_missing_filament_defaults(
        {}, ["Bambu PLA Basic @BBL H2C", "Bambu PETG HF @BBL H2C"], BAMBU, filament_count=2
    )

    values = result.config["filament_density"]
    assert len(values) == 2
    assert values[0] != values[1], "PLA and PETG do not have the same density"


def test_a_value_the_project_already_has_is_never_overwritten():
    config = {"filament_prime_volume": ["99", "99"]}

    result = fill_missing_filament_defaults(
        config, ["Bambu PLA Basic @BBL H2C"] * 2, BAMBU, filament_count=2
    )

    assert result.config["filament_prime_volume"] == ["99", "99"]
    assert "filament_prime_volume" not in result.filled


def test_keys_that_are_not_per_filament_are_left_alone():
    """`required_nozzle_HRC` sits in the filament preset but a real H2C project
    holds four entries for six filaments -- it is sized by nozzle variant.
    Filling it per slot and letting the per-variant expansion widen it produced
    eight, and the shape regression test caught it."""
    result = fill_missing_filament_defaults(
        {}, ["Bambu PLA Basic @BBL H2C"] * 4, BAMBU, filament_count=4
    )

    assert "required_nozzle_HRC" not in result.config
    assert all(k.startswith("filament_") for k in result.filled)


def test_preset_plumbing_is_never_copied_into_a_project():
    """Copying `include` or the compatibility conditions would claim the
    project *is* that preset."""
    result = fill_missing_filament_defaults(
        {}, ["Bambu PLA Basic @BBL H2C"], BAMBU, filament_count=1
    )

    for key in ("include", "compatible_printers", "filament_settings_id", "filament_colour"):
        assert key not in result.filled


def test_a_multi_valued_preset_entry_is_not_split_across_slots():
    """It cannot be one slot's value, and truncating to the first element
    would invent data."""
    library = PresetLibrary(_vendor_dir("h2c"))
    result = fill_missing_filament_defaults({}, ["Bambu PLA Basic @BBL H2C"] * 2, library, 2)

    for key, value in result.config.items():
        assert len(value) == 2, (key, value)


def test_an_unresolvable_preset_name_fills_nothing():
    result = fill_missing_filament_defaults({}, ["No Such Preset"], BAMBU, filament_count=1)
    assert result.filled == []


def test_a_real_conversion_carries_the_purge_volume_the_warning_was_about():
    archive, result = convert(sample_path("u1_toucan_plus"), "h2c")
    try:
        config = json.loads(archive.get_text("Metadata/project_settings.config"))
    finally:
        archive.close()

    n = result.filament_count
    # n, not 2n: real H2C and H2D files hold one entry per filament for this
    # one, even though most per-filament settings are stored per variant.
    assert len(config["filament_prime_volume"]) == n
    assert all(v not in ("", "0") for v in config["filament_prime_volume"])


def test_a_blank_filament_gcode_takes_the_targets_placeholder():
    """Bambu Studio compares every G-code field against the preset the project
    names and shows a "Modified G-code ... confirm these are safe to prevent
    any damage to the machine" dialog on any difference. A Snapmaker source
    carries an empty filament_start_gcode; the Bambu preset's own value is the
    comment "; filament start gcode". Empty against a comment executes nothing
    either way, but a safety dialog on every converted file teaches people to
    click through safety dialogs."""
    result = fill_missing_filament_defaults(
        {"filament_start_gcode": ["", ""]}, ["Bambu PLA Basic @BBL H2C"] * 2, BAMBU, 2
    )

    assert result.config["filament_start_gcode"] == ["; filament start gcode\n"] * 2
    assert "filament_start_gcode" in result.filled


def test_gcode_the_user_actually_wrote_is_never_replaced():
    """It does run, and a vendor placeholder would silently discard it."""
    mine = ["M106 S255", "M106 S255"]
    result = fill_missing_filament_defaults(
        {"filament_start_gcode": list(mine)}, ["Bambu PLA Basic @BBL H2C"] * 2, BAMBU, 2
    )

    assert result.config["filament_start_gcode"] == mine


def test_only_gcode_fields_are_refilled_when_blank():
    """An empty value elsewhere can be meaningful, so blankness alone is not
    a reason to overwrite."""
    result = fill_missing_filament_defaults(
        {"filament_notes": [""]}, ["Bambu PLA Basic @BBL H2C"], BAMBU, 1
    )
    assert result.config["filament_notes"] == [""]


def test_every_gcode_field_in_a_real_conversion_matches_its_preset():
    """The whole point: no Modified G-code dialog."""
    from convert.pipeline import _machine_preset_name
    from core.preset_resolver import flatten

    archive, _ = convert(sample_path("u1_toucan_plus"), "h2c")
    try:
        config = json.loads(archive.get_text("Metadata/project_settings.config"))
    finally:
        archive.close()

    library = PresetLibrary(_vendor_dir("h2c"))
    filament = flatten("filament", library.get("filament", config["filament_settings_id"][0]), library)
    machine = flatten("machine", library.get("machine", _machine_preset_name("h2c")), library)

    def first(value):
        if isinstance(value, list):
            value = value[0] if value else ""
        return value or ""

    for key in (k for k in config if k.endswith("_gcode")):
        expected = filament.get(key) if key.startswith("filament_") else machine.get(key)
        assert first(config[key]) == first(expected), key
