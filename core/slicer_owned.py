"""Settings the target slicer must derive for itself.

The division of labour this tool settled on, after testing against Bambu
Studio directly:

    we own the model, the colours and the print recipe;
    the slicer owns the machine.

Machine-shaped settings -- nozzle variants, per-extruder kinematics, AMS
routing, purge matrices, and every per-filament array that is stored once per
extruder variant -- turned out to be impossible to synthesise correctly from
static data. Their widths depend on how the *installed* slicer combines
printer variants with each filament's compatibility, and they change between
versions: a project saved by Bambu Studio 2.5 lists 5 nozzle variants and 17
per-filament entries for 6 filaments (3 for most filaments, 2 for one whose
preset supports fewer), while an older sample lists 4 and 12. Guessing any of
these widths yields "Invalid configuration file".

Omitting them instead is both simpler and correct: the slicer fills them from
its own presets for the printer the project names, which is exactly the
hardware description we want anyway. Confirmed in Bambu Studio -- with these
keys absent the converted project opens with geometry, colours and the user's
print recipe intact.
"""
from __future__ import annotations

# Machine hardware description: the printer's own properties, not the user's
# choices. Regenerating these from a preset was the original design; leaving
# them out entirely is what actually works.
_MACHINE_OWNED = {
    "nozzle_diameter",
    "nozzle_type",
    "nozzle_volume",
    "nozzle_height",
    "printer_extruder_id",
    "printer_extruder_variant",
    "extruder_type",
    "extruder_variant_list",
    "extruder_colour",
    "extruder_offset",
    "extruder_printable_area",
    "extruder_printable_height",
    "extruder_max_nozzle_count",
    "extruder_clearance_radius",
    "max_layer_height",
    "min_layer_height",
    "upward_compatible_machine",
    # per-extruder retraction / wipe kinematics
    "deretraction_speed",
    "long_retractions_when_cut",
    "retract_before_wipe",
    "retract_length_toolchange",
    "retract_lift_above",
    "retract_lift_below",
    "retract_restart_extra",
    "retract_restart_extra_toolchange",
    "retract_when_changing_layer",
    "retraction_distances_when_cut",
    "retraction_length",
    "retraction_minimum_travel",
    "retraction_speed",
    "wipe",
    "wipe_distance",
    "z_hop",
    "z_hop_types",
    # AMS / physical extruder routing
    "master_extruder_id",
    "physical_extruder_map",
    "extruder_ams_count",
    "extruder_nozzle_stats",
    # purge volumes, sized per extruder as well as per filament pair
    "flush_volumes_matrix",
    "flush_volumes_vector",
    "flush_multiplier",
    # the per-variant interleaving itself
    "filament_extruder_variant",
    "filament_self_index",
    "filament_nozzle_map",
    "filament_volume_map",
}

# References to the *source* slicer's own presets and assets. These are not
# kinematics, so the list above never caught them, and every one of them
# survived conversion still describing the machine the project came from.
#
# The visible one was `bed_custom_model`. A Bambu project carries an absolute
# path into Bambu Studio's install:
#
#     C:/Program Files/Bambu Studio/resources/profiles/BBL/bbl-3dp-X1.stl
#
# Snapmaker Orca loads it and draws Bambu's X1 bed as a solid black slab
# sitting on the U1's plate. The user spotted it before this code did. A real
# U1 project leaves the field empty.
#
# The rest are preset names: a U1 project claiming its process is compatible
# only with "Bambu Lab A1 0.4 nozzle", and inheriting from "0.20mm Standard
# @BBL A1". Like the filament ids in convert/filament_mapping.py, these are
# references into a library the target doesn't have. Dropping them lets the
# target's own printer preset supply the answer, which is the right one.
_SOURCE_MACHINE_REFERENCES = {
    "bed_custom_model",
    "bed_custom_texture",
    "default_print_profile",
    "default_filament_profile",
    "print_compatible_printers",
    "inherits_group",
}


def slicer_owned_keys(variant_options: set[str]) -> set[str]:
    """Everything to leave out, for a target with these per-variant options.

    `variant_options` comes from the target fork's own PrintConfig.cpp: the
    filament settings it stores once per extruder variant. Their width is the
    part we cannot reconstruct, so they go too.
    """
    return (_MACHINE_OWNED | _SOURCE_MACHINE_REFERENCES | set(variant_options)
            | {k for k in _MACHINE_OWNED if k.startswith("machine_max_")})


def strip_slicer_owned(config: dict, variant_options: set[str]) -> tuple[dict, list[str]]:
    owned = slicer_owned_keys(variant_options)
    stripped = {k: v for k, v in config.items() if k not in owned and not k.startswith("machine_max_")}
    dropped = sorted(k for k in config if k not in stripped)
    return stripped, dropped
