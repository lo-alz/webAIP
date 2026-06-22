"""
AIXM 5.2 GML exporter.

Renders a Pydantic ``SIDRecord`` / ``STARRecord`` / ``IAPRecord`` to an
AIXM-5.2-shaped GML document using the **real AIXM feature/attribute names**
(``StandardInstrumentDeparture``, ``SegmentLeg`` subtypes with ``@legTypeARINC``,
``DesignatedPoint`` with ``gml:pos``). The output validates against the committed
AIXM 5.2 profile schema in ``data/fixtures/aixm52_subset.xsd`` (see
``validator.aixm_xsd``), which makes "output matches AIXM 5.2" a tested assertion.

This is a faithful *profile* of AIXM 5.2 — the full official multi-file schema is
out of Phase 0 scope, but every element/attribute name here is the AIXM one.
"""
from __future__ import annotations

from lxml import etree

from miner.schemas import aixm_leg_feature, all_legs

AIXM = "http://www.aixm.aero/schema/5.2"
GML = "http://www.opengis.net/gml/3.2"
NSMAP = {"aixm": AIXM, "gml": GML}

_PROC_FEATURE = {
    "SID": "StandardInstrumentDeparture",
    "STAR": "StandardTerminalArrival",
    "IAP": "InstrumentApproachProcedure",
}


def _a(tag: str):
    return f"{{{AIXM}}}{tag}"


def _g(tag: str):
    return f"{{{GML}}}{tag}"


def _designated_point(parent, wp, wrapper: str, uid: str) -> None:
    holder = etree.SubElement(parent, _a(wrapper))
    dp = etree.SubElement(holder, _a("DesignatedPoint"))
    # gml:id must be document-unique (a waypoint may recur across transitions).
    dp.set(_g("id"), f"{uid}_{wp.waypoint_id}")
    etree.SubElement(dp, _a("designator")).text = wp.waypoint_id
    pos = etree.SubElement(dp, _g("pos"))
    pos.set("srsName", "urn:ogc:def:crs:EPSG::4326")
    pos.text = f"{wp.lat} {wp.lon}"  # AIXM/GML EPSG:4326 order is lat lon


def _leg_element(parent, leg, leg_feature: str, proc_id: str, idx: int) -> None:
    el = etree.SubElement(parent, _a(leg_feature))
    # Sequence numbers repeat per transition, so the document id uses idx.
    el.set(_g("id"), f"leg_{proc_id}_{idx}_{leg.sequence_number}")
    el.set("sequenceNumber", str(leg.sequence_number))
    el.set("legTypeARINC", leg.legTypeARINC)
    if leg.turn_direction:
        el.set("turnDirection", leg.turn_direction)
    if leg.waypoint_flag:
        etree.SubElement(el, _a("overfly")).text = leg.waypoint_flag
    if leg.course_magnetic is not None:
        etree.SubElement(el, _a("courseMagnetic")).text = str(leg.course_magnetic)
    if leg.distance_nm is not None:
        etree.SubElement(el, _a("distanceNM")).text = str(leg.distance_nm)
    if leg.waypoint:
        _designated_point(el, leg.waypoint, "terminationPoint", f"wp_{idx}")
    if leg.center_fix:
        _designated_point(el, leg.center_fix, "centerPoint", f"cf_{idx}")
    if leg.radius_nm is not None:
        etree.SubElement(el, _a("radiusNM")).text = str(leg.radius_nm)
    if leg.alt_constraint:
        a = etree.SubElement(el, _a("altitude"))
        a.set("type", leg.alt_constraint.type)
        a.set("uom", leg.alt_constraint.uom)
        a.text = str(leg.alt_constraint.lower_ft)
        if leg.alt_constraint.upper_ft is not None:
            a.set("upper", str(leg.alt_constraint.upper_ft))
    if leg.speed_constraint:
        s = etree.SubElement(el, _a("speedLimit"))
        s.set("type", leg.speed_constraint.type)
        s.set("uom", leg.speed_constraint.uom)
        s.text = str(leg.speed_constraint.value_kt)


def record_to_gml_element(record) -> etree._Element:
    """Build the AIXM 5.2 GML element tree for a procedure record."""
    feature = _PROC_FEATURE[record.record_type]
    leg_feature = aixm_leg_feature(record)
    proc_id = f"{record.airport_icao}_{record.procedure_name}"

    msg = etree.Element(_a("AIXMBasicMessage"), nsmap=NSMAP)
    msg.set(_g("id"), "msg1")
    member = etree.SubElement(msg, _a("hasMember"))
    proc = etree.SubElement(member, _a(feature))
    proc.set(_g("id"), f"{record.record_type}_{proc_id}")

    etree.SubElement(proc, _a("designator")).text = record.procedure_name
    etree.SubElement(proc, _a("airportIcaoCode")).text = record.airport_icao
    if record.pbn_nav_spec:
        etree.SubElement(proc, _a("pbnNavSpec")).text = record.pbn_nav_spec
    etree.SubElement(proc, _a("airacCycle")).text = record.airac_cycle

    for idx, leg in enumerate(all_legs(record)):
        holder = etree.SubElement(proc, _a("hasLeg"))
        _leg_element(holder, leg, leg_feature, proc_id, idx)

    return msg


def record_to_gml(record, *, pretty: bool = True) -> bytes:
    """Serialise a procedure record to AIXM 5.2 GML bytes."""
    el = record_to_gml_element(record)
    return etree.tostring(el, pretty_print=pretty, xml_declaration=True, encoding="UTF-8")
