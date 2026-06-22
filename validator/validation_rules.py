"""
Hard-reject validation rules (master spec §5).

Any submission that fails these checks scores zero before accuracy scoring runs.
Transcribed from the spec with the helper stubs (`all_legs`, `get_country_bbox`)
given concrete implementations.
"""
from __future__ import annotations

import decimal
from dataclasses import dataclass
from typing import Union

from miner.schemas import IAPRecord, SIDRecord, STARRecord, all_legs

Record = Union[SIDRecord, STARRecord, IAPRecord]


@dataclass(frozen=True)
class BBox:
    """Inclusive lat/lon bounding box."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contains(self, lat: decimal.Decimal, lon: decimal.Decimal) -> bool:
        return (
            self.lat_min <= float(lat) <= self.lat_max
            and self.lon_min <= float(lon) <= self.lon_max
        )


# Country bounding boxes keyed by ICAO prefix. Generous boxes (incl. terminal
# area waypoints just outside the landmass). Extend per country in Phase 1.
_COUNTRY_BBOX: dict[str, BBox] = {
    "K": BBox(18.0, 72.0, -180.0, -64.0),    # USA (CONUS + AK + HI span)
    "P": BBox(13.0, 72.0, -180.0, -129.0),   # US Pacific/Alaska (PAxx, PHxx)
    "C": BBox(40.0, 84.0, -142.0, -52.0),    # Canada
    "EG": BBox(49.0, 61.0, -9.0, 2.5),       # United Kingdom
    "LF": BBox(41.0, 51.5, -5.5, 9.8),       # France
    "VH": BBox(21.5, 23.0, 113.0, 115.0),    # Hong Kong
    "Z": BBox(15.0, 54.0, 73.0, 135.0),      # China
}


def get_country_bbox(icao_prefix: str) -> BBox:
    """Return the bounding box for an ICAO prefix (2-letter, then 1-letter)."""
    if icao_prefix[:2] in _COUNTRY_BBOX:
        return _COUNTRY_BBOX[icao_prefix[:2]]
    if icao_prefix[:1] in _COUNTRY_BBOX:
        return _COUNTRY_BBOX[icao_prefix[:1]]
    # Unknown country: whole-earth box (cannot hard-reject on geography).
    return BBox(-90.0, 90.0, -180.0, 180.0)


def validate_submission(record: Record) -> list[str]:
    """Returns list of errors. Empty list = passes hard gate."""
    errors: list[str] = []

    # 1. US named waypoints must come from NASR CIFP (no guessing).
    if record.airport_icao.startswith("K") or record.airport_icao.startswith("P"):
        for leg in all_legs(record):
            if leg.waypoint and leg.waypoint.waypoint_type == "NAMED":
                if leg.waypoint.source_db == "CONSENSUS":
                    errors.append(
                        f"Leg {leg.sequence_number}: US named waypoint "
                        f"{leg.waypoint.waypoint_id} must use NASR_CIFP source"
                    )

    # 2. Altitude uom must be FT.
    for leg in all_legs(record):
        if leg.alt_constraint and leg.alt_constraint.uom != "FT":
            errors.append(f"Leg {leg.sequence_number}: altitude uom must be FT")

    # 3. Speed must be a sane positive integer (kt).
    for leg in all_legs(record):
        if leg.speed_constraint:
            if leg.speed_constraint.value_kt <= 0 or leg.speed_constraint.value_kt > 999:
                errors.append(
                    f"Leg {leg.sequence_number}: invalid speed "
                    f"{leg.speed_constraint.value_kt} kt"
                )

    # 4. RF legs must have center_fix and radius_nm.
    for leg in all_legs(record):
        if leg.path_terminator == "RF":
            if not leg.center_fix or not leg.radius_nm:
                errors.append(
                    f"Leg {leg.sequence_number}: RF leg missing center_fix or radius"
                )

    # 5. Waypoint coordinates must be within the country bounding box.
    bbox = get_country_bbox(record.airport_icao[:2])
    for leg in all_legs(record):
        if leg.waypoint:
            if not bbox.contains(leg.waypoint.lat, leg.waypoint.lon):
                errors.append(
                    f"Waypoint {leg.waypoint.waypoint_id} outside country "
                    f"bounding box for {record.airport_icao}"
                )

    return errors
