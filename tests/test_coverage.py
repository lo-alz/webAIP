"""Coverage gate: missing airport => zero score for the whole cycle (spec §2.5, §6)."""
from validator.scoring import score_miner_cycle
from tests.helpers import make_sid


def test_full_coverage_scores_above_zero():
    score = score_miner_cycle(
        miner_uid=1, cycle="2606",
        assigned=["KLAX"],
        submissions={"KLAX": [make_sid()]},
        get_ground_truth=lambda icao, cycle: make_sid(),
    )
    assert score > 0.0


def test_partial_coverage_is_zero():
    score = score_miner_cycle(
        miner_uid=1, cycle="2606",
        assigned=["KLAX", "KSFO", "KSEA"],
        submissions={"KLAX": [make_sid()], "KSFO": [make_sid()]},
        get_ground_truth=lambda icao, cycle: make_sid(),
    )
    assert score == 0.0


def test_omission_callback_invoked():
    seen = {}

    def log_omission(uid, cycle, missing):
        seen["missing"] = missing

    score_miner_cycle(
        miner_uid=7, cycle="2606",
        assigned=["KLAX", "KSFO"],
        submissions={"KLAX": [make_sid()]},
        get_ground_truth=lambda icao, cycle: None,
        log_omission=log_omission,
    )
    assert seen["missing"] == {"KSFO"}
