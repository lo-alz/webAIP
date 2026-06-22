"""
Assemble a typed AIXM 5.2 record from LLM-extracted *structure* + DB coordinates.

This is the integrity-critical join: the LLM supplies identifiers and structure
(``miner.extractor``), and every coordinate is filled in by looking the
identifier up in the authoritative waypoint index (``miner.waypoint_db``). The
LLM never contributes a coordinate.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

from miner.extractor.base import ExtractedLeg, ExtractedProcedure
from miner.schemas import (
    AltConstraint,
    EnrouteTransition,
    ProcedureLeg,
    RunwayTransition,
    SIDRecord,
    SpeedConstraint,
)


def _alt(d: Optional[dict]) -> Optional[AltConstraint]:
    if not d:
        return None
    return AltConstraint(
        type=d["type"], lower_ft=int(d["lower_ft"]),
        upper_ft=d.get("upper_ft"),
    )


def _speed(d: Optional[dict]) -> Optional[SpeedConstraint]:
    if not d:
        return None
    return SpeedConstraint(type=d["type"], value_kt=int(d["value_kt"]))


def _leg(e: ExtractedLeg, index, region: Optional[str]) -> ProcedureLeg:
    wp_id = e.get("waypoint_id")
    center_id = e.get("center_fix_id")
    waypoint = index.lookup(wp_id, region) if wp_id else None
    if wp_id and waypoint is None:
        raise KeyError(f"Waypoint {wp_id!r} not found in index (region={region})")
    center = index.lookup(center_id, region) if center_id else None
    if center_id and center is None:
        raise KeyError(f"Center fix {center_id!r} not found in index")
    return ProcedureLeg(
        sequence_number=int(e["sequence_number"]),
        path_terminator=e["path_terminator"],
        waypoint=waypoint,
        waypoint_flag=e.get("waypoint_flag"),
        alt_constraint=_alt(e.get("alt_constraint")),
        speed_constraint=_speed(e.get("speed_constraint")),
        course_magnetic=e.get("course_magnetic"),
        distance_nm=e.get("distance_nm"),
        turn_direction=e.get("turn_direction"),
        center_fix=center,
        radius_nm=e.get("radius_nm"),
    )


def build_sid_from_extraction(
    extracted: ExtractedProcedure,
    index,
    airac: str,
    *,
    region: Optional[str] = None,
    source_url: str = "",
    confidence: float = 0.95,
) -> SIDRecord:
    """Build a SIDRecord, looking up every coordinate from ``index``."""
    runway_transitions: list[RunwayTransition] = []
    enroute_transitions: list[EnrouteTransition] = []

    for t in extracted.get("transitions", []) or []:
        legs = [_leg(e, index, region) for e in t.get("legs", [])]
        if t.get("kind") == "runway":
            runway_transitions.append(
                RunwayTransition(
                    runway_designator=t["designator"],
                    transition_id=t.get("transition_id", ""),
                    legs=legs,
                )
            )
        else:
            enroute_transitions.append(
                EnrouteTransition(
                    transition_fix=t["designator"],
                    transition_id=t.get("transition_id", ""),
                    legs=legs,
                )
            )

    common = [_leg(e, index, region) for e in extracted.get("common_route", []) or []]

    return SIDRecord(
        airport_icao=extracted["airport_icao"],
        procedure_name=extracted["procedure_name"],
        runway_transitions=runway_transitions,
        common_route=common,
        enroute_transitions=enroute_transitions,
        pbn_nav_spec=extracted.get("pbn_nav_spec"),
        airac_cycle=airac,
        extraction_confidence=confidence,
        source_url=source_url,
        coverage_source="miner",
        extracted_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
