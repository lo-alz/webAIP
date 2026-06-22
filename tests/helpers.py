"""Shared test builders."""
from __future__ import annotations

import decimal

from miner.schemas import (
    AltConstraint,
    ProcedureLeg,
    RunwayTransition,
    SIDRecord,
    SpeedConstraint,
    WGS84Point,
)


def named_point(wid: str, lat: str = "34.06031111", lon: str = "-118.40599444",
                source_db: str = "NASR_CIFP", waypoint_type: str = "NAMED") -> WGS84Point:
    return WGS84Point(
        lat=decimal.Decimal(lat), lon=decimal.Decimal(lon),
        waypoint_id=wid, waypoint_type=waypoint_type, source_db=source_db,
    )


def make_sid() -> SIDRecord:
    """A minimal valid US SID with one runway transition + common route."""
    return SIDRecord(
        airport_icao="KLAX",
        procedure_name="LOOP6",
        runway_transitions=[
            RunwayTransition(
                runway_designator="25R",
                transition_id="LOOP6.25R",
                legs=[
                    ProcedureLeg(
                        sequence_number=10, path_terminator="IF",
                        waypoint=named_point("LADYJ", lat="33.80833333", lon="-118.36666667"),
                        waypoint_flag="FLY_BY",
                        alt_constraint=AltConstraint(type="AT_OR_ABOVE", lower_ft=400),
                    )
                ],
            )
        ],
        common_route=[
            ProcedureLeg(
                sequence_number=20, path_terminator="TF",
                waypoint=named_point("DOTSS"),
                waypoint_flag="FLY_BY",
                speed_constraint=SpeedConstraint(type="AT_OR_BELOW", value_kt=250),
            )
        ],
        enroute_transitions=[],
        pbn_nav_spec="D1",
        airac_cycle="2606",
        extraction_confidence=0.95,
        source_url="test",
        extracted_at="2026-06-22T00:00:00Z",
    )
