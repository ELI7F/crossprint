"""End-to-end orchestration: parse a source .3mf, classify + remap its
active config for a different printer, and produce the converted .3mf.

Supports any pair among the registered models below: Snapmaker U1 and every
Bambu Lab model the vendored BBL library (profiles/bambu_h2c -- the full
vendor directory, not H2C-only) actually defines a "<model> 0.4 nozzle"
system preset for. Verification depth varies by model -- see
convert/color_mapping.py's module docstring for exactly what's confirmed
against a real project file (H2C, H2D, A1 mini) versus best-effort
(everything else in the same hotend class). Models outside this registry
are rejected with a clear error rather than silently extrapolated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from convert.color_mapping import map_colors_to_bambu, map_colors_to_u1, remap_object_extruders
from convert.filament_mapping import map_filaments_to_target
from convert.filament_variants import expand_per_variant_options
from convert.layer_heights import clamp_layer_height_profile
from convert.paint_transfer import remap_paint_colors
from convert.plate_layout import bed_size, relayout_for_target_bed
from convert.settings_diff import compute_different_settings_to_system
from core.archive import PathOrStream, ThreeMFArchive
from core.field_policy import FieldPolicy
from core.model_settings import ModelSettings
from core.preset_resolver import PresetLibrary, flatten
from core.shapes import harmonize_shapes
from core.slicer_owned import strip_slicer_owned
from core.project_settings import ProjectSettings
from core.vocabulary import (
    filter_to_vocabulary,
    load_enums,
    load_option_types,
    load_ranges,
    load_variant_options,
    load_vocabulary,
    repair_enum_values,
    repair_out_of_range,
    repair_value_types,
)

PROFILES_ROOT = Path(__file__).parent.parent / "profiles"

# slug -> exact printer_model string, taken from the vendored system machine
# presets themselves (profiles/bambu_h2c/machine/*.json's own "printer_model"
# field), not guessed -- see the diagnostic that built this list.
MODEL_REGISTRY: dict[str, str] = {
    "u1": "Snapmaker U1",
    "h2c": "Bambu Lab H2C",
    "h2d": "Bambu Lab H2D",
    "h2d-pro": "Bambu Lab H2D Pro",
    "h2s": "Bambu Lab H2S",
    "x2d": "Bambu Lab X2D",
    "a1": "Bambu Lab A1",
    "a1-mini": "Bambu Lab A1 mini",
    "a2l": "Bambu Lab A2L",
    "p1p": "Bambu Lab P1P",
    "p1s": "Bambu Lab P1S",
    "p2s": "Bambu Lab P2S",
    "x1": "Bambu Lab X1",
    "x1c": "Bambu Lab X1 Carbon",
    "x1e": "Bambu Lab X1E",
}
_MODEL_BY_PRINTER_MODEL = {v: k for k, v in MODEL_REGISTRY.items()}

# Models with enough real-project verification (see color_mapping.py) to
# recommend without a "largely unverified" caveat. Everything else in
# MODEL_REGISTRY still works (same general mechanism), just less checked.
_WELL_VERIFIED_MODELS = {"u1", "h2c", "h2d", "a1-mini"}


def _vendor_dir(slug: str) -> Path:
    return PROFILES_ROOT / ("snapmaker_u1" if slug == "u1" else "bambu_h2c")


def _machine_preset_name(slug: str) -> str:
    model = MODEL_REGISTRY[slug]
    return f"{model} (0.4 nozzle)" if slug == "u1" else f"{model} 0.4 nozzle"


def _model_tag(slug: str) -> str:
    """The marker a vendor puts in filament preset names to tie them to a
    printer -- "Bambu PLA Basic @BBL H2C", "Snapmaker PLA Matte @U1"."""
    if slug == "u1":
        return "@U1"
    return f"@BBL {MODEL_REGISTRY[slug].removeprefix('Bambu Lab ')}"


# Whole Metadata/ files dropped rather than carried over -- see
# policy/field_map.yaml's trailing comment for why each is safe to drop.
# Settings whose length tracks the project's filament count rather than the
# printer's hardware, so the target preset is not a valid shape reference.
_FILAMENT_SIZED_KEYS = {
    "filament_colour", "filament_type", "filament_settings_id", "filament_ids", "filament_map",
    "different_settings_to_system", "flush_volumes_matrix", "flush_volumes_vector",
}

_DROP_FILE_PREFIXES = ("Metadata/filament_settings_", "Metadata/process_settings_", "Metadata/machine_settings_")
_DROP_FILE_NAMES = ("slice_info.config", "filament_sequence.json")


def _is_dropped_file(name: str) -> bool:
    if name.startswith(_DROP_FILE_PREFIXES):
        return True
    base = name.rsplit("/", 1)[-1]
    if base in _DROP_FILE_NAMES:
        return True
    return base.startswith("plate_") and base.endswith(".json")


class UnsupportedSourceError(ValueError):
    pass


def detect_vendor(project: ProjectSettings) -> str:
    """Returns the registry slug (e.g. "h2d", "a1-mini", "u1") for a
    project's active printer_model, or raises if it's not a model this tool
    has any data for."""
    model = project.printer_model or ""
    slug = _MODEL_BY_PRINTER_MODEL.get(model)
    if slug is None:
        raise UnsupportedSourceError(
            f"unsupported source printer_model={model!r} -- this tool only knows about: "
            f"{', '.join(sorted(MODEL_REGISTRY.values()))} (see profiles/SOURCES.md and "
            "convert/color_mapping.py for what's verified vs. best-effort per model)"
        )
    return slug


@dataclass
class ConversionResult:
    source_vendor: str
    target_vendor: str
    filament_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class SourceInfo:
    vendor: str
    printer_model: str
    filament_count: int
    available_targets: list[str]


def inspect_source(source_path: PathOrStream) -> SourceInfo:
    """Cheap peek at a .3mf's active printer/color config, without doing any
    conversion work -- used by the web UI to show what it detected and let
    the user pick a target before committing to a conversion."""
    # Closed on the way out: inspection reads one small part, and leaving the
    # container open would hold a file handle for a request that is done with it.
    with ThreeMFArchive.open(source_path) as archive:
        project_text = archive.get_text("Metadata/project_settings.config")
        if project_text is None:
            raise ValueError("no Metadata/project_settings.config -- not a slicer-saved 3mf project")
        project = ProjectSettings.parse(project_text)
    vendor = detect_vendor(project)
    return SourceInfo(
        vendor=vendor,
        printer_model=project.printer_model or "",
        filament_count=len(project.filament_colour),
        available_targets=[slug for slug in MODEL_REGISTRY if slug != vendor],
    )


def convert(source_path: PathOrStream, target: str) -> tuple[ThreeMFArchive, ConversionResult]:
    if target not in MODEL_REGISTRY:
        raise ValueError(f"unknown target {target!r}, expected one of {sorted(MODEL_REGISTRY)}")

    archive = ThreeMFArchive.open(source_path)
    project_text = archive.get_text("Metadata/project_settings.config")
    if project_text is None:
        raise ValueError(f"{source_path} has no Metadata/project_settings.config -- not a slicer-saved 3mf project")
    project = ProjectSettings.parse(project_text)

    source_vendor = detect_vendor(project)
    if source_vendor == target:
        raise ValueError(f"source is already {target} ({project.printer_model!r}) -- nothing to convert")

    policy = FieldPolicy.load()
    passthrough, _regenerate_src, _drop = policy.split(project.data)

    target_library = PresetLibrary(_vendor_dir(target))
    target_preset_name = _machine_preset_name(target)
    target_machine = target_library.get("machine", target_preset_name)
    if target_machine is None:
        raise LookupError(f"{target_preset_name!r} not found in {_vendor_dir(target)}")
    flat_target_machine = flatten("machine", target_machine, target_library)

    if target == "u1":
        mapping = map_colors_to_u1(project.filament_colour, project.filament_type, project.filament_settings_id)
    else:
        mapping = map_colors_to_bambu(
            MODEL_REGISTRY[target],
            project.filament_colour,
            project.filament_type,
            project.filament_settings_id,
            target_library,
            target_preset_name,
        )

    result = ConversionResult(
        source_vendor=source_vendor,
        target_vendor=target,
        filament_count=len(mapping.filament_colour),
        warnings=list(mapping.warnings),
    )
    if target not in _WELL_VERIFIED_MODELS:
        result.warnings.append(
            f"{MODEL_REGISTRY[target]} isn't independently verified against a real project file -- "
            "it uses the same mechanism as verified models in its hardware class, but double-check "
            "the result opens cleanly in Bambu Studio before printing."
        )

    new_config = dict(passthrough)
    # Fill every regenerate-bucket key the target machine preset actually defines,
    # except ones this target's hardware class must never carry (see mapping.exclude_fields) --
    # not "drop everything and hope the slicer has a sane fallback."
    for key in policy.regenerate:
        if key in mapping.exclude_fields:
            continue
        if key in flat_target_machine:
            new_config[key] = flat_target_machine[key]
    # Color/AMS routing (computed, not a flat machine-preset copy) wins over the generic fill above.
    new_config.update(mapping.target_fields)
    # Machine identity, set explicitly regardless of what the source called these.
    new_config["printer_settings_id"] = target_preset_name
    new_config["printer_model"] = flat_target_machine.get("printer_model", MODEL_REGISTRY[target])
    new_config["printer_variant"] = flat_target_machine.get("printer_variant", "0.4")
    target_print_profile_name = flat_target_machine.get("default_print_profile", project.print_settings_id)
    new_config["print_settings_id"] = target_print_profile_name

    # Re-point the filament presets at ones the target actually has. Keeping the
    # source's names leaves a dangling reference to a "custom" preset whose
    # definition conversion deletes, which Bambu Studio rejects outright --
    # see convert/filament_mapping.py.
    filament_mapping = map_filaments_to_target(
        filament_types=mapping.filament_type,
        target_library=target_library,
        model_tag=_model_tag(target),
        fallback_preset=(flat_target_machine.get("default_filament_profile") or [None])[0],
    )
    new_config["filament_settings_id"] = filament_mapping.filament_settings_id
    result.warnings.extend(filament_mapping.warnings)

    # A dual-hotend target stores every per-filament setting once per extruder
    # variant. The source printer is single-variant, so its arrays are half the
    # length the target indexes into -- and that alone gets the project
    # rejected. See convert/filament_variants.py.
    expansion = expand_per_variant_options(
        new_config,
        variant_options=load_variant_options(_vendor_dir(target)),
        filament_count=result.filament_count,
        variant_names=mapping.filament_variants,
    )
    new_config = expansion.config
    if expansion.expanded:
        result.warnings.append(
            f"expanded {len(expansion.expanded)} per-filament setting(s) to one entry per extruder "
            f"variant, as {MODEL_REGISTRY[target]} stores them per variant."
        )

    # Drop everything the target slicer has no definition for. The source is a
    # different fork with ~200 settings of its own, and handing those to the
    # target makes it reject the project outright -- see core/vocabulary.py.
    # This runs before the deviation list below so that list can never name a
    # key that is no longer in the config.
    new_config, dropped = filter_to_vocabulary(new_config, load_vocabulary(_vendor_dir(target)))
    if dropped:
        result.warnings.append(
            f"dropped {len(dropped)} setting(s) that {MODEL_REGISTRY[target]}'s slicer doesn't define "
            f"(e.g. {', '.join(dropped[:3])}) -- they have no equivalent on the target and keeping "
            "them would make it refuse to open the file."
        )

    # Shared option names don't guarantee shared *values*: Snapmaker writes
    # `ironing_pattern: rectilinear`, which Bambu Studio can't parse. Substitute
    # the target preset's own value for anything outside its enum.
    flat_target_print = (
        flatten("process", target_library.get("process", target_print_profile_name), target_library)
        if target_library.get("process", target_print_profile_name) is not None
        else {}
    )
    new_config, substitutions = repair_enum_values(
        new_config,
        target_enums=load_enums(_vendor_dir(target)),
        source_enums=load_enums(_vendor_dir(source_vendor)),
        fallbacks={**flat_target_print, **flat_target_machine},
    )
    # Same story one level down: the forks also disagree about a setting's
    # *type*, and a value the target can't parse for its declared type is
    # fatal -- it aborts the whole config load, which the slicer then reports
    # as an invalid configuration file with no geometry.
    new_config, retyped = repair_value_types(
        new_config,
        option_types=load_option_types(_vendor_dir(target)),
        fallbacks={**flat_target_print, **flat_target_machine},
    )
    substitutions += retyped

    # Bounds diverge as well, and this is the failure that hides: Orca reports
    # "invalid values found in the 3mf" and quietly substitutes its own
    # defaults, so the project opens but the settings never arrive.
    new_config, rebounded = repair_out_of_range(
        new_config,
        ranges=load_ranges(_vendor_dir(target)),
        fallbacks={**flat_target_print, **flat_target_machine},
    )
    substitutions += rebounded
    if substitutions:
        result.warnings.append(
            f"{MODEL_REGISTRY[target]} doesn't accept some of the source's setting values; "
            f"used its own instead ({'; '.join(substitutions)})."
        )

    # Drop settings the target stores with a different arity -- typically a
    # per-extruder vector where the source has one number. Keeping them (and
    # marking them as deliberate overrides below) produced a project that
    # loaded its config but showed no geometry. See core/shapes.py.
    new_config, reshaped = harmonize_shapes(
        new_config,
        target_defaults={**flat_target_print, **flat_target_machine},
        keep=load_variant_options(_vendor_dir(target)) | _FILAMENT_SIZED_KEYS,
    )
    if reshaped:
        result.warnings.append(
            f"dropped {len(reshaped)} setting(s) that {MODEL_REGISTRY[target]} stores per extruder "
            f"rather than as a single value (e.g. {', '.join(reshaped[:3])}); its own values apply."
        )

    # Hand the machine layer back to the slicer. Its widths depend on how the
    # installed version pairs printer variants with filament compatibility, and
    # guessing them is what produced "Invalid configuration file" -- see
    # core/slicer_owned.py.
    new_config, slicer_keys = strip_slicer_owned(new_config, load_variant_options(_vendor_dir(target)))
    if slicer_keys:
        result.warnings.append(
            f"left {len(slicer_keys)} machine setting(s) for {MODEL_REGISTRY[target]}'s slicer to fill in "
            "from its own presets (nozzle variants, per-extruder kinematics, purge volumes)."
        )

    # The project now names the target's stock print preset while carrying the
    # source's own print values. Tell the slicer which of those values deviate,
    # or it will serve the preset's defaults and discard them -- see
    # convert/settings_diff.py for how that failure was found.
    target_print_profile = target_library.get("process", target_print_profile_name)
    if target_print_profile is not None:
        new_config["different_settings_to_system"] = compute_different_settings_to_system(
            config=new_config,
            flat_print_profile=flatten("process", target_print_profile, target_library),
            filament_count=result.filament_count,
        )
    else:
        result.warnings.append(
            f"target print preset {target_print_profile_name!r} not found in the vendored profile "
            "library, so per-setting deviations couldn't be marked -- the slicer may reset print "
            "settings (layer height, infill, walls) to its own defaults when opening this file."
        )

    archive.set_text("Metadata/project_settings.config", ProjectSettings(data=new_config).to_json())

    model_settings_text = archive.get_text("Metadata/model_settings.config")
    if model_settings_text is not None:
        ms = ModelSettings.parse(model_settings_text)
        remap_object_extruders(ms, mapping.slot_map)
        archive.set_text("Metadata/model_settings.config", ms.to_xml())

    # Object coordinates are absolute and laid out from the *source* bed. Move
    # them onto the target's bed, or they sit in a corner of a larger one and
    # fall outside a smaller one -- see convert/plate_layout.py.
    layout = relayout_for_target_bed(
        archive,
        model_settings_xml=archive.get_text("Metadata/model_settings.config"),
        source_bed=bed_size(project.get_list("printable_area")),
        target_bed=bed_size(flat_target_machine.get("printable_area")),
    )
    if layout.objects_moved:
        result.warnings.append(
            f"re-placed {layout.objects_moved} object(s) across {layout.plate_count} plate(s) for "
            f"{MODEL_REGISTRY[target]}'s bed."
        )
    result.warnings.extend(layout.warnings)

    # An adaptive layer-height profile is only valid within the target's own
    # nozzle range; leave one height out of bounds and the slicer throws the
    # whole profile away -- see convert/layer_heights.py.
    heights = clamp_layer_height_profile(archive, flat_target_machine)
    if heights.points_clamped:
        low, high = heights.allowed
        result.warnings.append(
            f"clamped {heights.points_clamped} of {heights.points_total} variable layer-height point(s) "
            f"into {MODEL_REGISTRY[target]}'s {low:g}-{high:g} mm range (highest was {heights.extreme:.3f} mm); "
            "the rest of the profile is preserved."
        )

    paint_report = remap_paint_colors(archive, mapping.slot_map, max_target_slot=result.filament_count)
    if paint_report.out_of_range:
        result.warnings.append(
            f"{len(paint_report.out_of_range)} painted triangle(s) reference a color slot "
            "beyond the target's filament list -- check paint in the slicer before printing."
        )

    for name in list(archive.names()):
        if _is_dropped_file(name):
            archive.remove(name)

    return archive, result
