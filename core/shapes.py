"""Only keep a setting when we can express it in the shape the target uses.

The two slicers disagree about a setting's *arity* as well as its name, type
and value. Snapmaker stores `travel_speed` as a single number; Bambu stores it
once per extruder. Roughly thirty speed and acceleration settings are like
this. Handing the target a bare scalar where it expects a per-extruder vector
-- and, worse, telling it via `different_settings_to_system` that the scalar
is a deliberate override -- produced a project that loaded its config but came
up with no geometry at all.

Rather than invent per-extruder values the user never chose, drop those keys
and let the target's own preset supply them. The user loses a speed they
almost certainly inherited from a Snapmaker preset rather than picked, and
keeps a project that opens.

Verified in Bambu Studio: with these keys dropped the converted project loads
with its geometry, colours and print recipe (layer height, shell layers,
supports, infill) intact.
"""
from __future__ import annotations


def _shape_of(value) -> tuple[str, int]:
    return ("list", len(value)) if isinstance(value, list) else ("scalar", 1)


def harmonize_shapes(config: dict, target_defaults: dict, keep: set[str]) -> tuple[dict, list[str]]:
    """Drop config entries whose arity doesn't match the target's own.

    `keep` names settings whose length legitimately tracks the project rather
    than the printer -- filament arrays and the like -- which the target's
    preset can't be compared against.
    """
    harmonized = dict(config)
    dropped: list[str] = []

    for key, target_value in target_defaults.items():
        if key not in harmonized or key in keep:
            continue
        mine, theirs = harmonized[key], target_value
        if not isinstance(theirs, (list, str)) or not isinstance(mine, (list, str)):
            continue
        if _shape_of(mine)[0] == _shape_of(theirs)[0]:
            continue
        del harmonized[key]
        dropped.append(key)

    return harmonized, sorted(dropped)
