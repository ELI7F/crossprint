"""Load policy/field_map.yaml and classify project_settings.config fields.

See that file's header comment for the reasoning behind each bucket.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

Policy = Literal["passthrough", "regenerate", "drop"]

DEFAULT_FIELD_MAP_PATH = Path(__file__).parent.parent / "policy" / "field_map.yaml"


@dataclass
class FieldPolicy:
    default_policy: Policy
    regenerate: set[str]
    drop: set[str]

    @classmethod
    def load(cls, path: Path = DEFAULT_FIELD_MAP_PATH) -> FieldPolicy:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            default_policy=data.get("default_policy", "passthrough"),
            regenerate=set(data.get("regenerate") or []),
            drop=set(data.get("drop") or []),
        )

    def classify(self, key: str) -> Policy:
        if key in self.regenerate:
            return "regenerate"
        if key in self.drop:
            return "drop"
        return self.default_policy

    def split(self, config: dict) -> tuple[dict, dict, dict]:
        """Partition a flat config dict into (passthrough, regenerate, drop) sub-dicts."""
        buckets: dict[Policy, dict] = {"passthrough": {}, "regenerate": {}, "drop": {}}
        for k, v in config.items():
            buckets[self.classify(k)][k] = v
        return buckets["passthrough"], buckets["regenerate"], buckets["drop"]
