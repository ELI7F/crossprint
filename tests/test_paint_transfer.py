from __future__ import annotations

from core.archive import ThreeMFArchive
from convert.paint_transfer import (
    decode_leaf_paint_color,
    encode_leaf_paint_color,
    geometry_part_names,
    remap_paint_colors,
    scan_paint_colors,
)

from .conftest import sample_path


def test_decode_matches_the_one_real_example_seen_in_a_sample_file():
    # paint_color="0C" from 3D/Objects/Assembly_149.model in the real
    # majorasmask_8color_snapmakeru1.3mf sample.
    assert decode_leaf_paint_color("0C") == 3


def test_encode_decode_roundtrip_for_a_wide_range_of_slots():
    for v in range(0, 60):
        code = encode_leaf_paint_color(v)
        assert decode_leaf_paint_color(code) == v, (v, code)


def test_decode_rejects_split_triangle_codes_instead_of_guessing():
    # split_sides bits (low 2 bits of the first chronological nibble) != 0
    # -- e.g. a nibble of 0b0001 ("1") is a split marker, not a leaf.
    assert decode_leaf_paint_color("1") is None
    assert decode_leaf_paint_color("2") is None
    assert decode_leaf_paint_color("3") is None


def test_scan_real_8color_sample_has_only_in_range_slots():
    """Strongest available real-world check on the whole decode derivation:
    every leaf paint_color in an actual 8-color model should decode to a
    slot in [1, 8]."""
    archive = ThreeMFArchive.open(sample_path("u1_majorasmask"))
    counts = scan_paint_colors(archive)
    assert counts, "expected to find at least some painted triangles"
    assert all(1 <= slot <= 8 for slot in counts), counts


def test_geometry_part_names_finds_object_files():
    archive = ThreeMFArchive.open(sample_path("u1_majorasmask"))
    parts = geometry_part_names(archive)
    assert "3D/3dmodel.model" in parts
    assert any(p.startswith("3D/Objects/") for p in parts)


def test_remap_with_identity_leaves_real_sample_byte_identical():
    archive = ThreeMFArchive.open(sample_path("u1_majorasmask"))
    before = {name: archive.get_bytes(name) for name in geometry_part_names(archive)}

    identity = {i: i for i in range(1, 9)}
    report = remap_paint_colors(archive, identity)

    assert report.leaf_codes_found > 0
    assert report.leaf_codes_remapped == 0
    for name, data in before.items():
        assert archive.get_bytes(name) == data, name


def test_remap_actually_rewrites_and_stays_decodable():
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n<model><resources><object id="1">'
        b'<mesh><triangles>'
        b'<triangle v1="0" v2="1" v3="2" paint_color="0C"/>'  # slot 3
        b'<triangle v1="1" v2="2" v3="3" paint_color="4"/>'  # slot 1
        b"</triangles></mesh></object></resources></model>"
    )
    archive = ThreeMFArchive()
    archive.set_bytes("3D/3dmodel.model", xml)

    report = remap_paint_colors(archive, {3: 5, 1: 1})

    assert report.leaf_codes_found == 2
    assert report.leaf_codes_remapped == 1
    new_data = archive.get_bytes("3D/3dmodel.model")
    assert b'paint_color="' + encode_leaf_paint_color(5).encode() + b'"' in new_data
    assert b'paint_color="4"' in new_data  # slot 1 -> 1, untouched


def test_remap_reports_out_of_range_targets():
    xml = b'<triangle paint_color="0C"/>'  # decodes to slot 3
    archive = ThreeMFArchive()
    archive.set_bytes("3D/3dmodel.model", xml)

    report = remap_paint_colors(archive, {3: 3}, max_target_slot=2)
    assert report.out_of_range == [("3D/3dmodel.model", 3)]
