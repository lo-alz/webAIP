#!/usr/bin/env python3
"""
Build the global waypoint index (SQLite) from NASR CIFP (+ fixtures).

Parses NASR CIFP terminal/enroute waypoint records into
``data/waypoints/global_waypoint_index.db``. If a live CIFP file is present in
``$NASR_DATA_DIR`` it is parsed; the committed KLAX fixtures are always loaded so
the Phase 0 PoC is reproducible offline.

Usage:
    python scripts/build_waypoint_index.py [--airac 2606]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.extractor.arinc424 import parse_navaids  # noqa: E402
from miner.waypoint_db import DEFAULT_DB_PATH, WaypointIndex  # noqa: E402
from validator.ground_truth.nasr_cifp import parse_waypoints  # noqa: E402

FIXTURE_WAYPOINTS = Path("data/fixtures/klax_cifp_waypoints.dat")


def find_live_cifp(nasr_dir: Path) -> list[Path]:
    """Locate live CIFP record files (e.g. FAACIFP18) in the NASR data dir."""
    if not nasr_dir.exists():
        return []
    hits: list[Path] = []
    for pat in ("FAACIFP*", "*.dat", "*.txt"):
        hits.extend(p for p in nasr_dir.glob(pat) if p.is_file())
    return sorted(set(hits))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the global waypoint index")
    ap.add_argument("--airac", default=os.environ.get("AIRAC_CYCLE", "2606"))
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    args = ap.parse_args()

    index = WaypointIndex(args.db)
    index.create()
    total = 0

    nasr_dir = Path(os.environ.get("NASR_DATA_DIR", "data/nasr"))
    live = find_live_cifp(nasr_dir)
    if live:
        for f in live:
            rows = parse_waypoints(f, args.airac)
            n = index.load(rows)
            total += n
            print(f"  NASR CIFP {f.name}: {n} waypoints")
            navaids = parse_navaids(f, args.airac)
            m = index.load(navaids)
            total += m
            print(f"  NASR CIFP {f.name}: {m} navaids (VOR/DME/TACAN/NDB)")
    else:
        print(f"  (no live CIFP in {nasr_dir}/ — using committed fixtures)")

    if FIXTURE_WAYPOINTS.exists():
        rows = parse_waypoints(FIXTURE_WAYPOINTS, args.airac)
        n = index.load(rows)
        total += n
        print(f"  fixture {FIXTURE_WAYPOINTS.name}: {n} waypoints")

    print("\nWaypoints loaded per source:")
    for src, cnt in index.counts_by_source().items():
        print(f"  {src:12} {cnt}")
    print(f"\nTotal rows written: {total}")
    print(f"Index: {Path(args.db).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
