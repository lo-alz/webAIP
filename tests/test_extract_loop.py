"""
Loop 2 (Extract → AIXM 5.2 → self-check) — US coded SID path, offline.

Runs against committed data (FAACIFP18, the waypoint index, Loop 1's manifest).
Loop 1's manifest must exist; the session/CI builds it via scripts/census_loop.py,
and these tests skip cleanly if it is absent rather than failing spuriously.
"""
from __future__ import annotations

import copy

import pytest

from miner.loop import extract
from miner.loop.extract import Context, extract_step, run_extract
from miner.loop.framework import FLAGGED, OK, PENDING
from tests.helpers import make_sid

MANIFEST = extract.MANIFEST_DIR / "US_2607.json"
from miner.waypoint_db import DEFAULT_DB_PATH  # noqa: E402

_READY = (MANIFEST.exists() and extract.CIFP_PATH.exists()
          and __import__("os").path.exists(DEFAULT_DB_PATH))
pytestmark = pytest.mark.skipif(
    not _READY,
    reason="Loop 1 manifest / CIFP / waypoint index missing — "
           "run scripts/census_loop.py --airac 2607 first")


@pytest.fixture(scope="module")
def ctx():
    c, _ = Context.for_us("2607", write_gml=False, validate_xsd=False)
    return c


# ── self-check helpers (synthetic records) ───────────────────────────────────
def test_seq_monotonic_true_for_clean_record():
    assert extract._seq_monotonic(make_sid()) is True


def test_seq_monotonic_false_on_decreasing_sequence():
    rec = make_sid()
    rec.common_route[0].sequence_number = 5   # < the runway transition's 10? different list
    rec.runway_transitions[0].legs.append(
        copy.deepcopy(rec.runway_transitions[0].legs[0]))  # duplicate seq in one list
    assert extract._seq_monotonic(rec) is False


def test_unresolved_waypoints_counts_missing_fix():
    rec = make_sid()
    assert extract._unresolved_waypoints(rec) == 0
    rec.common_route[0].waypoint = None       # TF leg with no resolved fix
    assert extract._unresolved_waypoints(rec) == 1


def test_unresolved_ignores_heading_legs():
    rec = make_sid()
    rec.common_route[0].waypoint = None
    rec.common_route[0].path_terminator = "VA"  # heading-to-altitude legitimately has no fix
    assert extract._unresolved_waypoints(rec) == 0


# ── source grouping ──────────────────────────────────────────────────────────
def test_sid_lines_grouped_for_klax(ctx):
    assert "KLAX" in ctx.sid_lines
    assert len(ctx.sid_lines["KLAX"]) > 0


# ── per-airport step ─────────────────────────────────────────────────────────
def test_extract_klax_full_exhaustivity(ctx):
    res = extract_step(ctx)({"icao": "KLAX"})
    d = res.data
    assert d["sids_expected"] == d["sids_produced"]   # every counted SID produced
    assert d["missing"] == []                          # no exhaustivity gap
    dotss2 = [p for p in d["procedures"] if p["proc"] == "DOTSS2"][0]
    assert dotss2["legs"] > 0 and dotss2["flags"] == []  # RNAV SID resolves cleanly


def test_extract_pending_when_no_sids(ctx):
    res = extract_step(ctx)({"icao": "KZZZ"})
    assert res.status == PENDING
    assert res.data["sids_expected"] == 0


def test_exhaustivity_gap_flagged_for_phantom_sid(ctx):
    # Inject a SID id the CIFP can't produce → Loop 2 must flag the gap.
    local = copy.copy(ctx)
    local.manifest = dict(ctx.manifest)
    klax = copy.deepcopy(ctx.manifest["KLAX"])
    klax["manifest"]["sids"] = ["DOTSS2", "PHANTOM9"]
    local.manifest["KLAX"] = klax
    res = extract_step(local)({"icao": "KLAX"})
    assert "exhaustivity_gap" in res.data["flags"]
    assert "PHANTOM9" in res.data["missing"]
    assert res.status == FLAGGED


# ── end-to-end driver ────────────────────────────────────────────────────────
def test_run_extract_writes_report_and_is_deterministic(tmp_path):
    kw = dict(limit=12, force=True, write_gml=False, validate_xsd=False,
              log=lambda *_: None, state_dir=tmp_path, out_dir=tmp_path)
    a = run_extract("2607", **kw)
    b = run_extract("2607", **kw)
    # Reports are deterministic once timestamps are excluded (the rows carry none).
    assert a["airports"] == b["airports"]
    assert a["summary"]["sids_produced"] == b["summary"]["sids_produced"]
    assert (tmp_path / "US_2607.extract.json").exists()


def test_run_extract_missing_manifest_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "MANIFEST_DIR", tmp_path)  # empty dir → no manifest
    with pytest.raises(FileNotFoundError):
        run_extract("9999", log=lambda *_: None, state_dir=tmp_path, out_dir=tmp_path)
