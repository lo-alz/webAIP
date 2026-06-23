#!/usr/bin/env python3
"""
Scan the global ICAO IFR airport universe and aggregate by country.

Spine: OurAirports open data (airports.csv, runways.csv, countries.csv).
Procedure counts (SID/STAR/APP) are real for the **US** from the FAA CIFP
(FAACIFP18) when present; for other countries they are null until that country's
parser exists (which is exactly what the AIPhunter parsing-difficulty table
prioritises).

Outputs:
  data/airports.json                  per-airport rows (ICAO, country, runways,
                                       and US SID/STAR/APP counts)
  dashboard/airports_by_country.json  per-country aggregates (the linked table)

Usage: python scripts/scan_airports.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data"
CACHE = ROOT / "data" / "ourairports"
CIFP = Path(os.environ.get("NASR_DATA_DIR", ROOT / "data" / "nasr")) / "FAACIFP18"

ICAO_RE = re.compile(r"^[A-Z]{4}$")
# "IFR airport" = an ICAO-coded field with published instrument procedures. We
# classify this ONLY from real procedure data, never from runway geometry. Today
# that data exists for the US NAS (FAA CIFP); other countries' IFR status is
# unknown until their parser runs, so their counts fall back to all ICAO airports.
CIFP_ISO = {"US", "PR", "VI", "GU", "MP", "AS"}  # FAA CIFP coverage (exact procedures)


def fetch_csv(name: str) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    local = CACHE / name
    if not local.exists():
        url = f"{OURAIRPORTS}/{name}"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                local.write_bytes(r.read())
            print(f"  downloaded {name}")
        except Exception as e:
            print(f"  ! could not download {name}: {e}", file=sys.stderr)
            return []
    with open(local, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def us_procedure_counts() -> tuple[dict, dict]:
    """Count distinct SID/STAR/APP procedures per US airport from the CIFP."""
    per_airport = defaultdict(lambda: {"sids": set(), "stars": set(), "apps": set()})
    if not CIFP.exists():
        print(f"  (no CIFP at {CIFP} — US procedure counts skipped)")
        return {}, {}
    sub_map = {"D": "sids", "E": "stars", "F": "apps"}
    with open(CIFP, encoding="latin-1") as fh:
        for ln in fh:
            if len(ln) < 19 or ln[4:5] != "P":
                continue
            sub = ln[12:13]
            if sub not in sub_map:
                continue
            apt = ln[6:10].strip()
            proc = ln[13:19].strip()
            if apt and proc:
                per_airport[apt][sub_map[sub]].add(proc)
    out = {a: {k: len(v) for k, v in d.items()} for a, d in per_airport.items()}
    print(f"  CIFP: procedures for {len(out)} US airports")
    return out, sub_map


def main() -> int:
    print("Loading OurAirports data...")
    airports = fetch_csv("airports.csv")
    runways = fetch_csv("runways.csv")
    countries = fetch_csv("countries.csv")
    if not airports:
        print("No airport data — aborting.", file=sys.stderr)
        return 2

    cname = {c["code"]: c["name"] for c in countries}

    # runway counts per airport ident (exclude closed runways)
    rwy_count = Counter()
    for r in runways:
        if r.get("closed", "0") in ("1", "true", "True"):
            continue
        ident = r.get("airport_ident", "").strip()
        if ident:
            rwy_count[ident] += 1

    us_proc, _ = us_procedure_counts()

    per_airport = []
    by_country = defaultdict(lambda: {
        "airports": 0, "ifr_airports": 0, "runways": 0, "ifr_runways": 0,
        "sids": 0, "stars": 0, "apps": 0, "prefixes": Counter(),
        "procedures_known": False,
    })

    for a in airports:
        ident = a.get("ident", "").strip().upper()
        if not ICAO_RE.match(ident):
            continue
        atype = a.get("type", "")
        if atype in ("closed",):
            continue
        iso = a.get("iso_country", "").strip() or "??"
        rwys = rwy_count.get(ident, 0)
        procs = us_proc.get(ident)
        # IFR status from procedure data only: known for the US NAS (CIFP),
        # unknown (None) elsewhere until that country is parsed.
        ifr = (procs is not None) if iso in CIFP_ISO else None

        row = {
            "icao": ident, "name": a.get("name", ""), "iso_country": iso,
            "type": atype, "runways": rwys, "ifr": ifr,
            "sids": procs["sids"] if procs else None,
            "stars": procs["stars"] if procs else None,
            "apps": procs["apps"] if procs else None,
        }
        per_airport.append(row)

        c = by_country[iso]
        c["airports"] += 1
        c["ifr_airports"] += 1 if ifr else 0
        c["runways"] += rwys
        c["ifr_runways"] += rwys if ifr else 0
        c["prefixes"][ident[:2]] += 1
        # CIFP is the authoritative full procedure source for the US NAS only;
        # a handful of border idents leak into it, so attribute country-level
        # totals to the US alone (per-airport counts above stay accurate).
        if procs and iso == "US":
            c["procedures_known"] = True
            c["sids"] += procs["sids"]
            c["stars"] += procs["stars"]
            c["apps"] += procs["apps"]

    # finalise country rows
    country_rows = []
    for iso, c in by_country.items():
        top_prefixes = [p for p, _ in c["prefixes"].most_common(4)]
        country_rows.append({
            "iso_country": iso,
            "country": cname.get(iso, iso),
            "icao_prefixes": top_prefixes,
            "airports": c["airports"],
            "ifr_known": iso in CIFP_ISO,
            "ifr_airports": c["ifr_airports"] if iso in CIFP_ISO else None,
            "runways": c["runways"],
            "ifr_runways": c["ifr_runways"] if iso in CIFP_ISO else None,
            "sids": c["sids"] if c["procedures_known"] else None,
            "stars": c["stars"] if c["procedures_known"] else None,
            "apps": c["apps"] if c["procedures_known"] else None,
            "procedures_source": "FAA_CIFP" if c["procedures_known"] else None,
        })
    country_rows.sort(key=lambda r: (-r["airports"], r["country"]))

    (ROOT / "data" / "airports.json").write_text(json.dumps(per_airport, indent=0))
    (ROOT / "dashboard" / "airports_by_country.json").write_text(
        json.dumps({"generated_from": "OurAirports + FAA CIFP (US)",
                    "countries": country_rows}, indent=2))

    tot_ifr = sum(r["ifr_airports"] or 0 for r in country_rows)
    print(f"\n{len(per_airport)} ICAO airports across {len(country_rows)} countries; "
          f"{tot_ifr} with known IFR procedures (US/CIFP) — other countries pending.")
    print("Top 12 by ICAO airport count:")
    for r in country_rows[:12]:
        ifr = r["ifr_airports"]
        ifr_s = str(ifr) if ifr is not None else "pend"
        p = f" SID/STAR/APP={r['sids']}/{r['stars']}/{r['apps']}" if r["sids"] is not None else ""
        print(f"  {r['iso_country']:3} {r['country'][:26]:26} all={r['airports']:5} "
              f"ifr={ifr_s:>5} rwy={r['runways']:5}{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
