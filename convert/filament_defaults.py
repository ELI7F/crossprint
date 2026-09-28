"""Materialise the target's filament preset values the project has no answer for.

A real project file written by the slicer is a *flattened* preset plus the
user's overrides: every key the filament preset defines appears in it, whether
or not the user touched it. A converted project was not -- it carried only
what the source happened to have, which left 98 of the target's 137 filament
keys absent.

Mostly that is harmless, because the slicer falls back to the preset the
project names. Not always. Converting a Snapmaker U1 project to an H2C
produced a file Bambu Studio opened with:

    Warning: Partial purging volume set to 0. Multi-color printing may cause
             color mixing in models.

`filament_prime_volume` is 30 in every real H2C file and 45 in every real H2D
one, and it comes from the filament preset. The U1 side never sets it -- U1
has no prime-tower purge of that kind -- so there was nothing to carry and
nothing filled the gap. The slicer read it as zero, which means colour
bleeding into the model.

So each slot's missing keys are taken from the preset that slot now names.
Per slot, not from one preset for all: a project mixing PLA and PLA-CF gets
each material's own values, which is the whole reason the slots name
different presets.

Nothing here can change the user's recipe. Every value written equals the
target preset's own, so `different_settings_to_system` does not mark any of
them as a deviation -- the file simply stops being silent about settings it
was always going to inherit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.preset_resolver import PRESET_META_KEYS, PresetLibrary, flatten

#: Preset plumbing and identity, not settings. `include` and the
#: compatibility conditions describe how a preset is assembled and resolved;
#: copying them into a project would claim the project *is* that preset. The
#: filament identity fields are set explicitly by the pipeline from the
#: chosen preset, and must not be overwritten here.
_NOT_A_SETTING = PRESET_META_KEYS | {
    "include",
    "compatible_printers",
    "compatible_printers_condition",
    "compatible_prints",
    "compatible_prints_condition",
    "filament_settings_id",
    "filament_colour",
    "filament_type",
    "filament_vendor",
    "filament_ids",
}


@dataclass
class FilamentDefaultsResult:
    config: dict
    filled: list[str] = field(default_factory=list)


def fill_missing_filament_defaults(
    config: dict,
    filament_settings_id: list[str],
    target_library: PresetLibrary,
    filament_count: int,
) -> FilamentDefaultsResult:
    """Add, per slot, the filament-preset values the project doesn't carry.

    Only keys absent from the config are touched -- anything the project
    already says, including a value the user tuned, is left exactly as it is.
    """
    if filament_count <= 0:
        return FilamentDefaultsResult(config=config)

    # One flattened preset per slot, resolved once.
    per_slot: list[dict] = []
    for slot in range(filament_count):
        name = filament_settings_id[slot] if slot < len(filament_settings_id) else ""
        preset = target_library.get("filament", name) if name else None
        per_slot.append(flatten("filament", preset, target_library) if preset is not None else {})

    candidates: set[str] = set()
    for flat in per_slot:
        candidates |= {k for k in flat if k not in _NOT_A_SETTING and _is_per_filament(k)}

    out = dict(config)
    filled: list[str] = []
    for key in sorted(candidates):
        if key in out and not _is_blank_gcode(key, out[key]):
            continue
        values = [_slot_value(flat.get(key)) for flat in per_slot]
        # A key no resolved preset actually defines has nothing to fill from.
        if all(v is None for v in values):
            continue
        out[key] = [("" if v is None else v) for v in values]
        filled.append(key)

    return FilamentDefaultsResult(config=out, filled=filled)


def _is_blank_gcode(key: str, value) -> bool:
    """An empty filament G-code field, which the target's own placeholder fills.

    Bambu Studio compares each G-code field against the preset the project
    names and, on any difference, shows:

        Modified G-code -- The 3mf has following modified G-code in filament
        or printer presets: -filament_start_gcode. Please confirm that these
        modified G-codes are safe to prevent any damage to the machine!

    Carrying an empty `filament_start_gcode` from a Snapmaker source into a
    project naming a Bambu preset triggers exactly that. Nothing is wrong --
    the preset's own value is the comment line "; filament start gcode", and
    empty against a comment is a difference that executes nothing either way.
    But a safety dialog on every single converted file teaches people to click
    through safety dialogs, which is worse than the difference it reports.

    Only blank values qualify. G-code the user actually wrote is theirs, and
    replacing it with a vendor placeholder would be silently discarding
    something that does run.
    """
    if not key.endswith("_gcode"):
        return False
    values = value if isinstance(value, list) else [value]
    return all(v is None or not str(v).strip() for v in values)


def _is_per_filament(key: str) -> bool:
    """Whether a preset key is safe to write once per filament slot.

    Not every key in a filament preset is stored per filament in a project.
    `required_nozzle_HRC` sits in the filament preset but a real H2C project
    holds four entries for six filaments -- it is sized by nozzle variant.
    Writing it per slot and letting the per-variant expansion widen it
    produced eight, and the shape regression test caught it: mismatched arity
    is the bug class that once had Bambu Studio rejecting converted files as
    "Invalid configuration file" outright.

    So this fills only what the vendor's own naming marks as belonging to a
    filament. It is deliberately conservative -- `nozzle_temperature` and
    `hot_plate_temp` are per filament too without the prefix -- but those come
    across from the source anyway, and a key wrongly filled is worse than one
    left absent: absent means the slicer uses its preset, wrong arity means it
    refuses the file.
    """
    return key.startswith("filament_")


def _slot_value(value) -> str | None:
    """One slot's worth of a preset entry.

    Preset values are usually single-element lists -- `["30"]` -- which is the
    value for one filament, to be repeated per slot. A longer list is a
    genuinely multi-valued entry that cannot be split across slots, so it is
    left alone rather than truncated to its first element.
    """
    if isinstance(value, list):
        if len(value) != 1:
            return None
        value = value[0]
    return None if value is None else str(value)
