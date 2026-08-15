"""A structured account of what conversion actually did to a project.

`ConversionResult.warnings` predates this and stays: it's a flat list of
prose sentences, which is the right shape for "something here deserves your
attention" and the wrong shape for "show me the diff". Every interesting
thing conversion does is a *change*, and most changes are not warnings --
re-pointing the filament presets, rewriting the printer identity and
recomputing the deviation list all happen on every single conversion and are
exactly what a user wants to see before trusting the output.

So this records all of it, categorised and machine-readable, and the UI
renders it as a change list. Warnings remain a view onto the subset with
`needs_check` set, so nothing that used to be surfaced stops being surfaced.

The one constraint worth knowing about: the web UI receives this in a
response *header*, alongside the converted file, so that showing the diff
costs no second conversion. Headers are not a place for unbounded data --
a project can drop 200 settings, and the full list is several KB. `to_json`
therefore takes an explicit budget and truncates from the longest item lists
first, recording how many it left out. The CLI has no such limit and prints
everything.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Iterator

# Display order is deliberate: identity first (what printer is this now?),
# then the recipe, then the reconciliation work, then physical layout, and
# finally the things the user has to check themselves.
CATEGORY_LABELS: dict[str, str] = {
    "printer": "Printer identity",
    "process": "Print profile",
    "filaments": "Filaments",
    "colors": "Colours and routing",
    "settings": "Settings reconciled",
    "geometry": "Objects and plates",
    "paint": "Colour painting",
    "files": "Bundled files",
    "verify": "Check before printing",
}
_CATEGORY_ORDER = {name: i for i, name in enumerate(CATEGORY_LABELS)}


@dataclass(frozen=True)
class Change:
    """One thing conversion did, and why.

    `items` carries the specifics -- setting names, `old -> new` pairs -- so
    the summary line can stay readable while the detail is still there for
    anyone who wants it.
    """

    category: str
    title: str
    detail: str = ""
    items: tuple[str, ...] = ()
    needs_check: bool = False

    def as_dict(self, max_items: int | None = None) -> dict:
        items = list(self.items)
        omitted = 0
        if max_items is not None and len(items) > max_items:
            omitted = len(items) - max_items
            items = items[:max_items]
        out: dict = {"category": self.category, "title": self.title}
        if self.detail:
            out["detail"] = self.detail
        if items:
            out["items"] = items
        if omitted:
            out["omitted"] = omitted
        if self.needs_check:
            out["needsCheck"] = True
        return out


@dataclass
class ChangeReport:
    changes: list[Change] = field(default_factory=list)

    def add(
        self,
        category: str,
        title: str,
        *,
        detail: str = "",
        items: Iterable[str] = (),
        needs_check: bool = False,
    ) -> Change:
        if category not in CATEGORY_LABELS:
            raise ValueError(f"unknown report category {category!r}")
        change = Change(category, title, detail, tuple(items), needs_check)
        self.changes.append(change)
        return change

    def add_rename(self, category: str, title: str, pairs: Iterable[tuple[str, str]], *, detail: str = "") -> None:
        """Record `old -> new` moves, skipping the ones that didn't move.

        Nothing is gained by telling someone a value stayed the same, and a
        change list padded with no-ops is one people stop reading.

        The separator is ASCII on purpose. A prettier arrow printed to a
        Windows console in any non-UTF-8 code page raises UnicodeEncodeError
        and kills the whole report mid-line; the web UI substitutes a real
        arrow at render time, where the encoding is known.
        """
        items = [f"{old} -> {new}" for old, new in pairs if old != new]
        if items:
            self.add(category, title, detail=detail, items=items)

    def ordered(self) -> list[Change]:
        # Stable within a category: insertion order is pipeline order, which
        # is the order the work actually happened in.
        return sorted(self.changes, key=lambda c: _CATEGORY_ORDER[c.category])

    def needs_check(self) -> list[Change]:
        return [c for c in self.ordered() if c.needs_check]

    def __len__(self) -> int:
        return len(self.changes)

    def __bool__(self) -> bool:
        return bool(self.changes)

    # -- serialisation ----------------------------------------------------

    def to_json(self, max_items: int | None = None, budget_bytes: int | None = None) -> list[dict]:
        """Categorised dicts, optionally trimmed to fit a byte budget.

        Trimming takes from the longest item lists first so that a change
        with two specifics keeps both while one with two hundred loses the
        tail -- the alternative, dropping whole changes, would hide entire
        categories of work to make room for the detail of one.
        """
        payload = [c.as_dict(max_items) for c in self.ordered()]
        if budget_bytes is None:
            return payload

        limit = max_items if max_items is not None else max((len(c.items) for c in self.changes), default=0)
        while _encoded_size(payload) > budget_bytes and limit > 0:
            limit = max(0, min(limit - 1, _longest_item_list(payload) - 1))
            payload = [c.as_dict(limit) for c in self.ordered()]
        return payload

    def to_header(self, budget_bytes: int = 6000) -> str:
        return json.dumps(self.to_json(max_items=25, budget_bytes=budget_bytes), ensure_ascii=True)

    # -- text -------------------------------------------------------------

    def text_lines(self, indent: str = "  ") -> Iterator[str]:
        """The CLI rendering: every change, every item, nothing truncated."""
        current = None
        for change in self.ordered():
            if change.category != current:
                current = change.category
                yield f"{CATEGORY_LABELS[current]}:"
            flag = " (check)" if change.needs_check else ""
            yield f"{indent}{change.title}{flag}"
            if change.detail:
                yield f"{indent}{indent}{change.detail}"
            for item in change.items:
                yield f"{indent}{indent}- {item}"


def _encoded_size(payload: list[dict]) -> int:
    return len(json.dumps(payload, ensure_ascii=True))


def _longest_item_list(payload: list[dict]) -> int:
    return max((len(c.get("items", ())) for c in payload), default=0)
