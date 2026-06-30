"""
Loop 2 — Extract → AIXM 5.2 → self-check (US SIDs, offline-deterministic).

Consumes Loop 1's manifest (``data/manifests/US_<airac>.json``) and, for every
SID the census counted, produces a typed ``SIDRecord`` from the authoritative
coded CIFP, serialises it to AIXM 5.2 GML, and runs an engineered self-check
before storing it. Loop 2 is *graded against Loop 1*: every SID id the census
enumerated must come back out as a non-empty, schema-valid record
(``exhaustivity_gap`` otherwise).

Scope of this increment: SIDs only — ``arinc424.parse_sid`` is the implemented
coded parser; STAR/IAP coded parsers are the next sub-increment, so those classes
are reported ``deferred`` rather than silently dropped. Everything runs against
committed data — no LLM, no network.

The coded path's coordinates come straight from the CIFP via the waypoint index,
so accuracy-vs-ground-truth is exact by construction; the self-check therefore
targets the failure modes that remain: schema/units, XSD conformance, unresolved
waypoints (index gaps), leg-sequence sanity, and cross-loop exhaustivity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from miner.extractor.arinc424 import is_sid_record, parse_sid
from miner.extractor.arinc424 import _f as _cifp_field  # verified column map
from miner.loop.framework import FLAGGED, OK, PENDING, LoopRunner, StepResult
from miner.schemas import all_legs
from miner.schemas.aixm52_gml import record_to_gml
from miner.waypoint_db import DEFAULT_DB_PATH, WaypointIndex
from validator.aixm_xsd import validate_gml
from validator.validation_rules import validate_submission

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = ROOT / "data" / "manifests"
AIXM_DIR = ROOT / "data" / "aixm"
CIFP_PATH = ROOT / "data" / "nasr" / "FAACIFP18"


@dataclass
class Context:
    """Parse-once inputs shared across every airport in a run."""
    airac: str
    index: WaypointIndex
    sid_lines: dict[str, list[str]]                  # {icao: [SID record lines]}
    manifest: dict[str, dict]                        # {icao: census row}
    write_gml: bool = True
    validate_xsd: bool = True
    aixm_dir: Path = AIXM_DIR

    @classmethod
    def for_us(cls, airac: str, *, write_gml: bool = True, validate_xsd: bool = True,
               aixm_dir: Path | None = None) -> tuple["Context", list[dict]]:
        manifest = _load_manifest(airac)
        rows = manifest["airports"]
        sid_lines = _group_sid_lines(CIFP_PATH)
        ctx = cls(
            airac=airac,
            index=WaypointIndex(DEFAULT_DB_PATH),
            sid_lines=sid_lines,
            manifest={r["icao"]: r for r in rows},
            write_gml=write_gml,
            validate_xsd=validate_xsd,
            aixm_dir=aixm_dir or AIXM_DIR,
        )
        return ctx, rows


def _load_manifest(airac: str) -> dict:
    path = MANIFEST_DIR / f"US_{airac}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run Loop 1 first: "
            f"python scripts/census_loop.py --airac {airac}")
    return json.loads(path.read_text())


def _group_sid_lines(cifp_path: Path) -> dict[str, list[str]]:
    """One pass over the CIFP → {airport_icao: [SID record lines]}.

    parse_sid re-scans whatever source it's given, so we hand it just one
    airport's lines instead of the whole 53 MB file per procedure.
    """
    out: dict[str, list[str]] = {}
    if not cifp_path.exists():
        return out
    with open(cifp_path, encoding="latin-1") as fh:
        for ln in fh:
            ln = ln.rstrip("\n")
            if len(ln) >= 13 and is_sid_record(ln):
                out.setdefault(_cifp_field(ln, "airport"), []).append(ln)
    return out


# ── per-procedure self-check ─────────────────────────────────────────────────
def _seq_monotonic(record) -> bool:
    """Sequence numbers strictly increase within each leg list (segment)."""
    lists = ([t.legs for t in record.runway_transitions]
             + [record.common_route]
             + [t.legs for t in record.enroute_transitions])
    for legs in lists:
        seqs = [lg.sequence_number for lg in legs]
        if any(b <= a for a, b in zip(seqs, seqs[1:])):
            return False
    return True


def _unresolved_waypoints(record) -> int:
    """Legs that name a fix but whose coordinate did not resolve from the index."""
    n = 0
    for leg in all_legs(record):
        # A heading-terminator leg legitimately has no waypoint; only count legs
        # that should have one (a fix-terminator without a resolved point).
        if leg.waypoint is None and leg.path_terminator not in _HEADING_TERMINATORS:
            n += 1
    return n


_HEADING_TERMINATORS = {"VA", "VI", "VM", "VR", "VD", "CA", "CI", "CR", "CD", "FA", "FM", "FC", "FD"}


def _check_record(ctx: Context, record) -> list[str]:
    """Run the engineered self-check on one assembled SIDRecord."""
    flags: list[str] = []
    if validate_submission(record):           # hard gate: units, bbox, RF, US source
        flags.append("hard_validation")
    if ctx.validate_xsd:
        ok, _ = validate_gml(record_to_gml(record))
        if not ok:
            flags.append("xsd_invalid")
    if _unresolved_waypoints(record):
        flags.append("unresolved_waypoints")  # index gap — a fix had no coordinate
    if not _seq_monotonic(record):
        flags.append("seq_nonmonotonic")
    return flags


def extract_step(ctx: Context):
    """Per-airport step: extract every census SID and self-check it."""
    def step(item: dict) -> StepResult:
        icao = item["icao"]
        expected = ((ctx.manifest.get(icao, {}).get("manifest") or {}).get("sids")) or []
        if not expected:
            # Nothing the census counted to extract here — covered, not an error.
            return StepResult(key=icao, status=PENDING,
                              data={"icao": icao, "airac": ctx.airac,
                                    "sids_expected": 0, "sids_produced": 0,
                                    "missing": [], "procedures": [], "flags": [],
                                    "deferred": ["stars", "apps"]})

        lines = ctx.sid_lines.get(icao, [])
        procedures: list[dict] = []
        produced: list[str] = []
        flags: list[str] = []

        for sid in expected:
            try:
                rec = parse_sid(lines, icao, sid, ctx.airac, ctx.index)
            except Exception as e:
                flags.append(f"parse_error:{sid}")
                procedures.append({"proc": sid, "legs": 0, "flags": [f"parse_error:{type(e).__name__}"]})
                continue
            n_legs = len(list(all_legs(rec)))
            if n_legs == 0:
                flags.append(f"empty:{sid}")
                procedures.append({"proc": sid, "legs": 0, "flags": ["empty"]})
                continue
            pflags = _check_record(ctx, rec)
            produced.append(sid)
            if ctx.write_gml:
                _write_gml(ctx, icao, sid, rec)
            procedures.append({"proc": sid, "legs": n_legs, "flags": pflags})
            flags.extend(f"{f}:{sid}" for f in pflags)

        missing = [s for s in expected if s not in produced]
        if missing:
            flags.append("exhaustivity_gap")   # graded against Loop 1's manifest

        row = {
            "icao": icao, "airac": ctx.airac,
            "sids_expected": len(expected), "sids_produced": len(produced),
            "missing": missing, "procedures": procedures,
            "deferred": ["stars", "apps"],     # coded STAR/IAP parsers not yet built
            "flags": flags,
        }
        status = FLAGGED if flags else OK
        return StepResult(key=icao, status=status, data=row, flags=flags)

    return step


def _write_gml(ctx: Context, icao: str, sid: str, record) -> None:
    out = ctx.aixm_dir / f"US_{ctx.airac}" / icao
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{sid}.gml").write_bytes(record_to_gml(record))


def run_extract(airac: str, *, limit: int | None = None, force: bool = False,
                write_gml: bool = True, validate_xsd: bool = True, log=print,
                state_dir: Path | None = None, out_dir: Path | None = None,
                aixm_dir: Path | None = None) -> dict:
    """Run Loop 2 over the census manifest and write the per-cycle extract report.

    Returns the report dict; writes ``<out_dir>/US_<airac>.extract.json``.
    """
    ctx, rows = Context.for_us(airac, write_gml=write_gml, validate_xsd=validate_xsd,
                               aixm_dir=aixm_dir)
    rows = sorted(rows, key=lambda r: r["icao"])
    if limit:
        rows = rows[:limit]
    log(f"Loop 2 · US extract · AIRAC {airac} · {len(rows)} airports "
        f"· CIFP SID-airports={len(ctx.sid_lines)} · xsd={'on' if validate_xsd else 'off'}")

    runner = LoopRunner("extract_us", airac, log=log, state_dir=state_dir)
    runner.run(rows, extract_step(ctx), key_fn=lambda r: r["icao"], force=force)

    items = runner.read_records()
    records = sorted((it["data"] for it in items.values() if it.get("data")),
                     key=lambda r: r["icao"])
    by_status: dict[str, int] = {}
    flags: dict[str, int] = {}
    sids_expected = sids_produced = 0
    for it in items.values():
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1
        d = it.get("data") or {}
        sids_expected += d.get("sids_expected", 0)
        sids_produced += d.get("sids_produced", 0)
        for fl in it.get("flags", []):
            key = fl.split(":", 1)[0]            # collapse per-proc suffixes in the histogram
            flags[key] = flags.get(key, 0) + 1

    out_dir = out_dir or MANIFEST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "loop": "extract_us",
        "airac": airac,
        "country": "US",
        "scope": "SID (coded ARINC 424 / FAA CIFP)",
        "source": "FAA_CIFP",
        "graded_against": f"US_{airac}.json (Loop 1 manifest)",
        "summary": {"total": len(records), "by_status": by_status,
                    "sids_expected": sids_expected, "sids_produced": sids_produced,
                    "flags": flags},
        "airports": records,
    }
    out_path = out_dir / f"US_{airac}.extract.json"
    out_path.write_text(json.dumps(out, indent=2))
    log(f"  → {out_path} ({len(records)} rows · "
        f"{sids_produced}/{sids_expected} SIDs)")
    return out
