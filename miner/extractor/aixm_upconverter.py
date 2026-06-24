"""
AIXM up-converter — the catalog's ``parser_method in {aixm_upconvert, aixm_native}``
tier (the highest-leverage tier: 31 countries publish AIXM 5.1/4.5 via EAD).

Coded AIXM carries coordinates natively, so — like the ARINC 424 tier — this
module produces both:

  * ``parse_waypoints`` -> ``WaypointRow`` rows for the waypoint index
  * ``extract``         -> structure-only ``ExtractedProcedure`` for the miner
  * ``to_ground_truth`` -> a ``SIDRecord`` with coordinates straight from the AIXM
    geometry (the independent reference the PoC scores against)

Element lookup uses XPath ``local-name()`` so the same code reads AIXM 5.1 and
4.5 (which differ only in namespace URI); up-conversion to 5.2 is then the
``aixm52_gml`` serializer the rest of the pipeline already uses.

The committed EGLL fixture is a *representative subset* of an EAD AIXM 5.1 export:
``aixm:DesignatedPoint`` features with real ``gml:pos`` geometry, and a compact
leg encoding on the procedure. Real EAD leg geometry (xlink-referenced
``SegmentLeg``/``TerminationFix``) is mapped when live EAD access lands; the point
geometry — what coordinates depend on — is already the real shape.
"""
from __future__ import annotations

import datetime as _dt
import decimal
from pathlib import Path
from typing import Optional

from miner.coordinates import canonical
from miner.extractor.base import ExtractedLeg, ExtractedProcedure
from miner.schemas import (
    AltConstraint,
    EnrouteTransition,
    ProcedureLeg,
    RunwayTransition,
    SIDRecord,
    SpeedConstraint,
    WGS84Point,
)
from miner.waypoint_db import WaypointRow


def _root(source):
    from lxml import etree  # lazy: lxml is a core dep but keep import local

    if hasattr(source, "xpath"):          # already a parsed lxml element
        return source
    if isinstance(source, (str, Path)) and Path(str(source)).exists():
        return etree.parse(str(source)).getroot()
    if isinstance(source, (bytes, bytearray)):
        return etree.fromstring(source)
    return etree.fromstring(str(source).encode())


def _text(el, name: str) -> Optional[str]:
    vals = el.xpath(f'.//*[local-name()="{name}"]/text()')
    return vals[0].strip() if vals else None


def _points(root) -> dict[str, dict]:
    """designator -> {region, lat_text, lon_text} from aixm:DesignatedPoint."""
    out: dict[str, dict] = {}
    for dp in root.xpath('//*[local-name()="DesignatedPoint"]'):
        desig = _text(dp, "designator")
        pos = dp.xpath('.//*[local-name()="pos"]/text()')
        if not desig or not pos:
            continue
        parts = pos[0].split()
        if len(parts) < 2:
            continue
        lat = canonical(decimal.Decimal(parts[0]))   # gml:pos is "lat lon" (EPSG:4326)
        lon = canonical(decimal.Decimal(parts[1]))
        out[desig] = {
            "region": _text(dp, "icaoRegion") or "",
            "lat": lat,
            "lon": lon,
        }
    return out


def parse_waypoints(source, airac: str, *, source_db: str = "EAD") -> list[WaypointRow]:
    """AIXM DesignatedPoint geometry -> waypoint index rows."""
    rows: list[WaypointRow] = []
    for desig, p in _points(_root(source)).items():
        rows.append(
            WaypointRow(
                waypoint_id=desig, region=p["region"],
                lat=p["lat"], lon=p["lon"],
                source=source_db, airac=airac, waypoint_type="NAMED",
            )
        )
    return rows


def _extracted_leg(leg_el) -> ExtractedLeg:
    point = (leg_el.get("point") or "").strip() or None
    fly_over = (leg_el.get("flyOver") or "").lower() == "true"
    e: ExtractedLeg = {
        "sequence_number": int(leg_el.get("seq")),
        "path_terminator": (leg_el.get("pathTerminator") or "").strip(),
    }
    if point:
        e["waypoint_id"] = point
        e["waypoint_flag"] = "FLY_OVER" if fly_over else "FLY_BY"
    alt_type = leg_el.get("altType")
    if alt_type and leg_el.get("altFt"):
        ac: dict = {"type": alt_type, "lower_ft": int(leg_el.get("altFt"))}
        if alt_type == "BETWEEN" and leg_el.get("alt2Ft"):
            ac["upper_ft"] = int(leg_el.get("alt2Ft"))
        e["alt_constraint"] = ac
    if leg_el.get("spdType") and leg_el.get("spdKt"):
        e["speed_constraint"] = {"type": leg_el.get("spdType"), "value_kt": int(leg_el.get("spdKt"))}
    if leg_el.get("courseMag"):
        e["course_magnetic"] = float(leg_el.get("courseMag"))
    if leg_el.get("distanceNm"):
        e["distance_nm"] = float(leg_el.get("distanceNm"))
    if leg_el.get("turn"):
        e["turn_direction"] = leg_el.get("turn")
    return e


def _sid_element(root):
    sids = root.xpath('//*[local-name()="StandardInstrumentDeparture"]')
    if not sids:
        raise ValueError("No StandardInstrumentDeparture feature in AIXM source")
    return sids[0]


def extract(source) -> ExtractedProcedure:
    """Structure-only extraction (no coordinates) — the miner's input."""
    sid = _sid_element(_root(source))
    transitions = []
    for t in sid.xpath('.//*[local-name()="transition"]'):
        transitions.append({
            "kind": t.get("kind", "runway"),
            "designator": t.get("designator", ""),
            "transition_id": t.get("id", ""),
            "legs": [_extracted_leg(lg) for lg in t.xpath('./*[local-name()="leg"]')],
        })
    common = [
        _extracted_leg(lg)
        for lg in sid.xpath('.//*[local-name()="commonRoute"]/*[local-name()="leg"]')
    ]
    return ExtractedProcedure(
        record_type="SID",
        airport_icao=_text(sid, "airportICAO") or "",
        procedure_name=_text(sid, "designator") or "",
        pbn_nav_spec=_text(sid, "pbnRequirement"),
        transitions=transitions,
        common_route=common,
    )


def _native_point(points: dict, wp_id: str) -> Optional[WGS84Point]:
    p = points.get(wp_id)
    if not p:
        return None
    return WGS84Point(
        lat=decimal.Decimal(p["lat"]), lon=decimal.Decimal(p["lon"]),
        waypoint_id=wp_id, waypoint_type="NAMED", source_db="EAD",
    )


def _native_leg(e: ExtractedLeg, points: dict) -> ProcedureLeg:
    wp_id = e.get("waypoint_id")
    ac = e.get("alt_constraint")
    sc = e.get("speed_constraint")
    return ProcedureLeg(
        sequence_number=e["sequence_number"],
        path_terminator=e["path_terminator"],
        waypoint=_native_point(points, wp_id) if wp_id else None,
        waypoint_flag=e.get("waypoint_flag"),
        alt_constraint=AltConstraint(**ac) if ac else None,
        speed_constraint=SpeedConstraint(**sc) if sc else None,
        course_magnetic=e.get("course_magnetic"),
        distance_nm=e.get("distance_nm"),
        turn_direction=e.get("turn_direction"),
    )


def to_ground_truth(source, airac: str = "2607", source_url: str = "EAD_AIXM") -> SIDRecord:
    """A SIDRecord with coordinates straight from the AIXM geometry (the PoC's
    independent reference; coordinates do NOT pass through the waypoint index)."""
    root = _root(source)
    points = _points(root)
    ext = extract(root)
    rwy, enr = [], []
    for t in ext.get("transitions", []):
        legs = [_native_leg(e, points) for e in t["legs"]]
        if t["kind"] == "runway":
            rwy.append(RunwayTransition(runway_designator=t["designator"],
                                        transition_id=t["transition_id"], legs=legs))
        else:
            enr.append(EnrouteTransition(transition_fix=t["designator"],
                                         transition_id=t["transition_id"], legs=legs))
    common = [_native_leg(e, points) for e in ext.get("common_route", [])]
    return SIDRecord(
        airport_icao=ext["airport_icao"], procedure_name=ext["procedure_name"],
        runway_transitions=rwy, common_route=common, enroute_transitions=enr,
        pbn_nav_spec=ext.get("pbn_nav_spec"), airac_cycle=airac,
        extraction_confidence=1.0, source_url=source_url,
        coverage_source="miner", extracted_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )


class AixmExtractor:
    """Registry adapter exposing the structure-only ``Extractor`` interface."""

    def extract(self, source_ref: str) -> ExtractedProcedure:
        return extract(source_ref)
