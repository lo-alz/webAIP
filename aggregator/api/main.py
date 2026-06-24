"""
AeroAIP aggregator read API (FastAPI) over the AIRAC-versioned PostGIS store.

Endpoints (all cycle-scoped where relevant):
  GET /health                              liveness + postgis version
  GET /airports?airac=                     airports with procedure counts
  GET /procedures?airport=&airac=&type=    procedure list
  GET /procedures/{id}                     procedure + ordered legs (with coords)
  GET /procedures/{id}/geojson             FeatureCollection (fixes + route line)
                                           — ready for CesiumJS
  GET /procedures/{id}/kml                  KML download (static path + gx:Track)
                                           — opens in Google Earth

Static front-ends mounted same-origin (when present):
  /viz/        CesiumJS 3D procedure viewer
  /dashboard/  project checklist + Gantt + parsing-difficulty pipeline view

Connection comes from ``$DATABASE_URL``. Run:
    DATABASE_URL=postgresql://... uvicorn aggregator.api.main:app --port 8080
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from visualization.kml_export import procedure_to_kml

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

# Serve the static front-ends same-origin as the API (so their fetch() calls
# need no CORS): the CesiumJS viewer at /viz and the project dashboard at
# /dashboard — http://<host>/viz/?pid=<id> and http://<host>/dashboard/
_REPO = Path(__file__).resolve().parents[2]
for _route, _dir in (("/viz", _REPO / "visualization" / "cesium"),
                     ("/dashboard", _REPO / "dashboard")):
    if _dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount(_route, StaticFiles(directory=str(_dir), html=True),
                  name=_route.strip("/"))


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


def _route_legs(c, pid: int) -> list[dict]:
    """Coordinate-bearing legs for a procedure, ordered — shared by geojson + kml."""
    return c.execute(
        """SELECT waypoint_id, segment, sequence_number, path_terminator,
                  alt_type, alt_lower_ft,
                  ST_X(geom) AS lon, ST_Y(geom) AS lat
           FROM procedure_leg
           WHERE procedure_id=%s AND geom IS NOT NULL
           ORDER BY segment, sequence_number""",
        (pid,),
    ).fetchall()


@app.get("/procedures/{pid}/geojson")
def procedure_geojson(pid: int):
    """Fixes as 3D Point features + the ordered route as a 3D LineString.

    Coordinates are ``[lon, lat, alt_m]`` where ``alt_m`` is the leg's lower
    altitude constraint (feet MSL → metres), or 0 when unconstrained — so a
    CesiumJS client renders the procedure as a climbing/descending path.
    """
    with _conn() as c:
        _procedure_or_404(c, pid)
        pts = _route_legs(c, pid)

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


@app.get("/procedures/{pid}/kml")
def procedure_kml(pid: int):
    """KML download: static extruded 3D path + a gx:Track flythrough (Google Earth)."""
    with _conn() as c:
        proc = _procedure_or_404(c, pid)
        legs = _route_legs(c, pid)
    kml = procedure_to_kml(proc, legs)
    fname = "_".join(str(proc.get(k) or "").strip().replace(" ", "")
                     for k in ("airport_icao", "procedure_name")) or f"procedure_{pid}"
    return Response(
        content=kml,
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}.kml"'},
    )
