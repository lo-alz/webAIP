"""AIXM 5.2 procedure schemas."""
from .aixm52_procedure import (  # noqa: F401
    WGS84Point,
    AltConstraint,
    SpeedConstraint,
    WaypointFlag,
    ProcedureLeg,
    RunwayTransition,
    EnrouteTransition,
    SIDRecord,
    STARRecord,
    IAPRecord,
    all_legs,
    aixm_leg_feature,
    AIXM_NAMESPACE,
)
