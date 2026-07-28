# Profile library provenance

System presets are vendored from the official open-source slicer repos rather than
extracted from a local installer .exe, since both are AGPL-3.0 and publish
`resources/profiles/<Vendor>/` directly. This is the same data the installers embed,
without needing to reverse-engineer NSIS payloads, and it's easy to refresh later.

| Dir | Repo | Path | Commit | Date |
|---|---|---|---|---|
| `bambu_h2c/` | https://github.com/bambulab/BambuStudio | `resources/profiles/BBL` | `12f17b06f4f537f9c03162d08bb70cf733c42839` | 2026-06-27 |
| `snapmaker_u1/` | https://github.com/Snapmaker/OrcaSlicer | `resources/profiles/Snapmaker` | `da278db86c20d2487b72f41425691736d52b6727` | 2026-07-23 |

Each dir is the **full vendor library** (all printer models, not just H2C/U1), because
`inherits` chains can reach through other printers' presets (e.g. a filament preset for
one printer inherits from another printer's preset for the same material). Trimming to
only H2C/U1-named files risks silently breaking chain resolution when a future preset
update reorganizes the inheritance graph.

Binary cover/texture assets (`*.stl`, `*.png`, `*.svg`) were stripped — only `*.json`
config files are needed to resolve and flatten presets.

To refresh: re-run the sparse clone in the plan's Phase 0 notes, `cp` the vendor dir over,
strip binaries, and update the commit hashes above. Re-run `tests/test_roundtrip.py`
afterward — a vendor update can rename or restructure presets referenced by field_map.yaml.

## Config vocabularies (`*/config_vocabulary.json`)

`tools/extract_vocabulary.py` scrapes every quoted identifier out of each fork's
`src/libslic3r/PrintConfig.cpp` — the file where every config option is registered — and
writes it next to that vendor's presets. `core/vocabulary.py` merges it with the preset
library at runtime to decide which settings a target slicer can actually resolve.

| Dir | Source |
|---|---|
| `bambu_h2c/config_vocabulary.json` | `bambulab/BambuStudio`, `master`, `src/libslic3r/PrintConfig.cpp` |
| `snapmaker_u1/config_vocabulary.json` | `Snapmaker/OrcaSlicer`, `main`, `src/libslic3r/PrintConfig.cpp` |

Scraping all quoted identifiers rather than only `this->add("…")` calls is deliberate:
whole families of options are registered from loops over static string lists in the same
file (`machine_max_acceleration_` + axis, `filament_extruder_override_keys`, …), and the
narrower pattern demonstrably missed them. Over-including a key is harmless; missing one
silently deletes a real setting. `tests/test_vocabulary.py` validates coverage against
real project files from both slicers.

## paint_color encoding (convert/paint_transfer.py)

The per-triangle `paint_color` hex-string encoding wasn't guessed from the one `"0C"`
example found in a sample file — it was reverse-engineered from BambuStudio's actual
serializer, fetched from `github.com/bambulab/BambuStudio` at the same commit as above:

- `src/libslic3r/Model.cpp` — `FacetsAnnotation::get_triangle_as_string` /
  `set_triangle_from_string` (the nibble packing/unpacking).
- `src/libslic3r/Model.hpp` — `enum class EnforcerBlockerType` (confirms `Extruder1=1,
  Extruder2=2, Extruder3=3, ...`, i.e. the decoded value *is* the same 1-indexed logical
  color slot as the `extruder` metadata used in model_settings.config).
- `src/libslic3r/TriangleSelector.cpp` — `TriangleSelector::serialize` (the surrounding
  bit-stream comment that explains the split-triangle vs leaf-triangle nibble shapes).

Verified against real data: decoding every `paint_color` in the 8-color
majorasmask_8color_snapmakeru1.3mf sample produces only slots 1-8, with zero decode
failures on non-split (leaf) triangles.
