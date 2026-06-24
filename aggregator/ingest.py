"""
Ingest AIXM 5.2 procedure records into the AIRAC-versioned PostGIS store.

Split into a pure, offline-testable mapping and a thin executor:

  * ``record_to_rows(record)`` — typed SID/STAR/IAP record -> plain dict of table
    rows (procedure / legs / waypoints). No database, fully unit-testable.
  * ``init_schema(conn)`` / ``ingest(conn, record)`` — execute against PostgreSQL
    via psycopg (imported lazily so the pure path needs no driver).

Ingest is idempotent per ``(airac, airport, record_type, procedure_name)``: the
existing procedure for that key is replaced (legs cascade), so re-running a cycle
is safe. Coordinates are written both as NUMERIC(11,8) (the lossless canonical
value) and as a 4326 geometry (for spatial queries / GeoJSON export).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from validator.scoring import segmented_legs

SCHEMA_SQL = Path(__file__).resolve().parent / "db" / "schema.sql"


def _segment_str(seg: tuple) -> str:
    return ":".join(str(s) for s in seg)


def _leg_rows(record) -> tuple[list[dict], dict]:
    legs: list[dict] = []
    waypoints: dict[tuple, dict] = {}
    for seg, leg in segmented_legs(record):
        wp = leg.waypoint
        row = {
            "segment": _segment_str(seg),
            "sequence_number": leg.sequence_number,
            "path_terminator": leg.path_terminator,
            "waypoint_id": wp.waypoint_id if wp else None,
            "waypoint_type": wp.waypoint_type if wp else None,
            "source_db": wp.source_db if wp else None,
            "waypoint_flag": leg.waypoint_flag,
            "alt_type": leg.alt_constraint.type if leg.alt_constraint else None,
            "alt_lower_ft": leg.alt_constraint.lower_ft if leg.alt_constraint else None,
            "alt_upper_ft": leg.alt_constraint.upper_ft if leg.alt_constraint else None,
            "speed_type": leg.speed_constraint.type if leg.speed_constraint else None,
            "speed_kt": leg.speed_constraint.value_kt if leg.speed_constraint else None,
            "course_magnetic": leg.course_magnetic,
            "distance_nm": leg.distance_nm,
            "turn_direction": leg.turn_direction,
            "radius_nm": leg.radius_nm,
            "lat": wp.lat if wp else None,
            "lon": wp.lon if wp else None,
        }
        legs.append(row)
        for pt in (wp, leg.center_fix):
            if pt is not None:
                waypoints[(pt.waypoint_id,)] = {
                    "waypoint_id": pt.waypoint_id, "region": "",
                    "source_db": pt.source_db, "lat": pt.lat, "lon": pt.lon,
                }
    return legs, waypoints


def record_to_rows(record, airac: Optional[str] = None) -> dict:
    """Map a typed AIXM 5.2 record to plain table-row dicts (no DB)."""
    airac = airac or record.airac_cycle
    legs, waypoints = _leg_rows(record)
    proc = {
        "airac": airac,
        "airport_icao": record.airport_icao,
        "record_type": record.record_type,
        "procedure_name": record.procedure_name,
        "approach_type": getattr(record, "approach_type", None),
        "runway_designator": getattr(record, "runway_designator", None),
        "pbn_nav_spec": record.pbn_nav_spec,
        "extraction_confidence": record.extraction_confidence,
        "coverage_source": record.coverage_source,
        "source_url": record.source_url,
        "extracted_at": record.extracted_at,
    }
    for w in waypoints.values():
        w["airac"] = airac
    return {"procedure": proc, "legs": legs, "waypoints": list(waypoints.values())}


# ── executor (psycopg) ───────────────────────────────────────────────────────
def connect(dsn: Optional[str] = None):
    """Open a psycopg connection from ``dsn`` or ``$DATABASE_URL``."""
    import os

    import psycopg  # lazy

    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("No DATABASE_URL / dsn provided for the aggregator")
    return psycopg.connect(dsn)


def init_schema(conn) -> None:
    conn.execute(SCHEMA_SQL.read_text())
    conn.commit()


_GEOM = "ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)"


def ingest(conn, record, airac: Optional[str] = None) -> int:
    """Idempotently ingest one record. Returns the procedure id."""
    rows = record_to_rows(record, airac)
    proc, legs, waypoints = rows["procedure"], rows["legs"], rows["waypoints"]
    with conn.transaction():
        cur = conn.cursor()
        for w in waypoints:
            cur.execute(
                f"""INSERT INTO waypoint (airac, waypoint_id, region, source_db, lat, lon, geom)
                    VALUES (%(airac)s, %(waypoint_id)s, %(region)s, %(source_db)s,
                            %(lat)s, %(lon)s, {_GEOM})
                    ON CONFLICT (airac, waypoint_id, region) DO UPDATE SET
                        source_db=excluded.source_db, lat=excluded.lat,
                        lon=excluded.lon, geom=excluded.geom""",
                {**w, "lat": float(w["lat"]), "lon": float(w["lon"])},
            )
        cur.execute(
            """DELETE FROM procedure
               WHERE airac=%(airac)s AND airport_icao=%(airport_icao)s
                 AND record_type=%(record_type)s AND procedure_name=%(procedure_name)s""",
            proc,
        )
        pid = cur.execute(
            """INSERT INTO procedure
                 (airac, airport_icao, record_type, procedure_name, approach_type,
                  runway_designator, pbn_nav_spec, extraction_confidence,
                  coverage_source, source_url, extracted_at)
               VALUES (%(airac)s, %(airport_icao)s, %(record_type)s, %(procedure_name)s,
                  %(approach_type)s, %(runway_designator)s, %(pbn_nav_spec)s,
                  %(extraction_confidence)s, %(coverage_source)s, %(source_url)s,
                  %(extracted_at)s)
               RETURNING id""",
            proc,
        ).fetchone()[0]
        for leg in legs:
            geom = _GEOM if leg["lat"] is not None else "NULL"
            params = dict(leg, procedure_id=pid)
            if leg["lat"] is not None:
                params["lat_f"] = float(leg["lat"])
                params["lon_f"] = float(leg["lon"])
            cur.execute(
                f"""INSERT INTO procedure_leg
                     (procedure_id, segment, sequence_number, path_terminator,
                      waypoint_id, waypoint_type, source_db, waypoint_flag,
                      alt_type, alt_lower_ft, alt_upper_ft, speed_type, speed_kt,
                      course_magnetic, distance_nm, turn_direction, radius_nm,
                      lat, lon, geom)
                   VALUES (%(procedure_id)s, %(segment)s, %(sequence_number)s,
                      %(path_terminator)s, %(waypoint_id)s, %(waypoint_type)s,
                      %(source_db)s, %(waypoint_flag)s, %(alt_type)s, %(alt_lower_ft)s,
                      %(alt_upper_ft)s, %(speed_type)s, %(speed_kt)s, %(course_magnetic)s,
                      %(distance_nm)s, %(turn_direction)s, %(radius_nm)s, %(lat)s, %(lon)s,
                      {('ST_SetSRID(ST_MakePoint(%(lon_f)s, %(lat_f)s), 4326)') if leg['lat'] is not None else 'NULL'})""",
                params,
            )
    return pid
