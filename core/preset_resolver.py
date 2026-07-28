"""Resolve `inherits` chains against a vendor's official system preset library.

Bambu Studio / Orca "ConfigBase" presets are split into three independent
namespaces -- machine, process, filament -- each inheriting only within its
own namespace (confirmed against profiles/bambu_h2c and profiles/snapmaker_u1:
every machine preset's `inherits` names another machine preset, etc). `inherits`
is a preset *name* (unique within its namespace+vendor), not a file path;
"" or absent means the preset is either a root (a `fdm_*_common.json` base)
or an already-fully-flattened snapshot that doesn't need further resolution --
both shapes were found in real project files, see profiles/SOURCES.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

PresetType = Literal["machine", "process", "filament"]
_PRESET_TYPES: tuple[PresetType, ...] = ("machine", "process", "filament")

# Fields that describe the preset record itself rather than a print/machine
# setting. Always overlay normally during flatten() (a child's own "name"
# should still win over its parent's), but exclude from diff_against_base()
# since comparing them against a parent is meaningless -- "name" and
# "setting_id" differ from the parent by definition.
PRESET_META_KEYS = {
    "inherits",
    "from",
    "instantiation",
    "setting_id",
    "filament_id",
    "version",
    "different_settings_to_system",
    "name",
}


@dataclass
class PresetLibrary:
    """In-memory index of one vendor's system preset directory
    (profiles/<vendor>/{machine,process,filament}/*.json)."""

    vendor_dir: Path
    _by_type_name: dict[tuple[PresetType, str], dict] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        for preset_type in _PRESET_TYPES:
            type_dir = self.vendor_dir / preset_type
            if not type_dir.is_dir():
                continue
            for f in type_dir.glob("*.json"):
                data = json.loads(f.read_text(encoding="utf-8"))
                name = data.get("name")
                if not name:
                    continue
                self._by_type_name[(preset_type, name)] = data

    def get(self, preset_type: PresetType, name: str) -> dict | None:
        return self._by_type_name.get((preset_type, name))

    def names(self, preset_type: PresetType) -> list[str]:
        return [n for (t, n) in self._by_type_name if t == preset_type]

    def __len__(self) -> int:
        return len(self._by_type_name)


def flatten(
    preset_type: PresetType,
    config: dict[str, Any],
    library: PresetLibrary,
    _seen: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return every field at its final effective value: walk the inherits
    chain from the root down, then overlay `config`'s own fields on top.

    An `inherits` name the library doesn't have (e.g. a preset that isn't a
    system preset, or a vendor library that's gone stale) is treated as a
    missing-but-tolerable parent: flatten falls back to `config` as-is rather
    than failing the whole resolve, since in practice `config` itself is
    often already complete (see ProjectSettings docstring).
    """
    parent_name = config.get("inherits") or ""
    if not parent_name:
        return dict(config)
    if parent_name in _seen:
        raise ValueError(f"circular inherits chain: {parent_name!r} already visited in {sorted(_seen)}")
    parent = library.get(preset_type, parent_name)
    if parent is None:
        return dict(config)
    base = flatten(preset_type, parent, library, _seen | {parent_name})
    merged = dict(base)
    merged.update(config)
    return merged


def diff_against_base(
    flat_config: dict[str, Any],
    base_preset: dict[str, Any],
    ignore_keys: set[str] = PRESET_META_KEYS,
) -> dict[str, Any]:
    """Inverse of flatten: the subset of flat_config whose value differs from
    (or is absent from) base_preset. Used to write a preset back out in
    Bambu Studio's own "inherits + delta" style -- which is what most real
    project files look like -- instead of a fully flattened blob."""
    return {k: v for k, v in flat_config.items() if k not in ignore_keys and base_preset.get(k) != v}
