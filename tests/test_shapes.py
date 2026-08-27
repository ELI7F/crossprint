"""Collapsing a per-extruder vector onto a single-valued target.

A Bambu project stores roughly thirty speed and acceleration settings once
per *nozzle variant*; Snapmaker U1 stores one number. When the variants
disagreed, conversion used to discard the setting entirely and let the
target's stock preset supply it -- so a user who had tuned an infill speed to
350 opened the converted project and found 270, with the setting they had set
nowhere in the file.

The variants are not alternatives of equal standing. Index 0 is the first
extruder's standard nozzle, which is the configuration a 0.4 mm project is
actually printing with; the rest belong to High Flow nozzles that are not
installed. Taking index 0 gives the user their own value back.
"""
from __future__ import annotations

from core.shapes import harmonize_shapes


def test_disagreeing_variants_collapse_to_the_primary_extruder():
    # The real shape from an H2D project: index 0 is the standard nozzle.
    config = {"outer_wall_speed": ["200", "500", "200", "500", "500"]}

    harmonized, dropped, reshaped, collapsed = harmonize_shapes(
        config, target_defaults={"outer_wall_speed": "200"}, keep=set()
    )

    assert harmonized["outer_wall_speed"] == "200"
    assert collapsed == ["outer_wall_speed"]
    assert dropped == [] and reshaped == []


def test_the_users_value_survives_rather_than_the_targets_default():
    """The failure this fixes, stated as the user experienced it."""
    config = {"sparse_infill_speed": ["350", "600", "350", "600", "600"]}

    harmonized, dropped, _, collapsed = harmonize_shapes(
        config, target_defaults={"sparse_infill_speed": "270"}, keep=set()
    )

    assert harmonized["sparse_infill_speed"] == "350", "the user set 350; 270 is the target's preset"
    assert "sparse_infill_speed" not in dropped
    assert collapsed == ["sparse_infill_speed"]


def test_an_all_equal_vector_is_still_a_plain_unwrap():
    """Nothing is discarded here, so it must not be reported as a collapse."""
    config = {"top_surface_speed": ["200", "200", "200"]}

    harmonized, _, reshaped, collapsed = harmonize_shapes(
        config, target_defaults={"top_surface_speed": "200"}, keep=set()
    )

    assert harmonized["top_surface_speed"] == "200"
    assert reshaped == ["top_surface_speed"] and collapsed == []


def test_growing_a_scalar_into_a_vector_is_unaffected():
    config = {"travel_speed": "500"}

    harmonized, _, reshaped, collapsed = harmonize_shapes(
        config, target_defaults={"travel_speed": ["1", "1"]}, keep=set()
    )

    assert harmonized["travel_speed"] == ["500", "500"]
    assert reshaped == ["travel_speed"] and collapsed == []


def test_kept_keys_are_never_reshaped():
    """Filament-sized arrays track the project, not the printer."""
    config = {"filament_colour": ["#FFF", "#000"]}

    harmonized, dropped, reshaped, collapsed = harmonize_shapes(
        config, target_defaults={"filament_colour": "#FFF"}, keep={"filament_colour"}
    )

    assert harmonized["filament_colour"] == ["#FFF", "#000"]
    assert dropped == [] and reshaped == [] and collapsed == []


def test_a_declared_scalar_holding_a_disagreeing_list_is_still_left_alone():
    """That path has no preset behind it, so nothing confirms the list is
    per-extruder and index 0 would be a guess rather than a reading."""
    config = {"some_setting": ["1", "2"]}

    harmonized, dropped, reshaped, collapsed = harmonize_shapes(
        config, target_defaults={}, keep=set(), target_types={"some_setting": "coFloat"}
    )

    assert harmonized["some_setting"] == ["1", "2"]
    assert reshaped == [] and collapsed == [] and dropped == []
