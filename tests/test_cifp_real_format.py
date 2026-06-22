"""
Verify the parser against the LIVE FAA CIFP when present (skipped in CI / offline).
Run `bash scripts/download_waypoint_dbs.sh` to populate data/nasr/FAACIFP18.
"""
import os
from pathlib import Path

import pytest

from validator.ground_truth.nasr_cifp import parse_sid, parse_waypoints
from miner.waypoint_db import WaypointIndex

CIFP = Path(os.environ.get("NASR_DATA_DIR", "data/nasr")) / "FAACIFP18"
pytestmark = pytest.mark.skipif(not CIFP.exists(),
                                reason="live FAACIFP18 not downloaded")


def test_parses_many_waypoints():
    rows = parse_waypoints(CIFP, "2607")
    # The real CIFP holds tens of thousands of waypoints.
    assert len(rows) > 10000
    # Coordinates must be plausible WGS-84 decimals.
    for r in rows[:200]:
        assert -90 <= float(r.lat) <= 90
        assert -180 <= float(r.lon) <= 180


def test_klax_dotss2_parses_from_live_file(tmp_path):
    db = WaypointIndex(tmp_path / "wp.db")
    db.load(parse_waypoints(CIFP, "2607"))
    sid = parse_sid(CIFP, "KLAX", "DOTSS2", "2607", db)
    assert sid.runway_transitions, "expected runway transitions"
    # Every named leg resolved to a real coordinate from the same file.
    from miner.schemas import all_legs
    named = [l for l in all_legs(sid) if l.waypoint]
    assert named
    assert all(l.waypoint.source_db == "NASR_CIFP" for l in named)
