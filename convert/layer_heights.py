"""Clamp a variable-layer-height profile into the target printer's range.

A project can carry an adaptive layer height profile -- the designer paints
thinner layers over curved regions and thicker ones over flat ones -- stored in
`Metadata/layer_heights_profile.txt` as one line per object:

    object_id=1|z;height;z;height;...

The permitted height range is a property of the printer and nozzle, and the
machines disagree: a Snapmaker U1 with a 0.4 nozzle allows up to 0.32 mm, a
Bambu H2C only 0.28. Carrying a U1 profile over unchanged leaves heights the
target cannot print, and the slicer's response is all-or-nothing -- Bambu
Studio discards the *entire* profile and warns "the variable layer height
profile has been reset because some layer heights exceed the allowed range of
the current nozzle".

That costs the whole profile to save a fraction of it. On the real project that
prompted this, 81 of 429 points exceeded 0.28 mm (the highest being 0.307);
clamping those to the limit keeps the other 348 exactly as designed, so the
model still prints with most of its adaptive detail instead of a flat uniform
height.

Clamping is a compromise, not a free lunch: the clamped regions lose the
designer's intended thickness. It is reported so the user can judge.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.archive import ThreeMFArchive

PROFILE_PART = "Metadata/layer_heights_profile.txt"


@dataclass
class LayerHeightClampResult:
    points_clamped: int = 0
    points_total: int = 0
    objects: int = 0
    allowed: tuple[float, float] | None = None
    extreme: float | None = None  # the furthest out-of-range height found


def allowed_range(flat_machine: dict) -> tuple[float, float] | None:
    """The tightest (min, max) layer height across the target's extruders.

    Both are per-extruder lists in the preset. Taking the strictest bound of
    each keeps the profile valid whichever extruder ends up printing it.
    """

    def bound(key: str, pick):
        value = flat_machine.get(key)
        values = value if isinstance(value, list) else [value]
        numbers = []
        for item in values:
            try:
                numbers.append(float(item))
            except (TypeError, ValueError):
                continue
        return pick(numbers) if numbers else None

    low, high = bound("min_layer_height", max), bound("max_layer_height", min)
    if low is None or high is None or not 0 < low < high:
        return None
    return low, high


def clamp_layer_height_profile(archive: ThreeMFArchive, flat_machine: dict) -> LayerHeightClampResult:
    """Bring every height in the profile inside the target's range, in place."""
    result = LayerHeightClampResult()

    text = archive.get_text(PROFILE_PART)
    if text is None:
        return result

    bounds = allowed_range(flat_machine)
    if bounds is None:
        return result
    low, high = bounds
    result.allowed = bounds

    lines = []
    for line in text.splitlines():
        if "|" not in line:
            lines.append(line)
            continue

        prefix, _, payload = line.partition("|")
        values = [v for v in payload.split(";") if v != ""]
        rewritten: list[str] = []
        for index, raw in enumerate(values):
            # Alternating z, height -- only the heights are bounded.
            if index % 2 == 0:
                rewritten.append(raw)
                continue
            try:
                height = float(raw)
            except ValueError:
                rewritten.append(raw)
                continue

            result.points_total += 1
            clamped = min(max(height, low), high)
            if clamped != height:
                result.points_clamped += 1
                if result.extreme is None or abs(height - clamped) > abs(result.extreme - clamped):
                    result.extreme = height
                rewritten.append(f"{clamped:.6f}")
            else:
                rewritten.append(raw)

        result.objects += 1
        lines.append(f"{prefix}|{';'.join(rewritten)}")

    if result.points_clamped:
        archive.set_text(PROFILE_PART, "\n".join(lines) + ("\n" if text.endswith("\n") else ""))
    return result
