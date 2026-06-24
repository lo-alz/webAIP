"""
eAIP HTML parser — the catalog's ``parser_method == "eaip_html"`` tier (the
largest tier: 84 countries publish EUROCONTROL-style HTML eAIPs).

eAIP procedure tables give the leg STRUCTURE (sequence, path terminator, fix
identifier, fly-by/over, altitude/speed constraints). They do **not** give the
authoritative coordinates, so — like every chart-derived tier — this parser
returns structure only; coordinates are filled from the waypoint index in
``miner.assemble``. Where a country has no coded waypoint DB at all (e.g. Hong
Kong's procedures cross-reference the ICAO APAC regional set), the assemble step
runs with ``on_missing="consensus"``.

Parsing uses ``lxml.html`` (a core dependency — no BeautifulSoup needed). The
committed VHHH fixture mirrors the CAAS HK eAIP procedure-table shape; per-state
table dialects are added as their parsers are onboarded.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from miner.extractor.base import ExtractedLeg, ExtractedProcedure

_ALT_TYPE = {"+": "AT_OR_ABOVE", "-": "AT_OR_BELOW", "B": "BETWEEN"}
_SPD_TYPE = {"+": "AT_OR_ABOVE", "-": "AT_OR_BELOW"}


def _doc(source):
    from lxml import html as lxml_html  # lazy import

    if isinstance(source, (str, Path)) and Path(str(source)).exists():
        return lxml_html.fromstring(Path(source).read_text())
    if isinstance(source, (bytes, bytearray)):
        return lxml_html.fromstring(source)
    return lxml_html.fromstring(str(source))


def _cell(row, idx: int) -> str:
    tds = row.xpath('./td')
    if idx >= len(tds):
        return ""
    return (tds[idx].text_content() or "").strip()


def _alt(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw[0] == "B":  # BETWEEN: 'B4000/6000'
        nums = [n for n in raw[1:].replace("/", " ").split() if n.lstrip("-").isdigit()]
        if len(nums) == 2:
            return {"type": "BETWEEN", "lower_ft": int(nums[0]), "upper_ft": int(nums[1])}
        return None
    if raw[0] in _ALT_TYPE:
        body = raw[1:].strip()
        return {"type": _ALT_TYPE[raw[0]], "lower_ft": int(body)} if body.isdigit() else None
    return {"type": "AT", "lower_ft": int(raw)} if raw.isdigit() else None


def _speed(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw[0] in _SPD_TYPE:
        body = raw[1:].strip()
        return {"type": _SPD_TYPE[raw[0]], "value_kt": int(body)} if body.isdigit() else None
    return {"type": "AT", "value_kt": int(raw)} if raw.isdigit() else None


def _leg_from_row(row) -> Optional[ExtractedLeg]:
    seq = _cell(row, 0)
    if not seq.isdigit():
        return None
    term = _cell(row, 1).upper()
    fix = _cell(row, 2).strip()
    fly = _cell(row, 3).upper().replace("-", "_")
    e: ExtractedLeg = {"sequence_number": int(seq), "path_terminator": term}
    if fix:
        e["waypoint_id"] = fix
        e["waypoint_flag"] = "FLY_OVER" if "OVER" in fly else "FLY_BY"
    alt = _alt(_cell(row, 4))
    if alt:
        e["alt_constraint"] = alt
    spd = _speed(_cell(row, 5))
    if spd:
        e["speed_constraint"] = spd
    return e


def _table_legs(table) -> list[ExtractedLeg]:
    legs = []
    for row in table.xpath('.//tbody/tr') or table.xpath('.//tr'):
        leg = _leg_from_row(row)
        if leg:
            legs.append(leg)
    return legs


def extract(source) -> ExtractedProcedure:
    """Parse an eAIP procedure block into structure-only ``ExtractedProcedure``."""
    doc = _doc(source)
    procs = doc.xpath('//*[@data-record-type]')
    proc = procs[0] if procs else doc
    transitions = []
    common: list[ExtractedLeg] = []
    for table in proc.xpath('.//table'):
        kind = (table.get("data-kind") or "").strip()
        legs = _table_legs(table)
        if not legs:
            continue
        if kind == "common":
            common.extend(legs)
        else:
            transitions.append({
                "kind": kind or "runway",
                "designator": table.get("data-designator", ""),
                "transition_id": table.get("data-transition-id", ""),
                "legs": legs,
            })
    return ExtractedProcedure(
        record_type=proc.get("data-record-type", "SID"),
        airport_icao=proc.get("data-airport", ""),
        procedure_name=proc.get("data-procedure", ""),
        pbn_nav_spec=proc.get("data-pbn") or None,
        transitions=transitions,
        common_route=common,
    )


class EaipHtmlExtractor:
    """Registry adapter exposing the structure-only ``Extractor`` interface."""

    def extract(self, source_ref: str) -> ExtractedProcedure:
        return extract(source_ref)
