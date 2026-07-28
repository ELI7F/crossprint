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
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.preset_resolver import PresetLibrary, flatten

# Preferred sub-brand per material, most-generic first. "Basic" is Bambu's
# plain variant and the safest default; a project's real settings ride on top
# of it regardless, so this only decides which stock preset it inherits from.
_PREFERRED_VARIANTS = ("Basic", "Pure", "Lite", "Matte", "HF", "Tough")


@dataclass
class FilamentMappingResult:
    filament_settings_id: list[str]
    warnings: list[str] = field(default_factory=list)


def _preset_type(library: PresetLibrary, name: str) -> str | None:
    preset = library.get("filament", name)
    if preset is None:
        return None
    value = flatten("filament", preset, library).get("filament_type")
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _score(name: str, model_tag: str) -> tuple:
    """Rank candidate presets: exact printer tag first, then a preferred
    variant, then the shortest name (which is Bambu's un-suffixed default --
    "Bambu PLA Basic @BBL H2C" beats "... @BBL H2C 0.2 nozzle")."""
    variant_rank = len(_PREFERRED_VARIANTS)
    for i, variant in enumerate(_PREFERRED_VARIANTS):
        if variant in name:
            variant_rank = i
            break
    return (0 if model_tag in name else 1, variant_rank, len(name), name)


def map_filaments_to_target(
    filament_types: list[str],
    target_library: PresetLibrary,
    model_tag: str,
    fallback_preset: str | None,
) -> FilamentMappingResult:
    """Pick a target system filament preset per source filament.

    `model_tag` is the printer's marker inside preset names ("@BBL H2C",
    "@U1"), used to prefer presets tuned for this exact printer.
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
        candidates[material].sort(key=lambda n: _score(n, model_tag))

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
    return FilamentMappingResult(filament_settings_id=resolved, warnings=warnings)
