"""Re-point `filament_settings_id` at filament presets the target actually has.

A project names its filament presets by name. Carrying the source's names over
leaves a Bambu file saying it uses "Snapmaker PLA Matte @U1" -- a preset Bambu
Studio has never heard of. It then treats it as a *custom* preset bundled with
the project and goes looking for its definition in
`Metadata/filament_settings_N.config`, which conversion deletes. The result is
a dangling reference: Bambu Studio warns about "customized filament or printer
presets" and then rejects the project as an invalid configuration file.

So each filament is re-pointed at a real system preset of the target vendor,
matched on material type. The *values* (temperatures, flow, colour) still pass
through unchanged and are what actually get used; this only fixes the name the
project claims to inherit from, so the reference resolves.

`filament_ids` is the *same* kind of reference and needs the same treatment.
It holds the vendor's catalogue codes -- Bambu's are `GFA00`, `GFB01`, `GFL99`
-- and is what the slicer matches against its filament library and against AMS
tags. Rewriting only the preset name leaves the two disagreeing: a real project
converted here claimed `Bambu PLA Basic @BBL H2C` while still carrying
`P3e70acc`, a custom id inherited from an Anycubic project three conversions
back. The name resolved and the id did not. So the catalogue code is taken from
the same preset that supplied the name, and the two stay consistent by
construction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.preset_resolver import PresetLibrary, flatten

# Preferred sub-brand per material, most-generic first. "Basic" is Bambu's
# plain variant and the safest default; a project's real settings ride on top
# of it regardless, so this only decides which stock preset it inherits from.
_PREFERRED_VARIANTS = ("Basic", "Pure", "Lite", "Matte", "HF", "Tough")


@dataclass
class FilamentMappingResult:
    filament_settings_id: list[str]
    #: The target vendor's catalogue code per filament, taken from the same
    #: preset that supplied the name so the two cannot drift apart.
    filament_ids: list[str] = field(default_factory=list)
    #: Likewise the vendor label. A U1 project listing "Bambu Lab" as the maker
    #: of a Snapmaker preset is merely wrong rather than broken, but it comes
    #: from the same preset and costs nothing to keep straight.
    filament_vendor: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _preset_type(library: PresetLibrary, name: str) -> str | None:
    preset = library.get("filament", name)
    if preset is None:
        return None
    value = flatten("filament", preset, library).get("filament_type")
    if isinstance(value, list):
        return value[0] if value else None
    return value


_NOZZLE_SUFFIX = re.compile(r"(\d+(?:\.\d+)?)\s*nozzle", re.IGNORECASE)


def _nozzle_rank(name: str, nozzle: str) -> int:
    """How well a preset's nozzle qualifier fits the project's nozzle.

    Vendors publish a material three ways: unqualified ("Snapmaker PLA Basic
    @U1"), and per nozzle ("... @U1 0.4 nozzle", "... 0.6 nozzle"). Ranking is
    unqualified, then the matching nozzle, then any other -- deliberately in
    that order. Unqualified first preserves the behaviour verified against real
    project files for the common materials; the point of this function is only
    to stop a *wrong* nozzle winning when no unqualified preset exists.

    That was a real defect: a 0.4 mm project's PLA-CF slots were mapped to
    "Generic PLA-CF @U1 0.6 nozzle" while "Snapmaker PLA-CF @U1 0.4 nozzle"
    sat unused in the same library. Neither name carries a preferred variant
    word, so the tie fell through to "shortest name" -- and the 0.6 preset's
    name is three characters shorter.
    """
    match = _NOZZLE_SUFFIX.search(name)
    if match is None:
        return 0
    return 1 if match.group(1) == nozzle else 2


def _score(name: str, model_tag: str, nozzle: str) -> tuple:
    """Rank candidate presets: exact printer tag first, then nozzle fit, then a
    preferred variant, then the shortest name (which is Bambu's un-suffixed
    default -- "Bambu PLA Basic @BBL H2C" beats "... @BBL H2C 0.2 nozzle")."""
    variant_rank = len(_PREFERRED_VARIANTS)
    for i, variant in enumerate(_PREFERRED_VARIANTS):
        if variant in name:
            variant_rank = i
            break
    return (0 if model_tag in name else 1, _nozzle_rank(name, nozzle), variant_rank, len(name), name)


def map_filaments_to_target(
    filament_types: list[str],
    target_library: PresetLibrary,
    model_tag: str,
    fallback_preset: str | None,
    nozzle: str = "0.4",
) -> FilamentMappingResult:
    """Pick a target system filament preset per source filament.

    `model_tag` is the printer's marker inside preset names ("@BBL H2C",
    "@U1"), used to prefer presets tuned for this exact printer. `nozzle` keeps
    a preset published for a different nozzle from being chosen when one for
    this nozzle exists -- see `_nozzle_rank`.
    """
    candidates: dict[str, list[str]] = {}
    for name in target_library.names("filament"):
        preset = target_library.get("filament", name)
        if preset.get("instantiation") != "true":
            continue
        material = _preset_type(target_library, name)
        if material:
            candidates.setdefault(material, []).append(name)
    for material in candidates:
        candidates[material].sort(key=lambda n: _score(n, model_tag, nozzle))

    resolved: list[str] = []
    unmatched: set[str] = set()
    for material in filament_types:
        options = candidates.get(material)
        if options:
            resolved.append(options[0])
        else:
            unmatched.add(material)
            resolved.append(fallback_preset or "")

    warnings = []
    if unmatched:
        warnings.append(
            f"no {model_tag} preset found for material(s) {', '.join(sorted(unmatched))} -- "
            f"fell back to {fallback_preset!r}; pick the right filament in the slicer before printing."
        )
    return FilamentMappingResult(
        filament_settings_id=resolved,
        filament_ids=[_preset_field(target_library, name, "filament_id") for name in resolved],
        filament_vendor=[_preset_field(target_library, name, "filament_vendor") for name in resolved],
        warnings=warnings,
    )


def _preset_field(library: PresetLibrary, preset_name: str, field_name: str) -> str:
    """One field of a filament preset, or "" if the preset doesn't declare it.

    These fields are declared on the `@base` preset at the root of the inherits
    chain, not on the printer-specific leaf, so the chain has to be flattened
    to see them. An empty string is deliberate for the unknown case: it reads
    as "no entry", which is what a generic project carries, and is far safer
    than leaving the source vendor's value in place.
    """
    preset = library.get("filament", preset_name)
    if preset is None:
        return ""
    value = flatten("filament", preset, library).get(field_name)
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) if value else ""
