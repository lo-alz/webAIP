"""
Global waypoint index — the single source of truth for coordinate lookups.

Coordinates are stored as the canonical **8-dp text** string (not REAL/float) so
the SQLite round-trip is lossless and the exact-equality scoring in
``validator/scoring.py`` cannot be defeated by float drift. The public lookup
returns a ``WGS84Point`` carrying ``decimal.Decimal`` coordinates.

Schema (spec §9 build task, with lat/lon as TEXT for losslessness):
    waypoint_id TEXT, region TEXT, lat TEXT, lon TEXT, source TEXT, airac TEXT
    PRIMARY KEY (waypoint_id, region)
    INDEX on waypoint_id
"""
from __future__ import annotations

import decimal
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from miner.schemas import WGS84Point

DEFAULT_DB_PATH = os.environ.get(
    "WAYPOINT_DB_PATH", "data/waypoints/global_waypoint_index.db"
)

_SOURCE_TO_DB = {
    "NASR_CIFP": "NASR_CIFP",
    "EAD": "EAD",
    "NAVCANADA": "NAVCANADA",
    "CAAS_HK": "CAAS_HK",
}


@dataclass(frozen=True)
class WaypointRow:
    waypoint_id: str
    region: str
    lat: str          # canonical 8-dp text
    lon: str          # canonical 8-dp text
    source: str       # e.g. 'NASR_CIFP'
    airac: str        # e.g. '2606'
    waypoint_type: str = "NAMED"


class WaypointIndex:
    """Build and query the SQLite waypoint index."""

    def __init__(self, db_path: str | os.PathLike = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    # ── build ────────────────────────────────────────────────────────────────
    def create(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS waypoints (
                    waypoint_id   TEXT NOT NULL,
                    region        TEXT NOT NULL,
                    lat           TEXT NOT NULL,
                    lon           TEXT NOT NULL,
                    source        TEXT NOT NULL,
                    airac         TEXT NOT NULL,
                    waypoint_type TEXT NOT NULL DEFAULT 'NAMED',
                    PRIMARY KEY (waypoint_id, region)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_wp_id ON waypoints(waypoint_id)"
            )

    def load(self, rows: Iterable[WaypointRow]) -> int:
        """Upsert rows. Returns number of rows written."""
        self.create()
        n = 0
        with sqlite3.connect(self.db_path) as con:
            for r in rows:
                con.execute(
                    """
                    INSERT INTO waypoints
                        (waypoint_id, region, lat, lon, source, airac, waypoint_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(waypoint_id, region) DO UPDATE SET
                        lat=excluded.lat, lon=excluded.lon,
                        source=excluded.source, airac=excluded.airac,
                        waypoint_type=excluded.waypoint_type
                    """,
                    (r.waypoint_id, r.region, r.lat, r.lon, r.source, r.airac, r.waypoint_type),
                )
                n += 1
        return n

    def counts_by_source(self) -> dict[str, int]:
        with sqlite3.connect(self.db_path) as con:
            cur = con.execute(
                "SELECT source, COUNT(*) FROM waypoints GROUP BY source ORDER BY source"
            )
            return {src: cnt for src, cnt in cur.fetchall()}

    # ── query ────────────────────────────────────────────────────────────────
    def lookup(
        self,
        waypoint_id: str,
        region: Optional[str] = None,
        waypoint_type: str = "NAMED",
        source_db: str = "NASR_CIFP",
    ) -> Optional[WGS84Point]:
        """
        Look up a waypoint and return a ``WGS84Point`` with exact Decimal coords,
        or ``None`` if absent. ``region`` disambiguates duplicate identifiers
        across ICAO regions; if omitted, the first match wins.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Waypoint index not found at {self.db_path}. "
                "Run scripts/build_waypoint_index.py first."
            )
        with sqlite3.connect(self.db_path) as con:
            if region:
                cur = con.execute(
                    "SELECT lat, lon, source, waypoint_type FROM waypoints "
                    "WHERE waypoint_id=? AND region=?",
                    (waypoint_id, region),
                )
            else:
                cur = con.execute(
                    "SELECT lat, lon, source, waypoint_type FROM waypoints "
                    "WHERE waypoint_id=? LIMIT 1",
                    (waypoint_id,),
                )
            row = cur.fetchone()
        if not row:
            return None
        lat_txt, lon_txt, source, wtype = row
        return WGS84Point(
            lat=decimal.Decimal(lat_txt),
            lon=decimal.Decimal(lon_txt),
            waypoint_id=waypoint_id,
            waypoint_type=wtype or waypoint_type,
            source_db=_SOURCE_TO_DB.get(source, source_db),
        )
