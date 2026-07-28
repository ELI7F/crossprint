# Crossprint

**Crossprint** moves a sliced `.3mf` project onto a different printer's build
plate — between Snapmaker U1 and any Bambu Lab model, in any direction
(including Bambu-to-Bambu). Geometry, colours, per-triangle painting and the
print recipe come across; the target slicer supplies its own machine settings.

Snapmaker Orca and Bambu Studio share an OrcaSlicer ancestor, so the
container and config *shape* are identical — but they have diverged into
genuinely different dialects, and most of this tool's work is reconciling
that. See "Key design decisions" below, and `policy/field_map.yaml`.

## Supported printers

Run `cli.py models` for the live list. Verification depth varies, and the
tool tells you which you're getting:

| | Models | What that means |
|---|---|---|
| **Verified** | Snapmaker U1, Bambu H2C, H2D, A1 mini | Output checked field-by-field against a real project file saved by that printer's own slicer |
| **Best-effort** | H2D Pro, H2S, X2D, A1, A2L, P1P, P1S, P2S, X1, X1 Carbon, X1E | Same mechanism as a verified model in the same hardware class, but no real sample to check against — conversion works and warns you to confirm it opens cleanly before printing |

Anything outside this list is rejected with a clear error rather than
silently extrapolated.

## Setup

```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

## Usage

Web UI (drag-and-drop, runs locally on `127.0.0.1:5000`, opens your browser):

```
./run_web.ps1
```

CLI:

```
.venv/Scripts/python cli.py convert input.3mf --to h2d
.venv/Scripts/python cli.py models
```

To host it publicly, see **[DEPLOY.md](DEPLOY.md)** — the same module serves both
cases and needs no edits.

Source printer is auto-detected from the file's own `printer_model` — no
`--from` flag needed. Warnings (unverified target, AMS slot count outside
the verified reference case, color count beyond U1's 4 simultaneously
mounted toolheads) print to stderr; the file is still written.

## Tests

```
.venv/Scripts/python -m pytest
```

152 tests, run against real `.3mf` files rather than synthetic fixtures —
`tests/conftest.py` points at the user's own Downloads folder and skips
gracefully if a given sample isn't present.

## Architecture

```
core/               parsing/writing primitives, vendor-agnostic
  archive.py          .3mf OPC zip container (opaque-bytes-by-default)
  model_settings.py   Metadata/model_settings.config (per-object color slot + provenance)
  project_settings.py Metadata/project_settings.config (the actual active print config)
  preset_resolver.py  `inherits` chain resolution against a vendor preset library
  field_policy.py     loads policy/field_map.yaml, classifies config keys
  vocabulary.py       which settings/values/ranges each fork can actually resolve
  shapes.py           drops settings the target stores with a different arity
  slicer_owned.py     machine settings we deliberately leave for the slicer

policy/field_map.yaml  passthrough / regenerate / drop, per config key (data, not code)

convert/            the actual conversion logic
  color_mapping.py    logical color slot <-> physical extruder/AMS routing, per hotend class
  filament_mapping.py re-points filament presets at ones the target actually has
  paint_transfer.py   per-triangle paint_color remapping (reverse-engineered
                       bit-packing, see profiles/SOURCES.md)
  settings_diff.py    rebuilds different_settings_to_system (recipe-scoped) so the
                       slicer honors the source's recipe, not the target's defaults
  filament_variants.py per-extruder-variant expansion of per-filament settings
  pipeline.py         model registry + parse -> classify -> map colors -> build -> write

profiles/           vendored official system presets (see SOURCES.md for exact commits)
  bambu_h2c/          github.com/bambulab/BambuStudio, resources/profiles/BBL (all models)
  snapmaker_u1/       github.com/Snapmaker/OrcaSlicer, resources/profiles/Snapmaker
  */config_vocabulary.json   each fork's option names, enum values and types, from its PrintConfig.cpp

tools/extract_vocabulary.py  regenerates those vocabulary files
tests/test_target_slicer_accepts.py  simulates the target's config loader and
                     asserts our output would load — with real slicer-written
                     files as controls
web/app.py          Flask UI (localhost only) wrapping convert/pipeline.py
cli.py              convert <input> --to <model> [-o output] | models
```

If a converted file misbehaves, read **[DEBUGGING.md](DEBUGGING.md)** first — it has
the playbook (how to get ground truth from the slicer itself, how to bisect, what
fails loudly vs. silently) and the ranked future-work list.

## Key design decisions (why, not just what)

- **`project_settings.config` is ground truth; numbered
  `Metadata/*_settings_N.config` snapshots are disposable.** A real U1
  project was found carrying a stray *Bambu Lab P1S* `machine_settings_1.config`
  left over from the model's edit history — completely irrelevant to the
  project's actual active printer. Numbered snapshots are dropped on
  conversion rather than copied or reinterpreted.
- **The two slicers are forks, not the same program.** They disagree at
  several levels, and a converted file has to be reconciled at all three or
  the target rejects it. Getting this wrong produced failures that each
  looked unrelated — "Invalid configuration file", print settings reverting
  to target defaults, and "The file does not contain any geometry data" on
  a file whose geometry part was byte-for-byte identical to the source's:
  - *Setting names.* Snapmaker Orca defines ~250 settings Bambu has never
    heard of, about a third of a real project's config. `core/vocabulary.py`
    filters against each fork's own `PrintConfig.cpp` vocabulary.
  - *Setting values.* Shared names don't mean shared values: Snapmaker
    writes `ironing_pattern: rectilinear`, which Bambu can't parse (it takes
    `concentric`/`zig-zag`). Values are translated by their human label —
    Bambu's `zig-zag` is labelled "Rectilinear", so the user's actual choice
    survives — falling back to the target's own value, then to dropping the
    key so the printer's default applies silently.
  - *Setting types.* The same option can be declared differently in each
    fork. `skeleton_infill_line_width` is a `coFloatOrPercent` in Snapmaker,
    which stores `"100%"`; Bambu declares it a plain `coFloat` and **throws**
    on that value. This was the last fatal defect, and the one that produced
    both "Invalid configuration file" and "does not contain any geometry
    data" — the throw aborts the config load before the model is ever read.
  - *Preset names.* A filament preset named `Snapmaker PLA Matte @U1` reads
    to Bambu as a *custom* preset whose definition should be bundled in the
    project — and conversion deletes those bundles. `convert/filament_mapping.py`
    re-points each filament at a real target system preset by material type.
  - *Arity.* `travel_speed` is one number in Snapmaker and one **per extruder**
    in Bambu (~30 settings like this). Those are dropped rather than invented
    — see `core/shapes.py`.
  - *Ranges.* Bambu writes `prime_tower_brim_width: -1` to mean "auto";
    Snapmaker requires 0 or more. This one fails **quietly** — Orca reports
    "invalid values found in the 3mf" and silently substitutes its own
    defaults, so the project opens but the user's settings never arrive.
    Bounds are extracted per fork and out-of-range values replaced.
- **We own the model, the colours and the print recipe; the slicer owns the
  machine.** Nozzle variants, per-extruder kinematics, AMS routing and purge
  matrices cannot be synthesised from static data: their widths depend on how
  the *installed* slicer pairs printer variants with each filament's
  compatibility, and they differ between versions (one Bambu Studio build
  writes 5 nozzle variants and 17 per-filament entries for 6 filaments; an
  older sample has 4 and 12). Conversion omits them and lets the slicer fill
  them in — see `core/slicer_owned.py`. Likewise
  `different_settings_to_system` is scoped to a curated recipe allowlist:
  marking all 36 differing settings loaded the config but produced no
  geometry, while the recipe-only list loads correctly with the user's values
  applied.

  Each of these is reported in the conversion's warnings.
- **Within that vocabulary, the default policy is `passthrough`, not
  `regenerate`.** A survey of 745 unique keys across 5 real project files
  found most remaining differences were slicer-version skew rather than
  hardware incompatibility. Only fields with direct, confirmed evidence of
  being printer-identity/kinematics/AMS-routing/gcode/host-connection
  related are force-regenerated for the target.
- **A system machine preset describes hardware *capability*, not what a
  saved project actually contains — never copy machine fields from it
  verbatim.** This caused a real bug: Bambu Studio rejected a converted
  file outright as "Invalid configuration file" because kinematics arrays
  were sized for every nozzle variant the H2C model *supports* (5 slots,
  including an aftermarket E3D option) rather than the 4 a real project
  lists. A second instance of the same root cause: single-hotend models
  store `nozzle_type`/`nozzle_volume` as a bare scalar in a real project
  but as `list[1]` in the preset. `convert/color_mapping.py` uses verified
  real-project values for these instead.
- **Carrying a setting's value over isn't enough — the file has to say the
  value deviates from the preset it names.** `different_settings_to_system`
  was originally dropped as stale UI state. It isn't: it's the only thing
  telling the slicer that a project naming a stock preset doesn't hold that
  preset's values. Without it, converted files opened with the *target's*
  default layer height, infill and wall count, even though the source's
  were sitting in the file correctly converted the whole time.
  `convert/settings_diff.py` recomputes it against the target's own preset.
  It only ever names keys that preset defines: an earlier version carried the
  source's list forward, which named Snapmaker-only settings and made Bambu
  Studio refuse to load the file at all.
- **Hotend class is read from the model's *own* preset file, never the
  flattened one.** `master_extruder_id` is also set on the shared root
  preset every Bambu machine inherits from, so classifying a flattened
  preset would label the entire lineup as dual-hotend. Only H2C, H2D and
  H2D Pro set it themselves — H2S and X2D, despite the family naming, are
  single-hotend.
- **Color-count overflow raises an error, it doesn't silently merge
  colors.** Silently picking which colors to combine would change the
  user's art without asking.
- **U1 has no enforced color-count cap.** This overturned the original
  plan's assumption during implementation: a genuine real-world 8-color U1
  project exists with zero physical-routing metadata in its config. U1's 4
  simultaneously-mounted SnapSwap toolheads aren't a hard limit on total
  colors per print — beyond 4 the printer just prompts manual spool swaps,
  which needs no extra metadata in the file.

## Known limitations (not implemented)

- Dual-hotend (Vortek) targets always assume an AMS is attached and the
  stock nozzle configuration — no aftermarket E3D High Flow hotend. Both
  match the verified ground-truth samples; other configurations have no
  real example to check against.
- No color-merging fallback when the source has more colors than the target
  can take — errors out instead (see design decisions above).
- Splitting specific materials onto a Vortek printer's second extruder
  group isn't implemented — everything routes through group 1, matching the
  verified real-world reference cases.
- Split (subdivided) `paint_color` triangles — finer-than-facet paint
  boundaries — pass through unchanged rather than being decoded/remapped.
  Only matters once color-merging is implemented (identity mappings never
  need to touch them).
- Only the 0.4mm nozzle preset is targeted for every model; a project using
  a 0.2/0.6/0.8 nozzle gets converted onto the 0.4 profile.
