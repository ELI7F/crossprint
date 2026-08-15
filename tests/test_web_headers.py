"""The two response headers the change report travels in, and the pages.

Nothing here converts a project: the risky part isn't the conversion, it's
that a report large enough to be useful has to cross an HTTP header without
becoming something a proxy rejects or a browser can't parse.
"""
from __future__ import annotations

import base64
import gzip
import json

import pytest

from convert.pipeline import ConversionResult
from web.app import _full_report_header, _REPORT_HEADER_BUDGET, _result_header, app


def _big_result() -> ConversionResult:
    """A conversion about as noisy as the real worst case: a U1 project to an
    H2C names 173 dropped settings, 78 slicer-owned and 53 regenerated."""
    result = ConversionResult(source_vendor="u1", target_vendor="h2c", filament_count=4)
    result.note(
        "settings",
        "Dropped 173 setting(s) the target's slicer doesn't define",
        detail="They have no equivalent on the target.",
        items=[f"snapmaker_only_setting_{i}" for i in range(173)],
        warning="dropped 173 setting(s) that Bambu Lab H2C's slicer doesn't define",
    )
    result.note("settings", "Left 78 machine setting(s) for the slicer to fill in",
                items=[f"machine_key_{i}" for i in range(78)])
    # Preset names come out of user files and are not necessarily ASCII.
    result.report.add("filaments", "Re-pointed", items=["צבע כתום @U1 -> Bambu PLA Basic @BBL H2C"])
    return result


@pytest.fixture(scope="module")
def result():
    return _big_result()


def test_trimmed_header_stays_within_budget(result):
    assert len(_result_header(result)) <= _REPORT_HEADER_BUDGET


def test_trimmed_header_is_ascii(result):
    """A header carrying a raw non-ASCII byte is a protocol error, and the
    item lists it quotes come from user files."""
    assert _result_header(result).isascii()


def test_trimmed_header_never_sacrifices_a_warning(result):
    """Item lists are detail and can be cut; warnings are the point."""
    assert json.loads(_result_header(result))["warnings"] == result.warnings


def test_full_header_carries_every_item(result):
    changes = json.loads(gzip.decompress(base64.b64decode(_full_report_header(result))))
    assert sum(len(c.get("items", [])) for c in changes) == sum(len(c.items) for c in result.report.changes)
    assert not any("omitted" in c for c in changes)


def test_full_header_is_smaller_than_the_trimmed_one(result):
    """The whole reason for compressing: setting names repeat, so the full
    report costs less to send than the truncated copy of it."""
    assert len(_full_report_header(result)) < len(_result_header(result))


def test_full_header_is_declined_rather_than_oversized(monkeypatch):
    """Past the ceiling the header is dropped, not sent oversized -- the page
    falls back to the trimmed report, which is always there."""
    monkeypatch.setattr("web.app._FULL_REPORT_CEILING", 10)
    assert _full_report_header(_big_result()) is None


@pytest.fixture
def client():
    return app.test_client()


def test_converter_page_renders(client):
    body = client.get("/").get_data(as_text=True)
    assert "Crossprint" in body and 'id="dropzone"' in body


def test_help_page_lists_every_supported_printer(client):
    from convert.pipeline import MODEL_REGISTRY

    body = client.get("/help").get_data(as_text=True)
    for label in MODEL_REGISTRY.values():
        assert label in body, f"{label} missing from the help page's model table"


def test_help_page_explains_every_report_category(client):
    """A category that can appear in a report and isn't explained anywhere is
    a label the user has no way to interpret."""
    from convert.report import CATEGORY_LABELS

    body = client.get("/help").get_data(as_text=True)
    for label in CATEGORY_LABELS.values():
        assert label in body, f"report category {label!r} is never explained on the help page"


def test_pages_link_to_each_other(client):
    assert '/help' in client.get("/").get_data(as_text=True)
    assert 'href="/"' in client.get("/help").get_data(as_text=True)
