#!/usr/bin/env python3
"""
Phase 0 PoC — KLAX DOTSS2 RNAV SID, end to end (authentic FAA CIFP data).

    chart  --(LLM: structure only)-->  identifiers + terminators + constraints
    CIFP   --(DB lookup)----------->    exact WGS-84 coordinates
                              join  ->  AIXM 5.2 SIDRecord
                                    ->  AIXM 5.2 GML (XSD-validated)
                                    ->  scored vs NASR CIFP ground truth

Success criterion (spec §9): 100% coordinate match, > 90% constraint match.
Exits non-zero if the criteria are not met.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.assemble import build_sid_from_extraction  # noqa: E402
from miner.extractor.faa_dtpp import extract_klax_dotss2  # noqa: E402
from miner.schemas import all_legs  # noqa: E402
from miner.schemas.aixm52_gml import record_to_gml  # noqa: E402
from miner.waypoint_db import DEFAULT_DB_PATH, WaypointIndex  # noqa: E402
from validator.aixm_xsd import validate_gml  # noqa: E402
from validator.scoring import (  # noqa: E402
    constraint_match_rate,
    coordinate_match_rate,
    score_vs_ground_truth,
)
from validator.validation_rules import validate_submission  # noqa: E402
from validator.ground_truth.nasr_cifp import parse_sid  # noqa: E402

AIRAC = os.environ.get("AIRAC_CYCLE", "2607")
GT_PD = Path("data/fixtures/klax_dotss2_pd.dat")
PROC = "DOTSS2"
REGION = "K2"


def _print_legs(label, record):
    from validator.scoring import segmented_legs

    print(f"\n{label}:")
    current = None
    for seg, leg in segmented_legs(record):
        if seg != current:
            current = seg
            print(f"  [{' '.join(str(s) for s in seg)}]")
        wp = leg.waypoint
        coord = f"{wp.lat:>13}, {wp.lon:>15}" if wp else "— (heading leg)"
        wid = wp.waypoint_id if wp else "—"
        print(f"      seq {leg.sequence_number:>3} {leg.path_terminator:<2} "
              f"{wid:<6} {coord}")


def main() -> int:
    db = WaypointIndex(DEFAULT_DB_PATH)
    if not Path(DEFAULT_DB_PATH).exists():
        print("Waypoint index missing — run scripts/build_waypoint_index.py first.",
              file=sys.stderr)
        return 2

    print("=" * 70)
    print("AeroAIP Phase 0 PoC — KLAX DOTSS2 RNAV SID (authentic FAA CIFP)")
    print("=" * 70)

    # 1. LLM extracts STRUCTURE only (live chart if available, else fixture).
    extracted = extract_klax_dotss2(cycle=AIRAC)
    print(f"\n[1] Extracted structure: {extracted['procedure_name']} "
          f"({extracted['record_type']}), PBN={extracted.get('pbn_nav_spec')}, "
          f"{len(extracted.get('common_route', []))} common legs + "
          f"{len(extracted.get('transitions', []))} transitions")
    assert not _contains_coords(extracted), "LLM output must not contain coordinates"
    print("    ✓ LLM output carries NO coordinates (structure only)")

    # 2. Assemble record, looking up every coordinate from the CIFP index.
    submission = build_sid_from_extraction(
        extracted, db, AIRAC, region=REGION,
        source_url=f"https://aeronav.faa.gov/d-tpp/{AIRAC}/ (LOOP6)",
    )
    _print_legs("[2] Assembled SIDRecord (coords from NASR CIFP)", submission)

    # 3. Hard validation gate (spec §5).
    errors = validate_submission(submission)
    if errors:
        print("\n[3] ✗ Validation FAILED:")
        for e in errors:
            print("   -", e)
        return 1
    print("\n[3] ✓ Passed hard validation gate (spec §5)")

    # 4. AIXM 5.2 GML export + XSD conformance.
    gml = record_to_gml(submission)
    ok, xsd_errors = validate_gml(gml)
    if not ok:
        print("\n[4] ✗ AIXM 5.2 GML did NOT validate:")
        for e in xsd_errors:
            print("   -", e)
        return 1
    out = Path("data/fixtures/klax_dotss2.aixm52.gml")
    out.write_bytes(gml)
    print(f"[4] ✓ AIXM 5.2 GML valid against profile XSD → {out}")

    # 5. Ground truth from NASR CIFP PD records, scored.
    gt = parse_sid(GT_PD, "KLAX", PROC, AIRAC, db)
    coord = coordinate_match_rate(submission, gt)
    constr = constraint_match_rate(submission, gt)
    overall = score_vs_ground_truth(submission, gt)

    print("\n[5] Scoring vs NASR CIFP ground truth:")
    print(f"    coordinate match : {coord*100:6.2f}%   (target 100%)")
    print(f"    constraint match : {constr*100:6.2f}%   (target > 90%)")
    print(f"    overall score    : {overall*100:6.2f}%")

    ok_coord = coord >= 1.0
    ok_constr = constr > 0.90
    print("\n" + "=" * 70)
    if ok_coord and ok_constr:
        print("RESULT: ✓ PASS — 100% coordinates, >90% constraints")
        print("=" * 70)
        return 0
    print("RESULT: ✗ FAIL")
    print(f"  coordinate>=100%: {ok_coord}   constraint>90%: {ok_constr}")
    print("=" * 70)
    return 1


def _contains_coords(obj) -> bool:
    if isinstance(obj, dict):
        if any(k.lower() in {"lat", "lon", "latitude", "longitude"} for k in obj):
            return True
        return any(_contains_coords(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_coords(v) for v in obj)
    return False


if __name__ == "__main__":
    raise SystemExit(main())
