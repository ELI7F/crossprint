"""What config keys does a given slicer actually understand?

Snapmaker Orca and Bambu Studio share an OrcaSlicer ancestor, but each fork
has added settings the other has never heard of -- 180+ of them, roughly a
third of a real project's config. `policy/field_map.yaml` originally defaulted
every unrecognised key to `passthrough` on the reasoning that the two are the
same codebase and a stray key is harmless. That was wrong, and it was the root
cause behind a run of failures that each looked like a different bug:

  - "Invalid configuration file" on load;
  - print settings silently reverting to the target's defaults;
  - "The file does not contain any geometry data", with a geometry part that
    was byte-for-byte identical to the source's.

All three came from handing Bambu Studio a project full of settings it cannot
resolve. So conversion now filters the config down to the target's own
vocabulary, assembled from two sources that are already vendored:

  - `profiles/<vendor>/config_vocabulary.json` -- every quoted identifier in
    that fork's own PrintConfig.cpp, extracted by tools/extract_vocabulary.py.
  - the vendor's system preset library -- a few machine-scoped keys appear in
    presets without appearing in PrintConfig.cpp.

Plus the handful of keys that describe the config record itself rather than a
setting. The union is deliberately generous: keeping a key the target ignores
costs nothing, while dropping a real one silently deletes part of the user's
project.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from core.preset_resolver import PresetLibrary, flatten

# Not settings -- these describe the config record, and every real project
# file carries them.
CONFIG_RECORD_KEYS = frozenset({"from", "name", "version", "inherits", "different_settings_to_system"})


@lru_cache(maxsize=None)
def load_vocabulary(vendor_dir: Path) -> frozenset[str]:
    """Every config key the slicer that owns `vendor_dir` can resolve."""
    vocab: set[str] = set(CONFIG_RECORD_KEYS)

    vocab_path = vendor_dir / "config_vocabulary.json"
    if vocab_path.is_file():
        vocab |= set(json.loads(vocab_path.read_text(encoding="utf-8"))["keys"])

    library = PresetLibrary(vendor_dir)
    for preset_type in ("machine", "process", "filament"):
        for name in library.names(preset_type):
            vocab |= set(flatten(preset_type, library.get(preset_type, name), library))

    return frozenset(vocab)


@lru_cache(maxsize=None)
def load_enums(vendor_dir: Path) -> dict[str, dict[str, tuple[str, ...]]]:
    """Permitted values (and labels) per enum option, for options that declare
    them explicitly. Options absent from this map are simply not value-checked
    -- see tools/extract_vocabulary.py for why guessing is worse than not
    knowing."""
    vocab_path = vendor_dir / "config_vocabulary.json"
    if not vocab_path.is_file():
        return {}
    enums = json.loads(vocab_path.read_text(encoding="utf-8")).get("enums", {})
    return {
        key: {kind: tuple(items) for kind, items in entry.items()}
        for key, entry in enums.items()
    }


@lru_cache(maxsize=None)
def load_variant_options(vendor_dir: Path) -> set[str]:
    """Filament settings this slicer stores once per extruder variant rather
    than once per filament. Empty for a fork that has no such concept."""
    vocab_path = vendor_dir / "config_vocabulary.json"
    if not vocab_path.is_file():
        return set()
    return set(json.loads(vocab_path.read_text(encoding="utf-8")).get("variant_options", []))


@lru_cache(maxsize=None)
def load_option_types(vendor_dir: Path) -> dict[str, str]:
    """Each option's declared value type, e.g. `coFloat`, `coPercent`."""
    vocab_path = vendor_dir / "config_vocabulary.json"
    if not vocab_path.is_file():
        return {}
    return json.loads(vocab_path.read_text(encoding="utf-8")).get("types", {})


def value_fits_type(option_type: str, value: str) -> bool:
    """Would the target slicer be able to parse this value for this option?

    A "no" here is fatal, not cosmetic: the slicer's set_deserialize throws,
    load_from_json catches and returns an error, and the project is reported
    as an invalid configuration file -- taking the geometry down with it, so
    the user also sees "the file does not contain any geometry data".
    """
    if not isinstance(value, str) or value in ("", "nil"):
        return True  # empty and nil mean "unset" and are accepted everywhere

    text = value.strip()
    is_percent = text.endswith("%")
    numeric = text[:-1] if is_percent else text

    if option_type in ("coString", "coStrings"):
        return True
    if option_type in ("coFloat", "coFloats", "coInt", "coInts"):
        if is_percent:
            return False
        try:
            float(numeric)
        except ValueError:
            return False
        return True
    if option_type in ("coPercent", "coPercents"):
        if not is_percent:
            return False
        try:
            float(numeric)
        except ValueError:
            return False
        return True
    if option_type in ("coFloatOrPercent", "coFloatsOrPercents"):
        try:
            float(numeric)
        except ValueError:
            return False
        return True
    if option_type in ("coBool", "coBools"):
        return text.lower() in ("0", "1", "true", "false")
    # points, enums and anything unrecognised are validated elsewhere or left alone
    return True


def repair_value_types(config: dict, option_types: dict[str, str], fallbacks: dict) -> tuple[dict, list[str]]:
    """Replace values the target cannot parse for their option's type.

    The forks disagree on types as well as on names: Snapmaker stores
    `skeleton_infill_line_width` as a percent because it declares the option
    coFloatOrPercent, while Bambu declares it a plain coFloat and throws on
    "100%". Prefer the target preset's own value; drop the key if that
    doesn't fit either, so the printer's default applies.
    """
    repaired = dict(config)
    notes: list[str] = []

    for key, option_type in option_types.items():
        if key not in repaired:
            continue
        value = repaired[key]
        items = value if isinstance(value, list) else [value]
        if all(value_fits_type(option_type, item) for item in items):
            continue

        replacement = fallbacks.get(key)
        replacement_items = replacement if isinstance(replacement, list) else [replacement]
        if replacement is not None and all(value_fits_type(option_type, item) for item in replacement_items):
            repaired[key] = replacement
            notes.append(f"{key}: {value!r} -> {replacement!r}")
        else:
            del repaired[key]
            notes.append(f"{key}: {value!r} isn't valid for a {option_type}, using the printer's default")

    return repaired, notes


@lru_cache(maxsize=None)
def load_ranges(vendor_dir: Path) -> dict[str, dict[str, float]]:
    """Numeric bounds per option, for options that declare literal ones."""
    vocab_path = vendor_dir / "config_vocabulary.json"
    if not vocab_path.is_file():
        return {}
    return json.loads(vocab_path.read_text(encoding="utf-8")).get("ranges", {})


def repair_out_of_range(config: dict, ranges: dict[str, dict[str, float]], fallbacks: dict) -> tuple[dict, list[str]]:
    """Replace values outside the target's permitted numeric range.

    This one fails quietly rather than loudly, which is why it went unnoticed
    the longest: Bambu writes `prime_tower_brim_width: -1` to mean "auto",
    Snapmaker Orca requires 0 or more, and Orca's response is to report
    "invalid values found in the 3mf" and quietly substitute its own defaults
    -- so the project opens but the user's settings never arrive.

    Prefer the target preset's own value; drop the key if that is also out of
    range, so the printer's default applies.
    """
    repaired = dict(config)
    notes: list[str] = []

    def within(value) -> bool:
        if not isinstance(value, str):
            return True
        text = value.strip()
        if not text or text.endswith("%"):
            return True  # percents are validated by type, not by these bounds
        try:
            number = float(text)
        except ValueError:
            return True
        if "min" in bounds and number < bounds["min"]:
            return False
        if "max" in bounds and number > bounds["max"]:
            return False
        return True

    for key, bounds in ranges.items():
        if key not in repaired:
            continue
        value = repaired[key]
        items = value if isinstance(value, list) else [value]
        if all(within(item) for item in items):
            continue

        replacement = fallbacks.get(key)
        replacement_items = replacement if isinstance(replacement, list) else [replacement]
        if replacement is not None and all(within(item) for item in replacement_items):
            repaired[key] = replacement
            notes.append(f"{key}: {value!r} -> {replacement!r}")
        else:
            del repaired[key]
            notes.append(f"{key}: {value!r} is outside the allowed range, using the printer's default")

    return repaired, notes


def _translate_by_label(value: str, source_enum: dict, target_enum: dict) -> str | None:
    """The same setting under a renamed value: Snapmaker's ironing pattern
    `rectilinear` and Bambu's `zig-zag` are both labelled "Rectilinear"."""
    source_values, source_labels = source_enum.get("values", ()), source_enum.get("labels", ())
    target_values, target_labels = target_enum.get("values", ()), target_enum.get("labels", ())
    if not (source_labels and target_labels):
        return None
    try:
        label = source_labels[source_values.index(value)]
    except ValueError:
        return None
    if label in target_labels:
        return target_values[target_labels.index(label)]
    return None


def filter_to_vocabulary(config: dict, vocabulary: frozenset[str]) -> tuple[dict, list[str]]:
    """Split a config into (what the target understands, what it doesn't)."""
    kept = {k: v for k, v in config.items() if k in vocabulary}
    dropped = sorted(k for k in config if k not in vocabulary)
    return kept, dropped


def repair_enum_values(
    config: dict,
    target_enums: dict[str, dict[str, tuple[str, ...]]],
    source_enums: dict[str, dict[str, tuple[str, ...]]],
    fallbacks: dict,
) -> tuple[dict, list[str]]:
    """Fix enum values the target can't parse.

    The forks share option names but not always their permitted values --
    Snapmaker writes `ironing_pattern: rectilinear`, which Bambu Studio doesn't
    accept (it takes concentric/zig-zag). Left alone, the slicer either
    substitutes a value behind a "this file came from a newer version" dialog
    or rejects the file.

    Resolution order, most faithful first:
      1. translate by label, preserving what the user actually chose;
      2. otherwise use the target preset's own value;
      3. otherwise drop the key, so the target silently applies its default
         rather than warning about a value it can't read.

    Returns the repaired config and a description of each change.
    """
    repaired = dict(config)
    notes: list[str] = []

    for key, target_enum in target_enums.items():
        if key not in repaired:
            continue
        allowed = target_enum.get("values", ())
        value = repaired[key]
        values = value if isinstance(value, list) else [value]
        # An empty string means "unset" throughout these configs, not an enum value.
        if all(not isinstance(v, str) or v == "" or v in allowed for v in values):
            continue

        if isinstance(value, str):
            translated = _translate_by_label(value, source_enums.get(key, {}), target_enum)
            if translated is not None:
                repaired[key] = translated
                notes.append(f"{key}: {value!r} -> {translated!r}")
                continue

        replacement = fallbacks.get(key)
        if replacement is not None and (
            replacement in allowed if isinstance(replacement, str) else True
        ):
            repaired[key] = replacement
            notes.append(f"{key}: {value!r} -> {replacement!r}")
        else:
            del repaired[key]
            notes.append(f"{key}: {value!r} has no equivalent, using the printer's default")

    return repaired, notes
