"""
AIXM up-converter tier (miner/extractor/aixm_upconverter.py) — EGLL DET2J.
"""
from pathlib import Path

import pytest

from miner.assemble import build_sid_from_extraction
from miner.extractor import aixm_upconverter as aixm
from miner.schemas.aixm52_gml import record_to_gml
from miner.waypoint_db import WaypointIndex
from validator.aixm_xsd import validate_gml
from validator.scoring import constraint_match_rate, coordinate_match_rate
from validator.validation_rules import validate_submission

FIX = Path("data/fixtures/egll_det2j.aixm51.xml")


def test_extract_is_structure_only():
    ext = aixm.extract(FIX)
    assert ext["procedure_name"] == "DET2J"
    assert ext["pbn_nav_spec"] == "D1"
    assert "lat" not in str(ext).lower() and '"lon"' not in str(ext).lower()


def test_parse_waypoints_native_geometry():
    rows = aixm.parse_waypoints(FIX, "2607", source_db="EAD")
    assert {r.waypoint_id for r in rows} == {"OCK", "BIG", "DET"}
    assert all(r.source == "EAD" for r in rows)


@pytest.fixture()
def index(tmp_path):
    db = WaypointIndex(tmp_path / "wp.db")
    db.load(aixm.parse_waypoints(FIX, "2607", source_db="EAD"))
    return db


@pytest.fixture()
def submission(index):
    return build_sid_from_extraction(aixm.extract(FIX), index, "2607",
                                     source_url="EAD AIXM 5.1")


def test_submission_passes_validation(submission):
    assert validate_submission(submission) == []


def test_coordinate_match_100(submission):
    gt = aixm.to_ground_truth(FIX, "2607")
    assert coordinate_match_rate(submission, gt) == 1.0
    assert constraint_match_rate(submission, gt) > 0.90


def test_aixm_gml_valid(submission):
    ok, errors = validate_gml(record_to_gml(submission))
    assert ok, errors


def test_waypoints_sourced_from_ead(submission):
    from miner.schemas import all_legs
    wpts = [leg.waypoint for leg in all_legs(submission) if leg.waypoint]
    assert wpts and all(w.source_db == "EAD" for w in wpts)
