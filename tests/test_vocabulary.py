"""The vocabulary filter is the fix for a run of failures that each looked
like a separate bug -- "Invalid configuration file", print settings reverting
to target defaults, and "The file does not contain any geometry data" on a
file whose geometry part was byte-identical to the source's. All three came
from handing Bambu Studio a project full of Snapmaker-only settings.

Two properties matter, and they pull against each other:

  * completeness -- never drop a key the target actually understands, or
    conversion silently deletes part of the user's project;
  * discrimination -- actually remove the foreign keys, or the bug is back.

Both are checked against real project files written by the real slicers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from convert.pipeline import convert
from core.archive import ThreeMFArchive
from core.vocabulary import filter_to_vocabulary, load_enums, load_vocabulary, repair_enum_values

from .conftest import DOWNLOADS, sample_path

PROFILES = Path(__file__).parent.parent / "profiles"
BAMBU = PROFILES / "bambu_h2c"
SNAPMAKER = PROFILES / "snapmaker_u1"

# Real Bambu-Studio-written projects, spanning three printers and two major
# slicer versions. Every key in them is by definition valid Bambu vocabulary.
_REAL_BAMBU_FILES = {
    "h2c_antiwarp": DOWNLOADS / "תלת מימד" / "H2C_Anti-Warp-UltimateDiffuser-H2.3mf",
    "voronoi_nonu1": DOWNLOADS / "Voronoi+Toucan+AMS+170%" / "Voronoi+Toucan+AMS+170%.3mf",
    "h2d_hedgehog": DOWNLOADS / "תלת מימד" / "ArticulatedCuteHedgehog_Multicolor4ColorBambuStudioH2D.3mf",
    "a1mini_benchy": DOWNLOADS / "תלת מימד" / "benchy ams test.3mf",
}


def _project_keys(path: Path) -> set[str]:
    return set(json.loads(ThreeMFArchive.open(path).get_text("Metadata/project_settings.config")))


def test_vocabularies_load_and_are_substantial():
    assert len(load_vocabulary(BAMBU)) > 900
    assert len(load_vocabulary(SNAPMAKER)) > 900


@pytest.mark.parametrize("name", sorted(_REAL_BAMBU_FILES))
def test_bambu_vocabulary_covers_every_key_in_real_bambu_projects(name):
    """Completeness. A key a real Bambu file carries must never be filtered
    out of a converted one."""
    path = _REAL_BAMBU_FILES[name]
    if not path.exists():
        pytest.skip(f"sample not present: {path}")

    missing = _project_keys(path) - load_vocabulary(BAMBU)
    assert not missing, sorted(missing)


def test_snapmaker_vocabulary_covers_every_key_in_real_u1_projects():
    for name in ("u1_majorasmask", "u1_toucan_plus", "voronoi_u1"):
        missing = _project_keys(sample_path(name)) - load_vocabulary(SNAPMAKER)
        assert not missing, (name, sorted(missing))


def test_the_two_vocabularies_genuinely_differ():
    """Discrimination. If the forks had identical vocabularies there would be
    nothing to filter and no bug to fix."""
    bambu, snapmaker = load_vocabulary(BAMBU), load_vocabulary(SNAPMAKER)
    assert len(snapmaker - bambu) > 100
    # spot-check settings Orca has and Bambu does not
    for key in ("hole_to_polyhole", "slowdown_for_curled_perimeters", "extra_perimeters_on_overhangs"):
        assert key in snapmaker, key
        assert key not in bambu, key


def test_filter_removes_foreign_keys_from_a_real_u1_project():
    config = json.loads(ThreeMFArchive.open(sample_path("u1_toucan_plus")).get_text("Metadata/project_settings.config"))

    kept, dropped = filter_to_vocabulary(config, load_vocabulary(BAMBU))

    assert len(dropped) > 100, "expected a real U1 project to carry many Orca-only settings"
    assert set(kept) | set(dropped) == set(config)
    assert not set(dropped) & load_vocabulary(BAMBU)
    # the settings a user actually cares about must survive the filter
    for key in ("layer_height", "sparse_infill_density", "wall_loops", "enable_support", "filament_colour"):
        assert key in kept, key


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
def test_no_converted_file_contains_a_key_the_target_cannot_resolve(source_name, target):
    """The end-to-end guard. This is the assertion that would have caught the
    original bug, on the exact file that exposed it."""
    archive, _ = convert(sample_path(source_name), target)
    config = json.loads(archive.get_text("Metadata/project_settings.config"))

    vocabulary = load_vocabulary(SNAPMAKER if target == "u1" else BAMBU)
    foreign = set(config) - vocabulary
    assert not foreign, (source_name, target, sorted(foreign))


def test_conversion_reports_what_it_dropped():
    _, result = convert(sample_path("u1_toucan_plus"), "h2c")
    assert any("setting" in w and "Bambu Lab H2C" in w for w in result.warnings), result.warnings


def test_enum_maps_load_and_capture_the_known_fork_divergence():
    bambu, snapmaker = load_enums(BAMBU), load_enums(SNAPMAKER)
    assert len(bambu) > 30 and len(snapmaker) > 30
    # the exact divergence Bambu Studio reported when opening a converted file
    assert bambu["ironing_pattern"]["values"] == ("concentric", "zig-zag")
    assert snapmaker["ironing_pattern"]["values"] == ("rectilinear", "concentric")


def test_enum_options_with_copied_value_lists_are_resolved():
    """Some options don't push their own values but copy another's:
    `def->enum_values = def_top_fill_pattern->enum_values`. Leaving those
    unknown meant `internal_solid_infill_pattern` went unchecked and a
    converted project made Bambu Studio pop its "some values have been
    replaced" dialog. The reference is followed, not guessed -- both options
    must end up with exactly top_surface_pattern's list."""
    enums = load_enums(BAMBU)
    reference = enums["top_surface_pattern"]["values"]
    for key in ("bottom_surface_pattern", "internal_solid_infill_pattern"):
        assert enums[key]["values"] == reference, key
        assert "zig-zag" in enums[key]["values"] and "rectilinear" not in enums[key]["values"]


def test_copied_enum_lists_translate_snapmakers_value_by_label():
    """The concrete bug: Snapmaker writes `rectilinear`, Bambu labels its
    `zig-zag` "Rectilinear", so the user's choice survives the rename."""
    repaired, notes = repair_enum_values(
        {"internal_solid_infill_pattern": "rectilinear"},
        target_enums=load_enums(BAMBU),
        source_enums=load_enums(SNAPMAKER),
        fallbacks={},
    )
    assert repaired["internal_solid_infill_pattern"] == "zig-zag"
    assert notes


def test_repair_translates_by_label_preserving_the_users_choice():
    repaired, notes = repair_enum_values(
        {"ironing_pattern": "rectilinear"},
        target_enums=load_enums(BAMBU),
        source_enums=load_enums(SNAPMAKER),
        fallbacks={},
    )
    assert repaired["ironing_pattern"] == "zig-zag"  # same meaning, renamed value
    assert notes


def test_repair_drops_a_value_with_no_equivalent_rather_than_leaving_it_unreadable():
    repaired, notes = repair_enum_values(
        {"ensure_vertical_shell_thickness": "ensure_all"},
        target_enums=load_enums(BAMBU),
        source_enums=load_enums(SNAPMAKER),
        fallbacks={},
    )
    assert "ensure_vertical_shell_thickness" not in repaired
    assert any("no equivalent" in n for n in notes)


def test_repair_leaves_valid_values_alone():
    config = {"ironing_pattern": "concentric"}
    repaired, notes = repair_enum_values(
        config, target_enums=load_enums(BAMBU), source_enums=load_enums(SNAPMAKER), fallbacks={}
    )
    assert repaired == config
    assert notes == []


@pytest.mark.parametrize(
    "source_name,target",
    [("u1_toucan_plus", "h2c"), ("u1_majorasmask", "h2d"), ("h2c_antiwarp", "u1"), ("h2c_antiwarp", "a1-mini")],
)
def test_no_converted_file_carries_an_enum_value_the_target_cannot_parse(source_name, target):
    archive, _ = convert(sample_path(source_name), target)
    config = json.loads(archive.get_text("Metadata/project_settings.config"))

    enums = load_enums(SNAPMAKER if target == "u1" else BAMBU)
    for key, entry in enums.items():
        if key not in config:
            continue
        value = config[key]
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str) and item:
                assert item in entry["values"], (source_name, target, key, item)
