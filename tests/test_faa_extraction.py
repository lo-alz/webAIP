"""
KLAX DOTSS2 end-to-end (authentic FAA CIFP data): LLM structure + CIFP
coordinate lookup -> AIXM 5.2, scored vs NASR CIFP ground truth.
Success criterion (spec §9): 100% coordinate match, > 90% constraint match.
"""
from pathlib import Path

import pytest

from miner.assemble import build_sid_from_extraction
from miner.extractor.llm_router import extract_structure
from miner.schemas import all_legs
from miner.schemas.aixm52_gml import record_to_gml
from miner.waypoint_db import WaypointIndex
from validator.aixm_xsd import validate_gml
from validator.ground_truth.nasr_cifp import parse_sid, parse_waypoints
from validator.scoring import constraint_match_rate, coordinate_match_rate
from validator.validation_rules import validate_submission

FIX = Path("data/fixtures")
WP = FIX / "klax_cifp_waypoints.dat"
PD = FIX / "klax_dotss2_pd.dat"
LLM = FIX / "klax_dotss2_llm_extraction.json"


@pytest.fixture()
def index(tmp_path):
    db = WaypointIndex(tmp_path / "wp.db")
    db.load(parse_waypoints(WP, "2607"))
    return db


@pytest.fixture()
def submission(index):
    extracted = extract_structure(fixture_path=LLM)
    return build_sid_from_extraction(extracted, index, "2607", region="K2")


def test_llm_fixture_has_no_coordinates():
    extracted = extract_structure(fixture_path=LLM)
    text = str(extracted).lower()
    assert "lat" not in text and '"lon"' not in text


def test_submission_passes_validation(submission):
    assert validate_submission(submission) == []


def test_coordinate_match_is_100_percent(submission, index):
    gt = parse_sid(PD, "KLAX", "DOTSS2", "2607", index)
    assert coordinate_match_rate(submission, gt) == 1.0


def test_constraint_match_exceeds_90_percent(submission, index):
    gt = parse_sid(PD, "KLAX", "DOTSS2", "2607", index)
    assert constraint_match_rate(submission, gt) > 0.90


def test_aixm_gml_valid(submission):
    ok, errors = validate_gml(record_to_gml(submission))
    assert ok, errors


def test_has_runway_and_enroute_transitions(submission):
    assert {t.runway_designator for t in submission.runway_transitions} == {
        "RW24L", "RW24R", "RW25L", "RW25R"
    }
    assert {t.transition_fix for t in submission.enroute_transitions} == {"CLEEE", "CNERY"}


def test_va_heading_leg_has_no_waypoint(submission):
    va = [leg for leg in all_legs(submission) if leg.path_terminator == "VA"]
    assert va, "expected VA heading legs in DOTSS2"
    assert all(leg.waypoint is None for leg in va)


def test_altitude_constraints_present(submission):
    legs = all_legs(submission)
    assert any(l.alt_constraint and l.alt_constraint.type == "AT_OR_BELOW" for l in legs)
    assert any(l.alt_constraint and l.alt_constraint.type == "AT_OR_ABOVE" for l in legs)
