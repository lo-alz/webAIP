"""
US source adapters for Loop 1 (census), all offline against committed data.

Three independent inputs, three different lineages — which is exactly what makes
the cross-check meaningful:

  * **OurAirports** (community spine) — the *denominator*: which ICAO airports
    exist and how many runways. NOT a procedure authority.
  * **FAA CIFP / FAACIFP18** (authoritative coded) — the procedure *manifest*:
    the distinct SID/STAR/APP procedure identifiers per airport.
  * **FAA d-TPP** (authoritative charts, ``dtpp_current.xml``) — an *independent*
    chart inventory per airport, used to reconcile the CIFP manifest.

CIFP counts coded procedures; d-TPP counts published charts — so the two never
match exactly (KLAX: CIFP 24/24/32 vs d-TPP 40/34/26). The reconciliation in
``checks.py`` therefore tests coverage + order-of-magnitude, never equality.
"""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OURAIRPORTS_DIR = ROOT / "data" / "ourairports"
NASR_DIR = ROOT / "data" / "nasr"
CIFP_PATH = NASR_DIR / "FAACIFP18"
DTPP_PATH = NASR_DIR / "dtpp_current.xml"

# US + FAA-served territories whose procedures live in the CIFP.
US_ISO = {"US", "PR", "VI", "GU", "MP", "AS"}

# CIFP section P (airport/terminal) sub-section → procedure class.
_CIFP_SUB = {"D": "sids", "E": "stars", "F": "apps"}

# d-TPP chart_code → procedure class (charts that ARE a procedure).
_DTPP_PROC = {"DP": "sids", "ODP": "sids", "STR": "stars", "IAP": "apps"}


# ── OurAirports (denominator) ────────────────────────────────────────────────
def _read_csv(name: str) -> list[dict]:
    path = OURAIRPORTS_DIR / name
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def ourairports_us() -> tuple[list[dict], dict[str, int]]:
    """Return (US airport rows, {icao: open_runway_count}) from OurAirports.

    Airport rows are ICAO-coded, non-closed, US/territory fields — the universe
    Loop 1 must cover. Runway count excludes closed runways.
    """
    rwy = Counter()
    for r in _read_csv("runways.csv"):
        if r.get("closed", "0") in ("1", "true", "True"):
            continue
        ident = (r.get("airport_ident") or "").strip().upper()
        if ident:
            rwy[ident] += 1

    rows = []
    for a in _read_csv("airports.csv"):
        ident = (a.get("ident") or "").strip().upper()
        if len(ident) != 4 or not ident.isalpha():
            continue
        if a.get("type") == "closed":
            continue
        if (a.get("iso_country") or "").strip() not in US_ISO:
            continue
        rows.append({
            "icao": ident,
            "name": a.get("name", ""),
            "iso_country": a.get("iso_country", ""),
            "type": a.get("type", ""),
        })
    return rows, dict(rwy)


# ── FAA CIFP (authoritative procedure manifest) ──────────────────────────────
def cifp_manifest(path: Path = CIFP_PATH) -> dict[str, dict[str, list[str]]]:
    """Parse FAACIFP18 → {icao: {"sids":[ids], "stars":[ids], "apps":[ids]}}.

    Distinct procedure identifiers per airport (sorted) — the *names*, not just a
    count, so Loop 2 can later check it produced every one of them (exhaustivity).
    Mirrors scripts/scan_airports.py's column offsets (verified vs live CIFP).
    """
    if not path.exists():
        return {}
    acc: dict[str, dict[str, set]] = defaultdict(
        lambda: {"sids": set(), "stars": set(), "apps": set()})
    with open(path, encoding="latin-1") as fh:
        for ln in fh:
            if len(ln) < 19 or ln[4:5] != "P":
                continue
            cls = _CIFP_SUB.get(ln[12:13])
            if not cls:
                continue
            apt = ln[6:10].strip()
            proc = ln[13:19].strip()
            if apt and proc:
                acc[apt][cls].add(proc)
    return {apt: {cls: sorted(ids) for cls, ids in d.items()} for apt, d in acc.items()}


# ── FAA d-TPP (independent chart inventory) ──────────────────────────────────
def dtpp_inventory(path: Path = DTPP_PATH) -> dict[str, dict[str, int]]:
    """Parse dtpp_current.xml → {icao: {"sids":n, "stars":n, "apps":n}}.

    Counts published charts by class (DP/ODP=departure, STR=arrival, IAP=approach);
    a deliberately independent product from the coded CIFP, used only to reconcile.
    """
    if not path.exists():
        return {}
    out: dict[str, dict[str, int]] = {}
    for apt in ET.parse(path).getroot().iter("airport_name"):
        icao = (apt.get("icao_ident") or "").strip().upper()
        if len(icao) != 4:
            continue
        counts = {"sids": 0, "stars": 0, "apps": 0}
        for rec in apt.findall("record"):
            cls = _DTPP_PROC.get((rec.findtext("chart_code") or "").strip())
            if cls:
                counts[cls] += 1
        out[icao] = counts
    return out
