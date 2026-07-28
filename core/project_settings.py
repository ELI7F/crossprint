"""Parse/write Metadata/project_settings.config and the numbered
filament_settings_N.config / process_settings_N.config / machine_settings_N.config
snapshot files -- all four use the same flat JSON schema (BambuStudio/Orca
"ConfigBase" serialization): most print/filament/machine values are
single-element string arrays (per-extruder), a handful of machine-wide keys
are bare strings, and "inherits"/"from"/"name"/"version"/
"different_settings_to_system" describe the config itself rather than being
print settings.

project_settings.config is the authoritative *active* state for a project --
it's what actually gets sliced. The numbered snapshot files are a preset
history cache the slicer bundles alongside it and can reference presets for
printers that aren't even the project's current one -- see profiles/SOURCES.md
for a real example: a Snapmaker U1 project whose machine_settings_1.config was
a stray leftover "Bambu Lab P1S" printer preset from the model's edit history.
Treat project_settings.config as ground truth; treat numbered snapshots as
optional/disposable unless a caller has a specific reason to read one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ConfigJSON:
    """Thin wrapper around one flat JSON settings dict, preserving key order."""

    data: dict[str, Any]

    @classmethod
    def parse(cls, text: str) -> ConfigJSON:
        return cls(data=json.loads(text))

    def to_json(self) -> str:
        return json.dumps(self.data, indent=4, ensure_ascii=False) + "\n"

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def get_list(self, key: str) -> list[str] | None:
        v = self.data.get(key)
        if v is None:
            return None
        return v if isinstance(v, list) else [v]

    def get_scalar(self, key: str, index: int = 0, default: Any = None) -> Any:
        v = self.data.get(key, default)
        if isinstance(v, list):
            return v[index] if index < len(v) else default
        return v

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def keys(self):
        return self.data.keys()


class ProjectSettings(ConfigJSON):
    """Metadata/project_settings.config -- adds accessors for the fields the
    resolver and color mapper care about most. Values still live in the
    underlying flat dict; these are just named views onto it."""

    @property
    def printer_settings_id(self) -> str | None:
        return self.get("printer_settings_id")

    @printer_settings_id.setter
    def printer_settings_id(self, value: str) -> None:
        self.data["printer_settings_id"] = value

    @property
    def printer_model(self) -> str | None:
        return self.get("printer_model")

    @printer_model.setter
    def printer_model(self, value: str) -> None:
        self.data["printer_model"] = value

    @property
    def print_settings_id(self) -> str | None:
        return self.get("print_settings_id")

    @print_settings_id.setter
    def print_settings_id(self, value: str) -> None:
        self.data["print_settings_id"] = value

    @property
    def filament_settings_id(self) -> list[str]:
        return self.get_list("filament_settings_id") or []

    @property
    def filament_colour(self) -> list[str]:
        return self.get_list("filament_colour") or []

    @property
    def filament_type(self) -> list[str]:
        return self.get_list("filament_type") or []

    def filament_count(self) -> int:
        return len(self.filament_settings_id)
