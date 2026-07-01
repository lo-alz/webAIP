"""
Loop 2 — Extract → AIXM 5.2 → self-check (US SID/STAR/IAP, offline-deterministic).

Consumes Loop 1's manifest (``data/manifests/US_<airac>.json``) and, for every
procedure the census counted (SIDs, STARs, approaches), assembles a typed record
from the authoritative coded CIFP, serialises AIXM 5.2 GML, and runs an
engineered self-check before storing it. Loop 2 is *graded against Loop 1*: every
id the census enumerated must come back out as a non-empty, schema-valid record
(``exhaustivity_gap:<class>`` otherwise).

All three procedure classes use the coded ARINC 424 parsers
(``arinc424.parse_sid`` / ``parse_star`` / ``parse_iap``) with coordinates looked
up from the waypoint index — no LLM, no network. Because the coordinates come
straight from the CIFP, accuracy-vs-ground-truth is exact by construction; the
self-check targets the failure modes that remain: schema/units, XSD conformance,
unresolved waypoints (index gaps), leg-sequence sanity, and cross-loop
exhaustivity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from miner.extractor.arinc424 import _f as _cifp_field
from miner.extractor.arinc424 import (
    is_iap_record, is_sid_record, is_star_record,
    parse_iap, parse_sid, parse_star,
)
from miner.loop.framework import FLAGGED, OK, PENDING, LoopRunner, StepResult
from miner.schemas import IAPRecord, all_legs
from miner.schemas.aixm52_gml import record_to_gml
from miner.waypoint_db import DEFAULT_DB_PATH, WaypointIndex
from validator.aixm_xsd import validate_gml
from validator.validation_rules import validate_submission

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = ROOT / "data" / "manifests"
AIXM_DIR = ROOT / "data" / "aixm"
CIFP_PATH = ROOT / "data" / "nasr" / "FAACIFP18"

# manifest key → (CIFP record predicate, coded parser)
CLASSES = {
    "sids": (is_sid_record, parse_sid),
    "stars": (is_star_record, parse_star),
    "apps": (is_iap_record, parse_iap),
}


@dataclass
class Context:
    """Parse-once inputs shared across every airport in a run."""
    airac: str
    index: WaypointIndex
    lines: dict[str, dict[str, list[str]]]   # {class: {icao: [record lines]}}
    manifest: dict[str, dict]                # {icao: census row}
    write_gml: bool = True
    validate_xsd: bool = True
    aixm_dir: Path = AIXM_DIR

    @classmethod
    def for_us(cls, airac: str, *, write_gml: bool = True, validate_xsd: bool = True,
               aixm_dir: Path | None = None) -> tuple["Context", list[dict]]:
        manifest = _load_manifest(airac)
        rows = manifest["airports"]
        ctx = cls(
            airac=airac,
            index=WaypointIndex(DEFAULT_DB_PATH),
            lines=_group_lines(CIFP_PATH),
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


def _group_lines(cifp_path: Path) -> dict[str, dict[str, list[str]]]:
    """One pass over the CIFP → {class: {airport_icao: [record lines]}}.

    The coded parsers re-scan whatever source they're given, so we hand each one
    just its airport's slice instead of the whole 53 MB file per procedure.
    """
    groups: dict[str, dict[str, list[str]]] = {c: {} for c in CLASSES}
    if not cifp_path.exists():
        return groups
    with open(cifp_path, encoding="latin-1") as fh:
        for ln in fh:
            ln = ln.rstrip("\n")
            if len(ln) < 13:
                continue
            for cls, (pred, _) in CLASSES.items():
                if pred(ln):
                    groups[cls].setdefault(_cifp_field(ln, "airport"), []).append(ln)
                    break
    return groups


# ── per-record self-check ────────────────────────────────────────────────────
_HEADING_TERMINATORS = {"VA", "VI", "VM", "VR", "VD", "CA", "CI", "CR", "CD",
                        "FA", "FM", "FC", "FD"}


def _leg_lists(record) -> list[list]:
    """Leg lists that should each be a single, strictly increasing sequence.

    For IAPs the initial-approach segment concatenates several transitions (each
    with its own 010/020… numbering), so it is excluded from the monotonic check;
    the intermediate/final/missed lists are one coded route and are checked.
    """
    if isinstance(record, IAPRecord):
        return [record.intermediate_segment, record.final_segment,
                record.missed_approach_segment]
    return ([t.legs for t in record.runway_transitions]
            + [record.common_route]
            + [t.legs for t in record.enroute_transitions])


def _seq_monotonic(record) -> bool:
    for legs in _leg_lists(record):
        seqs = [lg.sequence_number for lg in legs]
        if any(b <= a for a, b in zip(seqs, seqs[1:])):
            return False
    return True


def _unresolved_waypoints(record) -> int:
    """Legs that name a fix but whose coordinate did not resolve from the index."""
    n = 0
    for leg in all_legs(record):
        if leg.waypoint is None and leg.path_terminator not in _HEADING_TERMINATORS:
            n += 1
    return n


def _check_record(ctx: Context, record) -> list[str]:
    flags: list[str] = []
    if validate_submission(record):
        flags.append("hard_validation")
    if ctx.validate_xsd:
        ok, _ = validate_gml(record_to_gml(record))
        if not ok:
            flags.append("xsd_invalid")
    if _unresolved_waypoints(record):
        flags.append("unresolved_waypoints")
    if not _seq_monotonic(record):
        flags.append("seq_nonmonotonic")
    return flags


def extract_step(ctx: Context):
    """Per-airport step: extract + self-check every census procedure, all classes."""
    def step(item: dict) -> StepResult:
        icao = item["icao"]
        man = (ctx.manifest.get(icao, {}).get("manifest") or {})
        counts: dict[str, dict] = {}
        procedures: list[dict] = []
        flags: list[str] = []
        any_expected = False

        for cls, (_, parser) in CLASSES.items():
            expected = man.get(cls) or []
            if expected:
                any_expected = True
            lines = ctx.lines[cls].get(icao, [])
            produced: list[str] = []
            for pid in expected:
                try:
                    rec = parser(lines, icao, pid, ctx.airac, ctx.index)
                except Exception as e:
                    flags.append(f"parse_error:{cls}:{pid}")
                    procedures.append({"class": cls, "proc": pid, "legs": 0,
                                       "flags": [f"parse_error:{type(e).__name__}"]})
                    continue
                n_legs = len(list(all_legs(rec)))
                if n_legs == 0:
                    flags.append(f"empty:{cls}:{pid}")
                    procedures.append({"class": cls, "proc": pid, "legs": 0, "flags": ["empty"]})
                    continue
                pflags = _check_record(ctx, rec)
                produced.append(pid)
                if ctx.write_gml:
                    _write_gml(ctx, icao, cls, pid, rec)
                procedures.append({"class": cls, "proc": pid, "legs": n_legs, "flags": pflags})
                flags.extend(f"{f}:{cls}:{pid}" for f in pflags)

            missing = [p for p in expected if p not in produced]
            if missing:
                flags.append(f"exhaustivity_gap:{cls}")
            counts[cls] = {"expected": len(expected), "produced": len(produced),
                           "missing": missing}

        row = {"icao": icao, "airac": ctx.airac, "counts": counts,
               "procedures": procedures, "flags": flags}
        if not any_expected:
            return StepResult(key=icao, status=PENDING, data=row)
        return StepResult(key=icao, status=FLAGGED if flags else OK, data=row, flags=flags)

    return step


def _write_gml(ctx: Context, icao: str, cls: str, pid: str, record) -> None:
    out = ctx.aixm_dir / f"US_{ctx.airac}" / icao / cls
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{pid}.gml").write_bytes(record_to_gml(record))


def run_extract(airac: str, *, limit: int | None = None, force: bool = False,
                write_gml: bool = True, validate_xsd: bool = True, log=print,
                state_dir: Path | None = None, out_dir: Path | None = None,
                aixm_dir: Path | None = None) -> dict:
    """Run Loop 2 over the census manifest and write the per-cycle extract report."""
    ctx, rows = Context.for_us(airac, write_gml=write_gml, validate_xsd=validate_xsd,
                               aixm_dir=aixm_dir)
    rows = sorted(rows, key=lambda r: r["icao"])
    if limit:
        rows = rows[:limit]
    have = {c: len(ctx.lines[c]) for c in CLASSES}
    log(f"Loop 2 · US extract · AIRAC {airac} · {len(rows)} airports "
        f"· CIFP airports {have} · xsd={'on' if validate_xsd else 'off'}")

    runner = LoopRunner("extract_us", airac, log=log, state_dir=state_dir)
    runner.run(rows, extract_step(ctx), key_fn=lambda r: r["icao"], force=force)

    items = runner.read_records()
    records = sorted((it["data"] for it in items.values() if it.get("data")),
                     key=lambda r: r["icao"])
    by_status: dict[str, int] = {}
    flags: dict[str, int] = {}
    totals = {c: {"expected": 0, "produced": 0} for c in CLASSES}
    for it in items.values():
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1
        for c, cc in (it.get("data") or {}).get("counts", {}).items():
            totals[c]["expected"] += cc["expected"]
            totals[c]["produced"] += cc["produced"]
        for fl in it.get("flags", []):
            key = fl.split(":", 1)[0]            # collapse per-proc suffixes
            flags[key] = flags.get(key, 0) + 1

    out_dir = out_dir or MANIFEST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "loop": "extract_us",
        "airac": airac,
        "country": "US",
        "scope": "SID + STAR + IAP (coded ARINC 424 / FAA CIFP)",
        "source": "FAA_CIFP",
        "graded_against": f"US_{airac}.json (Loop 1 manifest)",
        "summary": {"total": len(records), "by_status": by_status,
                    "procedures": totals, "flags": flags},
        "airports": records,
    }
    out_path = out_dir / f"US_{airac}.extract.json"
    out_path.write_text(json.dumps(out, indent=2))
    prod = " ".join(f"{c}={totals[c]['produced']}/{totals[c]['expected']}" for c in CLASSES)
    log(f"  → {out_path} ({len(records)} rows · {prod})")
    return out
