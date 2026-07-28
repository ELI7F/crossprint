"""Recompute `different_settings_to_system` for the target printer.

This field is how a project tells the slicer "my values deviate from the
preset I name." Without it, a project that names a stock system preset is
taken at its word: the slicer serves that preset's own values and silently
discards the user's layer height, infill density, wall count and so on --
even though those values are sitting right there in project_settings.config.

Both failure modes here were found the hard way, in this order:

1. An early version classified the field as stale UI state and dropped it.
   Converted files then opened showing the *target's* default print settings
   instead of the source's. The values had survived conversion intact -- the
   slicer just had no reason to believe they mattered.

2. The first fix carried the source project's own entries forward and unioned
   them into the computed diff. That named settings the target slicer has no
   equivalent for -- Snapmaker Orca's `enable_pressure_advance`,
   `extra_perimeters_on_overhangs`, `slowdown_for_curled_perimeters` -- and
   Bambu Studio then failed to load the file at all, reporting "The file does
   not contain any geometry data" even though the geometry part was byte-for-
   byte identical to the source's.

Hence the rule this module now follows: **only ever name a key the target's
own presets define.** A key outside that vocabulary is not a difference the
target can act on, and asserting it is actively harmful.

Structure, confirmed identical across real Snapmaker U1, Bambu H2C and Bambu
H2D project files: a flat list of exactly `1 + filament_count + 1`
semicolon-joined key lists --

    [print settings, filament 1, ... filament N, printer settings]

-- with an empty string for any section that matches its preset exactly.
"""
from __future__ import annotations

from core.preset_resolver import PRESET_META_KEYS

# Preset *names*, not settings -- a project naming a different preset than
# the one it's diffed against is the normal case, never a per-setting
# deviation to report.
_PRESET_ID_KEYS = {"print_settings_id", "printer_settings_id", "filament_settings_id"}
_NOT_A_SETTING = PRESET_META_KEYS | _PRESET_ID_KEYS

# The settings worth claiming as deliberate overrides: a user's print recipe.
#
# Marking *everything* that differs does not work. A 36-key list left Bambu
# Studio loading the config but producing no geometry, while a 7-key list of
# core recipe settings loaded correctly with the user's values applied --
# both verified in the application. The long list is mostly speeds,
# accelerations and tuning constants inherited from whichever preset the
# source project happened to sit on, not choices the user made, so scoping
# to the recipe loses little and is what actually opens.
RECIPE_KEYS = frozenset({
    # layers
    "layer_height",
    "initial_layer_print_height",
    # walls and shells
    "wall_loops",
    "wall_generator",
    "top_shell_layers",
    "top_shell_thickness",
    "bottom_shell_layers",
    "bottom_shell_thickness",
    "top_surface_pattern",
    "bottom_surface_pattern",
    # infill
    "sparse_infill_density",
    "sparse_infill_pattern",
    "infill_combination",
    "internal_solid_infill_pattern",
    # supports
    "enable_support",
    "support_type",
    "support_style",
    "support_threshold_angle",
    "support_on_build_plate_only",
    "support_interface_top_layers",
    "support_interface_bottom_layers",
    # adhesion and surface finish
    "brim_type",
    "brim_width",
    "brim_object_gap",
    "raft_layers",
    "ironing_type",
    "seam_position",
    "fuzzy_skin",
    # multi-material
    "enable_prime_tower",
    "prime_tower_width",
    "flush_into_objects",
    "flush_into_infill",
    "flush_into_support",
})


def diff_against_preset(config: dict, flat_preset: dict) -> list[str]:
    """Keys the project sets to something other than what the preset says.

    Iterating the *preset* rather than the config is what keeps the result
    inside the target's vocabulary: a converted project carries whatever keys
    the source slicer wrote, including ones the target has never heard of, and
    naming those breaks loading outright (see module docstring). It also
    naturally scopes the result -- a project holds print, filament and machine
    settings in one flat dict, and only the ones this preset defines belong in
    this section.
    """
    return sorted(
        key
        for key, preset_value in flat_preset.items()
        if key in RECIPE_KEYS and key not in _NOT_A_SETTING and key in config and config[key] != preset_value
    )


def compute_different_settings_to_system(
    config: dict,
    flat_print_profile: dict,
    filament_count: int,
) -> list[str]:
    """Build the field for a converted project.

    Only the print section is populated -- it carries the user's recipe, and
    its loss is what prompted this module.

    The filament sections are deliberately left empty. Filament values pass
    through conversion untouched but keep their *source* preset names, which
    generally don't exist in the target's library at all (a U1 project's
    "Panchroma PLA Matte @U1" has no counterpart under Bambu's). There is no
    target preset to meaningfully diff them against, and the source's own
    entries can't be reused because they name source-slicer-only settings.

    The printer section is likewise always empty: conversion rebuilds machine
    configuration wholesale from the target's own preset, so by construction
    nothing in it is a user deviation -- and an empty section is what makes
    the slicer normalize any machine-scoped value that leaked through from the
    source printer. This matches the real reference files, where a
    stock-printer H2C project carries no `different_settings_to_system` at
    all, including for the narrowed nozzle/kinematics arrays that differ from
    the raw system preset. Diffing against the flattened machine preset
    instead would flag every one of those as user-modified -- the same
    "preset describes capability, not instantiation" trap documented in
    convert/color_mapping.py.
    """
    return [";".join(diff_against_preset(config, flat_print_profile))] + [""] * filament_count + [""]
