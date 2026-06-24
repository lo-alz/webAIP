"""
KML export for AeroAIP procedures — "open in Google Earth".

``procedure_to_kml`` is a pure, DB-free function: it takes the same row shapes the
read API already produces (a ``procedure`` row + its ordered, coordinate-bearing
``legs``) and returns a KML document string. The document carries BOTH:

  * a **static 3D path** — one Placemark per waypoint plus an extruded LineString
    "curtain" dropped to the ground at true altitudes (matches the CesiumJS view); and
  * an animated **``gx:Track``** — Google Earth plays it as an aircraft flying the
    procedure (a fixed synthetic cadence; there is no real timing data in IFP records).

Coordinates are ``lon,lat,alt_m`` with ``altitudeMode=absolute``; altitude is the
leg's lower constraint (feet MSL → metres), 0 when unconstrained — identical to the
``/procedures/{id}/geojson`` endpoint.
"""
from __future__ import annotations

from typing import Optional

from lxml import etree

# Feet → metres (same constant the API uses for GeoJSON/Cesium heights).
_FT_TO_M = 0.3048

# Seconds between fixes for the gx:Track animation (synthetic — no real timing in IFP).
_TRACK_STEP_S = 8
# Arbitrary fixed epoch so output is deterministic (no wall-clock dependency).
_TRACK_EPOCH = "2026-01-01T00:00:00Z"

_KML_NS = "http://www.opengis.net/kml/2.2"
_GX_NS = "http://www.google.com/kml/ext/2.2"
_NSMAP = {None: _KML_NS, "gx": _GX_NS}

_ALT_SYMBOL = {"AT_OR_ABOVE": "≥", "AT_OR_BELOW": "≤", "AT": "=", "BETWEEN": "↕"}


def _alt_m(leg: dict) -> float:
    return (leg.get("alt_lower_ft") or 0) * _FT_TO_M


def _alt_text(leg: dict) -> str:
    ft = leg.get("alt_lower_ft")
    if ft is None:
        return ""
    return f"{_ALT_SYMBOL.get(leg.get('alt_type'), '')}{ft:,} ft"


def _coord(leg: dict) -> str:
    """`lon,lat,alt_m` — KML coordinate order (no spaces inside a tuple)."""
    return f"{leg['lon']},{leg['lat']},{_alt_m(leg):.1f}"


def _track_when(i: int) -> str:
    """Synthetic ISO-8601 timestamp `i` steps after the fixed epoch (HH:MM:SS only)."""
    total = i * _TRACK_STEP_S
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    return f"2026-01-01T{hh:02d}:{mm:02d}:{ss:02d}Z"


def _sub(parent, tag: str, text: Optional[str] = None, **attrs):
    el = etree.SubElement(parent, tag, **attrs)
    if text is not None:
        el.text = text
    return el


def procedure_to_kml(proc: dict, legs: list[dict]) -> str:
    """Render a procedure + its coordinate-bearing legs as a KML document string.

    ``legs`` must already be filtered to fixes with coordinates and ordered
    (segment, sequence_number) — exactly what ``_route_legs`` in the API returns.
    """
    title = " ".join(str(proc.get(k, "")) for k in ("airport_icao", "procedure_name")).strip() \
        or "AeroAIP procedure"
    rtype = proc.get("record_type") or "procedure"

    kml = etree.Element("{%s}kml" % _KML_NS, nsmap=_NSMAP)
    doc = _sub(kml, "Document")
    _sub(doc, "name", f"{title} ({rtype})")
    _sub(doc, "description", "AeroAIP — open instrument flight procedure (AIXM 5.2). "
                             "Static 3D path + gx:Track flythrough.")

    # ── styles ──────────────────────────────────────────────────────────────
    route_style = _sub(doc, "Style", id="aeroaip-route")
    ls = _sub(route_style, "LineStyle")
    _sub(ls, "color", "ff66d1ff")   # KML aabbggrr — opaque, #ffd166 → ff66d1ff
    _sub(ls, "width", "3")
    poly = _sub(route_style, "PolyStyle")
    _sub(poly, "color", "2066d1ff")  # translucent curtain fill
    wp_style = _sub(doc, "Style", id="aeroaip-wp")
    icon = _sub(wp_style, "IconStyle")
    _sub(icon, "color", "ffffff00")  # cyan
    istyle = _sub(icon, "Icon")
    _sub(istyle, "href", "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png")

    # ── one Placemark per waypoint ──────────────────────────────────────────
    for leg in legs:
        pm = _sub(doc, "Placemark")
        _sub(pm, "name", leg.get("waypoint_id") or "")
        _sub(pm, "styleUrl", "#aeroaip-wp")
        desc = f"leg {leg.get('sequence_number')} · {leg.get('path_terminator') or ''}"
        alt = _alt_text(leg)
        if alt:
            desc += f"<br/>altitude {alt}"
        seg = leg.get("segment")
        if seg:
            desc += f"<br/>segment {seg}"
        _sub(pm, "description", desc)
        pt = _sub(pm, "Point")
        _sub(pt, "altitudeMode", "absolute")
        _sub(pt, "coordinates", _coord(leg))

    # ── static route curtain (LineString, extruded to ground) ───────────────
    if len(legs) >= 2:
        pm = _sub(doc, "Placemark")
        _sub(pm, "name", f"{title} route")
        _sub(pm, "styleUrl", "#aeroaip-route")
        line = _sub(pm, "LineString")
        _sub(line, "extrude", "1")
        _sub(line, "tessellate", "1")
        _sub(line, "altitudeMode", "absolute")
        _sub(line, "coordinates", " ".join(_coord(leg) for leg in legs))

    # ── animated flythrough (gx:Track) ──────────────────────────────────────
    if len(legs) >= 2:
        pm = _sub(doc, "Placemark")
        _sub(pm, "name", f"{title} flythrough")
        track = _sub(pm, "{%s}Track" % _GX_NS)
        _sub(track, "altitudeMode", "absolute")
        for i, _ in enumerate(legs):
            _sub(track, "when", _track_when(i))
        for leg in legs:
            # gx:coord is space-separated `lon lat alt` (not comma-separated).
            _sub(track, "{%s}coord" % _GX_NS,
                 f"{leg['lon']} {leg['lat']} {_alt_m(leg):.1f}")

    return etree.tostring(kml, xml_declaration=True, encoding="UTF-8",
                          pretty_print=True).decode("utf-8")
