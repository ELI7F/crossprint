"""Phase 2 acceptance: flatten() must walk multi-level real inherits chains
(confirmed 3 levels deep for both vendors, e.g. U1 process preset ->
fdm_process_U1_common -> fdm_process_U1 -> ...) and produce a superset of the
child's own fields, with the child's own values winning over inherited ones.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.archive import ThreeMFArchive
from core.preset_resolver import PresetLibrary, diff_against_base, flatten

from .conftest import sample_path

PROFILES = Path(__file__).parent.parent / "profiles"


def test_libraries_load():
    h2c = PresetLibrary(PROFILES / "bambu_h2c")
    u1 = PresetLibrary(PROFILES / "snapmaker_u1")
    assert len(h2c) > 1000
    assert len(u1) > 300
    assert h2c.get("machine", "Bambu Lab H2C 0.4 nozzle") is not None
    assert u1.get("machine", "Snapmaker U1 (0.4 nozzle)") is not None


def test_flatten_machine_pulls_in_multilevel_ancestor_fields():
    lib = PresetLibrary(PROFILES / "bambu_h2c")
    h2c = lib.get("machine", "Bambu Lab H2C 0.4 nozzle")
    assert h2c is not None
    assert "bed_exclude_area" not in h2c  # only defined on the grandparent

    flat = flatten("machine", h2c, lib)
    assert "bed_exclude_area" in flat  # inherited from fdm_bbl_3dp_002_common
    assert flat["printer_model"] == "Bambu Lab H2C"  # child's own field preserved
    assert len(flat) > len(h2c)


def test_flatten_real_project_snapshot_against_u1_library():
    """The U1 sample's process_settings_1.config is a real 2-level delta
    (inherits "0.12 Fine @Snapmaker U1 (0.4 nozzle)", which itself inherits
    further) -- flatten it against the vendored U1 library."""
    archive = ThreeMFArchive.open(sample_path("u1_majorasmask"))
    child = json.loads(archive.get_text("Metadata/process_settings_1.config"))
    assert child["inherits"] == "0.12 Fine @Snapmaker U1 (0.4 nozzle)"
    assert "bridge_speed" not in child  # only on a deeper ancestor

    lib = PresetLibrary(PROFILES / "snapmaker_u1")
    flat = flatten("process", child, lib)

    assert "bridge_speed" in flat  # pulled from fdm_process_U1_common
    # child's own override must win over whatever the chain says
    assert flat["sparse_infill_density"] == "7%"
    assert flat["bottom_shell_layers"] == "6"


def test_diff_against_base_roundtrips_the_deltas():
    archive = ThreeMFArchive.open(sample_path("u1_majorasmask"))
    child = json.loads(archive.get_text("Metadata/process_settings_1.config"))
    lib = PresetLibrary(PROFILES / "snapmaker_u1")

    parent = lib.get("process", child["inherits"])
    parent_flat = flatten("process", parent, lib)
    child_flat = flatten("process", child, lib)

    diff = diff_against_base(child_flat, parent_flat)

    # Every field the real file explicitly overrode should reappear in the
    # diff with the same value (modulo the meta keys diff_against_base skips
    # on purpose: name/inherits/from/version/different_settings_to_system).
    explicit_fields = set(child) - {"name", "inherits", "from", "version", "different_settings_to_system", "print_settings_id"}
    for key in explicit_fields:
        assert key in diff, key
        assert diff[key] == child[key], key


def test_missing_inherits_parent_is_tolerated_not_fatal():
    lib = PresetLibrary(PROFILES / "snapmaker_u1")
    orphan = {"name": "test", "inherits": "Definitely Not A Real Preset Name", "foo": "bar"}
    assert flatten("process", orphan, lib) == dict(orphan)


# -- duplicate preset names ------------------------------------------------


def test_vendored_libraries_have_no_duplicate_preset_names():
    """The guard, applied to the libraries actually shipped.

    profiles/snapmaker_u1/process/ carried a stale `..._old.json` and 19
    `... copy.json` files, all declaring names that already existed. The index
    let the last file win, and "last" is the filesystem's directory order --
    so the same project converted on Windows and on the Linux host produced
    different output, silently.
    """
    from convert.pipeline import _vendor_dir

    for slug in ("u1", "h2c"):
        PresetLibrary(_vendor_dir(slug))  # raises DuplicatePresetError if any collide


def test_duplicate_names_raise_rather_than_letting_one_win(tmp_path):
    from core.preset_resolver import DuplicatePresetError

    process = tmp_path / "process"
    process.mkdir()
    (process / "a.json").write_text(json.dumps({"name": "Same Name", "layer_height": "0.2"}), encoding="utf-8")
    (process / "b.json").write_text(json.dumps({"name": "Same Name", "layer_height": "0.3"}), encoding="utf-8")

    with pytest.raises(DuplicatePresetError) as exc:
        PresetLibrary(tmp_path)

    assert "Same Name" in str(exc.value)
    assert "a.json" in str(exc.value) and "b.json" in str(exc.value)


def test_a_library_without_collisions_still_loads(tmp_path):
    process = tmp_path / "process"
    process.mkdir()
    (process / "a.json").write_text(json.dumps({"name": "One", "layer_height": "0.2"}), encoding="utf-8")
    (process / "b.json").write_text(json.dumps({"name": "Two", "layer_height": "0.3"}), encoding="utf-8")

    library = PresetLibrary(tmp_path)

    assert library.get("process", "One")["layer_height"] == "0.2"
    assert library.get("process", "Two")["layer_height"] == "0.3"
