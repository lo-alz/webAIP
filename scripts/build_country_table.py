#!/usr/bin/env python3
"""
Build the country parsing-difficulty table the dashboard "Pipeline" tab reads.

Merges:
  dashboard/airports_by_country.json   (counts: airports / IFR / runways / US procs)
  data/aip_sources_seed.json           (AIPhunter research: source, AIXM, score 1-10)

into:
  dashboard/countries.json             (one linked row per country)

The seed is scaffolded for every country found in the scan (default
"unresearched"); AIPhunter fills entries over time. Pass research batches with
--merge FILE (a JSON array of per-country objects keyed by "iso") to fold agent
output into the seed before rebuilding.

Usage:
  python scripts/build_country_table.py [--merge batchA.json --merge batchB.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "dashboard" / "airports_by_country.json"
SEED = ROOT / "data" / "aip_sources_seed.json"
OUT = ROOT / "dashboard" / "countries.json"

SEED_FIELDS = ["icao_prefix", "aip_authority", "aip_url", "source_type",
               "aixm_version", "access", "parsing_score", "confidence", "notes"]

RUBRIC = ("1=AIXM5.2 open · 2=AIXM5.1/4.5 or open ARINC424 · 3=open structured/clean eAIP · "
          "4=open eAIP irregular · 5=eAIP free-account (EAD) · 6=open vector PDF · "
          "7=PDF portal/account · 8=scanned PDF(OCR) or account+agreement · "
          "9=restricted/paid · 10=paper/none (+1 for portal/scale friction)")


def default_entry(scan_row: dict) -> dict:
    return {
        "icao_prefix": ",".join(scan_row.get("icao_prefixes", [])[:1]) or "",
        "aip_authority": None,
        "aip_url": None,
        "source_type": "unknown",
        "aixm_version": "nil",
        "access": "unknown",
        "parsing_score": None,
        "confidence": "none",
        "notes": "unresearched — run AIPhunter",
    }


def load_json(path: Path, fallback):
    if path.exists():
        return json.loads(path.read_text())
    return fallback


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="append", default=[],
                    help="JSON array of per-country research objects to fold into the seed")
    args = ap.parse_args()

    scan = load_json(SCAN, {"countries": []})["countries"]
    seed = load_json(SEED, {"rubric": RUBRIC, "countries": {}})
    seed.setdefault("countries", {})
    seed["rubric"] = RUBRIC

    # fold in research batches
    folded = 0
    for mf in args.merge:
        data = json.loads(Path(mf).read_text())
        rows = data if isinstance(data, list) else data.get("countries", [])
        for obj in rows:
            iso = obj.get("iso")
            if not iso:
                continue
            entry = seed["countries"].get(iso, {})
            for k in SEED_FIELDS:
                if k in obj and obj[k] not in (None, ""):
                    entry[k] = obj[k]
            seed["countries"][iso] = entry
            folded += 1

    # scaffold any country present in the scan but missing from the seed
    scaffolded = 0
    for row in scan:
        iso = row["iso_country"]
        if iso not in seed["countries"]:
            seed["countries"][iso] = default_entry(row)
            scaffolded += 1
        elif not seed["countries"][iso].get("icao_prefix"):
            seed["countries"][iso]["icao_prefix"] = ",".join(row.get("icao_prefixes", [])[:1])

    SEED.write_text(json.dumps(seed, indent=2, ensure_ascii=False) + "\n")

    # merge counts + seed -> final table
    rows = []
    for row in scan:
        iso = row["iso_country"]
        s = seed["countries"].get(iso, {})
        merged = {
            "iso": iso,
            "country": row["country"],
            "icao_prefixes": row.get("icao_prefixes", []),
            "airports": row["airports"],
            "ifr_airports": row.get("ifr_airports"),
            "ifr_known": row.get("ifr_known", False),
            "runways": row["runways"],
            "ifr_runways": row.get("ifr_runways"),
            "sids": row.get("sids"),
            "stars": row.get("stars"),
            "apps": row.get("apps"),
            "procedures_source": row.get("procedures_source"),
        }
        for k in SEED_FIELDS:
            merged[k] = s.get(k)
        merged["researched"] = s.get("parsing_score") is not None
        rows.append(merged)

    rows.sort(key=lambda r: (-r["airports"], r["country"]))
    researched = sum(1 for r in rows if r["researched"])
    OUT.write_text(json.dumps({
        "rubric": RUBRIC,
        "researched": researched,
        "total": len(rows),
        "countries": rows,
    }, indent=2, ensure_ascii=False) + "\n")

    print(f"folded {folded} research objects, scaffolded {scaffolded} new countries")
    print(f"countries.json: {len(rows)} countries, {researched} researched, "
          f"{len(rows)-researched} scaffold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
