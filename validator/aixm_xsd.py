"""
AIXM 5.2 GML conformance check.

Validates exporter output against the committed AeroAIP AIXM 5.2 profile schema
(``data/fixtures/aixm52_subset.xsd``), which uses the real AIXM feature/attribute
names. Optionally attempts to fetch the full official AIXM 5.2 schema, but the
committed subset makes conformance testable offline / in CI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

from lxml import etree

DEFAULT_XSD = Path("data/fixtures/aixm52_subset.xsd")


class AIXMValidationError(Exception):
    pass


def _load_schema(xsd_path: Path) -> etree.XMLSchema:
    with open(xsd_path, "rb") as fh:
        doc = etree.parse(fh)
    return etree.XMLSchema(doc)


def validate_gml(
    gml: Union[bytes, str, etree._Element],
    xsd_path: Union[str, Path] = DEFAULT_XSD,
) -> tuple[bool, list[str]]:
    """
    Validate an AIXM 5.2 GML document. Returns ``(ok, errors)``.
    Accepts bytes, a string, or an lxml element.
    """
    xsd_path = Path(xsd_path)
    if not xsd_path.exists():
        raise FileNotFoundError(f"AIXM profile schema not found at {xsd_path}")

    schema = _load_schema(xsd_path)

    if isinstance(gml, etree._Element):
        doc = gml.getroottree() if gml.getroottree() is not None else gml
        root = gml
    else:
        if isinstance(gml, str):
            gml = gml.encode("utf-8")
        root = etree.fromstring(gml)

    ok = schema.validate(root if isinstance(root, etree._Element) else root.getroot())
    errors = [f"line {e.line}: {e.message}" for e in schema.error_log]
    return ok, errors


def assert_valid(gml: Union[bytes, str, etree._Element],
                 xsd_path: Union[str, Path] = DEFAULT_XSD) -> None:
    ok, errors = validate_gml(gml, xsd_path)
    if not ok:
        raise AIXMValidationError("AIXM 5.2 GML validation failed:\n  " + "\n  ".join(errors))
