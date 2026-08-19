# Debugging playbook

Everything in this file was learned the expensive way, converting a real
project between Snapmaker Orca and Bambu Studio. If a converted file misbehaves,
start here rather than re-deriving it.

## The one rule

**Run the slicer. Don't reason about the file.**

Four consecutive fixes in this project were derived by careful analysis of the
`.3mf` contents. All four were wrong — each explained the symptom plausibly,
and each shipped a file that still failed. The fifth attempt opened Bambu
Studio, and the real cause fell out in minutes.

Analysis is for *narrowing* a hypothesis you can then test in the application.
It is not evidence.

## Getting ground truth

The single highest-value technique: **make the slicer author the answer.**

1. Build a file that *does* load — usually our geometry plus a known-good
   config lifted verbatim from a real project for that printer.
2. Open it in the target slicer.
3. **File → Save Project As.**

That output is a complete, valid project, written by the slicer itself, for the
exact printer and geometry in question. Diffing our config against it answers
questions that no amount of format reasoning can:

- which keys the slicer expects to be present,
- what array widths it actually uses (these are version-dependent),
- which of our keys it never writes at all.

Before any of that, ask the converter what it did:

```
python cli.py convert suspect.3mf --to h2c --dry-run --json
```

`--dry-run` writes nothing, and the JSON names every setting that was dropped,
substituted, reshaped or handed back to the slicer. Most reports of "it opened
but my settings are wrong" are answered there — a key you expected to survive
sitting in the dropped list, or a deviation count of zero — without opening a
slicer at all. It narrows *where* to look; it is not evidence about *why* (see
the one rule above).

## Bisecting a rejected file

Both slicers reject a bad config with a single unhelpful message, so bisect.

Two harness shapes, and the trap in each:

- **Overlay** our keys onto a known-good config, half at a time. Trap: keys
  whose length tracks the filament count. Mixing our 4-filament arrays into a
  6-filament config produces a state no real conversion emits, and the bisection
  then chases an artifact. Move every count-dependent key as one block —
  including `flush_volumes_matrix`, which is `filament_count²` per extruder
  despite not being `filament_`-prefixed.
- **Remove** keys from our own config, half at a time. Trap: strip far enough
  and the loader fails for want of something required, which looks like a hit
  but isn't.

Cross-check any culprit both ways before believing it.

## What is fatal vs. survivable

From `ConfigBase::load_from_json` and `set_deserialize_raw` in the slicer's own
source (`src/libslic3r/Config.cpp`):

| Condition | Result |
|---|---|
| Unknown key, after `handle_legacy` renames and its obsolete-key ignore list | **throws** |
| Value that won't parse for the option's declared type | **throws** |
| Bad `coEnum` / `coEnums` / `coBool` value | survivable — falls back to the option default, logs a substitution |
| Numeric value outside `def->min`/`def->max` | **silent** — the slicer reports "invalid values found" and quietly uses its own defaults |

Any throw is caught, `load_from_json` returns -1, and the import aborts *before
the model is read*. That is why a config error surfaces as **"The file does not
contain any geometry data"** even when the geometry part is byte-for-byte
identical to the source's. Do not chase the geometry when you see that message —
check the config first.

The silent range case is the nastiest: the file opens, nothing looks broken, and
the user's settings simply aren't there.

## Where the forks disagree

Five levels, all enforced by `core/vocabulary.py`, `core/shapes.py` and
`tools/extract_vocabulary.py`, which scrape each fork's `PrintConfig.cpp`:

1. **Names** — Orca defines ~250 settings Bambu has never heard of.
2. **Values** — same option, different enum vocabulary. Map by the *label*:
   Bambu's `zig-zag` is labelled "Rectilinear", which is Orca's value.
3. **Types** — `skeleton_infill_line_width` is `coFloatOrPercent` in Orca
   (holding `"100%"`) and plain `coFloat` in Bambu, which throws on it.
4. **Arity** — `travel_speed` is one number in Orca, one per extruder in Bambu.
5. **Ranges** — `prime_tower_brim_width: -1` means "auto" to Bambu; Orca
   requires ≥ 0.

Re-run `tools/extract_vocabulary.py` after any slicer update; all five checks
are data-driven from it.

## The division of labour

**We own the model, the colours and the print recipe. The slicer owns the
machine.**

Nozzle variants, per-extruder kinematics, AMS routing and purge matrices are
deliberately *omitted* (`core/slicer_owned.py`) rather than computed. Their
widths depend on how the installed slicer pairs printer variants with each
filament's compatibility, and they change between versions — one Bambu Studio
build writes 5 nozzle variants and 17 per-filament entries for 6 filaments where
an older sample has 4 and 12. Every attempt to synthesise them failed; omitting
them works, because the slicer fills them from its own presets for the printer
we name.

The same restraint applies to `different_settings_to_system`. Marking all 36
settings that differed loaded the config but produced no geometry; scoping it to
a curated recipe allowlist (`convert/settings_diff.py`) loads correctly with the
user's values applied. Claim only what the user actually chose.

## Multi-plate projects

Object positions are absolute world coordinates, and the world is a grid of
plates spaced `bed_size x 1.2` apart, `round(sqrt(n))` columns wide (both taken
from `PartPlate.cpp` / `PartPlate.hpp`, and confirmed by measuring two real
projects with different bed sizes before the source was consulted).

That means bed size is baked into every object's coordinates. Changing printer
without re-placing them leaves objects a whole bed away from where they belong:
on a bigger bed they cluster in a corner, on a smaller one they land outside it
and the slicer refuses to slice. `convert/plate_layout.py` handles this; if
positions ever look wrong, check its assumptions against a fresh multi-plate
project first.

## Future work

Ordered by expected value.

- **Verify more models.** Only U1, H2C, H2D and A1 mini are checked against real
  project files; the other 11 warn that they're unverified. The cheapest way to
  promote one is the *Save Project As* trick above — no sample file needed, just
  the printer selected in the slicer.
- **Settings-conflict cleanup.** A converted majorasmask project triggers "Ooze
  prevention is not supported with the prime tower enabled" — a real conflict
  carried over from the source. Worth detecting and resolving these pairs.
- **AMS-less and second-extruder-group routing** for the Vortek family, and
  non-0.4 mm nozzle targets. All currently error out rather than guess.
- **Colour-merge fallback** when a source exceeds the target's capacity, instead
  of the current hard error.
- **Range clamping instead of replacement.** Out-of-range values currently fall
  back to the target's value; clamping to the nearest bound would sometimes
  preserve intent better (e.g. `-1` → `0`).

## Memory: stream, don't load (2026-07-28)

The hosted converter refused any project over 220 MB uncompressed. The cap looked
like a hosting constraint -- 512 MB container, measured 1.5x peak-to-uncompressed
ratio, do the arithmetic -- so it had been treated as a fact to document rather
than a bug to fix. It was a bug.

Measuring where the bytes actually were took one command and reframed the whole
problem:

    total uncompressed: 726 MB
    geometry (.model):  725 MB  (100% of the archive)
    everything else:      1 MB

Conversion rewrites `3D/3dmodel.model` (build transforms, small), and two config
parts. The 32 object meshes carrying essentially all the bytes are never touched.
The old code read all of them into a dict of `bytes` on open and re-encoded all of
them on write -- a gigabyte of work to change a few hundred kilobytes.

`core/archive.py` now opens the container without reading any part, fetches parts
on demand (caching only the small ones), and on write copies anything the caller
never replaced straight from source to output in 1 MiB chunks. Uploads use
Werkzeug's already-spooled stream instead of `.read()`, and results spool too.
726 MB project: >1 GB peak and never completing, to **74 MB peak in 12 s**. A
66 MB project peaks at 70 MB -- cost is flat in project size now.

Two things worth carrying:

**Measure the distribution, not the total.** "The project is 726 MB" and "726 MB
of it is in parts we don't modify" lead to completely different fixes. The first
says buy a bigger container; the second says stop reading them.

**A chunked scanner needs an exact boundary rule, not a big-enough window.** The
first chunked paint scan retained a fixed-size tail so attributes spanning a chunk
boundary would still be seen whole. That silently drops any attribute longer than
the window, and split-triangle paint codes have no fixed upper bound -- real files
carry 180-character ones. It lost 15 codes on the 8-colour sample and reported no
error, because a scanner that misses input just returns a smaller count. Cutting
the tail at the last *unterminated* attribute is exact at any length. The test
runs it at a 97-byte chunk size, smaller than the codes it must not lose.

`tests/test_streaming.py` asserts the memory ceiling, byte-identical mesh
pass-through, and that boundary case, because a stray `get_bytes` in a loop over
geometry would restore the old behaviour without failing anything else.


## When one fix ships with another, don't credit them together (2026-07-28)

`different_settings_to_system` got two changes in the same commit: scope the diff
to keys the target's presets define, and filter it further to a hand-picked
recipe list. Loading started working, and the module recorded that a 36-key list
broke Bambu Studio while a 7-key list worked -- attributing the fix to the list
being *short*.

That was the wrong half. The long list had been built by unioning the *source
project's* entries in, so it named settings the target had no equivalent for.
The vocabulary rule fixed it; the length was incidental. The recipe filter then
quietly did damage for weeks: on a real P1S project, 27 settings differed from
the target's preset and 12 were declared, so every speed and acceleration the
owner had tuned was served from Snapmaker U1's preset instead. The user reported
"the settings still don't carry over" and was right both times.

Proof it was the wrong attribution: with the filter removed the same conversion
now declares 36 keys and Bambu Studio loads it with full geometry.

Two things to carry:

**A silent-loss bug needs a test that looks for absence.** Every existing test
asserted that declared keys were valid. None asserted that valid keys were
declared, so dropping 15 real settings passed 177 tests. The new test walks the
target's preset and fails on anything that differs without being declared.

**When a fix bundles two changes, the post-mortem must separate them.** The
comment recording the wrong cause is what kept the filter in place -- it read as
settled evidence ("both verified in the application") when only the outcome had
been verified, not the mechanism.

## Arity: reshape, don't discard (2026-08-14)

`core/shapes.py` dropped every setting whose arity differed between the forks,
reasoning that inventing per-extruder values the user never chose was worse
than losing a speed they probably inherited from a preset. The reasoning was
sound for the case that motivated it and much too broad for the general one.

What the forks actually disagree about is often only a wrapper:

    A1 source        real U1 file      what we produced
    ['200']          200               (dropped)
    ['6000']         10000             (dropped)

Unwrapping `['200']` to `200` invents nothing -- it is the same number written
the way the target writes it. 30 settings were being discarded over a pair of
brackets on one A1 project, and 29 going the other way on a U1 project. Every
speed and acceleration the owner had tuned, silently replaced by the target's
defaults. The user's report was that the conversion was "really bad", and the
settings vanishing is what they were seeing.

The rule now: convert when the conversion is lossless, drop only when it isn't.
A single-element list unwraps; an all-equal list unwraps; a list whose entries
disagree still drops, because the target has one slot and no basis to pick a
winner; a scalar broadcasts to one entry per extruder.

Two things worth carrying:

**The declared type is a better shape authority than the preset.** Comparing
against the target's preset only covers keys the preset happens to set.
`travel_acceleration` is declared `coFloat` by Snapmaker and `coFloats` by
Bambu while *neither* preset sets it, so it slipped through as `['10000']` in a
project that wanted a bare number. The vocabulary's declared types cover every
key the fork knows about. (Only the vector->scalar direction can use them
alone: growing a scalar needs a preset to say how many entries to make.)

**"Verified in the application" is scoped to what was verified.** The old
comment said dropping was verified in Bambu Studio, and it was -- the file
loaded with its geometry. What was never checked is whether anything *else*
would have loaded too. A verification that a fix works is not evidence that the
alternatives don't.

Re-verified in both applications after the change: Snapmaker Orca shows the A1
project's travel speed 700 and acceleration 6000 as modified values rather than
its own 500 and 10000, and Bambu Studio loads the broadcast direction with full
geometry and the U1 project's inner-wall and infill speeds applied.

## References to the source machine are their own bug class (2026-08-14)

`core/slicer_owned.py` was built around one idea -- the slicer owns the
machine -- and implemented it for kinematics only: nozzle variants,
retraction, purge matrices. It never covered the machine's *identity*, so six
settings kept describing the printer the project came from:

    bed_custom_model          C:/Program Files/Bambu Studio/.../bbl-3dp-X1.stl
    print_compatible_printers ['Bambu Lab A1 0.4 nozzle']
    default_print_profile     0.20mm Standard @BBL A1
    default_filament_profile  ['Bambu PLA Basic @BBL A1']
    inherits_group            ['0.20mm Standard @BBL A1', 'Anycubic PLA Silk...']
    filament_vendor           ['Bambu Lab', ...]

The first one is an absolute path into another slicer's installation
directory. Snapmaker Orca loaded it and drew Bambu's X1 bed as a black slab
standing on the U1's plate, and `print_compatible_printers` naming only the A1
is what left the printer panel showing a placeholder instead of the U1's
picture. Clearing all six also silenced an acceleration warning that had
looked unrelated.

**The user found this, not the tests.** They said "look at the black tray
stuck there and the machine name with no picture". Two runs earlier I had
opened the same file, seen the same screen, and reported it as clean, because
I was checking the things I had just changed -- plates, colours, speeds --
rather than looking at what was actually on screen. Verifying in the
application only pays if you look at the whole window.

The general shape is the same one `convert/filament_mapping.py` documents: a
reference into a library the target does not have. Worth asking of any field
that survives conversion -- does this name something that only exists on the
source machine? `tests/test_pipeline.py` now scans the entire output config for
any string naming the source vendor and fails on a single hit.

## Deviations are per-scope, and the scopes were not finished (2026-08-19)

A 14-colour P1S project converted to U1 opened with the wrong bed temperature,
the wrong max volumetric speed and the wrong flow ratio on the carbon-fibre
slots. The values were correct in the converted file the whole time.

This is the *same* bug as the original `different_settings_to_system` failure,
one scope down. That field has one section per scope -- print, then one per
filament, then printer -- and only the print section had ever been computed.
The filament sections were left empty on the reasoning that a converted project
keeps its source filament preset names, so there is nothing in the target's
library to diff against.

That reasoning was true when written and stopped being true when
`convert/filament_mapping.py` started re-pointing every slot at a real system
preset of the target vendor. Nobody went back. All 14 slots differed from the
preset they now named -- 14 to 18 keys each -- and declared none of it, so
Snapmaker Orca served its own values for every one.

**When a decision is justified by a fact about another module, that decision
has to be revisited when the other module changes.** The comment explaining why
the sections were empty was still there, still well argued, and no longer
describing the code around it. A stale rationale reads exactly like a live one.

Worth asking of the remaining empty scope too: the printer section is empty by a
different argument -- conversion rebuilds machine config wholesale, so nothing
in it is a user deviation -- and that one still holds, because it is a fact
about this pipeline rather than about another module's behaviour.

The same investigation turned up a second defect in one line of ranking code:
a 0.4 mm project's PLA-CF slots were mapped to `Generic PLA-CF @U1 0.6 nozzle`
while `Snapmaker PLA-CF @U1 0.4 nozzle` sat unused. Neither name carries a
preferred-variant word, so the tie fell through to "shortest name" -- and the
wrong-nozzle preset's name is three characters shorter. Tie-breakers chosen for
cosmetics decide real questions once the meaningful keys tie.

## The same code is not the same converter (2026-08-19)

The commit hash in `/healthz` answers "which code is running". It says nothing
about which *data* that code read, and this tool is mostly data: the vendored
preset libraries decide almost every value in the output.

`profiles/snapmaker_u1/process/` carried a
`0.20 Standard @Snapmaker U1 (0.4 nozzle)_old.json` beside the real one. Both
declared that name in their `name` field, and they differed in 17 settings.
`PresetLibrary` indexes by that name and let the last file win -- and "last" is
whatever order the filesystem hands back from `glob`, which is not the same on
Windows and Linux.

So the same project, converted with the same commit, produced different files
on a developer's machine and on the hosted instance. Locally the stale preset
won: the output declared `support_type` as a deviation and omitted
`wipe_speed`. On the server the real preset won and it was the reverse. Nothing
anywhere reported a problem. Nineteen more ` copy.json` files sat in the same
directory; those happened to be byte-identical to their originals, so they had
no effect and equally no warning.

Found only by converting a real file **through the live site** and diffing the
result against the same conversion run locally -- not by reading the health
check, which was green and correct and useless for this.

Two habits follow:

- **When a fix is deployed, compare outputs, not versions.** Convert the same
  file both ways and diff the config. A matching commit hash and a differing
  output is a state this repo has actually been in.
- **Ambiguity in vendored data is a bug even when it is currently harmless.**
  `PresetLibrary` now raises `DuplicatePresetError` rather than picking one,
  and a test asserts both shipped libraries are collision-free, because the
  next person to re-vendor a profile directory will not think to check.
