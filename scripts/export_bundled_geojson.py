#!/usr/bin/env python3
"""
Export procedures from committed airport CIFP slices as BUNDLED viewer GeoJSON.

The CesiumJS viewer normally reads `/procedures/{id}/geojson` from the live
aggregator API. This script produces the SAME FeatureCollection shape from a
committed fixture (data/fixtures/<icao>_cifp.dat), written to
``visualization/cesium/bundled/`` next to a small ``index.json`` manifest — so
the static viewer (GitHub Pages / Vercel) can list and fly those procedures
with no backend at all.

Usage:
    python scripts/export_bundled_geojson.py KJFK:PUCKY1 KJFK:SKORR6 KJFK:I22L

Requires the waypoint index (scripts/build_waypoint_index.py) so leg fixes
resolve to their authoritative coordinates.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aggregator.ingest import record_to_rows  # noqa: E402  (pure, no DB)
from miner.extractor.arinc424 import (  # noqa: E402
    is_iap_record, is_sid_record, is_star_record, _f,
    parse_iap, parse_sid, parse_star,
)
from miner.waypoint_db import WaypointIndex  # noqa: E402

FIXTURE_DIR = ROOT / "data" / "fixtures"
OUT_DIR = ROOT / "visualization" / "cesium" / "bundled"
_FT_TO_M = 0.3048  # same constant as the API

CLASSES = [
    ("SID", is_sid_record, parse_sid),
    ("STAR", is_star_record, parse_star),
    ("IAP", is_iap_record, parse_iap),
]


def _geojson(record) -> dict:
    """Exactly the read API's FeatureCollection: 3D fixes + the route line,
    ordered by (segment, sequence_number), coordinate-bearing legs only."""
    legs = record_to_rows(record)["legs"]
    pts = sorted((l for l in legs if l["lat"] is not None and l["lon"] is not None),
                 key=lambda l: (l["segment"], l["sequence_number"]))

    def coords(l):
        return [float(l["lon"]), float(l["lat"]), (l["alt_lower_ft"] or 0) * _FT_TO_M]

    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": coords(l)},
        "properties": {"waypoint_id": l["waypoint_id"], "segment": l["segment"],
                       "sequence_number": l["sequence_number"],
                       "path_terminator": l["path_terminator"],
                       "alt_type": l["alt_type"], "alt_lower_ft": l["alt_lower_ft"]},
    } for l in pts]
    if len(pts) >= 2:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [coords(l) for l in pts]},
            "properties": {"role": "route"},
        })
    return {"type": "FeatureCollection", "features": features}


def export_one(icao: str, proc: str, index: WaypointIndex) -> dict:
    fixture = FIXTURE_DIR / f"{icao.lower()}_cifp.dat"
    if not fixture.exists():
        raise FileNotFoundError(f"{fixture} — run scripts/fetch_airport_cifp.py first")
    lines = [ln.rstrip("\n") for ln in
             fixture.read_text(encoding="latin-1").splitlines() if ln.strip()]

    for rec_type, pred, parser in CLASSES:
        if any(pred(ln) and _f(ln, "proc_id") == proc for ln in lines):
            record = parser(lines, icao.upper(), proc, "bundle", index)
            gj = _geojson(record)
            n_pts = sum(1 for f in gj["features"] if f["geometry"]["type"] == "Point")
            if n_pts == 0:
                raise ValueError(f"{icao} {proc}: no coordinate-bearing legs")
            fname = f"{icao.lower()}_{proc.lower()}.geojson"
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / fname).write_text(json.dumps(gj, indent=1))
            print(f"  {icao.upper()} {proc} ({rec_type}): {n_pts} fixes -> bundled/{fname}")
            return {"icao": icao.upper(), "record_type": rec_type,
                    "procedure_name": proc, "file": fname}
    raise ValueError(f"{icao} {proc}: not found in {fixture.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bundle procedures for the static viewer")
    ap.add_argument("procs", nargs="+", metavar="ICAO:PROC",
                    help="e.g. KJFK:PUCKY1 KJFK:SKORR6 KJFK:I22L")
    args = ap.parse_args()

    index = WaypointIndex()
    manifest_path = OUT_DIR / "index.json"
    entries: list[dict] = []
    if manifest_path.exists():
        entries = json.loads(manifest_path.read_text())

    for spec in args.procs:
        icao, _, proc = spec.partition(":")
        if not proc:
            print(f"error: bad spec {spec!r} (want ICAO:PROC)", file=sys.stderr)
            return 2
        entry = export_one(icao, proc.upper(), index)
        entries = [e for e in entries
                   if not (e["icao"] == entry["icao"]
                           and e["procedure_name"] == entry["procedure_name"])]
        entries.append(entry)

    entries.sort(key=lambda e: (e["icao"], e["record_type"], e["procedure_name"]))
    manifest_path.write_text(json.dumps(entries, indent=1))
    print(f"  manifest: bundled/index.json ({len(entries)} procedures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
