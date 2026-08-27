"""Express a setting in the shape the target stores it in, or drop it.

The two slicers disagree about a setting's *arity* as well as its name, type
and value. Snapmaker stores `travel_speed` as a single number; Bambu stores it
once per extruder. Roughly thirty speed and acceleration settings are like
this. Handing the target a bare scalar where it expects a per-extruder vector
-- and, worse, telling it via `different_settings_to_system` that the scalar
is a deliberate override -- produced a project that loaded its config but came
up with no geometry at all.

The first fix simply dropped those keys, on the grounds that inventing
per-extruder values the user never chose is worse than losing a speed they
probably inherited from a preset anyway. That reasoning was too broad, and it
cost real settings:

    A1 source        real U1 file      what we produced
    ['200']          200               (dropped)
    ['6000']         10000             (dropped)

The A1 wraps every one of those values in a *single-element list*. Unwrapping
`['200']` to `200` invents nothing at all -- it is the same number the user
chose, written the way the target writes it. 21 settings were being discarded
over a pair of brackets on the A1 project that surfaced this, and 29 on a U1
project going the other way.

So the rule is now: convert the shape when the conversion is lossless, and drop
only when it isn't.

  * one-element list -> scalar: unwrap. Identical value.
  * n-element list, all equal -> scalar: unwrap. Identical value.
  * n-element list, values differ -> scalar: drop. The target has one slot and
    no basis to choose which extruder's value wins.
  * scalar -> n-element list: repeat it. Every extruder gets the value the user
    chose, which is what a single-extruder source meant by it. This mirrors
    convert/filament_variants.py, which already broadcasts per-filament
    settings across extruder variants for the same reason.
"""
from __future__ import annotations

# A fork's own declared type is the authority on arity, and it covers keys the
# fork's *preset* never mentions. Snapmaker declares `travel_acceleration` as
# coFloat and Bambu as coFloats, but neither preset sets it -- so comparing
# against presets alone left `['10000']` sitting in a U1 project that wanted a
# bare number. The vector types are the pluralised ones; note that
# `coFloatsOrPercents` is a vector while `coFloatOrPercent` is not.
_VECTOR_TYPES = ("coFloats", "coInts", "coBools", "coEnums", "coPercents", "coStrings", "coPoints")


def _declared_is_vector(option_type: str | None) -> bool | None:
    """True/False from the declared type, or None when it says nothing."""
    if not option_type:
        return None
    return option_type.startswith(_VECTOR_TYPES)


def _shape_of(value) -> tuple[str, int]:
    return ("list", len(value)) if isinstance(value, list) else ("scalar", 1)


def _reshape(value, target_value, allow_primary: bool = False):
    """`value` in the target's shape, and whether the primary entry was taken.

    Returns `(converted, took_primary)`, with `converted` None when the
    reshape would lose meaning.

    A source vector whose entries disagree used to be discarded outright,
    which handed the target's stock preset value to the user instead of
    theirs. But those entries are not alternatives of equal standing: a Bambu
    project stores a speed once per *nozzle variant*, and index 0 is the first
    extruder's standard nozzle -- the configuration a 0.4 mm project is
    actually printing with. `outer_wall_speed` on a real H2D file reads
    `['200', '500', '200', '500', '500']`, where 200 is the value the user set
    and 500 belongs to a High Flow nozzle that isn't installed.

    So `allow_primary` collapses to index 0 rather than dropping. The caller
    reports it separately, because unlike an all-equal unwrap this one does
    discard the other entries.
    """
    if isinstance(value, list) and not isinstance(target_value, list):
        if not value:
            return None, False
        # Every entry the same means the list was only ever a wrapper.
        if all(item == value[0] for item in value):
            return value[0], False
        return (value[0], True) if allow_primary else (None, False)

    if not isinstance(value, list) and isinstance(target_value, list):
        if not target_value:
            return None, False
        return [value] * len(target_value), False

    return None, False


def harmonize_shapes(
    config: dict,
    target_defaults: dict,
    keep: set[str],
    target_types: dict[str, str] | None = None,
) -> tuple[dict, list[str], list[str]]:
    """Reshape config entries to the target's arity, dropping the impossible.

    `keep` names settings whose length legitimately tracks the project rather
    than the printer -- filament arrays and the like -- which the target's
    preset can't be compared against.

    `target_defaults` supplies both the expected arity and, for vectors, the
    expected length. `target_types` covers the gap where the target declares a
    setting but its preset never sets one: the declared type still says whether
    it is a vector, which is enough to unwrap a list the target wants flat.
    Growing a scalar into a vector still needs a preset, since only the preset
    says how many entries the target expects.

    Returns the config, the keys dropped, the keys reshaped without loss, and
    the keys collapsed to the primary extruder's value.
    """
    harmonized = dict(config)
    dropped: list[str] = []
    reshaped: list[str] = []
    collapsed: list[str] = []

    for key, target_value in target_defaults.items():
        if key not in harmonized or key in keep:
            continue
        mine, theirs = harmonized[key], target_value
        if not isinstance(theirs, (list, str)) or not isinstance(mine, (list, str)):
            continue
        if _shape_of(mine)[0] == _shape_of(theirs)[0]:
            continue

        # allow_primary: the target's preset confirms this is a per-extruder
        # vector collapsing to a scalar, which is the one case where index 0
        # is known to be the configuration in use.
        converted, took_primary = _reshape(mine, theirs, allow_primary=True)
        if converted is None:
            del harmonized[key]
            dropped.append(key)
        else:
            harmonized[key] = converted
            (collapsed if took_primary else reshaped).append(key)

    for key, option_type in (target_types or {}).items():
        if key not in harmonized or key in keep or key in target_defaults:
            continue
        value = harmonized[key]
        if not isinstance(value, list) or _declared_is_vector(option_type) is not False:
            continue
        # Declared scalar, holding a list: unwrap only when that loses nothing.
        # No preset backs this path, so there is nothing confirming the list is
        # per-extruder, and index 0 would be a guess rather than a reading.
        converted, _ = _reshape(value, "")
        if converted is not None:
            harmonized[key] = converted
            reshaped.append(key)

    return harmonized, sorted(dropped), sorted(set(reshaped)), sorted(set(collapsed))
