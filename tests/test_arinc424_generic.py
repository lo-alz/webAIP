"""
Generalized ARINC 424 parser (miner/extractor/arinc424.py) — regression that the
move out of validator/ground_truth/nasr_cifp.py is behaviour-preserving, plus the
new source_db labelling.
"""
from pathlib import Path

import pytest

from miner.extractor import arinc424
from miner.schemas import all_legs
from miner.waypoint_db import WaypointIndex
from validator.ground_truth import nasr_cifp

FIX = Path("data/fixtures")
WP = FIX / "klax_cifp_waypoints.dat"
PD = FIX / "klax_dotss2_pd.dat"


def test_nasr_reexports_shared_parser():
    """The US wrapper re-exports the exact shared functions (identity)."""
    assert nasr_cifp.parse_sid is arinc424.parse_sid
    assert nasr_cifp.parse_waypoints is arinc424.parse_waypoints


@pytest.fixture()
def index(tmp_path):
    db = WaypointIndex(tmp_path / "wp.db")
    db.load(arinc424.parse_waypoints(WP, "2607"))
    return db


def test_parse_waypoints_default_source(index):
    rows = arinc424.parse_waypoints(WP, "2607")
    assert rows and all(r.source == "NASR_CIFP" for r in rows)


def test_parse_waypoints_source_db_label():
    """Non-US coded data can be labelled (e.g. NAV CANADA)."""
    rows = arinc424.parse_waypoints(WP, "2607", source_db="NAVCANADA")
    assert rows and all(r.source == "NAVCANADA" for r in rows)


def test_parse_sid_assembles_klax(index):
    sid = arinc424.parse_sid(PD, "KLAX", "DOTSS2", "2607", index)
    assert sid.procedure_name == "DOTSS2"
    assert {t.runway_designator for t in sid.runway_transitions} == {
        "RW24L", "RW24R", "RW25L", "RW25R"
    }
    legs = all_legs(sid)
    assert legs and all(leg.waypoint is None or leg.waypoint.source_db == "NASR_CIFP"
                        for leg in legs)
