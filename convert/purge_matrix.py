"""Carry the purge (flush) volume matrix across a hotend-class change.

`flush_volumes_matrix` holds how much filament to purge when changing from
each filament to each other one, in mm3. It was being dropped along with the
rest of the machine layer, on the reasoning that the slicer would supply its
own. It does not: Bambu Studio opens the project and reports

    Error: Purge volumes matrix do not match to the correct size!
    Warning: Partial purging volume set to 0. Multi-color printing may cause
             color mixing in models.

A purge volume of zero is not a cosmetic default -- it means colour bleeding
straight into the model on every tool change.

The shape is read from real project files rather than assumed, and it follows
the hotend class exactly:

    single hotend (A1, A1 mini, P1S, and Snapmaker U1)   n*n
    dual hotend   (H2C, H2D)                           2*n*n

with `flush_volumes_vector` 2n long in both. The diagonal is zero -- changing
a filament for itself purges nothing.

The dual-hotend layout is two n*n blocks, and the second is not a copy: across
every real H2C and H2D file checked, block 1 is block 0 plus exactly 15 mm3
per entry, held at a 900 mm3 ceiling. The second extruder simply needs a
little more to come clean.

So a matrix is reshaped rather than invented, which keeps the user's own purge
volumes -- they are computed from their actual filament colours, and a flat
default would be wrong for every pair.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Extra purge the second extruder needs, mm3 per entry. Measured, not chosen:
#: every real dual-hotend file has block 1 = block 0 + 15.
SECOND_EXTRUDER_EXTRA = 15

#: Ceiling seen in real files -- entries at 900 stay at 900 in both blocks.
MAX_FLUSH = 900


@dataclass
class PurgeMatrixResult:
    matrix: list[str] | None
    vector: list[str] | None
    warnings: list[str] = field(default_factory=list)


def _as_numbers(values) -> list[float] | None:
    if not isinstance(values, list) or not values:
        return None
    try:
        return [float(v) for v in values]
    except (TypeError, ValueError):
        return None


def reshape_purge_matrix(
    matrix,
    vector,
    filament_count: int,
    target_is_dual_hotend: bool,
) -> PurgeMatrixResult:
    """The source's purge volumes in the shape the target stores them.

    Returns `matrix=None` when the source has nothing usable to reshape, which
    leaves the field absent exactly as before -- inventing a matrix from
    nothing would put made-up purge volumes in front of a user who would
    reasonably assume they came from their own colours.
    """
    numbers = _as_numbers(matrix)
    n = filament_count
    if numbers is None or n <= 0:
        return PurgeMatrixResult(matrix=None, vector=None)

    block = n * n
    if len(numbers) == block:
        base = numbers
    elif len(numbers) == 2 * block:
        # Dual-hotend source: block 0 is the first extruder's, and the one to
        # keep -- it is the lower of the two, and the second is derived from it.
        base = numbers[:block]
    else:
        # Neither shape. The count of filaments and the matrix disagree, so any
        # reshape would be guesswork about which entries belong to which pair.
        return PurgeMatrixResult(
            matrix=None,
            vector=None,
            warnings=[
                f"the source's purge volume matrix is {len(numbers)} entries for {n} filament(s), "
                f"which is neither {block} nor {2 * block} -- it was left out rather than reshaped, so "
                "set purging volumes in the slicer before printing a multi-colour job."
            ],
        )

    if target_is_dual_hotend:
        second = [
            0.0 if _is_diagonal(i, n) else min(value + SECOND_EXTRUDER_EXTRA, MAX_FLUSH)
            for i, value in enumerate(base)
        ]
        out = base + second
    else:
        out = base

    # 2n in every real file, single and dual alike, so its length does not
    # depend on the hotend class.
    numeric_vector = _as_numbers(vector)
    out_vector = (
        [_fmt(v) for v in numeric_vector]
        if numeric_vector is not None and len(numeric_vector) == 2 * n
        else None
    )

    return PurgeMatrixResult(matrix=[_fmt(v) for v in out], vector=out_vector)


def _is_diagonal(index: int, n: int) -> bool:
    return index // n == index % n


def _fmt(value: float) -> str:
    """Whole numbers without a trailing ".0" -- real files store "346", not
    "346.0", and a differently spelled number reads as a user override."""
    return str(int(value)) if float(value).is_integer() else str(value)
