"""The change report: its own mechanics, and that a real conversion fills it in.

The report is what the web UI shows before anyone downloads a file, so the
properties worth pinning are the ones a user would notice if they broke:
that it stays consistent with the warning list, that it never claims a
change that didn't happen, and that it survives being squeezed into a
response header without silently losing whole categories of work.
"""
from __future__ import annotations

import json

import pytest

from convert.pipeline import convert
from convert.report import CATEGORY_LABELS, ChangeReport
from tests.conftest import sample_path


# -- report mechanics -----------------------------------------------------


def test_add_rename_skips_values_that_did_not_change():
    report = ChangeReport()
    report.add_rename("filaments", "Re-pointed", [("PLA @U1", "PLA @BBL H2C"), ("PETG", "PETG")])
    assert [c.items for c in report.changes] == [("PLA @U1 -> PLA @BBL H2C",)]


def test_rename_items_stay_ascii_so_a_legacy_console_can_print_them():
    """A prettier arrow here raised UnicodeEncodeError on a Windows console
    and truncated the CLI's report to its first three lines."""
    report = ChangeReport()
    report.add_rename("printer", "Identity", [("u1", "h2c")])
    assert report.changes[0].items[0].isascii()


def test_add_rename_records_nothing_when_everything_stayed_put():
    report = ChangeReport()
    report.add_rename("printer", "Identity", [("u1", "u1")])
    assert len(report) == 0


def test_unknown_category_is_rejected():
    with pytest.raises(ValueError):
        ChangeReport().add("not-a-category", "x")


def test_changes_group_by_category_in_display_order():
    report = ChangeReport()
    report.add("verify", "last")
    report.add("printer", "first")
    report.add("settings", "middle")
    assert [c.title for c in report.ordered()] == ["first", "middle", "last"]


def test_needs_check_selects_only_flagged_changes():
    report = ChangeReport()
    report.add("settings", "routine")
    report.add("verify", "look at this", needs_check=True)
    assert [c.title for c in report.needs_check()] == ["look at this"]


def test_to_json_trims_item_lists_to_fit_a_budget_without_dropping_changes():
    report = ChangeReport()
    report.add("settings", "many", items=[f"setting_{i}" for i in range(400)])
    report.add("verify", "few", items=["a", "b"])

    payload = report.to_json(budget_bytes=1500)

    assert len(json.dumps(payload)) <= 1500
    # Both changes survive; only the long list is shortened, and it says so.
    assert [c["title"] for c in payload] == ["many", "few"]
    assert payload[0]["omitted"] > 0
    assert len(payload[0]["items"]) + payload[0]["omitted"] == 400


def test_to_json_untrimmed_by_default():
    report = ChangeReport()
    report.add("settings", "many", items=[f"setting_{i}" for i in range(400)])
    assert len(report.to_json()[0]["items"]) == 400
    assert "omitted" not in report.to_json()[0]


def test_to_header_is_ascii_and_bounded():
    report = ChangeReport()
    # Filament and preset names come from user files, so non-ASCII reaches here.
    report.add("filaments", "Re-pointed", items=[f"צבע {i} → Bambu PLA Basic" for i in range(200)])
    header = report.to_header(budget_bytes=4000)
    assert header.isascii()
    assert len(header) <= 4000


def test_text_lines_print_every_item():
    report = ChangeReport()
    report.add("settings", "Dropped 2", detail="because", items=["alpha", "beta"])
    lines = list(report.text_lines())
    assert lines[0] == f"{CATEGORY_LABELS['settings']}:"
    assert any("alpha" in line for line in lines) and any("beta" in line for line in lines)
    assert any("because" in line for line in lines)


# -- what a real conversion produces --------------------------------------


@pytest.fixture(scope="module")
def u1_to_h2c():
    archive, result = convert(sample_path("u1_toucan_plus"), "h2c")
    archive.close()
    return result


def test_conversion_reports_the_work_it_always_does(u1_to_h2c):
    categories = {c.category for c in u1_to_h2c.report.ordered()}
    # These four happen on every conversion regardless of the file: the printer
    # is re-identified, the print preset is re-pointed, filaments are re-pointed
    # at presets the target has, and the source's snapshots are dropped.
    assert {"printer", "process", "filaments", "settings"} <= categories


def test_every_warning_has_a_matching_flagged_change(u1_to_h2c):
    """The two lists are two views of one set of events, so a warning with no
    change behind it (or the reverse) means they have drifted apart."""
    assert len(u1_to_h2c.warnings) == len(u1_to_h2c.report.needs_check())


def test_routine_changes_are_not_flagged_for_checking(u1_to_h2c):
    """A report where everything demands attention is one nobody reads."""
    assert len(u1_to_h2c.report.needs_check()) < len(u1_to_h2c.report)


def test_printer_identity_change_names_both_ends(u1_to_h2c):
    identity = [c for c in u1_to_h2c.report.ordered() if c.category == "printer"]
    assert identity, "printer identity is rewritten on every conversion"
    assert any("Snapmaker U1" in item and "H2C" in item for item in identity[0].items)


def test_deviation_count_is_reported_so_lost_settings_are_visible(u1_to_h2c):
    """Print settings reverting to the target's defaults was a real, silent
    bug. The count of marked deviations is how a user now sees it happening."""
    process = [c for c in u1_to_h2c.report.ordered() if c.category == "process"]
    marked = [c for c in process if "deviation" in c.title]
    assert marked and marked[0].items, marked
