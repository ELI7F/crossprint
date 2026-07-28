"""A converted project must only name filament presets the target actually has.

Carrying the source's names over left a Bambu file claiming to use "Snapmaker
PLA Matte @U1". Bambu Studio treats an unknown name as a *custom* preset
bundled with the project, looks for its definition in the numbered
`Metadata/filament_settings_N.config` files -- which conversion deletes -- and
rejects the project as an invalid configuration file, after first warning the
user about unvetted G-code in "customized presets".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from convert.filament_mapping import map_filaments_to_target
from convert.pipeline import MODEL_REGISTRY, convert
from core.archive import ThreeMFArchive
from core.preset_resolver import PresetLibrary

from .conftest import sample_path

PROFILES = Path(__file__).parent.parent / "profiles"
BAMBU = PresetLibrary(PROFILES / "bambu_h2c")
SNAPMAKER = PresetLibrary(PROFILES / "snapmaker_u1")


def _config(archive: ThreeMFArchive) -> dict:
    return json.loads(archive.get_text("Metadata/project_settings.config"))


def test_matches_material_type_and_prefers_the_plain_variant():
    result = map_filaments_to_target(["PLA", "PETG", "ABS"], BAMBU, "@BBL H2C", "Bambu PLA Basic @BBL H2C")

    assert result.warnings == []
    assert result.filament_settings_id == [
        "Bambu PLA Basic @BBL H2C",
        "Bambu PETG Basic @BBL H2C",
        "Bambu ABS @BBL H2C",
    ]


def test_every_chosen_preset_actually_exists_in_the_target_library():
    result = map_filaments_to_target(["PLA", "PETG", "ABS", "ASA", "TPU", "PVA"], BAMBU, "@BBL H2C", None)
    for name in result.filament_settings_id:
        assert BAMBU.get("filament", name) is not None, name


def test_unknown_material_falls_back_and_says_so():
    result = map_filaments_to_target(["PLA", "UNOBTANIUM"], BAMBU, "@BBL H2C", "Bambu PLA Basic @BBL H2C")

    assert result.filament_settings_id[1] == "Bambu PLA Basic @BBL H2C"
    assert any("UNOBTANIUM" in w for w in result.warnings)


def test_works_in_the_u1_direction_too():
    result = map_filaments_to_target(["PLA", "PETG"], SNAPMAKER, "@U1", None)
    for name in result.filament_settings_id:
        assert SNAPMAKER.get("filament", name) is not None, name
        assert "@U1" in name, name


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
def test_converted_files_never_reference_a_preset_the_target_lacks(source_name, target):
    """The end-to-end guard: no dangling custom-preset reference, in either
    direction, for any real sample."""
    archive, _ = convert(sample_path(source_name), target)
    config = _config(archive)

    library = SNAPMAKER if target == "u1" else BAMBU
    for name in config["filament_settings_id"]:
        assert library.get("filament", name) is not None, (source_name, target, name)

    # ...and the definitions those names would have needed are indeed gone,
    # which is exactly why the names had to be real system presets.
    assert not [n for n in archive.names() if "filament_settings_" in n]


def test_filament_count_and_colours_are_untouched_by_remapping():
    source = _config(ThreeMFArchive.open(sample_path("u1_toucan_plus")))
    archive, _ = convert(sample_path("u1_toucan_plus"), "h2c")
    config = _config(archive)

    assert len(config["filament_settings_id"]) == len(source["filament_colour"])
    assert config["filament_colour"] == source["filament_colour"]
    assert config["filament_type"] == source["filament_type"]


@pytest.mark.parametrize("target", sorted(set(MODEL_REGISTRY) - {"u1"}))
def test_every_bambu_target_resolves_its_filament_presets(target):
    archive, _ = convert(sample_path("u1_majorasmask"), target)
    for name in _config(archive)["filament_settings_id"]:
        assert BAMBU.get("filament", name) is not None, (target, name)
