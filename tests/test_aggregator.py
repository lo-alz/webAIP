"""
Stage 4 aggregator (aggregator/ingest.py + aggregator/api/main.py).

``record_to_rows`` is tested offline (no DB). The ingest + API tests are gated on
``DATABASE_URL`` pointing at a reachable PostGIS, and skip otherwise — so the
suite stays green in environments without a database.
"""
import os
from pathlib import Path

import pytest

from aggregator.ingest import init_schema, ingest, record_to_rows
from miner.extractor import aixm_upconverter as aixm

EGLL = Path("data/fixtures/egll_det2j.aixm51.xml")
TEST_AIRAC = "TST0"


# ── offline: the pure mapping ────────────────────────────────────────────────
def test_record_to_rows_shapes():
    rows = record_to_rows(aixm.to_ground_truth(EGLL, "2607"))
    proc = rows["procedure"]
    assert proc["airport_icao"] == "EGLL"
    assert proc["record_type"] == "SID"
    assert proc["procedure_name"] == "DET2J"
    assert len(rows["legs"]) == 4
    assert all(leg["segment"] == "RWY:09R" for leg in rows["legs"])
    assert {w["waypoint_id"] for w in rows["waypoints"]} == {"OCK", "BIG", "DET"}


def test_record_to_rows_heading_leg_has_no_coords():
    rows = record_to_rows(aixm.to_ground_truth(EGLL, "2607"))
    va = [leg for leg in rows["legs"] if leg["path_terminator"] == "VA"]
    assert va and all(leg["lat"] is None and leg["waypoint_id"] is None for leg in va)
    named = [leg for leg in rows["legs"] if leg["waypoint_id"]]
    assert named and all(leg["lat"] is not None for leg in named)


# ── live: PostGIS round-trip (gated) ─────────────────────────────────────────
@pytest.fixture()
def pg():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set — skipping live PostGIS tests")
    try:
        import psycopg
        conn = psycopg.connect(dsn)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PostGIS unreachable: {e}")
    init_schema(conn)
    conn.execute("DELETE FROM procedure WHERE airac=%s", (TEST_AIRAC,))
    conn.execute("DELETE FROM waypoint WHERE airac=%s", (TEST_AIRAC,))
    conn.commit()
    yield conn
    conn.execute("DELETE FROM procedure WHERE airac=%s", (TEST_AIRAC,))
    conn.execute("DELETE FROM waypoint WHERE airac=%s", (TEST_AIRAC,))
    conn.commit()
    conn.close()


def test_ingest_round_trip(pg):
    rec = aixm.to_ground_truth(EGLL, TEST_AIRAC)
    pid = ingest(pg, rec, TEST_AIRAC)
    n_legs = pg.execute("SELECT count(*) FROM procedure_leg WHERE procedure_id=%s",
                        (pid,)).fetchone()[0]
    assert n_legs == 4
    wkt = pg.execute(
        "SELECT ST_AsText(geom) FROM procedure_leg "
        "WHERE procedure_id=%s AND geom IS NOT NULL ORDER BY sequence_number LIMIT 1",
        (pid,)).fetchone()[0]
    assert wkt.startswith("POINT")


def test_ingest_is_idempotent(pg):
    rec = aixm.to_ground_truth(EGLL, TEST_AIRAC)
    ingest(pg, rec, TEST_AIRAC)
    ingest(pg, rec, TEST_AIRAC)
    n = pg.execute("SELECT count(*) FROM procedure WHERE airac=%s",
                   (TEST_AIRAC,)).fetchone()[0]
    assert n == 1


def test_api_geojson(pg):
    from fastapi.testclient import TestClient

    from aggregator.api.main import app

    pid = ingest(pg, aixm.to_ground_truth(EGLL, TEST_AIRAC), TEST_AIRAC)
    client = TestClient(app)

    assert client.get("/health").json()["status"] == "ok"
    listed = client.get("/procedures", params={"airac": TEST_AIRAC}).json()
    assert any(p["id"] == pid for p in listed)

    gj = client.get(f"/procedures/{pid}/geojson").json()
    assert gj["type"] == "FeatureCollection"
    assert any(f["geometry"]["type"] == "LineString" for f in gj["features"])
    assert any(f["geometry"]["type"] == "Point" for f in gj["features"])
