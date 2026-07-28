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

    # Filament and printer sections stay empty -- there's no target preset to
    # diff them against, and the source's entries name foreign settings.
    assert entries[1:] == [""] * (len(config["filament_colour"]) + 1)


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
