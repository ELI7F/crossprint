"""Map logical filament color slots between source and target printer, and
rebuild the target's physical-extruder/AMS routing fields.

Bambu's own lineup splits into two hardware architectures, confirmed from
the vendored system machine presets (profiles/bambu_h2c) rather than
assumed from model-family naming -- H2S and X2D *sound* like they'd share
H2C/H2D's dual-hotend Vortek design, but their system presets don't define
master_extruder_id at all, meaning they're actually single-hotend like every
other non-H2/X2 Bambu printer:

  - "vortek" (dual hotend, AMS + physical-extruder routing fields exist):
    H2C, H2D confirmed against real project files (identical narrowed
    nozzle-variant kinematics in both -- same physical hotend hardware).
    H2D Pro is assumed to share this (same product generation) but has no
    real sample to verify against yet.
  - "single" (one hotend, no physical-extruder routing concept at all --
    same shape as Snapmaker U1 in that respect): everything else --
    A1, A1 mini, A2L, H2S, P1P, P1S, P2S, X1, X1 Carbon, X1E, X2D.
    Verified against a real A1 mini project.

Capacity figures for the Vortek family match this project's own prior
research (H2C without AMS = 7, with AMS = 24; reused for H2D/H2D Pro absent
better data). Single-hotend AMS capacity isn't independently verified for
any specific model, so it's a soft warning past 4 (one AMS unit's worth of
slots) rather than a hard error.

U1's capacity handling needed real data to overturn during implementation
too (see map_colors_to_u1): a genuine, real-world Snapmaker U1 project
(majorasmask_8color_snapmakeru1.3mf, see profiles/SOURCES.md) uses 8 colors
with *zero* physical-routing metadata in project_settings.config. U1 has 4
simultaneously mounted SnapSwap toolheads, but total colors per print isn't
capped at 4; beyond that the printer prompts manual spool swaps mid-print.

Overflow policy for capacities that ARE enforced (Vortek family): raise
CapacityExceededError rather than silently merging colors. Silently picking
which colors to merge would change the user's art without asking; an
explicit, actionable error is the safer default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.model_settings import ModelSettings
from core.preset_resolver import PresetLibrary, flatten

HotendClass = Literal["vortek", "single"]

CAPACITY = {
    "vortek_no_ams": 7,
    "vortek_with_ams": 24,
    "single_hotend_one_ams_unit": 4,  # soft warning threshold only, not enforced -- see module docstring
    "u1_simultaneous_toolheads": 4,  # not a hard cap -- see map_colors_to_u1
}

# Nozzle-variant-dependent machine/kinematic fields shared by the whole
# Vortek family -- copied verbatim from two real, working projects (one H2C,
# one H2D) that independently produced *identical* values for every one of
# these keys, confirming they're a property of the shared hotend hardware,
# not something that needs re-deriving per model. Discovered the hard way:
# Bambu Studio rejected an early converted file outright as "Invalid
# configuration file" because these arrays were pulled straight from the
# system machine preset, which enumerates *every* nozzle variant the model
# supports (5 slots: Standard/High Flow/E3D High Flow on extruder 1,
# Standard/High Flow on extruder 2) rather than what a real saved project
# actually contains (4 slots -- no E3D High Flow, an aftermarket option).
# Covers the common default setup; a printer with an aftermarket nozzle
# installed would need a different (currently unsupported) shape.
_VORTEK_VERIFIED_MACHINE_FIELDS: dict[str, list[str]] = {
    "long_retractions_when_cut": ["1", "1", "1", "1"],
    "retract_length_toolchange": ["2", "2", "2", "2"],
    "retract_lift_below": ["319", "319", "319", "319"],
    "retraction_distances_when_cut": ["14", "14", "14", "14"],
    "machine_max_acceleration_e": ["5000"] * 8,
    "machine_max_acceleration_extruding": ["20000"] * 8,
    "machine_max_acceleration_retracting": ["5000"] * 8,
    "machine_max_acceleration_travel": ["9000"] * 8,
    "machine_max_acceleration_x": ["20000"] * 8,
    "machine_max_acceleration_y": ["20000"] * 8,
    "machine_max_acceleration_z": ["500"] * 8,
    "machine_max_jerk_e": ["2.5"] * 8,
    "machine_max_jerk_x": ["9"] * 8,
    "machine_max_jerk_y": ["9"] * 8,
    "machine_max_jerk_z": ["3"] * 8,
    "machine_max_speed_e": ["50"] * 8,
    "machine_max_speed_x": ["1000"] * 8,
    "machine_max_speed_y": ["1000"] * 8,
    "machine_max_speed_z": ["30"] * 8,
    "nozzle_type": ["hardened_steel"] * 4,
    "nozzle_volume": ["130", "133", "145", "148"],
    "printer_extruder_id": ["1", "1", "2", "2"],
    "printer_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow", "Direct Drive Standard", "Direct Drive High Flow"],
}

# Per-model AMS/physical-extruder routing. Unlike the kinematics above, this
# genuinely differs between H2C and H2D in the two real samples checked (a
# different AMS-to-extruder-group assignment), so it's kept per-model rather
# than shared. `extruder_nozzle_stats: None` means the real reference file
# for that model doesn't carry the key at all -- omit it, don't guess a
# value. `verified: False` models reuse H2D's numbers as a same-generation
# best-effort default; nothing confirms they're correct for that model.
_VORTEK_AMS_ROUTING: dict[str, dict] = {
    "Bambu Lab H2C": {
        "reference_filament_count": 6,
        "physical_extruder_map": ["1", "0"],
        "extruder_ams_count": ["1#1|4#0", "1#0|4#1"],
        "extruder_nozzle_stats": ["Standard#1", "Standard#4"],
        "verified": True,
    },
    "Bambu Lab H2D": {
        "reference_filament_count": 4,
        "physical_extruder_map": ["1", "0"],
        "extruder_ams_count": ["1#0|4#0", "1#0|4#0"],
        "extruder_nozzle_stats": None,
        "verified": True,
    },
}
_VORTEK_AMS_ROUTING["Bambu Lab H2D Pro"] = {**_VORTEK_AMS_ROUTING["Bambu Lab H2D"], "verified": False}

# Fields that only appear in Vortek-family (dual-hotend) real project files --
# confirmed absent from a real single-hotend project (A1 mini) even though
# the single-hotend system machine preset defines several of them (e.g.
# printer_extruder_id). Must never be injected for a single-hotend target,
# regardless of what the target's own flattened machine preset contains.
_SINGLE_HOTEND_EXCLUDED_FIELDS = {
    "master_extruder_id", "physical_extruder_map", "filament_map",
    "extruder_ams_count", "extruder_nozzle_stats",
    "printer_extruder_id", "printer_extruder_variant",
    "extruder_clearance_dist_to_rod", "extruder_max_nozzle_count",
    "extruder_printable_area", "extruder_variant_list",
    "long_retractions_when_cut", "nozzle_height", "retraction_distances_when_cut",
}
# Fields the Vortek system preset stores as a per-nozzle-variant *list* but a
# real single-hotend project stores as a plain *scalar* -- confirmed against
# a real A1 mini project file (list[1] in the system preset, bare string in
# the saved project). A second class of shape bug, same root cause as the
# Vortek one: the system preset describes hardware capability, not what a
# specific saved project actually contains.
_SINGLE_HOTEND_SCALAR_FIELDS = {"nozzle_type", "nozzle_volume"}

# On a dual-hotend printer every per-filament setting is stored once *per
# extruder variant*, so those arrays are filament_count x 2 rather than
# filament_count. These are the two variants real H2C and H2D project files
# use, in the order they appear there -- see convert/filament_variants.py for
# the layout and why getting it wrong makes the target reject the project.
_VORTEK_FILAMENT_VARIANTS = ["Direct Drive Standard", "Direct Drive High Flow"]
_SINGLE_HOTEND_FILAMENT_VARIANTS = ["Direct Drive Standard"]


class CapacityExceededError(Exception):
    def __init__(self, needed: int, capacity: int, printer: str):
        self.needed = needed
        self.capacity = capacity
        self.printer = printer
        super().__init__(
            f"{printer} can't take {needed} colors (capacity {capacity}). This tool won't "
            "silently merge colors for you -- reduce the color count in the source model, "
            "or increase target capacity (e.g. attach an AMS)."
        )


@dataclass
class ColorMappingResult:
    slot_map: dict[int, int]  # source 1-based slot -> target 1-based slot
    filament_colour: list[str]
    filament_type: list[str]
    filament_settings_id: list[str]
    target_fields: dict[str, object]  # fields to inject into project_settings.config (empty for U1)
    exclude_fields: set[str] = field(default_factory=set)  # regenerate-bucket keys to never inject for this target
    # Extruder variants each per-filament setting must be stored for; length 1
    # means the target keeps one entry per filament.
    filament_variants: list[str] = field(default_factory=lambda: list(_SINGLE_HOTEND_FILAMENT_VARIANTS))
    warnings: list[str] = field(default_factory=list)


def _require_capacity(count: int, capacity: int, printer: str) -> None:
    if count > capacity:
        raise CapacityExceededError(count, capacity, printer)


def hotend_class(machine_preset: dict) -> HotendClass:
    """Classify a printer from its OWN (unflattened) system machine preset.

    Must NOT be handed a flattened preset: `master_extruder_id` is also
    defined on `fdm_machine_common`, the shared root that *every* Bambu
    machine preset ultimately inherits from, as a generic fallback value --
    so a flattened preset has the key for every model and would classify
    the entire lineup as Vortek. Only the models that set it on their own
    preset file genuinely have a second hotend; verified across the whole
    vendored library (H2C/H2D/H2D Pro true; H2S/X2D/A1 mini/P1S/X1C false)."""
    return "vortek" if "master_extruder_id" in machine_preset else "single"


def map_colors_to_u1(
    filament_colour: list[str], filament_type: list[str], filament_settings_id: list[str]
) -> ColorMappingResult:
    """U1 has 4 simultaneously mounted SnapSwap toolheads and no AMS/
    physical-extruder grouping concept at all -- logical slot N maps
    straight to toolhead N, 1:1. filament_map/physical_extruder_map/
    master_extruder_id/extruder_ams_count/extruder_nozzle_stats don't apply
    and must simply be absent from U1 output (confirmed absent from every
    real U1 project file checked, including an 8-color one and one with a
    stray leftover H2C-family config -- see profiles/SOURCES.md). No hard
    capacity is enforced: beyond 4 colors the printer/firmware handles
    sequencing via manual spool swaps, which needs no extra metadata here."""
    count = len(filament_colour)
    warnings = []
    if count > CAPACITY["u1_simultaneous_toolheads"]:
        warnings.append(
            f"{count} colors exceeds U1's {CAPACITY['u1_simultaneous_toolheads']} simultaneously "
            "mounted toolheads -- the printer will prompt manual spool swaps mid-print."
        )
    return ColorMappingResult(
        slot_map={i: i for i in range(1, count + 1)},
        filament_colour=list(filament_colour),
        filament_type=list(filament_type),
        filament_settings_id=list(filament_settings_id),
        target_fields={},
        warnings=warnings,
    )


def map_colors_to_bambu(
    target_model: str,
    filament_colour: list[str],
    filament_type: list[str],
    filament_settings_id: list[str],
    bambu_library: PresetLibrary,
    target_machine_preset_name: str,
) -> ColorMappingResult:
    """Any Bambu target -- dispatches on hotend_class(), determined from the
    target's own flattened machine preset rather than a hardcoded model
    list (see module docstring for why H2S/X2D aren't in the Vortek set
    despite the naming)."""
    count = len(filament_colour)
    slot_map = {i: i for i in range(1, count + 1)}
    common = dict(
        slot_map=slot_map,
        filament_colour=list(filament_colour),
        filament_type=list(filament_type),
        filament_settings_id=list(filament_settings_id),
    )

    machine = bambu_library.get("machine", target_machine_preset_name)
    if machine is None:
        raise LookupError(f"{target_machine_preset_name!r} not found in the vendored Bambu profile library")
    flat_machine = flatten("machine", machine, bambu_library)

    if hotend_class(machine) == "single":
        warnings = []
        if count > CAPACITY["single_hotend_one_ams_unit"]:
            warnings.append(
                f"{count} colors exceeds one AMS unit's {CAPACITY['single_hotend_one_ams_unit']} slots -- "
                f"verify your AMS setup (multiple units chained?) for {target_model} in the slicer."
            )
        target_fields = {k: flat_machine[k] for k in _SINGLE_HOTEND_SCALAR_FIELDS if k in flat_machine and isinstance(flat_machine[k], list) and len(flat_machine[k]) == 1}
        target_fields = {k: v[0] for k, v in target_fields.items()}
        return ColorMappingResult(
            **common, target_fields=target_fields, exclude_fields=set(_SINGLE_HOTEND_EXCLUDED_FIELDS), warnings=warnings
        )

    # vortek
    _require_capacity(count, CAPACITY["vortek_with_ams"], f"{target_model} (with AMS)")
    routing = _VORTEK_AMS_ROUTING.get(target_model)
    warnings = []
    if routing is None:
        raise ValueError(
            f"{target_model} is a Vortek dual-hotend printer but has no verified (or best-effort) "
            "AMS routing data in convert/color_mapping.py's _VORTEK_AMS_ROUTING -- add an entry, "
            "ideally from a real project file for this model, before converting to it."
        )
    if not routing["verified"]:
        warnings.append(
            f"{target_model}'s AMS routing isn't independently verified (reusing Bambu Lab H2D's "
            "real values as a same-generation best-effort default) -- check AMS slot assignment "
            "in Bambu Studio before printing."
        )
    if count != routing["reference_filament_count"]:
        warnings.append(
            f"{count} colors (reference case for {target_model} was {routing['reference_filament_count']}): "
            "extruder_ams_count reused as-is -- open the result in Bambu Studio and check "
            "the AMS slot assignment before printing."
        )

    target_fields = {
        "master_extruder_id": flat_machine.get("master_extruder_id", "2"),
        "physical_extruder_map": routing["physical_extruder_map"],
        "filament_map": ["1"] * count,
        "extruder_ams_count": routing["extruder_ams_count"],
        **_VORTEK_VERIFIED_MACHINE_FIELDS,
    }
    if routing["extruder_nozzle_stats"] is not None:
        target_fields["extruder_nozzle_stats"] = routing["extruder_nozzle_stats"]

    return ColorMappingResult(
        **common,
        target_fields=target_fields,
        filament_variants=list(_VORTEK_FILAMENT_VARIANTS),
        warnings=warnings,
    )


def remap_object_extruders(model_settings: ModelSettings, slot_map: dict[int, int]) -> None:
    """Rewrite every object/part 'extruder' metadata value through slot_map,
    in place. A no-op for the identity map every map_colors_to_* function
    currently produces, but kept as a real operation (not assumed-identity)
    so a future merge/reorder strategy only has to change slot_map, not
    every caller."""
    for obj in model_settings.objects:
        if obj.extruder is not None:
            old = int(obj.extruder)
            if old in slot_map:
                obj.extruder = str(slot_map[old])
        for part in obj.parts:
            v = part.get_metadata("extruder")
            if v is not None:
                old = int(v)
                if old in slot_map:
                    part.set_metadata("extruder", str(slot_map[old]))
