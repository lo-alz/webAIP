"""
eAIP-HTML tier (miner/extractor/eaip_html.py) — VHHH OCEA1A — plus the CONSENSUS
fallback path in miner/assemble.py.
"""
from pathlib import Path

import pytest

from miner.assemble import build_sid_from_extraction
from miner.extractor import aixm_upconverter as aixm
from miner.extractor.base import ExtractedProcedure
from miner.extractor.eaip_html import extract as extract_html
from miner.schemas import all_legs
from miner.schemas.aixm52_gml import record_to_gml
from miner.waypoint_db import WaypointIndex
from validator.aixm_xsd import validate_gml
from validator.validation_rules import validate_submission

HTML = Path("data/fixtures/vhhh_ocea1a.eaip.html")
WP = Path("data/fixtures/vhhh_waypoints.aixm.xml")


def test_html_extract_structure_only():
    ext = extract_html(HTML)
    assert ext["airport_icao"] == "VHHH"
    assert ext["procedure_name"] == "OCEA1A"
    assert ext["pbn_nav_spec"] == "D1"
    assert "lat" not in str(ext).lower() and '"lon"' not in str(ext).lower()


def test_html_parses_constraints_and_flags():
    ext = extract_html(HTML)
    legs = ext["transitions"][0]["legs"]
    solly = next(l for l in legs if l.get("waypoint_id") == "SOLLY")
    assert solly["alt_constraint"] == {"type": "AT_OR_ABOVE", "lower_ft": 4000}
    ocean = next(l for l in legs if l.get("waypoint_id") == "OCEAN")
    assert ocean["speed_constraint"] == {"type": "AT_OR_BELOW", "value_kt": 250}
    tamot = next(l for t in ext["transitions"] + [{"legs": ext["common_route"]}]
                 for l in t["legs"] if l.get("waypoint_id") == "TAMOT")
    assert tamot["waypoint_flag"] == "FLY_OVER"


@pytest.fixture()
def index(tmp_path):
    db = WaypointIndex(tmp_path / "wp.db")
    db.load(aixm.parse_waypoints(WP, "2607", source_db="CAAS_HK"))
    return db


def test_assemble_resolves_all_fixes(index):
    sub = build_sid_from_extraction(extract_html(HTML), index, "2607",
                                    on_missing="consensus")
    assert validate_submission(sub) == []
    wpts = [leg.waypoint for leg in all_legs(sub) if leg.waypoint]
    assert wpts and all(w.source_db == "CAAS_HK" for w in wpts)
    ok, errors = validate_gml(record_to_gml(sub))
    assert ok, errors


def _proc_with_missing_fix() -> ExtractedProcedure:
    return ExtractedProcedure(
        record_type="SID", airport_icao="VHHH", procedure_name="TESTX",
        transitions=[{
            "kind": "runway", "designator": "07L", "transition_id": "TESTX.07L",
            "legs": [{
                "sequence_number": 10, "path_terminator": "TF",
                "waypoint_id": "NOWAY", "waypoint_flag": "FLY_BY",
                "fallback_latlon": ("22.40000000", "113.70000000"),
            }],
        }],
        common_route=[],
    )


def test_consensus_path_flags_and_downgrades(index):
    """A fix absent from every DB falls back to CONSENSUS (not abort) and caps
    confidence at the LOW ceiling."""
    sub = build_sid_from_extraction(_proc_with_missing_fix(), index, "2607",
                                    on_missing="consensus")
    leg = all_legs(sub)[0]
    assert leg.waypoint.source_db == "CONSENSUS"
    assert sub.extraction_confidence <= 0.4


def test_default_policy_still_raises(index):
    """Without the consensus policy a missing fix aborts the record (US strictness)."""
    with pytest.raises(KeyError):
        build_sid_from_extraction(_proc_with_missing_fix(), index, "2607")
