"""
AIXM 5.2 procedure schemas (master spec CLAUDE.md §4.8).

The Pydantic field names are kept exactly as the spec defines them so the
validation (§5) and scoring (§6) logic transcribes verbatim. AIXM 5.2 itself is
GML-based and governed by the EUROCONTROL/FAA Change Control Board; to make the
*output* genuinely AIXM-5.2-shaped, this module also pins the correct AIXM
feature/attribute names that ``aixm52_gml.py`` serialises to:

    SIDRecord   -> aixm:StandardInstrumentDeparture
    STARRecord  -> aixm:StandardTerminalArrival
    IAPRecord   -> aixm:InstrumentApproachProcedure
    ProcedureLeg-> aixm:SegmentLeg (DepartureLeg / ArrivalLeg / ApproachLeg)
                   with the ARINC 424 path terminator in @legTypeARINC
    WGS84Point  -> aixm:DesignatedPoint
"""
from __future__ import annotations

import decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

# AIXM 5.2 target namespace (GML 3.2 based).
AIXM_NAMESPACE = "http://www.aixm.aero/schema/5.2"


class WGS84Point(BaseModel):
    """
    WGS-84 coordinates stored to 8 decimal places (lossless from ARINC 424 DMS).
    Source: always the NASR CIFP / EAD waypoint database — NEVER chart extraction.

    Serialises to aixm:DesignatedPoint.
    """

    lat: decimal.Decimal   # e.g. Decimal('34.06030556')
    lon: decimal.Decimal   # e.g. Decimal('-118.53327222')
    waypoint_id: str       # 5-letter ICAO identifier (e.g. 'SADDE') or lat/lon code
    waypoint_type: Literal["NAMED", "UNNAMED_LATLON", "NAVAID", "AIRPORT", "RUNWAY"]
    source_db: Literal["NASR_CIFP", "EAD", "NAVCANADA", "CAAS_HK", "CONSENSUS"]

    @field_validator("lat")
    @classmethod
    def lat_range(cls, v):
        if not (-90 <= v <= 90):
            raise ValueError(f"Latitude {v} out of range [-90, 90]")
        return v

    @field_validator("lon")
    @classmethod
    def lon_range(cls, v):
        if not (-180 <= v <= 180):
            raise ValueError(f"Longitude {v} out of range [-180, 180]")
        return v


class AltConstraint(BaseModel):
    type: Literal["AT", "AT_OR_ABOVE", "AT_OR_BELOW", "BETWEEN"]
    lower_ft: int
    upper_ft: Optional[int] = None  # only for BETWEEN
    uom: Literal["FT"] = "FT"


class SpeedConstraint(BaseModel):
    type: Literal["AT", "AT_OR_ABOVE", "AT_OR_BELOW"]
    value_kt: int
    uom: Literal["KT"] = "KT"


class WaypointFlag(str, Enum):
    FLY_BY = "FLY_BY"      # Aircraft may begin turn before reaching waypoint
    FLY_OVER = "FLY_OVER"  # Aircraft must cross waypoint before turning


# The 23 ARINC 424-18 path terminators (spec §4.3). Pinned here so the GML
# exporter can write them straight into aixm:SegmentLeg/@legTypeARINC.
PATH_TERMINATORS = (
    "IF", "TF", "CF", "DF", "FA", "FC", "FD", "FM",
    "CA", "CD", "CI", "CR", "RF", "AF",
    "VA", "VD", "VI", "VM", "VR",
    "HA", "HF", "HM", "PI",
)


class ProcedureLeg(BaseModel):
    """Serialises to aixm:SegmentLeg with @legTypeARINC = path_terminator."""

    sequence_number: int
    path_terminator: Literal[
        "IF", "TF", "CF", "DF", "FA", "FC", "FD", "FM",
        "CA", "CD", "CI", "CR", "RF", "AF",
        "VA", "VD", "VI", "VM", "VR",
        "HA", "HF", "HM", "PI",
    ]
    waypoint: Optional[WGS84Point] = None  # None for heading-terminator legs (VA, CA, etc.)
    waypoint_flag: Optional[Literal["FLY_BY", "FLY_OVER"]] = None
    alt_constraint: Optional[AltConstraint] = None
    speed_constraint: Optional[SpeedConstraint] = None
    course_magnetic: Optional[float] = None   # degrees magnetic
    course_true: Optional[float] = None        # degrees true
    distance_nm: Optional[float] = None
    turn_direction: Optional[Literal["L", "R"]] = None
    center_fix: Optional[WGS84Point] = None   # RF legs only
    radius_nm: Optional[float] = None          # RF legs only

    @property
    def legTypeARINC(self) -> str:  # noqa: N802 — AIXM attribute name, kept verbatim
        """AIXM 5.2 aixm:SegmentLeg/@legTypeARINC value."""
        return self.path_terminator


class RunwayTransition(BaseModel):
    runway_designator: str     # e.g. '24L', '06R'
    transition_id: str         # e.g. 'KLAX6.KARIN'
    legs: list[ProcedureLeg]


class EnrouteTransition(BaseModel):
    transition_fix: str        # 5-letter identifier
    transition_id: str
    legs: list[ProcedureLeg]


class SIDRecord(BaseModel):
    """Serialises to aixm:StandardInstrumentDeparture."""

    AIXM_FEATURE: str = "StandardInstrumentDeparture"
    record_type: Literal["SID"] = "SID"
    airport_icao: str              # e.g. 'KLAX'
    procedure_name: str            # e.g. 'LOOP6'
    runway_transitions: list[RunwayTransition]
    common_route: list[ProcedureLeg]
    enroute_transitions: list[EnrouteTransition]
    pbn_nav_spec: Optional[str]    # e.g. 'D1' = RNAV 1 GNSS
    airac_cycle: str               # e.g. '2606'
    extraction_confidence: float   # 0.0–1.0
    source_url: str
    coverage_source: Literal["miner", "sentinel_fallback"] = "miner"
    extracted_at: str              # ISO 8601


class STARRecord(BaseModel):
    """Serialises to aixm:StandardTerminalArrival."""

    AIXM_FEATURE: str = "StandardTerminalArrival"
    record_type: Literal["STAR"] = "STAR"
    airport_icao: str
    procedure_name: str
    enroute_transitions: list[EnrouteTransition]
    common_route: list[ProcedureLeg]
    runway_transitions: list[RunwayTransition]
    pbn_nav_spec: Optional[str]
    airac_cycle: str
    extraction_confidence: float
    source_url: str
    coverage_source: Literal["miner", "sentinel_fallback"] = "miner"
    extracted_at: str


class IAPRecord(BaseModel):
    """Serialises to aixm:InstrumentApproachProcedure."""

    AIXM_FEATURE: str = "InstrumentApproachProcedure"
    record_type: Literal["IAP"] = "IAP"
    airport_icao: str
    procedure_name: str            # e.g. 'ILS Z RWY 24L'
    approach_type: Literal[
        "ILS", "LOC", "LOC_BC", "LDA", "SDF",
        "RNAV_GNSS", "RNAV_RNP", "RNP_AR",
        "VOR", "VOR_DME", "TACAN", "NDB", "NDB_DME",
        "VISUAL",
    ]
    runway_designator: str
    initial_approach_segments: list[ProcedureLeg]
    intermediate_segment: list[ProcedureLeg]
    final_segment: list[ProcedureLeg]
    missed_approach_segment: list[ProcedureLeg]
    decision_altitude_ft: Optional[int] = None        # DA for precision
    minimum_descent_altitude_ft: Optional[int] = None  # MDA for non-precision
    visibility_rvr_m: Optional[int] = None
    pbn_nav_spec: Optional[str]
    airac_cycle: str
    extraction_confidence: float
    source_url: str
    coverage_source: Literal["miner", "sentinel_fallback"] = "miner"
    extracted_at: str


# ── helpers shared by validation (§5) and scoring (§6) ───────────────────────

# AIXM 5.2 concrete leg feature per record type (spec class hierarchy:
# aixm:SegmentLeg -> DepartureLeg / ArrivalLeg / ApproachLeg).
_LEG_FEATURE = {
    "SID": "DepartureLeg",
    "STAR": "ArrivalLeg",
    "IAP": "ApproachLeg",
}


def aixm_leg_feature(record) -> str:
    """Return the concrete AIXM 5.2 SegmentLeg feature name for ``record``."""
    return _LEG_FEATURE.get(getattr(record, "record_type", None), "SegmentLeg")


def all_legs(record) -> list[ProcedureLeg]:
    """
    Flatten every ProcedureLeg in a record (all transitions + common route),
    regardless of record type. Used by validation and scoring.
    """
    legs: list[ProcedureLeg] = []
    for attr in ("runway_transitions", "enroute_transitions"):
        for trans in getattr(record, attr, []) or []:
            legs.extend(trans.legs)
    legs.extend(getattr(record, "common_route", []) or [])
    for attr in (
        "initial_approach_segments",
        "intermediate_segment",
        "final_segment",
        "missed_approach_segment",
    ):
        legs.extend(getattr(record, attr, []) or [])
    return legs
