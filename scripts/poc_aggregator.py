#!/usr/bin/env python3
"""
Pilot — Stage 4 PostGIS aggregator, end to end.

Builds three AIXM 5.2 SID records across three parser tiers (EGLL via AIXM
up-convert, VHHH via eAIP HTML, KLAX via ARINC 424 CIFP), ingests them into the
AIRAC-versioned PostGIS store, then queries them back — including a spatial
ST_AsText and a GeoJSON-style route — to prove the structured store works.

Connection from ``$DATABASE_URL`` (falls back to the local dev cluster). Run:
    DATABASE_URL=postgresql://postgres@127.0.0.1:5433/aeroaip python scripts/poc_aggregator.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aggregator.ingest import connect, ingest, init_schema  # noqa: E402
from miner.assemble import build_sid_from_extraction  # noqa: E402
from miner.extractor import aixm_upconverter as aixm  # noqa: E402
from miner.extractor.arinc424 import parse_sid, parse_waypoints  # noqa: E402
from miner.extractor.eaip_html import extract as extract_html  # noqa: E402
from miner.waypoint_db import WaypointIndex  # noqa: E402

FIX = Path("data/fixtures")
AIRAC = "2607"
DEFAULT_DSN = "postgresql://postgres@127.0.0.1:5433/aeroaip"


def _records(db):
    egll = aixm.to_ground_truth(FIX / "egll_det2j.aixm51.xml", AIRAC)
    vhhh = build_sid_from_extraction(
        extract_html(FIX / "vhhh_ocea1a.eaip.html"), db, AIRAC,
        on_missing="consensus", source_url="CAAS HK eAIP")
    klax = parse_sid(FIX / "klax_dotss2_pd.dat", "KLAX", "DOTSS2", AIRAC, db)
    return [egll, vhhh, klax]


def main() -> int:
    os.environ.setdefault("DATABASE_URL", DEFAULT_DSN)
    print("=" * 70)
    print("AeroAIP pilot — Stage 4 PostGIS aggregator (EGLL + VHHH + KLAX)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as td:
        db = WaypointIndex(Path(td) / "wp.db")
        db.load(aixm.parse_waypoints(FIX / "egll_det2j.aixm51.xml", AIRAC, source_db="EAD"))
        db.load(aixm.parse_waypoints(FIX / "vhhh_waypoints.aixm.xml", AIRAC, source_db="CAAS_HK"))
        db.load(parse_waypoints(FIX / "klax_cifp_waypoints.dat", AIRAC))

        try:
            conn = connect()
        except Exception as e:  # noqa: BLE001
            print(f"\n✗ Could not connect to PostGIS ({e}).")
            print("  Set DATABASE_URL to a PostGIS instance and retry.")
            return 2

        with conn:
            init_schema(conn)
            print(f"\n[1] Schema initialised on {conn.info.dbname}")

            ids = {}
            for rec in _records(db):
                pid = ingest(conn, rec, AIRAC)
                ids[rec.airport_icao] = pid
            print(f"[2] Ingested {len(ids)} procedures: "
                  + ", ".join(f"{k}={v}" for k, v in ids.items()))

            n_proc = conn.execute("SELECT count(*) FROM procedure WHERE airac=%s",
                                  (AIRAC,)).fetchone()[0]
            n_legs = conn.execute(
                "SELECT count(*) FROM procedure_leg pl JOIN procedure p ON p.id=pl.procedure_id "
                "WHERE p.airac=%s", (AIRAC,)).fetchone()[0]
            n_wp = conn.execute("SELECT count(*) FROM waypoint WHERE airac=%s",
                                (AIRAC,)).fetchone()[0]
            print(f"[3] Stored: {n_proc} procedures, {n_legs} legs, {n_wp} waypoints")

            # Spatial read-back: first EGLL fix as WKT.
            wkt = conn.execute(
                """SELECT pl.waypoint_id, ST_AsText(pl.geom) AS wkt
                   FROM procedure_leg pl JOIN procedure p ON p.id=pl.procedure_id
                   WHERE p.airport_icao='EGLL' AND pl.geom IS NOT NULL
                   ORDER BY pl.segment, pl.sequence_number LIMIT 1""").fetchone()
            print(f"[4] Spatial read-back (EGLL first fix): {wkt[0]} -> {wkt[1]}")

            # Idempotency: re-ingest EGLL, expect the same count.
            ingest(conn, _records(db)[0], AIRAC)
            n_proc2 = conn.execute("SELECT count(*) FROM procedure WHERE airac=%s",
                                   (AIRAC,)).fetchone()[0]
            ok = n_proc2 == n_proc
            print(f"[5] Idempotent re-ingest: {n_proc} -> {n_proc2} "
                  f"({'stable ✓' if ok else 'DUPLICATED ✗'})")

        print("\n" + "=" * 70)
        print("RESULT:", "✓ PASS — records ingested, spatial query works, idempotent"
              if ok else "✗ FAIL")
        print("=" * 70)
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
