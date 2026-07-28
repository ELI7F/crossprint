"""Expand per-filament settings to one entry per extruder variant.

On a dual-hotend printer a filament isn't tuned once, it's tuned *per
extruder*, and the slicer stores that as a flat array of
`filament_count x variant_count` entries, interleaved per filament:

    [f0v0, f0v1, f1v0, f1v1, ...]

Confirmed against real project files from three printers -- H2C with 6
filaments has 12 entries, H2D with 4 has 8, a 5-colour H2C has 10, and
single-hotend A1 mini has exactly one per filament. `filament_self_index`
in those files reads ['1','1','2','2','3','3',...], which is the
interleaving written out explicitly.

Which settings are per-variant isn't guessable -- it's the
`filament_options_with_variant` set in each fork's PrintConfig.cpp, so
tools/extract_vocabulary.py captures it and this module applies it.

Getting this wrong is fatal, not cosmetic. A U1 project has one entry per
filament because U1 is single-variant; handing those arrays to a dual-hotend
Bambu leaves every per-variant option half the length the slicer indexes
into, and the project is rejected as an invalid configuration file. This was
the defect that survived four earlier rounds of fixes, found by bisecting a
real conversion against a known-good file inside Bambu Studio itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VariantExpansionResult:
    config: dict
    expanded: list[str] = field(default_factory=list)


def expand_per_variant_options(
    config: dict,
    variant_options: set[str],
    filament_count: int,
    variant_names: list[str],
) -> VariantExpansionResult:
    """Grow every per-variant filament array to filament_count x len(variant_names).

    A single-variant target (`variant_names` of length 1) needs no change --
    one entry per filament is already the right shape.
    """
    variant_count = len(variant_names)
    if variant_count <= 1 or filament_count == 0:
        return VariantExpansionResult(config=dict(config))

    expanded_config = dict(config)
    expanded: list[str] = []
    target_length = filament_count * variant_count

    for key in variant_options:
        value = expanded_config.get(key)
        if not isinstance(value, list) or len(value) != filament_count:
            continue  # absent, already expanded, or an unexpected shape -- leave it alone
        expanded_config[key] = [item for item in value for _ in range(variant_count)]
        expanded.append(key)

    # These two describe the interleaving itself rather than carrying a
    # per-filament value, so they're built rather than repeated.
    if "filament_extruder_variant" in variant_options:
        expanded_config["filament_extruder_variant"] = variant_names * filament_count
        expanded.append("filament_extruder_variant")
    if "filament_self_index" in variant_options:
        expanded_config["filament_self_index"] = [
            str(i + 1) for i in range(filament_count) for _ in range(variant_count)
        ]
        expanded.append("filament_self_index")

    assert all(
        len(expanded_config[k]) == target_length for k in expanded
    ), "every expanded option must end up filament_count x variant_count long"

    expanded += _expand_flush_options(expanded_config, filament_count, variant_count)

    return VariantExpansionResult(config=expanded_config, expanded=sorted(set(expanded)))


def _expand_flush_options(config: dict, filament_count: int, variant_count: int) -> list[str]:
    """Purge volumes are stored per extruder as well, in place.

    `flush_volumes_matrix` is a filament x filament matrix repeated once per
    extruder -- real files show 2*n^2 entries on dual-hotend printers (72 for
    6 filaments, 50 for 5, 32 for 4) and plain n^2 on single-hotend ones.
    `flush_multiplier` follows the same split: one value per extruder on a
    dual, a bare scalar on a single.
    """
    changed: list[str] = []

    matrix = config.get("flush_volumes_matrix")
    if isinstance(matrix, list) and len(matrix) == filament_count * filament_count:
        config["flush_volumes_matrix"] = list(matrix) * variant_count
        changed.append("flush_volumes_matrix")

    multiplier = config.get("flush_multiplier")
    if isinstance(multiplier, str):
        config["flush_multiplier"] = [multiplier] * variant_count
        changed.append("flush_multiplier")

    return changed
