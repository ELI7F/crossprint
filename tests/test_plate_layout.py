"""Objects have to be re-placed for the target's bed.

Positions in a .3mf are absolute, and the world is a grid of plates spaced by
the *source* printer's bed size. Converting without touching them left an
11-plate A1 mini project (180 mm bed) with every object outside a Bambu H2C's
printable area: Bambu Studio reported "objects are laid over the boundary" and
disabled slicing.

The layout rule is Bambu's own (`PartPlate.cpp` / `PartPlate.hpp`), not
inferred — see convert/plate_layout.py.
"""
from __future__ import annotations

import json
import re

import pytest

from convert.pipeline import convert
from convert.plate_layout import bed_size, column_count, object_plate_index
from core.archive import ThreeMFArchive

from .conftest import sample_path


def _item_positions(archive: ThreeMFArchive) -> dict[str, tuple[float, float]]:
    data = archive.get_bytes("3D/3dmodel.model")
    start, end = data.find(b"<build"), data.find(b"</build>")
    build = data[start:end].decode("utf-8", "replace")
    out = {}
    for m in re.finditer(r'<item objectid="(\d+)"[^>]*transform="([^"]+)"', build):
        parts = m.group(2).split()
        out[m.group(1)] = (float(parts[9]), float(parts[10]))
    return out


def _bed_of(archive: ThreeMFArchive):
    config = json.loads(archive.get_text("Metadata/project_settings.config"))
    return bed_size(config.get("printable_area"))


def test_column_count_matches_bambus_formula():
    """round(sqrt(n)), +1 when sqrt exceeds it. Verified against two real
    projects: 11 plates render in 4 columns, 9 plates in 3."""
    assert column_count(11) == 4
    assert column_count(9) == 3
    assert column_count(1) == 1
    assert column_count(2) == 2
    assert column_count(4) == 2
    assert column_count(5) == 3


def test_bed_size_reads_the_printable_area_polygon():
    assert bed_size(["0x0", "180x0", "180x180", "0x180"]) == (180.0, 180.0)
    assert bed_size(["0x0", "330x0", "330x320", "0x320"]) == (330.0, 320.0)
    assert bed_size(None) is None
    assert bed_size(["0x0"]) is None


def test_every_object_lands_inside_the_target_bed():
    """The actual regression: an 11-plate 180 mm project onto a 330x320 bed."""
    source = ThreeMFArchive.open(sample_path("a1mini_woody"))
    archive, _ = convert(sample_path("a1mini_woody"), "h2c")

    plate_of = object_plate_index(archive.get_text("Metadata/model_settings.config"))
    target_bed = _bed_of(archive)
    assert target_bed == (330.0, 320.0)

    cols = column_count(max(plate_of.values()) + 1)
    stride_x, stride_y = target_bed[0] * 1.2, target_bed[1] * 1.2

    for object_id, (x, y) in _item_positions(archive).items():
        index = plate_of.get(object_id)
        if index is None:
            continue
        row, col = divmod(index, cols)
        local_x = x - col * stride_x
        local_y = y + row * stride_y
        # Origins are object anchors rather than bounding boxes, so allow a
        # margin; the point is they sit on their plate, not a bed away from it.
        assert -40 <= local_x <= target_bed[0] + 40, (object_id, index, local_x)
        assert -40 <= local_y <= target_bed[1] + 40, (object_id, index, local_y)

    # ...and they genuinely moved, rather than the test passing by luck.
    assert _item_positions(source) != _item_positions(archive)


def test_objects_stay_on_the_plate_the_user_put_them_on():
    source_plates = object_plate_index(
        ThreeMFArchive.open(sample_path("a1mini_woody")).get_text("Metadata/model_settings.config")
    )
    archive, _ = convert(sample_path("a1mini_woody"), "h2c")
    assert object_plate_index(archive.get_text("Metadata/model_settings.config")) == source_plates


@pytest.mark.parametrize(
    "source_name,target",
    [("a1mini_woody", "u1"), ("a1mini_woody", "h2c"), ("u1_majorasmask", "a1-mini")],
)
def test_every_object_anchor_lands_on_its_own_plate(source_name, target):
    """Holds in both directions, including onto a smaller bed. Anchors landing
    on-plate is what the slicer's boundary check reacts to; a very large model
    can still overhang, which is what the shrink warning is for."""
    archive, _ = convert(sample_path(source_name), target)
    bed = _bed_of(archive)
    plate_of = object_plate_index(archive.get_text("Metadata/model_settings.config"))
    cols = column_count(max(plate_of.values()) + 1)

    for object_id, (x, y) in _item_positions(archive).items():
        index = plate_of.get(object_id)
        if index is None:
            continue
        row, col = divmod(index, cols)
        local = (x - col * bed[0] * 1.2, y + row * bed[1] * 1.2)
        assert 0 <= local[0] <= bed[0], (source_name, target, object_id, local)
        assert 0 <= local[1] <= bed[1], (source_name, target, object_id, local)


def test_conversion_reports_the_replacement():
    _, result = convert(sample_path("a1mini_woody"), "h2c")
    assert any("re-placed" in w and "plate" in w for w in result.warnings), result.warnings


def test_shrinking_bed_is_flagged():
    """H2C (330x320) down to U1 (270x270): everything is re-centred, but the
    user needs telling that some of it may no longer fit."""
    _, result = convert(sample_path("h2c_antiwarp"), "u1")
    assert any("smaller" in w for w in result.warnings), result.warnings


@pytest.mark.parametrize(
    "source_name,target",
    [
        ("u1_majorasmask", "h2c"),
        ("u1_toucan_plus", "h2c"),
        ("a1mini_woody", "u1"),      # 11 plates onto a bigger bed, verified in Snapmaker Orca
        ("a1mini_woody", "h2c"),     # ...and onto a bigger one still, verified in Bambu Studio
        ("u1_majorasmask", "a1-mini"),  # 9 plates onto a *smaller* bed
    ],
)
def test_single_and_multi_plate_projects_both_survive(source_name, target):
    source = ThreeMFArchive.open(sample_path(source_name))
    archive, _ = convert(sample_path(source_name), target)

    assert len(_item_positions(archive)) == len(_item_positions(source))
    assert object_plate_index(archive.get_text("Metadata/model_settings.config")) == object_plate_index(
        source.get_text("Metadata/model_settings.config")
    )
