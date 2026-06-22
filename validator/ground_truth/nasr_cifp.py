"""
NASR CIFP (ARINC 424-18) parser — the US ground truth.

Parses the real FAA CIFP (``FAACIFP18``, 132-char fixed-width records):
  * waypoint records — terminal ``PC`` and enroute ``EA`` -> coordinate index rows
  * procedure records — ``PD`` SID / ``PE`` STAR / ``PF`` approach -> typed records

The column offsets in ``CIFP_FIELDS`` are the real ARINC 424-18 positions and are
**verified against the live FAA CIFP** (see tests/test_cifp_real_format.py and
docs/SOURCES.md §1/§7). Note the ARINC section/subsection split:
  * Airport records  (section 'P'): subsection is at column 13.
  * Enroute records  (section 'E'): subsection is at column 6.

Coordinates are converted with the single source-of-truth converter in
``miner.coordinates`` and stored as canonical 8-dp text — identical to what the
miner's lookup returns, which is *why* exact coordinate equality holds.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Iterable, Optional

from miner.coordinates import canonical, cifp_packed_to_decimal
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

# ── real ARINC 424-18 column map (0-based python slices), verified live ──────
CIFP_FIELDS = {
    # common
    "record_type": (0, 1),     # 'S'
    "section": (4, 5),         # 'P' airport / 'E' enroute / 'D' navaid ...
    "enr_subsection": (5, 6),  # enroute subsection ('A' = waypoint)
    "airport": (6, 10),        # ICAO airport ident (airport-section records)
    "region": (10, 12),        # ICAO region code
    "apt_subsection": (12, 13),  # airport subsection (C=wp, D=SID, E=STAR, F=IAP)
    # waypoints (PC terminal / EA enroute) — coords share the same columns
    "wp_id": (13, 18),         # 5-char identifier
    "wp_region": (19, 21),     # ICAO region code
    "wp_lat": (32, 41),        # packed 'N33542830'   (hemisphere + 8)
    "wp_lon": (41, 51),        # packed 'W118292809'  (hemisphere + 9)
    # procedures (PD / PE / PF)
    "proc_id": (13, 19),       # 6-char procedure identifier
    "route_type": (19, 20),    # ARINC route type (see _ROUTE_*)
    "transition_id": (20, 25), # runway designator or transition fix
    "seq": (26, 29),           # sequence number
    "fix_id": (29, 34),        # leg termination fix (blank for heading legs)
    "fix_region": (34, 36),
    "fix_section": (36, 37),
    "fix_subsection": (37, 38),
    "desc_code": (39, 43),     # waypoint description code (overfly etc.)
    "turn_dir": (43, 44),      # 'L'/'R'
    "rnp": (44, 47),
    "path_term": (47, 49),     # ARINC 424 2-letter terminator
    "course_mag": (70, 74),    # tenths of a degree ('2510' = 251.0)
    "distance": (74, 78),      # tenths of a NM
    "alt_desc": (82, 83),      # '+','-','B',' '
    "alt1": (84, 89),          # feet MSL
    "alt2": (89, 94),          # feet MSL (BETWEEN upper)
    "trans_alt": (94, 99),     # transition altitude (not a leg constraint)
    "speed": (99, 102),        # knots IAS
}

# ARINC 424 SID route-type categories (digits differ for conventional / RNAV /
# FMS variants; classify by category rather than a single code).
_ROUTE_RUNWAY = set("14FTWZ")
_ROUTE_ENROUTE = set("36SV")
# everything else (e.g. '2','5','M') is treated as common route.


def _f(line: str, field: str) -> str:
    a, b = CIFP_FIELDS[field]
    return line[a:b].strip()


def iter_lines(source) -> Iterable[str]:
    """Yield non-empty record lines from a file path, text, or iterable."""
    if isinstance(source, (str, Path)) and Path(str(source)).exists():
        lines = Path(source).read_text().splitlines()
    elif isinstance(source, str):
        lines = source.splitlines()
    else:
        lines = list(source)
    for ln in lines:
        if ln.strip() and not ln.lstrip().startswith("#"):
            yield ln


def is_terminal_waypoint(line: str) -> bool:
    return _f(line, "section") == "P" and line[12:13] == "C"


def is_enroute_waypoint(line: str) -> bool:
    return _f(line, "section") == "E" and line[5:6] == "A"


def is_sid_record(line: str) -> bool:
    return _f(line, "section") == "P" and line[12:13] == "D"


# ── waypoint records → index rows ────────────────────────────────────────────
def parse_waypoints(source, airac: str) -> list[WaypointRow]:
    """Parse terminal (PC) and enroute (EA) waypoint records into index rows."""
    rows: list[WaypointRow] = []
    for ln in iter_lines(source):
        if not (is_terminal_waypoint(ln) or is_enroute_waypoint(ln)):
            continue
        lat_raw = _f(ln, "wp_lat")
        lon_raw = _f(ln, "wp_lon")
        if not lat_raw or not lon_raw:
            continue
        try:
            lat = cifp_packed_to_decimal(lat_raw)
            lon = cifp_packed_to_decimal(lon_raw)
        except (ValueError, Exception):
            continue
        rows.append(
            WaypointRow(
                waypoint_id=_f(ln, "wp_id"),
                region=_f(ln, "wp_region"),
                lat=canonical(lat),
                lon=canonical(lon),
                source="NASR_CIFP",
                airac=airac,
                waypoint_type="NAMED",
            )
        )
    return rows


# ── procedure records → typed leg ────────────────────────────────────────────
def _alt_constraint(line: str) -> Optional[AltConstraint]:
    a1 = _f(line, "alt1")
    if not a1 or not a1.isdigit():
        return None
    desc = _f(line, "alt_desc")
    a2 = _f(line, "alt2")
    type_map = {"+": "AT_OR_ABOVE", "-": "AT_OR_BELOW", "B": "BETWEEN", "": "AT"}
    ctype = type_map.get(desc, "AT")
    return AltConstraint(
        type=ctype,
        lower_ft=int(a1),
        upper_ft=int(a2) if (ctype == "BETWEEN" and a2.isdigit()) else None,
    )


def _speed_constraint(line: str) -> Optional[SpeedConstraint]:
    sp = _f(line, "speed")
    if not sp or not sp.isdigit():
        return None
    return SpeedConstraint(type="AT_OR_BELOW", value_kt=int(sp))


def _tenths(line: str, field: str) -> Optional[float]:
    raw = _f(line, field)
    return int(raw) / 10.0 if raw.isdigit() else None


def _overfly(line: str) -> Optional[str]:
    desc = _f(line, "desc_code")
    if not _f(line, "fix_id"):
        return None
    return "FLY_OVER" if "Y" in desc else "FLY_BY"


def _leg_from_line(line: str, index) -> ProcedureLeg:
    fix_id = _f(line, "fix_id")
    region = _f(line, "fix_region")
    waypoint = index.lookup(fix_id, region or None) if fix_id else None
    return ProcedureLeg(
        sequence_number=int(_f(line, "seq")),
        path_terminator=_f(line, "path_term"),
        waypoint=waypoint,
        waypoint_flag=_overfly(line),
        alt_constraint=_alt_constraint(line),
        speed_constraint=_speed_constraint(line),
        course_magnetic=_tenths(line, "course_mag"),
        distance_nm=_tenths(line, "distance"),
        turn_direction=_f(line, "turn_dir") or None,
    )


def _route_category(rt: str) -> str:
    if rt in _ROUTE_RUNWAY:
        return "runway"
    if rt in _ROUTE_ENROUTE:
        return "enroute"
    return "common"


def parse_sid(source, airport: str, procedure: str, airac: str, index,
              source_url: str = "") -> SIDRecord:
    """
    Assemble a SIDRecord from CIFP PD records for ``airport`` + ``procedure``.
    Coordinates are looked up from ``index`` (the same path the miner uses).
    """
    rwy: dict[str, list[ProcedureLeg]] = {}
    common: list[ProcedureLeg] = []
    enr: dict[str, list[ProcedureLeg]] = {}

    for ln in iter_lines(source):
        if not is_sid_record(ln):
            continue
        if _f(ln, "airport") != airport or _f(ln, "proc_id") != procedure:
            continue
        category = _route_category(_f(ln, "route_type"))
        trans = _f(ln, "transition_id")
        leg = _leg_from_line(ln, index)
        if category == "runway":
            rwy.setdefault(trans, []).append(leg)
        elif category == "enroute":
            enr.setdefault(trans, []).append(leg)
        else:
            common.append(leg)

    runway_transitions = [
        RunwayTransition(runway_designator=t, transition_id=f"{procedure}.{t}", legs=legs)
        for t, legs in rwy.items()
    ]
    enroute_transitions = [
        EnrouteTransition(transition_fix=t, transition_id=f"{procedure}.{t}", legs=legs)
        for t, legs in enr.items()
    ]

    return SIDRecord(
        airport_icao=airport,
        procedure_name=procedure,
        runway_transitions=runway_transitions,
        common_route=common,
        enroute_transitions=enroute_transitions,
        pbn_nav_spec=None,  # PBN nav-spec lives in a separate CIFP continuation record
        airac_cycle=airac,
        extraction_confidence=1.0,
        source_url=source_url or "NASR_CIFP",
        coverage_source="miner",
        extracted_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
