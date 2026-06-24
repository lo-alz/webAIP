#!/usr/bin/env python3
"""
Pilot — EGLL DET2J RNAV SID via the AIXM up-converter tier, end to end.

    AIXM 5.1  --(up-convert: structure)-->  identifiers + terminators + constraints
    AIXM 5.1  --(DesignatedPoint geometry)-> waypoint index (source_db=EAD)
                              join (assemble) -> AIXM 5.2 SIDRecord
                                              -> AIXM 5.2 GML (XSD-validated)
                                              -> scored vs the AIXM's own coordinates

This proves the ``aixm_version`` override + a non-US waypoint-DB source feeding the
same assemble/GML/scoring pipeline the US/CIFP PoC uses. Runs fully offline on the
committed fixture. Exits non-zero if the criteria are not met.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.assemble import build_sid_from_extraction  # noqa: E402
from miner.extractor import aixm_upconverter as aixm  # noqa: E402
from miner.schemas.aixm52_gml import record_to_gml  # noqa: E402
from miner.waypoint_db import WaypointIndex  # noqa: E402
from validator.aixm_xsd import validate_gml  # noqa: E402
from validator.scoring import constraint_match_rate, coordinate_match_rate  # noqa: E402
from validator.validation_rules import validate_submission  # noqa: E402

FIX = Path("data/fixtures/egll_det2j.aixm51.xml")
AIRAC = "2607"


def main() -> int:
    print("=" * 70)
    print("AeroAIP pilot — EGLL DET2J RNAV SID (AIXM 5.1 -> 5.2 up-convert)")
    print("=" * 70)

    # 0. Waypoint index from the AIXM DesignatedPoint geometry (source_db=EAD).
    with tempfile.TemporaryDirectory() as td:
        db = WaypointIndex(Path(td) / "wp.db")
        n = db.load(aixm.parse_waypoints(FIX, AIRAC, source_db="EAD"))
        print(f"\n[0] Waypoint index: {n} EAD points loaded from AIXM geometry")

        # 1. Up-convert STRUCTURE only (no coordinates).
        extracted = aixm.extract(FIX)
        assert not _coords(extracted), "AIXM structure extraction must carry no coordinates"
        print(f"[1] Extracted structure: {extracted['procedure_name']} "
              f"({extracted['record_type']}), PBN={extracted.get('pbn_nav_spec')}, "
              f"{len(extracted.get('transitions', []))} transitions — no coordinates ✓")

        # 2. Assemble, looking up every coordinate from the index.
        submission = build_sid_from_extraction(
            extracted, db, AIRAC, source_url="EAD AIXM 5.1 (EGLL DET2J)")
        print(f"[2] Assembled SIDRecord — coords from EAD index")

        # 3. Hard validation gate.
        errors = validate_submission(submission)
        if errors:
            print("\n[3] ✗ Validation FAILED:")
            for e in errors:
                print("   -", e)
            return 1
        print("[3] ✓ Passed hard validation gate")

        # 4. AIXM 5.2 GML + XSD conformance.
        ok, xsd_errors = validate_gml(record_to_gml(submission))
        if not ok:
            print("\n[4] ✗ AIXM 5.2 GML did NOT validate:", xsd_errors)
            return 1
        print("[4] ✓ AIXM 5.2 GML valid against profile XSD")

        # 5. Score vs the AIXM's own native coordinates.
        gt = aixm.to_ground_truth(FIX, AIRAC)
        coord = coordinate_match_rate(submission, gt)
        constr = constraint_match_rate(submission, gt)
        print("\n[5] Scoring vs AIXM native geometry:")
        print(f"    coordinate match : {coord*100:6.2f}%   (target 100%)")
        print(f"    constraint match : {constr*100:6.2f}%   (target > 90%)")

        ok = coord >= 1.0 and constr > 0.90
        print("\n" + "=" * 70)
        print("RESULT:", "✓ PASS — 100% coordinates, >90% constraints" if ok else "✗ FAIL")
        print("=" * 70)
        return 0 if ok else 1


def _coords(obj) -> bool:
    if isinstance(obj, dict):
        if any(k.lower() in {"lat", "lon", "latitude", "longitude"} for k in obj):
            return True
        return any(_coords(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_coords(v) for v in obj)
    return False


if __name__ == "__main__":
    raise SystemExit(main())
