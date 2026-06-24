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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException

app = FastAPI(title="AeroAIP Aggregator", version="0.1")

# Feet → metres: GeoJSON/Cesium heights are metres, ARINC 424 altitudes are feet MSL.
_FT_TO_M = 0.3048

# Dev convenience: allow the static viewer (or any origin) to call the read API
# from the browser. Permissive on purpose — this is a read-only public dataset.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)

# Serve the CesiumJS viewer same-origin as the API at /viz (so no CORS needed
# for the page itself): http://<host>/viz/cesium/?pid=<id>
_VIZ_DIR = Path(__file__).resolve().parents[2] / "visualization"
if _VIZ_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/viz", StaticFiles(directory=str(_VIZ_DIR), html=True), name="viz")


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
    """Fixes as 3D Point features + the ordered route as a 3D LineString.

    Coordinates are ``[lon, lat, alt_m]`` where ``alt_m`` is the leg's lower
    altitude constraint (feet MSL → metres), or 0 when unconstrained — so a
    CesiumJS client renders the procedure as a climbing/descending path.
    """
    with _conn() as c:
        _procedure_or_404(c, pid)
        pts = c.execute(
            """SELECT waypoint_id, segment, sequence_number, path_terminator,
                      alt_type, alt_lower_ft,
                      ST_X(geom) AS lon, ST_Y(geom) AS lat
               FROM procedure_leg
               WHERE procedure_id=%s AND geom IS NOT NULL
               ORDER BY segment, sequence_number""",
            (pid,),
        ).fetchall()

    def _coords(p):
        return [p["lon"], p["lat"], (p["alt_lower_ft"] or 0) * _FT_TO_M]

    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": _coords(p)},
        "properties": {"waypoint_id": p["waypoint_id"], "segment": p["segment"],
                       "sequence_number": p["sequence_number"],
                       "path_terminator": p["path_terminator"],
                       "alt_type": p["alt_type"], "alt_lower_ft": p["alt_lower_ft"]},
    } for p in pts]
    if len(pts) >= 2:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [_coords(p) for p in pts]},
            "properties": {"role": "route"},
        })
    return {"type": "FeatureCollection", "features": features}
