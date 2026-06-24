"""
AeroAIP aggregator read API (FastAPI) over the AIRAC-versioned PostGIS store.

Endpoints (all cycle-scoped where relevant):
  GET /health                              liveness + postgis version
  GET /airports?airac=                     airports with procedure counts
  GET /procedures?airport=&airac=&type=    procedure list
  GET /procedures/{id}                     procedure + ordered legs (with coords)
  GET /procedures/{id}/geojson             FeatureCollection (fixes + route line)
                                           — ready for CesiumJS / Google Earth

Connection comes from ``$DATABASE_URL``. Run:
    DATABASE_URL=postgresql://... uvicorn aggregator.api.main:app --port 8080
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException

app = FastAPI(title="AeroAIP Aggregator", version="0.1")


def _conn():
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise HTTPException(503, "DATABASE_URL not configured")
    return psycopg.connect(dsn, row_factory=dict_row)


@app.get("/health")
def health():
    with _conn() as c:
        v = c.execute("SELECT postgis_version() AS v").fetchone()["v"]
    return {"status": "ok", "postgis": v}


@app.get("/airports")
def airports(airac: Optional[str] = None):
    sql = ("SELECT airport_icao, airac, count(*) AS procedures "
           "FROM procedure {where} GROUP BY airport_icao, airac "
           "ORDER BY airport_icao, airac")
    where, params = ("", {})
    if airac:
        where, params = ("WHERE airac=%(airac)s", {"airac": airac})
    with _conn() as c:
        return c.execute(sql.format(where=where), params).fetchall()


@app.get("/procedures")
def procedures(airport: Optional[str] = None, airac: Optional[str] = None,
               type: Optional[str] = None):
    clauses, params = [], {}
    if airport:
        clauses.append("airport_icao=%(airport)s"); params["airport"] = airport.upper()
    if airac:
        clauses.append("airac=%(airac)s"); params["airac"] = airac
    if type:
        clauses.append("record_type=%(type)s"); params["type"] = type.upper()
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c:
        return c.execute(
            f"""SELECT id, airac, airport_icao, record_type, procedure_name,
                       approach_type, runway_designator, pbn_nav_spec,
                       extraction_confidence, coverage_source
                FROM procedure {where}
                ORDER BY airport_icao, record_type, procedure_name""",
            params,
        ).fetchall()


def _procedure_or_404(c, pid: int) -> dict:
    proc = c.execute("SELECT * FROM procedure WHERE id=%s", (pid,)).fetchone()
    if not proc:
        raise HTTPException(404, f"procedure {pid} not found")
    return proc


@app.get("/procedures/{pid}")
def procedure_detail(pid: int):
    with _conn() as c:
        proc = _procedure_or_404(c, pid)
        legs = c.execute(
            """SELECT segment, sequence_number, path_terminator, waypoint_id,
                      source_db, waypoint_flag, alt_type, alt_lower_ft, alt_upper_ft,
                      speed_type, speed_kt, course_magnetic, distance_nm,
                      turn_direction, lat, lon
               FROM procedure_leg WHERE procedure_id=%s
               ORDER BY segment, sequence_number""",
            (pid,),
        ).fetchall()
    return {"procedure": proc, "legs": legs}


@app.get("/procedures/{pid}/geojson")
def procedure_geojson(pid: int):
    """Fixes as Point features + the ordered route as a LineString."""
    with _conn() as c:
        _procedure_or_404(c, pid)
        pts = c.execute(
            """SELECT waypoint_id, segment, sequence_number,
                      ST_X(geom) AS lon, ST_Y(geom) AS lat
               FROM procedure_leg
               WHERE procedure_id=%s AND geom IS NOT NULL
               ORDER BY segment, sequence_number""",
            (pid,),
        ).fetchall()
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
        "properties": {"waypoint_id": p["waypoint_id"], "segment": p["segment"],
                       "sequence_number": p["sequence_number"]},
    } for p in pts]
    if len(pts) >= 2:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[p["lon"], p["lat"]] for p in pts]},
            "properties": {"role": "route"},
        })
    return {"type": "FeatureCollection", "features": features}
