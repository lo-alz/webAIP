"""
ARINC 424-18 coded-procedure parser — the catalog's ``source_type == "ARINC424"``
tier (registry key ``parser_method == "arinc424"``).

This is the shared, source-neutral implementation of the FAA NASR CIFP parser
that previously lived in ``validator/ground_truth/nasr_cifp.py``; that module is
now a thin US ground-truth wrapper that re-exports from here. Coded ARINC 424
data (FAA CIFP, NAV CANADA, and other national coded sets that follow the same
record layout) carries coordinates natively, so this parser produces both:

  * waypoint records  -> ``WaypointRow``  (loaded into the waypoint index)
  * procedure records -> typed ``SIDRecord`` (coordinates looked up from the
    index, exactly like the miner path, so exact-equality scoring holds)

The column offsets in ``CIFP_FIELDS`` are the real ARINC 424-18 positions,
verified against the live FAA CIFP (see tests/test_cifp_real_format.py and
docs/SOURCES.md §1/§7). Per-country profiles can override the column map and the
``source`` label later; the FAA-verified map is the default profile.

Section/subsection split:
  * Airport records (section 'P'): subsection is at column 13.
  * Enroute records (section 'E'): subsection is at column 6.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Iterable, Optional

from miner.coordinates import canonical, cifp_packed_to_decimal
from miner.schemas import (
    AltConstraint,
    EnrouteTransition,
    IAPRecord,
    ProcedureLeg,
    RunwayTransition,
    SIDRecord,
    SpeedConstraint,
    STARRecord,
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


def is_star_record(line: str) -> bool:
    return _f(line, "section") == "P" and line[12:13] == "E"


def is_iap_record(line: str) -> bool:
    return _f(line, "section") == "P" and line[12:13] == "F"


def is_navaid(line: str) -> bool:
    """VHF navaid (VOR/DME/TACAN) or enroute NDB — ARINC 424 section 'D'."""
    return _f(line, "section") == "D"


def is_runway_record(line: str) -> bool:
    """Airport runway record (threshold coordinates) — section 'P' subsection 'G'."""
    return _f(line, "section") == "P" and line[12:13] == "G"


# ── waypoint records → index rows ────────────────────────────────────────────
def parse_waypoints(source, airac: str, *, source_db: str = "NASR_CIFP") -> list[WaypointRow]:
    """
    Parse terminal (PC) and enroute (EA) waypoint records into index rows.

    ``source_db`` labels the provenance of the rows (e.g. ``"NASR_CIFP"`` for the
    US, ``"NAVCANADA"`` for NAV CANADA coded data) so the waypoint index can
    track per-source coverage.
    """
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
                source=source_db,
                airac=airac,
                waypoint_type="NAMED",
            )
        )
    return rows


# ── navaid records → index rows ──────────────────────────────────────────────
# VHF navaid (section D): identifier at 13-16, ICAO region at 19-21. The record
# carries up to two coordinate blocks — the VOR position at col 32 and a
# collocated DME position at col 55. VOR-bearing navaids use the VOR position;
# DME/TACAN-only records leave it blank and carry coords in the DME block.
_NAV_ID = (13, 17)
_NAV_REGION = (19, 21)
_NAV_LAT_VOR, _NAV_LON_VOR = (32, 41), (41, 51)
_NAV_LAT_DME, _NAV_LON_DME = (55, 64), (64, 74)


def _navaid_coords(line: str) -> Optional[tuple[str, str]]:
    lat, lon = line[slice(*_NAV_LAT_VOR)], line[slice(*_NAV_LON_VOR)]
    if lat[:1] not in ("N", "S"):                       # DME/TACAN-only → DME block
        lat, lon = line[slice(*_NAV_LAT_DME)], line[slice(*_NAV_LON_DME)]
    return (lat, lon) if lat[:1] in ("N", "S") else None


def parse_navaids(source, airac: str, *, source_db: str = "NASR_CIFP") -> list[WaypointRow]:
    """Parse VHF navaid + enroute NDB (section 'D') records into index rows.

    Terminating legs on conventional (non-RNAV) procedures point at VORs/NDBs, so
    without these the coordinate lookup misses — the dominant Loop 2 flag before
    this. Stored as ``waypoint_type='NAVAID'``; ``WaypointIndex.lookup`` keys on
    (id, region) regardless of type, so ``parse_sid``/``parse_star`` resolve them.
    """
    rows: list[WaypointRow] = []
    for ln in iter_lines(source):
        if not is_navaid(ln):
            continue
        ident = ln[slice(*_NAV_ID)].strip()
        if not ident:
            continue
        coords = _navaid_coords(ln)
        if not coords:
            continue
        try:
            lat = cifp_packed_to_decimal(coords[0])
            lon = cifp_packed_to_decimal(coords[1])
        except (ValueError, Exception):
            continue
        rows.append(
            WaypointRow(
                waypoint_id=ident,
                region=ln[slice(*_NAV_REGION)].strip(),
                lat=canonical(lat),
                lon=canonical(lon),
                source=source_db,
                airac=airac,
                waypoint_type="NAVAID",
            )
        )
    return rows


# ── runway threshold records → index rows ────────────────────────────────────
def parse_runway_thresholds(source, airac: str, *,
                            source_db: str = "NASR_CIFP") -> list[WaypointRow]:
    """Parse airport runway records (section 'P' subsection 'G') into index rows.

    Approach missed-approach points reference the runway threshold as ``RWxx``.
    These idents are only unique *per airport* (every field has an ``RW06L``), so
    rows are keyed by the airport ICAO in the ``region`` column and resolved via
    the procedure's airport in ``_leg_from_line``. The threshold identifier and
    packed coordinates share the same columns as terminal waypoints.
    """
    rows: list[WaypointRow] = []
    for ln in iter_lines(source):
        if not is_runway_record(ln):
            continue
        ident = _f(ln, "wp_id")            # 'RW06L'
        airport = _f(ln, "airport")        # keyed per-airport, not per-region
        lat_raw, lon_raw = _f(ln, "wp_lat"), _f(ln, "wp_lon")
        if not (ident and airport and lat_raw and lon_raw):
            continue
        try:
            lat = cifp_packed_to_decimal(lat_raw)
            lon = cifp_packed_to_decimal(lon_raw)
        except (ValueError, Exception):
            continue
        rows.append(
            WaypointRow(
                waypoint_id=ident,
                region=airport,
                lat=canonical(lat),
                lon=canonical(lon),
                source=source_db,
                airac=airac,
                waypoint_type="RUNWAY",
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


def _leg_from_line(line: str, index, airport: str = "") -> ProcedureLeg:
    fix_id = _f(line, "fix_id")
    waypoint = None
    if fix_id:
        if _f(line, "fix_subsection") == "G" and airport:
            # Runway threshold (RWxx) — not region-unique, so keyed per-airport.
            waypoint = index.lookup(fix_id, airport)
        else:
            waypoint = index.lookup(fix_id, _f(line, "fix_region") or None)
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
    Assemble a SIDRecord from coded SID records for ``airport`` + ``procedure``.
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
        if not _f(ln, "path_term"):
            continue  # continuation record (extra data on a leg), not a leg itself
        category = _route_category(_f(ln, "route_type"))
        trans = _f(ln, "transition_id")
        leg = _leg_from_line(ln, index, airport)
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
        source_url=source_url or "ARINC424",
        coverage_source="miner",
        extracted_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )


# ── STAR ─────────────────────────────────────────────────────────────────────
# STAR route types are direction-reversed from SIDs: the enroute (arrival)
# transition comes first, then the common body, then the runway transition.
_STAR_ENROUTE = set("147F")
_STAR_COMMON = set("258M")
_STAR_RUNWAY = set("369")


def _star_category(rt: str) -> str:
    if rt in _STAR_ENROUTE:
        return "enroute"
    if rt in _STAR_RUNWAY:
        return "runway"
    return "common"


def parse_star(source, airport: str, procedure: str, airac: str, index,
               source_url: str = "") -> STARRecord:
    """Assemble a STARRecord from coded STAR records (section P subsection E)."""
    rwy: dict[str, list[ProcedureLeg]] = {}
    common: list[ProcedureLeg] = []
    enr: dict[str, list[ProcedureLeg]] = {}

    for ln in iter_lines(source):
        if not is_star_record(ln):
            continue
        if _f(ln, "airport") != airport or _f(ln, "proc_id") != procedure:
            continue
        if not _f(ln, "path_term"):
            continue  # continuation record, not a leg
        category = _star_category(_f(ln, "route_type"))
        trans = _f(ln, "transition_id")
        leg = _leg_from_line(ln, index, airport)
        if category == "runway":
            rwy.setdefault(trans, []).append(leg)
        elif category == "enroute":
            enr.setdefault(trans, []).append(leg)
        else:
            common.append(leg)

    return STARRecord(
        airport_icao=airport,
        procedure_name=procedure,
        enroute_transitions=[
            EnrouteTransition(transition_fix=t, transition_id=f"{procedure}.{t}", legs=legs)
            for t, legs in enr.items()
        ],
        common_route=common,
        runway_transitions=[
            RunwayTransition(runway_designator=t, transition_id=f"{procedure}.{t}", legs=legs)
            for t, legs in rwy.items()
        ],
        pbn_nav_spec=None,
        airac_cycle=airac,
        extraction_confidence=1.0,
        source_url=source_url or "ARINC424",
        coverage_source="miner",
        extracted_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )


# ── IAP ──────────────────────────────────────────────────────────────────────
# Approach type from the FAA coded procedure identifier's leading letter.
_APPROACH_TYPE = {
    "I": "ILS", "L": "LOC", "B": "LOC_BC", "X": "LDA", "U": "SDF",
    "R": "RNAV_GNSS", "H": "RNAV_RNP", "P": "RNAV_GNSS",
    "V": "VOR", "S": "VOR", "D": "VOR_DME", "T": "TACAN",
    "N": "NDB", "Q": "NDB_DME",
}
_RWY_RE = re.compile(r"(\d{2}[LRC]?)")
# Waypoint description code byte 4 (col 42): A=IAF, I=IF, F=FAF, M=MAP.
_IAP_MARKER = (42, 43)


def parse_iap(source, airport: str, procedure: str, airac: str, index,
              source_url: str = "") -> IAPRecord:
    """Assemble an IAPRecord from coded approach records (section P subsection F).

    Segmentation uses the ARINC 424 leg description code (byte 4): route-type 'A'
    legs are the approach transitions (initial); the coded final-approach route is
    split into intermediate (up to the FAF), final (FAF through the MAP), and
    missed (after the MAP). If markers are absent the whole route falls to final.
    """
    transitions: list[ProcedureLeg] = []
    main: list[tuple[str, ProcedureLeg]] = []

    for ln in iter_lines(source):
        if not is_iap_record(ln):
            continue
        if _f(ln, "airport") != airport or _f(ln, "proc_id") != procedure:
            continue
        if not _f(ln, "path_term"):
            continue  # continuation record, not a leg
        leg = _leg_from_line(ln, index, airport)
        if _f(ln, "route_type") == "A":
            transitions.append(leg)
        else:
            main.append((ln[slice(*_IAP_MARKER)], leg))

    intermediate: list[ProcedureLeg] = []
    final: list[ProcedureLeg] = []
    missed: list[ProcedureLeg] = []
    map_idx = next((i for i, (m, _) in enumerate(main) if m == "M"), None)
    if map_idx is None:
        final = [lg for _, lg in main]
    else:
        faf_idx = next((i for i, (m, _) in enumerate(main) if m == "F"), 0)
        intermediate = [lg for _, lg in main[:faf_idx]]
        final = [lg for _, lg in main[faf_idx:map_idx + 1]]
        missed = [lg for _, lg in main[map_idx + 1:]]

    rwy_match = _RWY_RE.search(procedure)
    return IAPRecord(
        airport_icao=airport,
        procedure_name=procedure,
        approach_type=_APPROACH_TYPE.get(procedure[:1], "VOR"),
        runway_designator=rwy_match.group(1) if rwy_match else "",
        initial_approach_segments=transitions,
        intermediate_segment=intermediate,
        final_segment=final,
        missed_approach_segment=missed,
        pbn_nav_spec=None,
        airac_cycle=airac,
        extraction_confidence=1.0,
        source_url=source_url or "ARINC424",
        coverage_source="miner",
        extracted_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
