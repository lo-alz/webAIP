"""
Loop 2 (Extract → AIXM 5.2 → self-check) — US coded SID/STAR/IAP path, offline.

Runs against committed data (FAACIFP18, the waypoint index incl. navaids, Loop 1's
manifest). Skips cleanly when those large gitignored inputs are absent (fresh CI).
"""
from __future__ import annotations

import copy
import os

import pytest

from miner.loop import extract
from miner.loop.extract import Context, extract_step, run_extract
from miner.loop.framework import FLAGGED, PENDING
from miner.schemas import IAPRecord, all_legs
from miner.waypoint_db import DEFAULT_DB_PATH, WaypointIndex
from tests.helpers import make_sid

MANIFEST = extract.MANIFEST_DIR / "US_2607.json"
_READY = (MANIFEST.exists() and extract.CIFP_PATH.exists()
          and os.path.exists(DEFAULT_DB_PATH))
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


def test_unresolved_waypoints_counts_missing_fix():
    rec = make_sid()
    assert extract._unresolved_waypoints(rec) == 0
    rec.common_route[0].waypoint = None
    assert extract._unresolved_waypoints(rec) == 1


def test_unresolved_ignores_heading_legs():
    rec = make_sid()
    rec.common_route[0].waypoint = None
    rec.common_route[0].path_terminator = "VA"
    assert extract._unresolved_waypoints(rec) == 0


# ── navaid resolution (task 1) ───────────────────────────────────────────────
def test_navaids_resolve_from_index():
    idx = WaypointIndex()
    for wid, reg in [("GMN", "K2"), ("SLI", "K2")]:   # Gorman, Seal Beach VORs
        p = idx.lookup(wid, reg)
        assert p is not None and p.waypoint_type == "NAVAID"


def test_klax_conventional_sids_resolve_clean(ctx):
    # Before navaids these flagged unresolved_waypoints; now every KLAX SID is clean.
    res = extract_step(ctx)({"icao": "KLAX"})
    sid_flags = [f for p in res.data["procedures"] if p["class"] == "sids" for f in p["flags"]]
    assert sid_flags == []


# ── STAR / IAP parsing (task 2) ──────────────────────────────────────────────
def test_extract_klax_all_classes_exhaustive(ctx):
    res = extract_step(ctx)({"icao": "KLAX"})
    c = res.data["counts"]
    for cls in ("sids", "stars", "apps"):
        assert c[cls]["produced"] == c[cls]["expected"] > 0
        assert c[cls]["missing"] == []


def test_iap_segmentation_shape(ctx):
    # KLAX I06L (ILS 06L) must segment into a final + missed approach.
    from miner.extractor.arinc424 import parse_iap
    rec = parse_iap(ctx.lines["apps"].get("KLAX", []), "KLAX", "I06L", "2607", ctx.index)
    assert isinstance(rec, IAPRecord)
    assert rec.approach_type == "ILS" and rec.runway_designator == "06L"
    assert len(rec.final_segment) > 0 and len(rec.missed_approach_segment) > 0
    assert len(list(all_legs(rec))) > 0


# ── per-airport step ─────────────────────────────────────────────────────────
def test_extract_pending_when_nothing_counted(ctx):
    res = extract_step(ctx)({"icao": "KZZZ"})
    assert res.status == PENDING
    assert all(res.data["counts"][c]["expected"] == 0 for c in ("sids", "stars", "apps"))


def test_exhaustivity_gap_flagged_per_class(ctx):
    local = copy.copy(ctx)
    local.manifest = dict(ctx.manifest)
    klax = copy.deepcopy(ctx.manifest["KLAX"])
    klax["manifest"]["sids"] = ["DOTSS2", "PHANTOM9"]   # phantom the CIFP can't produce
    local.manifest["KLAX"] = klax
    res = extract_step(local)({"icao": "KLAX"})
    assert "exhaustivity_gap:sids" in res.data["flags"]
    assert "PHANTOM9" in res.data["counts"]["sids"]["missing"]
    assert res.status == FLAGGED


# ── end-to-end driver ────────────────────────────────────────────────────────
def test_run_extract_writes_report_and_is_deterministic(tmp_path):
    kw = dict(limit=12, force=True, write_gml=False, validate_xsd=False,
              log=lambda *_: None, state_dir=tmp_path, out_dir=tmp_path)
    a = run_extract("2607", **kw)
    b = run_extract("2607", **kw)
    assert a["airports"] == b["airports"]
    assert a["summary"]["procedures"] == b["summary"]["procedures"]
    assert (tmp_path / "US_2607.extract.json").exists()


def test_run_extract_missing_manifest_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "MANIFEST_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        run_extract("9999", log=lambda *_: None, state_dir=tmp_path, out_dir=tmp_path)
