"""AIXM 5.2 GML export validates against the committed profile XSD."""
from lxml import etree

from miner.schemas.aixm52_gml import AIXM, GML, record_to_gml, record_to_gml_element
from validator.aixm_xsd import assert_valid, validate_gml
from tests.helpers import make_sid


def test_export_validates_against_xsd():
    gml = record_to_gml(make_sid())
    ok, errors = validate_gml(gml)
    assert ok, errors


def test_uses_real_aixm_feature_names():
    el = record_to_gml_element(make_sid())
    xml = etree.tostring(el).decode()
    assert "StandardInstrumentDeparture" in xml
    assert "DepartureLeg" in xml
    assert "DesignatedPoint" in xml
    assert 'legTypeARINC="IF"' in xml or 'legTypeARINC=' in xml


def test_pos_carries_lat_lon_and_srs():
    el = record_to_gml_element(make_sid())
    pos = el.find(f".//{{{GML}}}pos")
    assert pos is not None
    assert pos.get("srsName") == "urn:ogc:def:crs:EPSG::4326"
    lat, lon = pos.text.split()
    assert lat.startswith("33") or lat.startswith("34")


def test_invalid_document_is_rejected():
    bad = etree.Element(f"{{{AIXM}}}AIXMBasicMessage")  # missing gml:id + members
    ok, errors = validate_gml(bad)
    assert not ok
    assert errors


def test_assert_valid_passes_for_good_doc():
    assert_valid(record_to_gml(make_sid()))  # should not raise
