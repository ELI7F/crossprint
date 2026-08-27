"""Reconcile a source project's filament arrays to one consistent length.

A project describes its filaments across several parallel arrays -- colour,
material type, preset name, catalogue id, extruder map -- with one entry per
filament. Conversion reads the count from `filament_colour` and trusts the
rest to match.

Real files do not always agree with themselves. Both of these came out of the
user's own library:

  * A 10-filament H2C project whose `filament_colour` holds 9 entries while
    `filament_type`, `filament_settings_id`, `filament_ids` and `filament_map`
    all hold 10 -- and whose own `different_settings_to_system` is 12 long,
    which in the `1 + n + 1` layout is the file stating outright that n is 10.
    Reading 9 produced a deviation list sized for a filament count the project
    does not have, and made every per-filament diff skip itself, because the
    arrays it compares are one longer than the count it was given.
  * An A1 mini project carrying `filament_colour` and `filament_settings_id`
    but no `filament_type` at all. Material is what filament mapping matches
    on, so an absent type list mapped to *no presets whatsoever*: the
    converted project named one colour and zero filaments.

So the count is taken as the widest of the arrays that genuinely hold one
entry per filament, cross-checked against the file's own declaration, and the
short ones are padded. Only that allowlist is consulted: `filament_retraction_length`
in the same file is 20 entries -- one per filament *per extruder variant* --
and treating it as a filament count would double it.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Arrays that hold exactly one entry per filament. Deliberately not "anything
# starting with filament_": several of those are per-variant and would inflate
# the count -- see the module docstring.
_ONE_PER_FILAMENT = ("filament_colour", "filament_type", "filament_settings_id", "filament_ids", "filament_map")

# Longest first, so "PLA-CF" is not read as "PLA".
_MATERIALS = (
    "PLA-CF", "PETG-CF", "PET-CF", "PAHT-CF", "PA6-CF", "PA-CF", "PPS-CF", "PPA-CF", "ABS-GF", "PA6-GF",
    "ASA-CF", "ASA-Aero", "PLA Aero", "TPU-AMS", "PVA", "HIPS", "PETG", "PCTG", "ABS", "ASA", "TPU",
    "PAHT", "PET", "PLA", "PC", "PA", "PP", "EVA", "PHA",
)
_FALLBACK_MATERIAL = "PLA"


@dataclass
class FilamentSlots:
    count: int
    colour: list[str]
    type: list[str]
    settings_id: list[str]
    warnings: list[str] = field(default_factory=list)


def _material_from_preset_name(name: str) -> str | None:
    """The material a preset name spells out, e.g. "Bambu PLA Basic @BBL A1M".

    Matching on the name is a reading, not a guess: vendors put the material in
    the preset name by convention, and it is the only place left to look once
    `filament_type` is absent.
    """
    for material in _MATERIALS:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(material)}(?![A-Za-z0-9-])", name, re.IGNORECASE):
            return material
    return None


def _pad(values: list[str], count: int, filler: str) -> list[str]:
    return list(values) + [filler] * (count - len(values))


def normalize_filament_slots(
    colour: list[str],
    type_: list[str],
    settings_id: list[str],
    extra_lengths: dict[str, int] | None = None,
    declared_count: int | None = None,
) -> FilamentSlots:
    """One consistent filament count, with every array padded to match.

    `extra_lengths` is the length of any other one-per-filament array the
    source carries; `declared_count` is what the file's own
    `different_settings_to_system` says (its length minus the print and
    printer sections). Both only ever raise the count -- a project is never
    truncated, because dropping a filament would drop whatever is painted with
    it.
    """
    lengths = [len(colour), len(type_), len(settings_id)]
    lengths += list((extra_lengths or {}).values())
    if declared_count is not None:
        lengths.append(declared_count)
    count = max(lengths, default=0)

    warnings: list[str] = []
    disagreeing = {
        "filament_colour": len(colour),
        "filament_type": len(type_),
        "filament_settings_id": len(settings_id),
        **(extra_lengths or {}),
    }
    short = sorted(k for k, n in disagreeing.items() if n != count)
    if short and count:
        warnings.append(
            f"the source's filament lists disagree on how many filaments it has ({', '.join(short)} "
            f"shorter than {count}); padded to {count} so no filament is lost -- check the filament "
            "list in the slicer."
        )

    # Colour last-resort is white rather than a repeat of the previous slot: a
    # duplicate reads as a deliberate pairing of two slots, white reads as
    # unset, which is what it is.
    padded_colour = _pad(colour, count, "#FFFFFF")

    padded_type = list(type_)
    if len(padded_type) < count:
        # Prefer the material named by each slot's own preset, then the most
        # common material in the project, and only then a bare default.
        common = Counter(t for t in type_ if t).most_common(1)
        default = common[0][0] if common else _FALLBACK_MATERIAL
        inferred_any = False
        for slot in range(len(padded_type), count):
            name = settings_id[slot] if slot < len(settings_id) else ""
            material = _material_from_preset_name(name) if name else None
            inferred_any = inferred_any or material is not None
            padded_type.append(material or default)
        if not type_:
            warnings.append(
                "the source declares no filament_type at all; material was read from each filament's "
                f"preset name{'' if inferred_any else f', falling back to {default}'} -- confirm the "
                "filaments in the slicer before printing."
            )

    return FilamentSlots(
        count=count,
        colour=padded_colour,
        type=padded_type,
        settings_id=_pad(settings_id, count, ""),
        warnings=warnings,
    )
