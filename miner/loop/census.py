"""
Loop 1 — Coverage Census (US, offline-deterministic).

For every US ICAO airport in the OurAirports spine, produce one census row:

    {
      icao, name, type, iso_country,
      runways,                       # OurAirports (denominator)
      parser_method, downloadable,   # how this country's AIP is acquired/parsed
      counts:   {sids, stars, apps}, # distinct procedures (FAA CIFP)
      manifest: {sids:[ids], ...},   # the actual procedure identifiers
      dtpp:     {sids, stars, apps}, # independent chart inventory (FAA d-TPP)
      flags:    [...],               # union of the three verification layers
      airac,
    }

The manifest (procedure *ids*, not just counts) is what Loop 2 will later be
graded against for exhaustivity. Everything here runs against committed data —
no network, no LLM.

Run via ``scripts/census_loop.py``; the heavy source parses (CIFP, d-TPP,
OurAirports) happen once and are shared across all airports through ``Context``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from miner.loop import checks, sources_us
from miner.loop.framework import FLAGGED, OK, PENDING, LoopRunner, StepResult

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = ROOT / "data" / "manifests"
SEED_PATH = ROOT / "data" / "aip_sources_seed.json"


@dataclass
class Context:
    """Shared, parse-once inputs for every airport in a run."""
    airac: str
    oa_runways: dict[str, int]
    cifp: dict[str, dict[str, list[str]]]
    dtpp: dict[str, dict[str, int]]
    parser_method: str
    downloadable: str

    @classmethod
    def for_us(cls, airac: str) -> tuple["Context", list[dict]]:
        rows, oa_runways = sources_us.ourairports_us()
        cifp = sources_us.cifp_manifest()
        dtpp = sources_us.dtpp_inventory()
        seed = _seed_entry("US")
        # Offline reachability: the authoritative source is present locally.
        downloadable = "cached" if sources_us.CIFP_PATH.exists() else "unreachable"
        ctx = cls(
            airac=airac, oa_runways=oa_runways, cifp=cifp, dtpp=dtpp,
            parser_method=seed.get("parser_method") or "arinc424",
            downloadable=downloadable,
        )
        return ctx, rows


def _seed_entry(iso: str) -> dict:
    if SEED_PATH.exists():
        seed = json.loads(SEED_PATH.read_text())
        return seed.get("countries", {}).get(iso, {})
    return {}


def census_step(ctx: Context):
    """Return a per-airport step bound to ``ctx`` (closure keeps the runner generic)."""
    def step(item: dict) -> StepResult:
        icao = item["icao"]
        manifest = ctx.cifp.get(icao, {"sids": [], "stars": [], "apps": []})
        counts = {cls: len(manifest.get(cls, [])) for cls in ("sids", "stars", "apps")}
        dtpp = ctx.dtpp.get(icao)

        row = {
            "icao": icao,
            "name": item.get("name", ""),
            "type": item.get("type", ""),
            "iso_country": item.get("iso_country", ""),
            "runways": ctx.oa_runways.get(icao, 0),
            "parser_method": ctx.parser_method,
            "downloadable": ctx.downloadable,
            "counts": counts,
            "manifest": manifest,
            "dtpp": dtpp,
            "airac": ctx.airac,
        }

        # ── verification layers ──────────────────────────────────────────────
        # US family: K (CONUS) + FAA-served territories P (Guam/Pacific),
        # T (Puerto Rico/Virgin Is.), N (American Samoa).
        flags = (checks.sanity_checks(row, allowed_prefixes=("K", "P", "T", "N"))
                 + checks.denominator_check(row, ctx.oa_runways))
        has_procedures = any(counts.values())
        if has_procedures:
            flags = flags + checks.dtpp_reconcile(counts, dtpp)
        row["flags"] = flags

        if not has_procedures:
            # No CIFP procedures for this ICAO field — covered, but nothing to
            # parse. That is a real census outcome, not an error.
            return StepResult(key=icao, status=PENDING, data=row, flags=flags)
        status = FLAGGED if flags else OK
        return StepResult(key=icao, status=status, data=row, flags=flags)

    return step


def run_census(airac: str, *, limit: int | None = None, force: bool = False,
               log=print, state_dir: Path | None = None,
               out_dir: Path | None = None) -> dict:
    """Run Loop 1 over the US universe and write the per-cycle manifest.

    The manifest is assembled from the full checkpoint (resume-complete), so a
    resumed run still emits every airport — not just the ones it reprocessed.
    Returns the manifest dict; writes ``<out_dir>/US_<airac>.json``.
    """
    ctx, rows = Context.for_us(airac)
    rows.sort(key=lambda r: r["icao"])
    if limit:
        rows = rows[:limit]
    log(f"Loop 1 · US census · AIRAC {airac} · {len(rows)} airports "
        f"· CIFP={len(ctx.cifp)} d-TPP={len(ctx.dtpp)} airports")

    runner = LoopRunner("census_us", airac, log=log, state_dir=state_dir)
    runner.run(rows, census_step(ctx), key_fn=lambda r: r["icao"], force=force)

    # Assemble from the checkpoint (every airport ever done this cycle), so a
    # resume never drops previously-completed rows. Deterministic: sorted, no ts.
    items = runner.read_records()
    records = sorted((it["data"] for it in items.values() if it.get("data")),
                     key=lambda r: r["icao"])
    by_status: dict[str, int] = {}
    flags: dict[str, int] = {}
    for it in items.values():
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1
        for fl in it.get("flags", []):
            flags[fl] = flags.get(fl, 0) + 1

    out_dir = out_dir or MANIFEST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "loop": "census_us",
        "airac": airac,
        "country": "US",
        "denominator_source": "OurAirports",
        "procedure_source": "FAA_CIFP",
        "reconcile_source": "FAA_dTPP",
        "summary": {"total": len(records), "by_status": by_status, "flags": flags},
        "airports": records,
    }
    out_path = out_dir / f"US_{airac}.json"
    out_path.write_text(json.dumps(out, indent=2))
    log(f"  → {out_path} ({len(records)} rows)")
    return out
