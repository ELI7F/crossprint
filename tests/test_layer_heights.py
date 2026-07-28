"""A variable layer-height profile has to fit the target's nozzle range.

The permitted range is a printer property and the machines disagree: a
Snapmaker U1 with a 0.4 nozzle allows layers up to 0.32 mm, a Bambu H2C only
0.28. Carry a U1 profile over unchanged and Bambu Studio discards the *whole*
profile — "the variable layer height profile has been reset because some layer
heights exceed the allowed range of the current nozzle" — so the model prints
at a flat uniform height instead of the adaptive one the designer set up.

Clamping the out-of-range points keeps the rest of the profile intact.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from convert.layer_heights import PROFILE_PART, allowed_range, clamp_layer_height_profile
from convert.pipeline import convert
from core.archive import ThreeMFArchive
from core.preset_resolver import PresetLibrary, flatten

from .conftest import DOWNLOADS, sample_path

PROFILES = Path(__file__).parent.parent / "profiles"
WHALE = DOWNLOADS / "Flexi Humpback Whale by 3D_Flexseeds.3mf"


def _machine(vendor: str, preset: str) -> dict:
    library = PresetLibrary(PROFILES / vendor)
    return flatten("machine", library.get("machine", preset), library)


def _heights(archive: ThreeMFArchive) -> list[float]:
    text = archive.get_text(PROFILE_PART)
    if text is None:
        return []
    out: list[float] = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        values = [v for v in line.partition("|")[2].split(";") if v]
        out += [float(v) for i, v in enumerate(values) if i % 2 == 1]
    return out


def test_allowed_range_takes_the_strictest_bound_across_extruders():
    assert allowed_range(_machine("bambu_h2c", "Bambu Lab H2C 0.4 nozzle")) == (0.08, 0.28)
    assert allowed_range(_machine("snapmaker_u1", "Snapmaker U1 (0.4 nozzle)")) == (0.08, 0.32)
    assert allowed_range({}) is None
    assert allowed_range({"min_layer_height": ["0.3"], "max_layer_height": ["0.1"]}) is None


def test_profile_without_the_part_is_a_no_op():
    archive = ThreeMFArchive()
    result = clamp_layer_height_profile(archive, _machine("bambu_h2c", "Bambu Lab H2C 0.4 nozzle"))
    assert result.points_clamped == 0 and result.points_total == 0


def test_only_heights_are_clamped_and_z_breakpoints_are_left_alone():
    archive = ThreeMFArchive()
    # z;height pairs -- 0.35 and 0.02 are out of a 0.08-0.28 range, z values are not.
    archive.set_text(PROFILE_PART, "object_id=1|0.000000;0.350000;10.000000;0.200000;20.000000;0.020000")

    result = clamp_layer_height_profile(archive, _machine("bambu_h2c", "Bambu Lab H2C 0.4 nozzle"))

    assert result.points_clamped == 2
    assert result.points_total == 3
    values = archive.get_text(PROFILE_PART).partition("|")[2].split(";")
    assert [float(v) for v in values[0::2]] == [0.0, 10.0, 20.0]  # z untouched
    assert [float(v) for v in values[1::2]] == [0.28, 0.20, 0.08]


def test_an_in_range_profile_is_left_byte_identical():
    archive = ThreeMFArchive()
    original = "object_id=1|0.000000;0.200000;10.000000;0.150000"
    archive.set_text(PROFILE_PART, original)

    result = clamp_layer_height_profile(archive, _machine("bambu_h2c", "Bambu Lab H2C 0.4 nozzle"))

    assert result.points_clamped == 0
    assert archive.get_text(PROFILE_PART) == original


@pytest.mark.skipif(not WHALE.exists(), reason="whale sample not present")
def test_real_project_keeps_its_profile_within_the_h2c_range():
    """The project that prompted this: 81 of 429 points exceeded 0.28 mm, the
    highest 0.307. All 429 must survive, none out of range."""
    source_heights = _heights(ThreeMFArchive.open(WHALE))
    assert max(source_heights) > 0.28  # the source really is out of H2C's range

    archive, result = convert(WHALE, "h2c")
    heights = _heights(archive)

    assert len(heights) == len(source_heights), "clamping must not drop points"
    assert max(heights) <= 0.28 and min(heights) >= 0.08
    assert any("clamped" in w and "layer-height" in w for w in result.warnings), result.warnings


@pytest.mark.skipif(not WHALE.exists(), reason="whale sample not present")
def test_converting_to_a_roomier_printer_changes_nothing():
    """U1 allows 0.32, so a U1 profile needs no clamping on the way to another
    U1-range machine -- the guard must not touch a profile it doesn't need to."""
    archive, _ = convert(WHALE, "h2d")
    heights = _heights(archive)
    assert heights, "profile should still be present"
    assert max(heights) <= allowed_range(_machine("bambu_h2c", "Bambu Lab H2D 0.4 nozzle"))[1]
