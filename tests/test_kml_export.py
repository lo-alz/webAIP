"""
Offline tests for visualization/kml_export.py (no DB).

Rows are derived from the committed CesiumJS sample so the fixture and the KML
output stay in lock-step with the live ``/procedures/{id}/geojson`` shape.
"""
import json
from pathlib import Path

from lxml import etree

from visualization.kml_export import procedure_to_kml

SAMPLE = Path("visualization/cesium/sample_klax_dotss2.geojson")
NS = {"k": "http://www.opengis.net/kml/2.2",
      "gx": "http://www.google.com/kml/ext/2.2"}


def _legs():
    gj = json.loads(SAMPLE.read_text())
    return [{"waypoint_id": f["properties"]["waypoint_id"],
             "segment": f["properties"]["segment"],
             "sequence_number": f["properties"]["sequence_number"],
             "path_terminator": f["properties"]["path_terminator"],
             "alt_type": f["properties"]["alt_type"],
             "alt_lower_ft": f["properties"]["alt_lower_ft"],
             "lon": f["geometry"]["coordinates"][0],
             "lat": f["geometry"]["coordinates"][1]}
            for f in gj["features"] if f["geometry"]["type"] == "Point"]


def _root():
    proc = {"airport_icao": "KLAX", "procedure_name": "DOTSS2", "record_type": "SID"}
    return etree.fromstring(procedure_to_kml(proc, _legs()).encode()), _legs()


def test_kml_is_well_formed_with_expected_counts():
    root, legs = _root()
    assert len(root.findall(".//k:Point", NS)) == len(legs)          # one fix each
    assert len(root.findall(".//k:LineString", NS)) == 1             # route curtain
    assert len(root.findall(".//gx:Track", NS)) == 1                 # flythrough


def test_route_is_absolute_extruded_curtain():
    root, _ = _root()
    line = root.find(".//k:LineString", NS)
    assert line.find("k:altitudeMode", NS).text == "absolute"
    assert line.find("k:extrude", NS).text == "1"


def test_coordinates_carry_altitude_metres():
    root, legs = _root()
    # First waypoint: DLREY at 3000 ft → 914.4 m, KML order is lon,lat,alt.
    first = root.find(".//k:Point/k:coordinates", NS).text
    lon, lat, alt = first.split(",")
    assert float(lon) == legs[0]["lon"] and float(lat) == legs[0]["lat"]
    assert float(alt) == round(legs[0]["alt_lower_ft"] * 0.3048, 1)


def test_track_when_and_coord_counts_match():
    root, legs = _root()
    whens = root.findall(".//gx:Track/k:when", NS)
    coords = root.findall(".//gx:Track/gx:coord", NS)
    assert len(whens) == len(coords) == len(legs)
    # gx:coord is space-separated `lon lat alt` (not comma-separated).
    assert len(coords[0].text.split()) == 3
