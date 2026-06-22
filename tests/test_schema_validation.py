"""Pydantic schema + hard-validation edge cases (spec §4.8, §5)."""
import decimal

import pytest
from pydantic import ValidationError

from miner.schemas import ProcedureLeg, WGS84Point
from validator.validation_rules import get_country_bbox, validate_submission
from tests.helpers import make_sid, named_point


def test_latitude_out_of_range_rejected():
    with pytest.raises(ValidationError):
        WGS84Point(lat=decimal.Decimal("95"), lon=decimal.Decimal("0"),
                   waypoint_id="X", waypoint_type="NAMED", source_db="NASR_CIFP")


def test_longitude_out_of_range_rejected():
    with pytest.raises(ValidationError):
        WGS84Point(lat=decimal.Decimal("0"), lon=decimal.Decimal("-181"),
                   waypoint_id="X", waypoint_type="NAMED", source_db="NASR_CIFP")


def test_unknown_path_terminator_rejected():
    with pytest.raises(ValidationError):
        ProcedureLeg(sequence_number=1, path_terminator="ZZ")


def test_legtypearinc_mirrors_path_terminator():
    leg = ProcedureLeg(sequence_number=1, path_terminator="RF")
    assert leg.legTypeARINC == "RF"


def test_validation_passes_clean_record():
    assert validate_submission(make_sid()) == []


def test_us_consensus_waypoint_rejected():
    sid = make_sid()
    sid.common_route[0].waypoint = named_point("DOTSS", source_db="CONSENSUS")
    errs = validate_submission(sid)
    assert any("NASR_CIFP" in e for e in errs)


def test_rf_leg_without_center_rejected():
    sid = make_sid()
    sid.common_route[0].path_terminator = "RF"
    sid.common_route[0].center_fix = None
    sid.common_route[0].radius_nm = None
    errs = validate_submission(sid)
    assert any("RF leg" in e for e in errs)


def test_waypoint_outside_bbox_rejected():
    sid = make_sid()
    # Move a waypoint into Europe — outside the US bbox.
    sid.common_route[0].waypoint = named_point(
        "DOTSS", lat="48.0", lon="2.0")
    errs = validate_submission(sid)
    assert any("bounding box" in e for e in errs)


def test_bbox_lookup_two_then_one_letter():
    assert get_country_bbox("EG").lat_max == 61.0   # UK (2-letter)
    assert get_country_bbox("KL").lat_min == 18.0   # USA (1-letter fallback)
