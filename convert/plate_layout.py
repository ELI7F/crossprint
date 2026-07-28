"""Re-place objects for the target printer's bed.

Object positions in a `.3mf` are absolute world coordinates, and the world is
laid out as a grid of plates whose spacing is derived from the *source*
printer's bed. Convert a project without touching them and every object stays
where it was on the old machine: on a larger bed they huddle in one corner, and
on a smaller one they fall outside it entirely and the slicer refuses to slice,
reporting "objects are laid over the boundary".

The layout rule is taken from Bambu Studio's own `PartPlate.cpp`, not inferred:

    LOGICAL_PART_PLATE_GAP = 1/5
    origin(0) =  col * (width * (1 + GAP))     // stride is bed size x 1.2
    origin(1) = -row * (depth * (1 + GAP))

    cols = round(sqrt(count)), +1 when sqrt(count) exceeds that round

Measured against two real projects before the source was consulted: an 11-plate
A1 mini (180 mm bed) spaces plates 216 mm apart in a 4-column grid, and a
9-plate U1 (270.5 mm bed) spaces them ~324 mm apart in 3 columns. Both are
exactly size x 1.2 with the formula above.

So each object is moved by: take its offset within its own source plate, centre
that offset on the target bed, and add the target plate's origin. Plate
membership and ordering are untouched -- objects stay on the plate the user put
them on.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from core.archive import ThreeMFArchive

GEOMETRY_PART = "3D/3dmodel.model"
_PLATE_GAP = 1 / 5  # LOGICAL_PART_PLATE_GAP

_BUILD_SECTION = re.compile(rb"<build\b.*?</build>", re.S)
_ITEM = re.compile(rb'(<item\b[^>]*?objectid="(\d+)"[^>]*?transform=")([^"]+)(")', re.S)


def column_count(plate_count: int) -> int:
    """Bambu's own grid width for N plates (PartPlate.hpp::compute_colum_count)."""
    value = math.sqrt(plate_count)
    rounded = round(value)
    return int(rounded + 1) if value > rounded else int(rounded)


def bed_size(printable_area: list[str] | None) -> tuple[float, float] | None:
    """(width, depth) from a `printable_area` polygon like
    ['0x0', '180x0', '180x180', '0x180']."""
    if not printable_area or len(printable_area) < 3:
        return None
    try:
        width = float(printable_area[1].split("x")[0])
        depth = float(printable_area[2].split("x")[1])
    except (ValueError, IndexError):
        return None
    return (width, depth) if width > 0 and depth > 0 else None


def _plate_origin(index: int, cols: int, size: tuple[float, float]) -> tuple[float, float]:
    row, col = divmod(index, cols)
    return col * size[0] * (1 + _PLATE_GAP), -row * size[1] * (1 + _PLATE_GAP)


@dataclass
class PlateLayoutResult:
    objects_moved: int = 0
    plate_count: int = 0
    source_bed: tuple[float, float] | None = None
    target_bed: tuple[float, float] | None = None
    warnings: list[str] = field(default_factory=list)


def object_plate_index(model_settings_xml: str) -> dict[str, int]:
    """object id -> 0-based index of the plate it sits on."""
    mapping: dict[str, int] = {}
    for index, block in enumerate(re.findall(r"<plate>.*?</plate>", model_settings_xml, re.S)):
        for object_id in re.findall(r'key="object_id"\s+value="(\d+)"', block):
            mapping[object_id] = index
    return mapping


def relayout_for_target_bed(
    archive: ThreeMFArchive,
    model_settings_xml: str | None,
    source_bed: tuple[float, float] | None,
    target_bed: tuple[float, float] | None,
) -> PlateLayoutResult:
    """Move every object onto the target bed, in place.

    A no-op when the beds match, when either size is unknown, or when the
    project has no plate information to work from -- in each case leaving the
    coordinates alone is safer than moving them on a guess.
    """
    result = PlateLayoutResult(source_bed=source_bed, target_bed=target_bed)

    if not source_bed or not target_bed:
        return result
    if model_settings_xml is None:
        return result

    plate_of = object_plate_index(model_settings_xml)
    result.plate_count = len(set(plate_of.values())) if plate_of else 0
    if not plate_of:
        return result

    geometry = archive.get_bytes(GEOMETRY_PART)
    if geometry is None:
        return result

    if source_bed == target_bed:
        return result  # same machine footprint; nothing to re-place

    cols = column_count(max(plate_of.values()) + 1)
    # Centre each plate's contents on the new bed rather than leaving them
    # against the old origin.
    recentre = ((target_bed[0] - source_bed[0]) / 2, (target_bed[1] - source_bed[1]) / 2)

    def move_item(match: re.Match) -> bytes:
        prefix, object_id, transform, suffix = match.groups()
        index = plate_of.get(object_id.decode("ascii"))
        if index is None:
            return match.group(0)

        parts = transform.decode("ascii").split()
        if len(parts) != 12:
            return match.group(0)  # unexpected shape -- leave it rather than corrupt it

        source_origin = _plate_origin(index, cols, source_bed)
        target_origin = _plate_origin(index, cols, target_bed)
        x, y = float(parts[9]), float(parts[10])
        parts[9] = f"{x - source_origin[0] + recentre[0] + target_origin[0]:.6f}"
        parts[10] = f"{y - source_origin[1] + recentre[1] + target_origin[1]:.6f}"

        result.objects_moved += 1
        return prefix + " ".join(parts).encode("ascii") + suffix

    def rewrite_build(match: re.Match) -> bytes:
        return _ITEM.sub(move_item, match.group(0))

    updated, count = _BUILD_SECTION.subn(rewrite_build, geometry, count=1)
    if count:
        archive.set_bytes(GEOMETRY_PART, updated)

    if result.objects_moved and (target_bed[0] < source_bed[0] or target_bed[1] < source_bed[1]):
        result.warnings.append(
            f"{MODEL_BED_SHRINK_HINT.format(sw=source_bed[0], sd=source_bed[1], tw=target_bed[0], td=target_bed[1])}"
        )
    return result


MODEL_BED_SHRINK_HINT = (
    "the target bed is smaller ({tw:.0f}x{td:.0f} mm vs {sw:.0f}x{sd:.0f} mm) -- objects were "
    "re-centred on it, but anything that no longer fits will need rearranging in the slicer."
)
