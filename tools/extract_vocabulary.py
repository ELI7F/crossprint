"""Build-time helper: extract each slicer fork's config vocabulary from its
own PrintConfig.cpp and vendor it as JSON under profiles/.

Why this exists: Snapmaker Orca and Bambu Studio share an OrcaSlicer ancestor
but have each added settings the other has never heard of. Carrying a
Snapmaker-only key like `hole_to_polyhole` into a file that claims to be a
Bambu project makes Bambu Studio reject it outright -- so conversion has to
filter the config down to what the *target* actually understands, and that
needs an authoritative list of what each fork defines.

Most config options are registered with a literal call:

    def = this->add("option_name", coFloat);

but not all -- whole families are registered from loops over static string
lists in the same file (`machine_max_acceleration_` + axis, the
`filament_extruder_override_keys` vector, `filament_options_with_variant`,
...). Scanning only for `this->add("...")` misses those, and checking the
result against real project files showed exactly that gap.

So this collects every quoted identifier in the file instead. That is
deliberately generous: over-including a key is harmless (the filter keeps a
setting the target may ignore), while under-including one silently deletes a
real setting from the user's project. Validated against real Bambu project
files in tests/test_vocabulary.py -- with the preset library merged in at
runtime, coverage of those files is complete.

Run this after refreshing profiles/ (see profiles/SOURCES.md) and commit the
result.

Usage:
    python tools/extract_vocabulary.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

SOURCES = {
    "bambu_h2c": "https://raw.githubusercontent.com/bambulab/BambuStudio/master/src/libslic3r/PrintConfig.cpp",
    "snapmaker_u1": "https://raw.githubusercontent.com/Snapmaker/OrcaSlicer/main/src/libslic3r/PrintConfig.cpp",
}

# Config keys are snake_case but not strictly lowercase -- `required_nozzle_HRC`
# is real, and an all-lowercase pattern silently dropped it.
_QUOTED_IDENTIFIER = re.compile(r'"([a-zA-Z][a-zA-Z0-9_]{2,})"')

# Enum options and their permitted values. The forks disagree here even where
# the option name is shared -- Bambu's `ironing_pattern` takes
# concentric/zig-zag while Snapmaker's takes rectilinear/concentric -- so a
# converted project can name a value the target cannot parse.
_OPTION_START = re.compile(r'this->add\(\s*"([a-zA-Z][a-zA-Z0-9_]*)"\s*,\s*(co[A-Za-z]+)')
_ENUM_VALUE = re.compile(r'enum_values\.push_back\(\s*"([^"]*)"\s*\)')
_ENUM_LABEL = re.compile(r'enum_labels\.push_back\(\s*(?:L\(\s*)?"([^"]*)"')


def extract_enums(source: str) -> dict[str, dict[str, list[str]]]:
    """Permitted values per enum option, plus their human labels.

    Labels matter because the forks sometimes rename a value while keeping its
    meaning: Bambu's ironing pattern `zig-zag` is *labelled* "Rectilinear",
    which is the value Snapmaker writes. Matching on label is what lets a
    converted project keep the setting the user actually chose, and it is
    exactly the substitution Bambu Studio performs itself.

    Only options that push their values explicitly are recorded. Some copy
    another option's list (`def->enum_values = def_top_fill_pattern->enum_values;`)
    and resolving that would mean interpreting C++; guessing there produced a
    bogus list of category labels and would have "repaired" perfectly valid
    values. Unknown is safer than wrong -- an option missing from this map is
    simply left alone.
    """
    enums: dict[str, dict[str, list[str]]] = {}
    parts = _OPTION_START.split(source)
    # parts == [preamble, key, type, body, key, type, body, ...]
    for i in range(1, len(parts) - 2, 3):
        key, option_type, body = parts[i], parts[i + 1], parts[i + 2]
        if "Enum" not in option_type:
            continue
        values = _ENUM_VALUE.findall(body)
        if not values:
            continue
        labels = _ENUM_LABEL.findall(body)
        entry: dict[str, list[str]] = {"values": values}
        # Only keep labels when they line up 1:1 with the values; a partial
        # list would silently mis-pair them.
        if len(labels) == len(values):
            entry["labels"] = labels
        enums[key] = entry
    return enums

PROFILES = Path(__file__).resolve().parent.parent / "profiles"


def extract(source: str) -> list[str]:
    return sorted(set(_QUOTED_IDENTIFIER.findall(source)))


_VARIANT_SET = re.compile(r"std::set<std::string>\s+filament_options_with_variant\s*=\s*\{(.*?)\};", re.S)


def extract_variant_options(source: str) -> list[str]:
    """Filament settings stored once per extruder variant rather than once per
    filament. On a dual-hotend printer their arrays are
    `filament_count x variant_count` long -- see convert/filament_variants.py.
    """
    match = _VARIANT_SET.search(source)
    if not match:
        return []
    return sorted(set(re.findall(r'"([a-zA-Z][a-zA-Z0-9_]*)"', match.group(1))))


_MIN = re.compile(r"->min\s*=\s*(-?\d+(?:\.\d*)?)")
_MAX = re.compile(r"->max\s*=\s*(-?\d+(?:\.\d*)?)")


def extract_ranges(source: str) -> dict[str, dict[str, float]]:
    """Numeric bounds per option.

    The forks disagree here too, and unlike an unknown key this fails
    *quietly*: Bambu declares `prime_tower_brim_width` with min -1, where the
    negative value means "auto", while Snapmaker requires 0 or more. Carrying
    Bambu's -1 into a Snapmaker project makes Orca report "invalid values
    found in the 3mf" and fall back to its own defaults -- the settings simply
    don't arrive.

    Only literal numeric bounds are recorded; anything computed is left
    unknown rather than guessed.
    """
    ranges: dict[str, dict[str, float]] = {}
    parts = _OPTION_START.split(source)
    for i in range(1, len(parts) - 2, 3):
        key, _option_type, body = parts[i], parts[i + 1], parts[i + 2]
        bounds: dict[str, float] = {}
        if (m := _MIN.search(body)) is not None:
            bounds["min"] = float(m.group(1))
        if (m := _MAX.search(body)) is not None:
            bounds["max"] = float(m.group(1))
        if bounds:
            ranges[key] = bounds
    return ranges


def extract_option_types(source: str) -> dict[str, str]:
    """Each option's declared value type (`coFloat`, `coPercent`, ...).

    The forks disagree about these too, and unlike a stray key a bad *value*
    is fatal: `set_deserialize` throws on it, load_from_json catches, returns
    -1, and the slicer reports the whole project as an invalid configuration
    file. Snapmaker declares `skeleton_infill_line_width` as
    coFloatOrPercent and happily stores "100%"; Bambu declares the same
    option as a plain coFloat, which cannot parse that.
    """
    return {key: option_type for key, option_type in _OPTION_START.findall(source)}


def main() -> int:
    for vendor, url in SOURCES.items():
        with urllib.request.urlopen(url) as response:
            source = response.read().decode("utf-8", errors="replace")

        keys = extract(source)
        enums = extract_enums(source)
        option_types = extract_option_types(source)
        variant_options = extract_variant_options(source)
        ranges = extract_ranges(source)
        if len(keys) < 400 or len(enums) < 20 or len(option_types) < 400:
            print(
                f"error: {vendor} yielded {len(keys)} keys / {len(enums)} enums / {len(option_types)} types "
                "-- the source layout probably changed",
                file=sys.stderr,
            )
            return 1

        out_path = PROFILES / vendor / "config_vocabulary.json"
        out_path.write_text(
            json.dumps(
                {
                    "source": url,
                    "keys": keys,
                    "enums": enums,
                    "types": option_types,
                    "variant_options": variant_options,
                    "ranges": ranges,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"{vendor}: {len(keys)} options, {len(enums)} enums, {len(option_types)} types, "
            f"{len(variant_options)} per-variant, {len(ranges)} ranged -> {out_path.relative_to(PROFILES.parent)}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
