"""Sources whose filament arrays disagree with each other.

Both cases here came out of a sweep over the user's own library, not from
imagination -- see convert/filament_slots.py. Every count downstream is
derived from one number, so getting it from the wrong array corrupts the
deviation list's length and silently disables every per-filament diff.
"""
from __future__ import annotations

from convert.filament_slots import _material_from_preset_name, normalize_filament_slots


def test_agreeing_arrays_are_left_exactly_alone():
    slots = normalize_filament_slots(["#FFF", "#000"], ["PLA", "PETG"], ["A", "B"])

    assert slots.count == 2
    assert slots.colour == ["#FFF", "#000"]
    assert slots.type == ["PLA", "PETG"]
    assert slots.settings_id == ["A", "B"]
    assert slots.warnings == []


def test_the_widest_array_sets_the_count():
    """A real H2C project held 9 colours against 10 of everything else. Reading
    9 sized the deviation list for a filament count the project doesn't have."""
    slots = normalize_filament_slots(
        colour=["#FFF"] * 9,
        type_=["PLA"] * 10,
        settings_id=["A"] * 10,
        extra_lengths={"filament_ids": 10, "filament_map": 10},
    )

    assert slots.count == 10
    assert len(slots.colour) == 10 and len(slots.type) == 10 and len(slots.settings_id) == 10
    assert slots.warnings and "filament_colour" in slots.warnings[0]


def test_padding_never_truncates():
    """Dropping a filament would drop whatever is painted with it."""
    slots = normalize_filament_slots(["#1", "#2", "#3"], ["PLA"], ["A"])
    assert slots.count == 3
    assert slots.colour == ["#1", "#2", "#3"]


def test_a_missing_type_list_is_read_from_the_preset_names():
    """An A1 mini project carried colours and preset names but no
    filament_type at all. Material is what filament mapping matches on, so an
    empty type list mapped to no presets whatsoever."""
    slots = normalize_filament_slots(
        colour=["#F79622"],
        type_=[],
        settings_id=["Bambu PLA Basic @BBL A1M"],
    )

    assert slots.type == ["PLA"]
    assert any("no filament_type" in w for w in slots.warnings)


def test_material_matching_prefers_the_longer_name():
    assert _material_from_preset_name("Bambu PLA-CF @BBL H2C") == "PLA-CF"
    assert _material_from_preset_name("Bambu PLA Basic @BBL A1M") == "PLA"
    assert _material_from_preset_name("Generic PETG @U1") == "PETG"
    assert _material_from_preset_name("Snapmaker TPU @U1") == "TPU"
    assert _material_from_preset_name("Something Unrecognised") is None


def test_unknown_material_falls_back_to_the_projects_own_common_type():
    slots = normalize_filament_slots(
        colour=["#1", "#2"],
        type_=["PETG"],
        settings_id=["Mystery Filament", "Mystery Filament"],
    )
    assert slots.type == ["PETG", "PETG"]


def test_padded_colour_is_white_rather_than_a_repeat():
    """A repeated colour reads as two slots deliberately sharing a filament;
    white reads as unset, which is what it is."""
    slots = normalize_filament_slots(["#AABBCC"], ["PLA", "PLA"], ["A", "B"])
    assert slots.colour == ["#AABBCC", "#FFFFFF"]


def test_empty_project_stays_empty():
    slots = normalize_filament_slots([], [], [])
    assert slots.count == 0 and slots.warnings == []
