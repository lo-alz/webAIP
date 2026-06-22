"""Scoring logic (spec §6): exact match discriminates, coverage gate works."""
import copy

from validator.scoring import (
    constraint_match_rate,
    coordinate_match_rate,
    score_miner_cycle,
    score_vs_ground_truth,
)
from tests.helpers import make_sid, named_point


def test_identical_records_score_perfectly():
    sid = make_sid()
    gt = copy.deepcopy(sid)
    assert coordinate_match_rate(sid, gt) == 1.0
    assert constraint_match_rate(sid, gt) == 1.0
    assert score_vs_ground_truth(sid, gt) == 1.0


def test_coordinate_mismatch_drops_to_zero():
    sid = make_sid()
    gt = copy.deepcopy(sid)
    # Perturb a coordinate by 1 unit in the last decimal place.
    sid.common_route[0].waypoint = named_point("DOTSS", lat="34.06031112")
    assert coordinate_match_rate(sid, gt) < 1.0


def test_wrong_identifier_fails_match():
    sid = make_sid()
    gt = copy.deepcopy(sid)
    sid.common_route[0].waypoint = named_point("WRONG")
    assert coordinate_match_rate(sid, gt) < 1.0


def test_path_terminator_mismatch_lowers_constraints():
    sid = make_sid()
    gt = copy.deepcopy(sid)
    sid.common_route[0].path_terminator = "DF"  # gt is TF
    assert constraint_match_rate(sid, gt) < 1.0


def test_speed_mismatch_lowers_constraints():
    sid = make_sid()
    gt = copy.deepcopy(sid)
    sid.common_route[0].speed_constraint.value_kt = 230  # gt is 250
    assert constraint_match_rate(sid, gt) < 1.0


def test_coverage_gate_zero_when_missing_airport():
    score = score_miner_cycle(
        miner_uid=1, cycle="2606",
        assigned=["KLAX", "KSFO"],
        submissions={"KLAX": [make_sid()]},   # KSFO missing
        get_ground_truth=lambda icao, cycle: None,
    )
    assert score == 0.0


def test_validation_failure_scores_zero_for_that_record():
    bad = make_sid()
    bad.common_route[0].waypoint = named_point("DOTSS", source_db="CONSENSUS")
    score = score_miner_cycle(
        miner_uid=1, cycle="2606",
        assigned=["KLAX"],
        submissions={"KLAX": [bad]},
        get_ground_truth=lambda icao, cycle: make_sid(),
    )
    assert score == 0.0
