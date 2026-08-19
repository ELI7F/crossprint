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


class DuplicatePresetError(ValueError):
    """Two files in one namespace declare the same preset name.

    This was not hypothetical. `profiles/snapmaker_u1/process/` carried a
    `0.20 Standard @Snapmaker U1 (0.4 nozzle)_old.json` alongside the real one,
    both declaring that name and differing in 17 settings, plus 19 ` copy.json`
    duplicates. The index simply let the last file win -- and "last" is decided
    by the filesystem's directory order, which differs between Windows and
    Linux. The same project converted on a developer's machine and on the
    hosted instance produced *different output*, with nothing anywhere saying
    so: locally the stale preset won and the converted file declared
    `support_type` as a deviation while omitting `wipe_speed`; on the server
    the real preset won and it was the other way round.

    Failing loudly is the whole point. A vendored library with two presets of
    the same name has no correct answer, and picking one silently is how that
    stayed invisible.
    """

    def __init__(self, preset_type: str, name: str, first: Path, second: Path) -> None:
        super().__init__(
            f"two {preset_type} presets both named {name!r}: {first.name} and {second.name}. "
            "Which one wins would depend on the filesystem's directory order, so conversion "
            "would differ between machines. Remove the stale or duplicate file."
        )
        self.preset_type = preset_type
        self.name = name
        self.files = (first, second)


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
            source_of: dict[str, Path] = {}
            # Sorted so the traversal order is the same on every filesystem.
            # The guard below makes order irrelevant to the *result*; this just
            # means a failure reproduces identically wherever it is seen.
            for f in sorted(type_dir.glob("*.json")):
                data = json.loads(f.read_text(encoding="utf-8"))
                name = data.get("name")
                if not name:
                    continue
                if name in source_of:
                    raise DuplicatePresetError(preset_type, name, source_of[name], f)
                source_of[name] = f
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
