"""Regression tests for two bugs, both around `different_settings_to_system`.

First: dropping the field made converted files open with the *target's*
default print settings -- layer height, infill and wall count silently
reverting -- even though the source's values were present and correct in
project_settings.config.

Then, fixing that by carrying the source project's entries forward made
things worse: it named settings the target slicer has no equivalent for, and
Bambu Studio refused to load the file at all ("The file does not contain any
geometry data") despite the geometry part being byte-identical to the
source's. Hence the invariant these tests protect: never name a key outside
the target's own preset vocabulary.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

from convert.pipeline import MODEL_REGISTRY, convert
from convert.settings_diff import compute_different_settings_to_system, diff_against_preset
from core.archive import ThreeMFArchive
from core.preset_resolver import PresetLibrary, flatten

from .conftest import sample_path

PROFILES = Path(__file__).parent.parent / "profiles"


def _config(archive: ThreeMFArchive) -> dict:
    return json.loads(archive.get_text("Metadata/project_settings.config"))


def _process_vocabulary(vendor_dir: Path) -> set[str]:
    """Every key any of this vendor's process presets defines -- the set of
    print settings the target slicer can actually resolve."""
    lib = PresetLibrary(vendor_dir)
    vocab: set[str] = set()
    for name in lib.names("process"):
        vocab |= set(flatten("process", lib.get("process", name), lib))
    return vocab


def _filament_vocabulary(vendor_dir: Path) -> set[str]:
    """Every key any of this vendor's filament presets defines."""
    lib = PresetLibrary(vendor_dir)
    vocab: set[str] = set()
    for name in lib.names("filament"):
        vocab |= set(flatten("filament", lib.get("filament", name), lib))
    return vocab


def test_structure_is_one_print_n_filament_one_printer():
    """Layout confirmed identical across real U1, H2C and H2D project files."""
    entries = compute_different_settings_to_system(
        config={"layer_height": "0.3"},
        flat_print_profile={"layer_height": "0.2"},
        filament_count=4,
    )
    assert len(entries) == 1 + 4 + 1
    assert entries[0] == "layer_height"
    assert entries[1:] == ["", "", "", "", ""]


def test_preset_name_keys_are_never_reported_as_settings():
    entries = compute_different_settings_to_system(
        config={"print_settings_id": "mine", "name": "mine", "layer_height": "0.3"},
        flat_print_profile={"print_settings_id": "stock", "name": "stock", "layer_height": "0.2"},
        filament_count=1,
    )
    assert entries[0] == "layer_height"


def test_keys_matching_the_target_preset_are_not_reported():
    entries = compute_different_settings_to_system(
        config={"layer_height": "0.2", "wall_loops": "3"},
        flat_print_profile={"layer_height": "0.2", "wall_loops": "2"},
        filament_count=1,
    )
    assert entries[0] == "wall_loops"


def test_keys_the_target_preset_does_not_define_are_never_named():
    """The load-breaking bug: a converted project carries source-slicer-only
    settings, and naming one the target can't resolve makes Bambu Studio
    reject the whole file."""
    entries = compute_different_settings_to_system(
        config={"layer_height": "0.3", "enable_pressure_advance": "1", "slowdown_for_curled_perimeters": "1"},
        flat_print_profile={"layer_height": "0.2"},
        filament_count=1,
    )
    assert entries[0] == "layer_height"


def test_diff_is_empty_when_nothing_deviates():
    entries = compute_different_settings_to_system(
        config={"layer_height": "0.2"},
        flat_print_profile={"layer_height": "0.2"},
        filament_count=2,
    )
    assert entries == ["", "", "", ""]


def test_diff_against_preset_ignores_config_only_keys():
    assert diff_against_preset({"only_in_config": "x"}, {"layer_height": "0.2"}) == []


@pytest.mark.parametrize(
    "source_name,target",
    [
        ("u1_toucan_plus", "h2c"),
        ("u1_majorasmask", "h2c"),
        ("u1_majorasmask", "h2d"),
        ("voronoi_u1", "h2c"),
        ("h2c_antiwarp", "u1"),
        ("h2c_antiwarp", "a1-mini"),
    ],
)
def test_no_converted_file_ever_names_a_key_the_target_cannot_resolve(source_name, target):
    """The guard that would have caught the "no geometry data" bug. Runs over
    real files in both directions, including U1 sources whose configs are full
    of Orca-only settings."""
    archive, _ = convert(sample_path(source_name), target)
    config = _config(archive)

    vendor_dir = PROFILES / ("snapmaker_u1" if target == "u1" else "bambu_h2c")
    vocab = _process_vocabulary(vendor_dir)

    entries = config["different_settings_to_system"]
    named = [k for k in entries[0].split(";") if k]
    assert named, f"{source_name}->{target} marked nothing at all"
    assert set(named) <= vocab, (source_name, target, sorted(set(named) - vocab))

    # The filament sections are now populated too, and the same invariant has
    # to hold for them: every key named must be one the target's own filament
    # presets define. Naming a foreign key here is the identical bug, one
    # scope down.
    filament_vocab = _filament_vocabulary(vendor_dir)
    for slot, section in enumerate(entries[1:-1]):
        slot_named = [k for k in section.split(";") if k]
        assert set(slot_named) <= filament_vocab, (
            source_name, target, slot, sorted(set(slot_named) - filament_vocab)
        )

    # The printer section stays empty: conversion rebuilds machine config
    # wholesale from the target's preset, so nothing in it is a user deviation.
    assert entries[-1] == ""


def test_real_conversion_marks_the_settings_that_differ_from_target_default():
    """End-to-end on the real file that surfaced both bugs: a U1 project whose
    print recipe (0.24mm layers, 4 top shells, support on) differs from the
    H2C stock preset it gets retargeted onto."""
    src_path = sample_path("u1_toucan_plus")
    source = _config(ThreeMFArchive.open(src_path))

    archive, _ = convert(src_path, "h2c")
    config = _config(archive)

    marked = set(config["different_settings_to_system"][0].split(";"))
    lib = PresetLibrary(PROFILES / "bambu_h2c")
    target_defaults = flatten("process", lib.get("process", config["print_settings_id"]), lib)

    # Values carried over untouched...
    for key in ("layer_height", "top_shell_layers", "enable_support", "sparse_infill_density"):
        assert config[key] == source[key], key

    # ...and every one that deviates from the target's stock preset is marked,
    # which is what stops the slicer serving its own default instead.
    for key in ("layer_height", "top_shell_layers", "enable_support"):
        assert config[key] != target_defaults[key], key
        assert key in marked, key

    # ...while ones that happen to match the target's default need no marking.
    assert config["sparse_infill_density"] == target_defaults["sparse_infill_density"]
    assert "sparse_infill_density" not in marked

    assert len(config["different_settings_to_system"]) == 1 + len(config["filament_colour"]) + 1


@pytest.mark.parametrize("target", sorted(set(MODEL_REGISTRY) - {"u1"}))
def test_every_bambu_target_produces_a_well_formed_field(target):
    archive, _ = convert(sample_path("u1_majorasmask"), target)
    config = _config(archive)

    entries = config["different_settings_to_system"]
    assert len(entries) == 1 + len(config["filament_colour"]) + 1
    assert all(isinstance(e, str) for e in entries)
    for key in (k for k in entries[0].split(";") if k):
        assert key in config, (target, key)


def test_every_real_difference_is_declared():
    """Nothing that differs from the target's own print preset may go
    undeclared.

    An undeclared difference is invisible: the value sits in the file, the
    slicer serves its preset's value instead, and nothing reports it. That is
    exactly what a hand-picked filter used to cause -- 27 settings differed on a
    real P1S project and 12 were declared, so the user's tuned speeds were
    silently replaced by Snapmaker U1's. Confirmed in Snapmaker Orca before and
    after the fix.
    """
    from convert.pipeline import _vendor_dir
    from convert.settings_diff import _NOT_A_SETTING, _same_value
    from core.preset_resolver import PresetLibrary, flatten

    archive, _ = convert(sample_path("bambu_da_boss"), "u1")
    buf = BytesIO()
    try:
        archive.write(buf)
    finally:
        archive.close()
    buf.seek(0)
    with ThreeMFArchive.open(buf) as out:
        config = json.loads(out.get_text("Metadata/project_settings.config"))

    library = PresetLibrary(_vendor_dir("u1"))
    preset = flatten("process", library.get("process", config["print_settings_id"]), library)
    declared = set(config["different_settings_to_system"][0].split(";"))

    undeclared = sorted(
        key
        for key, preset_value in preset.items()
        if key not in _NOT_A_SETTING
        and key in config
        and not _same_value(config[key], preset_value)
        and key not in declared
    )
    assert undeclared == [], f"differ from the target preset but not declared: {undeclared}"


def test_declared_keys_stay_inside_the_target_vocabulary():
    """The rule that actually fixed loading: never name a key the target has
    no equivalent for. Naming Snapmaker-only settings in a Bambu project made
    Bambu Studio report the file as having no geometry at all."""
    from core.vocabulary import load_vocabulary

    for source, target, vendor in (
        ("bambu_da_boss", "u1", "snapmaker_u1"),
        ("u1_toucan_plus", "h2c", "bambu_h2c"),
    ):
        archive, _ = convert(sample_path(source), target)
        buf = BytesIO()
        try:
            archive.write(buf)
        finally:
            archive.close()
        buf.seek(0)
        with ThreeMFArchive.open(buf) as out:
            config = json.loads(out.get_text("Metadata/project_settings.config"))

        vocabulary = load_vocabulary(Path(__file__).parent.parent / "profiles" / vendor)
        for section in config["different_settings_to_system"]:
            for key in filter(None, section.split(";")):
                assert key in vocabulary, f"{source}->{target} declares {key!r}, which {target} doesn't define"


# -- filament sections ----------------------------------------------------
#
# These were left empty for a long time on the reasoning that a converted
# project keeps its source filament preset names, so there is nothing in the
# target's library to diff against. filament_mapping.py made that false, and
# nobody revisited it: a 14-colour P1S project converted to U1 had every slot
# differing from the preset it named -- bed temp 55 against 65, max volumetric
# speed 18 against 15 -- and declared none of it, so Snapmaker Orca served its
# own values for all fourteen.


def test_filament_slot_names_a_key_it_actually_differs_on():
    from convert.settings_diff import diff_filament_slot

    config = {"hot_plate_temp": ["55", "65"], "nozzle_temperature": ["220", "220"]}
    preset = {"hot_plate_temp": ["65"], "nozzle_temperature": ["220"]}

    assert diff_filament_slot(config, preset, 0, 2) == ["hot_plate_temp"]
    assert diff_filament_slot(config, preset, 1, 2) == []


def test_filament_slot_never_names_a_key_outside_the_target_preset():
    """The invariant this whole module exists to protect, at filament scope."""
    from convert.settings_diff import diff_filament_slot

    config = {"snapmaker_only_key": ["1"], "hot_plate_temp": ["55"]}
    preset = {"hot_plate_temp": ["65"]}

    assert diff_filament_slot(config, preset, 0, 1) == ["hot_plate_temp"]


def test_filament_slot_skips_per_variant_arrays():
    """A dual-hotend target stores per-filament settings once per extruder
    variant, so index `slot` is a different filament's value entirely.
    Comparing it would assert a deviation that isn't one."""
    from convert.settings_diff import diff_filament_slot

    # 2 filaments x 2 variants -- four entries, not two.
    config = {"nozzle_temperature": ["220", "230", "220", "230"]}
    preset = {"nozzle_temperature": ["220"]}

    assert diff_filament_slot(config, preset, 0, 2) == []


def test_filament_slot_skips_multi_valued_preset_entries():
    from convert.settings_diff import diff_filament_slot

    config = {"some_vector": ["1", "2"]}
    preset = {"some_vector": ["1", "9", "9"]}

    assert diff_filament_slot(config, preset, 0, 2) == []


def test_field_keeps_its_length_when_filament_profiles_are_given():
    """The list length is part of the format the slicer parses: one print
    section, one per filament, one printer section."""
    config = {"hot_plate_temp": ["55", "55", "55"]}
    profiles = [{"hot_plate_temp": ["65"]}, None, {"hot_plate_temp": ["55"]}]

    field = compute_different_settings_to_system(config, {}, 3, profiles)

    assert len(field) == 5
    assert field[1] == "hot_plate_temp"  # differs
    assert field[2] == ""                # preset unresolved
    assert field[3] == ""                # matches
    assert field[-1] == ""               # printer section always empty


def test_omitting_filament_profiles_keeps_the_old_empty_behaviour():
    field = compute_different_settings_to_system({"hot_plate_temp": ["55"]}, {}, 1)
    assert field == ["", "", ""]
