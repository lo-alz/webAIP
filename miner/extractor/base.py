"""
Extractor contract.

The extractor returns procedure **structure only** — leg order, path terminators,
waypoint *identifiers*, altitude/speed constraints, PBN spec, transitions. It
MUST NOT return coordinates: those are looked up from the authoritative waypoint
database (`miner.waypoint_db`). This separation is the core integrity property of
the whole project, so it is encoded in the type contract here.
"""
from __future__ import annotations

from typing import Protocol, TypedDict


class ExtractedLeg(TypedDict, total=False):
    sequence_number: int
    path_terminator: str            # ARINC 424 2-letter code
    waypoint_id: str | None         # identifier ONLY — never coordinates
    waypoint_flag: str | None       # FLY_BY / FLY_OVER
    alt_constraint: dict | None     # {type, lower_ft, upper_ft?}
    speed_constraint: dict | None   # {type, value_kt}
    course_magnetic: float | None
    distance_nm: float | None
    turn_direction: str | None      # L / R
    radius_nm: float | None         # RF legs
    center_fix_id: str | None       # RF legs — identifier only


class ExtractedTransition(TypedDict, total=False):
    kind: str                       # 'runway' | 'enroute'
    designator: str                 # runway designator or transition fix
    transition_id: str
    legs: list[ExtractedLeg]


class ExtractedProcedure(TypedDict, total=False):
    record_type: str                # SID / STAR / IAP
    airport_icao: str
    procedure_name: str
    pbn_nav_spec: str | None
    transitions: list[ExtractedTransition]
    common_route: list[ExtractedLeg]


class Extractor(Protocol):
    def extract(self, source_ref: str) -> ExtractedProcedure:
        """Extract procedure structure from a chart/eAIP reference."""
        ...
