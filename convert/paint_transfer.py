"""Read/rewrite the per-triangle paint_color attribute inside 3D geometry
parts (3D/3dmodel.model and any 3D/Objects/*.model referenced from it --
NOT model_settings.config, which only carries the whole-object/part
"extruder" default).

Geometry files are treated as opaque byte blobs everywhere except this one
attribute: for a large mesh (the real 8-color mask sample has one geometry
part over 79,000 lines), a full XML parse + generic-serializer round trip
risks subtly reformatting float precision or attribute order. A targeted
regex substitution on just `paint_color="..."` touches nothing else.

Encoding, reverse-engineered from BambuStudio's actual serializer
(src/libslic3r/Model.cpp FacetsAnnotation::get_triangle_as_string /
set_triangle_from_string and src/libslic3r/Model.hpp's EnforcerBlockerType
enum, fetched from github.com/bambulab/BambuStudio -- see profiles/SOURCES.md)
rather than guessed from the one "0C" example seen in a sample file:

  - The attribute value is a string of hex digits, one per 4-bit nibble,
    written MOST-recently-generated-nibble-first -- i.e. read the string
    right-to-left to get chronological bitstream order.
  - A *leaf* (unsplit) triangle's state is EnforcerBlockerType V, which for
    V >= 3 (Extruder3, Extruder4, ...) *is* the same 1-indexed logical color
    slot number used by the "extruder" metadata elsewhere in this codebase
    (confirmed against the enum: Extruder1=1, Extruder2=2, Extruder3=3, ...).
    Chronological nibble sequence: V<3 -> single nibble (V<<2); V>=3 ->
    0xC marker, then floor((V-3)/15) 0xF nibbles, then a ((V-3) mod 15)
    remainder nibble. The written string is that sequence reversed.
  - *Split* (subdivided) triangles use a different, recursive nibble shape
    for finer-than-facet paint boundaries. This tool doesn't need to
    remap paint within a triangle, only between logical color slots, so
    split codes are detected and left untouched rather than parsed --
    decode_leaf_paint_color returns None for them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.archive import ThreeMFArchive

_PAINT_COLOR_RE = re.compile(rb'paint_color="([0-9A-Fa-f]+)"')

_SCAN_CHUNK = 4 << 20
_ATTRIBUTE_START = b'paint_color="'
# Just enough to hold an attribute *name* split across a read boundary; the
# value's length is handled exactly, not by a window (see _iter_paint_codes).
_SCAN_OVERLAP = len(_ATTRIBUTE_START)


def _iter_paint_codes(archive: ThreeMFArchive, part_name: str):
    """Yield every paint_color code in a part without holding the part.

    A geometry part can be a hundred megabytes; reading it whole just to count
    attributes was, before this, the single largest allocation a conversion
    made.

    The subtlety is the chunk boundary. An earlier version kept a fixed-size
    tail so an attribute spanning two reads would still be seen whole -- which
    quietly loses any attribute longer than that window, and split-triangle
    codes have no fixed upper bound (real files carry 180-character ones). It
    lost 15 codes on the 8-colour sample, and losing them is silent: the counts
    simply come out low.

    So the tail is cut at the last *unterminated* attribute instead. Everything
    already matched is dropped, so nothing is yielded twice; everything that
    might still be mid-attribute is retained however long it turns out to be,
    so nothing is missed.
    """
    tail = b""
    with archive.open_part(part_name) as stream:
        while chunk := stream.read(_SCAN_CHUNK):
            buffer = tail + chunk
            consumed = 0
            for match in _PAINT_COLOR_RE.finditer(buffer):
                yield match.group(1)
                consumed = match.end()

            rest = buffer[consumed:]
            start = rest.rfind(_ATTRIBUTE_START)
            # An attribute opened but not yet closed: keep all of it. Otherwise
            # keep only enough to reassemble a split attribute name.
            tail = rest[start:] if start != -1 else rest[-_SCAN_OVERLAP :]


def decode_leaf_paint_color(code: str) -> int | None:
    """1-indexed logical color slot for a leaf triangle's paint_color code,
    or None if `code` encodes a split triangle (or is malformed) -- callers
    must leave those untouched rather than guess."""
    if not code:
        return None
    chrono = code[::-1]
    first = int(chrono[0], 16)
    if first & 0b0011 != 0:
        return None  # split_sides != 0 -> not a leaf
    if first != 0xC:
        return first >> 2  # simple leaf, V in {0, 1, 2}
    # extended leaf: chrono = ['C', 'F' * k, remainder]
    if len(chrono) < 2:
        return None
    middle, remainder_ch = chrono[1:-1], chrono[-1]
    if any(ch not in "Ff" for ch in middle):
        return None  # doesn't match the expected all-F run -- don't guess
    k = len(middle)
    remainder = int(remainder_ch, 16)
    return 3 + 15 * k + remainder


def encode_leaf_paint_color(v: int) -> str:
    """Inverse of decode_leaf_paint_color."""
    if v < 0:
        raise ValueError(f"paint color slot must be >= 0, got {v}")
    if v < 3:
        return format(v << 2, "X")
    k, remainder = divmod(v - 3, 15)
    chrono = "C" + "F" * k + format(remainder, "X")
    return chrono[::-1]


def geometry_part_names(archive: ThreeMFArchive) -> list[str]:
    """Every part that can carry <triangle paint_color=.../> -- the root
    model plus any object files it references under 3D/Objects/."""
    return [n for n in archive.names() if n == "3D/3dmodel.model" or (n.startswith("3D/Objects/") and n.endswith(".model"))]


def scan_paint_colors(archive: ThreeMFArchive) -> dict[int, int]:
    """Count leaf triangles per logical color slot across every geometry
    part. Split (non-leaf) codes aren't counted -- can't be decoded to a
    single slot."""
    counts: dict[int, int] = {}
    for part_name in geometry_part_names(archive):
        for code in _iter_paint_codes(archive, part_name):
            v = decode_leaf_paint_color(code.decode("ascii"))
            if v is not None:
                counts[v] = counts.get(v, 0) + 1
    return counts


@dataclass
class PaintTransferReport:
    parts_scanned: list[str] = field(default_factory=list)
    leaf_codes_found: int = 0
    leaf_codes_remapped: int = 0
    split_codes_skipped: int = 0
    out_of_range: list[tuple[str, int]] = field(default_factory=list)  # (part_name, target slot) beyond max_target_slot


def remap_paint_colors(
    archive: ThreeMFArchive, slot_map: dict[int, int], max_target_slot: int | None = None
) -> PaintTransferReport:
    """Rewrite paint_color codes in `archive` in place, through slot_map.
    A code whose decoded slot maps to itself -- the common case, since
    convert/color_mapping.py currently only ever produces identity maps --
    is left byte-for-byte unchanged. Codes that aren't a recognized leaf
    (split triangles, or anything malformed) are left untouched, never
    guessed at."""
    report = PaintTransferReport()

    for part_name in geometry_part_names(archive):
        # Survey the part in chunks first. This alone answers the report, and
        # for an identity map -- the only kind convert/color_mapping.py
        # currently produces -- it is the whole job, so a 100 MB mesh is never
        # held in memory and core/archive.py streams it through untouched.
        needs_rewrite = False
        for raw_code in _iter_paint_codes(archive, part_name):
            if part_name not in report.parts_scanned:
                report.parts_scanned.append(part_name)
            v = decode_leaf_paint_color(raw_code.decode("ascii"))
            if v is None:
                report.split_codes_skipped += 1
                continue
            report.leaf_codes_found += 1
            target_v = slot_map.get(v, v)
            if max_target_slot is not None and target_v > max_target_slot:
                report.out_of_range.append((part_name, target_v))
            if target_v != v:
                needs_rewrite = True

        if not needs_rewrite:
            continue

        def replace_in(match: re.Match) -> bytes:
            v = decode_leaf_paint_color(match.group(1).decode("ascii"))
            if v is None:
                return match.group(0)
            target_v = slot_map.get(v, v)
            if target_v == v:
                return match.group(0)
            report.leaf_codes_remapped += 1
            return b'paint_color="' + encode_leaf_paint_color(target_v).encode("ascii") + b'"'

        data = archive.get_bytes(part_name)
        archive.set_bytes(part_name, _PAINT_COLOR_RE.sub(replace_in, data))

    return report
