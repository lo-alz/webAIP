"""
Loop 1 (Coverage Census) — framework, source adapters, and verification checks.

Runs entirely against committed US data (OurAirports CSVs, FAACIFP18, d-TPP XML),
so no network or LLM is needed.
"""
from __future__ import annotations

import json

import pytest

from miner.loop import checks, sources_us
from miner.loop.census import Context, census_step, run_census
from miner.loop.framework import OK, PENDING, SKIPPED, LoopRunner, StepResult


# ── framework: idempotency, resume, determinism ──────────────────────────────
def _double(item):
    return StepResult(key=item["icao"], status=OK, data={"icao": item["icao"], "n": item["n"]})


def test_runner_is_idempotent(tmp_path):
    items = [{"icao": "KAAA", "n": 1}, {"icao": "KBBB", "n": 2}]
    r1 = LoopRunner("t", "2607", state_dir=tmp_path, log=lambda *_: None)
    s1 = r1.run(items, _double)
    assert s1.by_status.get(OK) == 2

    # Re-run with a fresh runner over the same checkpoint → everything skipped.
    r2 = LoopRunner("t", "2607", state_dir=tmp_path, log=lambda *_: None)
    s2 = r2.run(items, _double)
    assert s2.by_status.get(SKIPPED) == 2
    assert OK not in s2.by_status


def test_runner_force_recomputes(tmp_path):
    items = [{"icao": "KAAA", "n": 1}]
    LoopRunner("t", "2607", state_dir=tmp_path, log=lambda *_: None).run(items, _double)
    s = LoopRunner("t", "2607", state_dir=tmp_path, log=lambda *_: None).run(
        items, _double, force=True)
    assert s.by_status.get(OK) == 1  # force ignores the checkpoint


def test_runner_records_errors_without_aborting(tmp_path):
    def boom(item):
        if item["icao"] == "KBAD":
            raise ValueError("nope")
        return StepResult(key=item["icao"], status=OK)

    items = [{"icao": "KAAA"}, {"icao": "KBAD"}, {"icao": "KCCC"}]
    s = LoopRunner("t", "2607", state_dir=tmp_path, log=lambda *_: None).run(items, boom)
    assert s.by_status.get(OK) == 2
    assert s.by_status.get("error") == 1  # the bad item is isolated, loop continues


# ── source adapters ──────────────────────────────────────────────────────────
def test_cifp_manifest_has_klax_dotss2():
    man = sources_us.cifp_manifest()
    assert "KLAX" in man, "CIFP fixture must include KLAX"
    assert "DOTSS2" in man["KLAX"]["sids"]
    # ids are distinct and sorted
    sids = man["KLAX"]["sids"]
    assert sids == sorted(sids)
    assert len(sids) == len(set(sids))


def test_dtpp_inventory_classifies_klax():
    inv = sources_us.dtpp_inventory()
    assert "KLAX" in inv
    assert inv["KLAX"]["sids"] > 0 and inv["KLAX"]["apps"] > 0


# ── verification checks ──────────────────────────────────────────────────────
def test_sanity_flags_count_mismatch():
    row = {"icao": "KLAX", "runways": 4,
           "manifest": {"sids": ["A", "B"], "stars": [], "apps": []},
           "counts": {"sids": 1, "stars": 0, "apps": 0}}  # wrong count
    assert "sids_count_mismatch" in checks.sanity_checks(row, allowed_prefixes=("K",))


def test_sanity_flags_duplicate_ids():
    row = {"icao": "KLAX", "runways": 4,
           "manifest": {"sids": ["A", "A"], "stars": [], "apps": []},
           "counts": {"sids": 2, "stars": 0, "apps": 0}}
    assert "sids_duplicate_ids" in checks.sanity_checks(row, allowed_prefixes=("K",))


def test_denominator_flags_runway_mismatch():
    row = {"icao": "KLAX", "runways": 9, "counts": {"sids": 1, "stars": 0, "apps": 0}}
    flags = checks.denominator_check(row, {"KLAX": 4})
    assert "runway_count_mismatch_vs_ourairports" in flags


def test_denominator_flags_absent_airport():
    row = {"icao": "KZZZ", "runways": 0, "counts": {}}
    assert "absent_from_ourairports" in checks.denominator_check(row, {"KLAX": 4})


def test_dtpp_reconcile_coverage_gap_and_divergence():
    # CIFP has SIDs, d-TPP has none → coverage gap.
    assert "sids_coverage_gap" in checks.dtpp_reconcile(
        {"sids": 5, "stars": 0, "apps": 0}, {"sids": 0, "stars": 0, "apps": 0})
    # Both present but >3× apart → divergence.
    assert "apps_count_divergent" in checks.dtpp_reconcile(
        {"sids": 0, "stars": 0, "apps": 10}, {"sids": 0, "stars": 0, "apps": 1})
    # Comparable counts → clean.
    assert checks.dtpp_reconcile(
        {"sids": 24, "stars": 24, "apps": 32}, {"sids": 40, "stars": 34, "apps": 26}) == []


def test_dtpp_reconcile_missing_record():
    assert checks.dtpp_reconcile({"sids": 1, "stars": 0, "apps": 0}, None) == ["no_dtpp_record"]


# ── end-to-end census ────────────────────────────────────────────────────────
def test_census_step_klax_clean():
    # Drive the per-airport step directly (parses sources once; no full loop).
    ctx, _ = Context.for_us("2607")
    res = census_step(ctx)({"icao": "KLAX", "name": "Los Angeles Intl",
                            "type": "large_airport", "iso_country": "US"})
    assert res.status == OK
    assert res.data["counts"]["sids"] == len(res.data["manifest"]["sids"])
    assert "DOTSS2" in res.data["manifest"]["sids"]
    assert res.data["flags"] == []                 # KLAX reconciles cleanly
    assert res.data["downloadable"] == "cached"


def test_census_step_pending_when_no_procedures():
    ctx, _ = Context.for_us("2607")
    # A bogus ICAO with no CIFP procedures → PENDING (covered, nothing to parse).
    res = census_step(ctx)({"icao": "KZZZ", "name": "", "type": "small_airport",
                            "iso_country": "US"})
    assert res.status == PENDING


def test_run_census_is_deterministic(tmp_path):
    kw = dict(limit=20, force=True, log=lambda *_: None,
              state_dir=tmp_path, out_dir=tmp_path)
    a = run_census("2607", **kw)["airports"]
    b = run_census("2607", **kw)["airports"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_run_census_resume_keeps_all_rows(tmp_path):
    # First pass: 10 airports. Second pass (no force): 25 airports total.
    # The manifest must contain all 25 — resumed rows are not dropped.
    base = dict(log=lambda *_: None, state_dir=tmp_path, out_dir=tmp_path)
    run_census("2607", limit=10, force=True, **base)
    out = run_census("2607", limit=25, **base)
    assert out["summary"]["total"] == 25
    assert len(out["airports"]) == 25
