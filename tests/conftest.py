"""Real sample .3mf files used by the round-trip / conversion tests.

These are the user's own downloaded model files, not vendored into the repo
(they're large, third-party, and already sit at a known path) -- tests skip
gracefully if a given sample isn't present on the machine running them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DOWNLOADS = Path.home() / "Downloads"

SAMPLES = {
    # A Snapmaker U1 8-color project with an interesting edit history: it embeds
    # a stray "Bambu Lab P1S" machine_settings snapshot from before the model was
    # re-targeted at U1 (see profiles/SOURCES.md). Good for exercising the
    # "numbered snapshot configs can be irrelevant to the active printer" case.
    "u1_majorasmask": DOWNLOADS / "majorasmask_8color_snapmakeru1.3mf",
    # A Bambu H2C project using stock system presets (no numbered snapshot
    # configs at all except one modified filament).
    "h2c_antiwarp": DOWNLOADS / "תלת מימד" / "H2C_Anti-Warp-UltimateDiffuser-H2.3mf",
    "benchy_ams": DOWNLOADS / "תלת מימד" / "benchy ams test.3mf",
    # Paired U1 / non-U1 exports of the same model -- useful for cross-checking
    # that geometry and painted colors are consistent between a U1 export and a
    # same-model Bambu export of the same underlying design.
    "voronoi_u1": DOWNLOADS / "Voronoi+Toucan+AMS+170%-U1.3mf",
    "voronoi_nonu1": DOWNLOADS / "Voronoi+Toucan+AMS+170%" / "Voronoi+Toucan+AMS+170%.3mf",
    # A 4-color U1 project. Real-world regression case: converting this to H2C
    # was the file that first surfaced the "machine preset enumerates every
    # nozzle variant, a real project only lists the ones actually configured"
    # bug -- Bambu Studio rejected the output as "Invalid configuration file"
    # (see convert/color_mapping.py's _H2C_VERIFIED_MACHINE_FIELDS).
    "u1_toucan_plus": DOWNLOADS / "Toucan-Plus by Rocket Luo.3mf",
}


def sample_path(name: str) -> Path:
    path = SAMPLES[name]
    if not path.exists():
        pytest.skip(f"sample file not present on this machine: {path}")
    return path


@pytest.fixture
def samples():
    return {name: (path if path.exists() else None) for name, path in SAMPLES.items()}
