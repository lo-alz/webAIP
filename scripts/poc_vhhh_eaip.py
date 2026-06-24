#!/usr/bin/env python3
"""
Pilot — VHHH OCEA1A RNAV SID via the eAIP-HTML tier, end to end.

    eAIP HTML --(parse: structure)-->  identifiers + terminators + constraints
    regional pts --(geometry)------->  waypoint index (source_db=CAAS_HK)
                           join (assemble) -> AIXM 5.2 SIDRecord
                                           -> AIXM 5.2 GML (XSD-validated)

Hong Kong has no open coded *procedure* database, so there is no independent
ground truth to score against (consensus tier). Success here is: structure parsed
from the HTML, every fix resolved from the regional point index (no CONSENSUS
needed), the record passes the hard gate, and the AIXM 5.2 GML validates. Runs
fully offline on committed fixtures.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.assemble import build_sid_from_extraction  # noqa: E402
from miner.extractor import aixm_upconverter as aixm  # noqa: E402
from miner.extractor.eaip_html import extract as extract_html  # noqa: E402
from miner.schemas import all_legs  # noqa: E402
from miner.schemas.aixm52_gml import record_to_gml  # noqa: E402
from miner.waypoint_db import WaypointIndex  # noqa: E402
from validator.aixm_xsd import validate_gml  # noqa: E402
from validator.validation_rules import validate_submission  # noqa: E402

HTML = Path("data/fixtures/vhhh_ocea1a.eaip.html")
WP = Path("data/fixtures/vhhh_waypoints.aixm.xml")
AIRAC = "2607"


def main() -> int:
    print("=" * 70)
    print("AeroAIP pilot — VHHH OCEA1A RNAV SID (eAIP HTML -> AIXM 5.2)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as td:
        db = WaypointIndex(Path(td) / "wp.db")
        n = db.load(aixm.parse_waypoints(WP, AIRAC, source_db="CAAS_HK"))
        print(f"\n[0] Waypoint index: {n} CAAS_HK regional points loaded")

        # 1. Parse the eAIP HTML STRUCTURE only.
        extracted = extract_html(HTML)
        assert not _coords(extracted), "eAIP HTML extraction must carry no coordinates"
        nlegs = sum(len(t["legs"]) for t in extracted.get("transitions", [])) \
            + len(extracted.get("common_route", []))
        print(f"[1] Parsed structure: {extracted['procedure_name']} "
              f"({extracted['record_type']}), PBN={extracted.get('pbn_nav_spec')}, "
              f"{nlegs} legs — no coordinates ✓")

        # 2. Assemble, resolving fixes from the regional index (consensus policy:
        #    a fix absent from every DB would fall back to CONSENSUS, not abort).
        submission = build_sid_from_extraction(
            extracted, db, AIRAC, on_missing="consensus",
            source_url="CAAS HK eAIP (VHHH OCEA1A)")

        resolved = [leg for leg in all_legs(submission) if leg.waypoint]
        consensus = [leg for leg in resolved if leg.waypoint.source_db == "CONSENSUS"]
        print(f"[2] Assembled SIDRecord — {len(resolved)} fixes resolved, "
              f"{len(consensus)} via CONSENSUS")

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

        ok = bool(resolved) and not consensus
        print("\n" + "=" * 70)
        print("RESULT:", "✓ PASS — structure parsed, all fixes resolved from regional DB"
              if ok else "✗ FAIL (or fixes needed CONSENSUS)")
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
