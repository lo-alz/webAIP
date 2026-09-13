#!/usr/bin/env python3
"""
Fetch + slice the FAA CIFP for specific airports into committed fixtures.

The full FAACIFP18 is ~53 MB and gitignored; a per-airport slice is a few KB and
can be committed, so the waypoint index (and the 3D viewer's bundled data) cover
that airport OFFLINE — no FAA reachability needed at build/run time.

A slice for one airport contains, verbatim (no reformatting, ever):
  * every section-P record for the airport (PA/PC/PD/PE/PF/PG/PI/PN…), and
  * every enroute waypoint (EA) and navaid (D) record whose identifier is
    referenced as a leg fix by that airport's PD/PE/PF procedure records.

Membership is decided by running the repo's OWN parsers over each candidate
record — never by fresh column arithmetic — so the slice logic can't drift from
the real parser.

Usage:
    python scripts/fetch_airport_cifp.py KJFK KEWR              # use local CIFP
    python scripts/fetch_airport_cifp.py --download KJFK KEWR   # fetch CIFP first

Output: data/fixtures/<icao>_cifp.dat (lower-case icao), one file per airport.
``build_waypoint_index.py`` loads every ``data/fixtures/*_cifp*.dat`` fixture.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miner.extractor.arinc424 import (  # noqa: E402
    _f,
    is_enroute_waypoint,
    is_iap_record,
    is_navaid,
    is_sid_record,
    is_star_record,
    parse_navaids,
    parse_waypoints,
)

NASR_DIR = Path(os.environ.get("NASR_DATA_DIR", ROOT / "data" / "nasr"))
CIFP_PATH = NASR_DIR / "FAACIFP18"
FIXTURE_DIR = ROOT / "data" / "fixtures"

# CIFP zips are published per AIRAC EFFECTIVE DATE (not cycle number) — see
# docs/SOURCES.md §1. Probe recent 28-day boundaries, newest first.
CIFP_BASE = "https://aeronav.faa.gov/Upload_313-d/cifp"
AIRAC_ANCHOR = dt.date(2026, 1, 22)  # a known CIFP effective date


def _candidate_dates(back: int = 3, ahead: int = 1) -> list[str]:
    k = (dt.date.today() - AIRAC_ANCHOR).days // 28
    return [(AIRAC_ANCHOR + dt.timedelta(days=28 * i)).strftime("%y%m%d")
            for i in range(k + ahead, k - back, -1)]


def download_cifp() -> Path:
    """Resolve the newest published CIFP zip, download, unzip FAACIFP18."""
    NASR_DIR.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for eff in _candidate_dates():
        url = f"{CIFP_BASE}/CIFP_{eff}.zip"
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=30):
                pass
        except Exception as e:  # not published (404) or unreachable
            last_err = e
            continue
        print(f"  downloading {url}")
        with urllib.request.urlopen(url, timeout=600) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for name in z.namelist():
                if name.upper().endswith("FAACIFP18"):
                    CIFP_PATH.write_bytes(z.read(name))
                    print(f"  unzipped {name} -> {CIFP_PATH} "
                          f"({CIFP_PATH.stat().st_size // 1_000_000} MB)")
                    return CIFP_PATH
        raise RuntimeError(f"{url}: zip contains no FAACIFP18")
    raise RuntimeError(f"no CIFP zip found at {CIFP_BASE} "
                       f"(tried {', '.join(_candidate_dates())}): {last_err}")


def _parsed_ident(line: str) -> str | None:
    """Identifier of an EA/D record AS THE REAL PARSER READS IT (else None)."""
    rows = (parse_navaids([line], "slice") if is_navaid(line)
            else parse_waypoints([line], "slice"))
    return rows[0].waypoint_id if rows else None


def slice_airport(lines: list[str], icao: str) -> list[str]:
    """All P-section records for ``icao`` + referenced EA/D records, verbatim."""
    icao = icao.upper()
    airport_p: list[str] = []
    fixes: set[str] = set()
    for ln in lines:
        if len(ln) < 51 or _f(ln, "section") != "P" or _f(ln, "airport") != icao:
            continue
        airport_p.append(ln)
        if is_sid_record(ln) or is_star_record(ln) or is_iap_record(ln):
            fid = _f(ln, "fix_id")
            if fid:
                fixes.add(fid)

    referenced: list[str] = []
    if fixes:
        for ln in lines:
            if len(ln) < 51:
                continue
            if not (is_enroute_waypoint(ln) or is_navaid(ln)):
                continue
            ident = _parsed_ident(ln)
            if ident and ident in fixes:
                referenced.append(ln)
    return airport_p + referenced


def main() -> int:
    ap = argparse.ArgumentParser(description="Slice the FAA CIFP per airport")
    ap.add_argument("icaos", nargs="+", help="ICAO idents, e.g. KJFK KEWR")
    ap.add_argument("--download", action="store_true",
                    help="download the latest CIFP zip first")
    ap.add_argument("--cifp", default=str(CIFP_PATH), help="path to FAACIFP18")
    args = ap.parse_args()

    cifp = Path(args.cifp)
    if args.download and not cifp.exists():
        cifp = download_cifp()
    if not cifp.exists():
        print(f"error: {cifp} not found (pass --download or fetch it first)",
              file=sys.stderr)
        return 2

    print(f"  reading {cifp}")
    lines = [ln.rstrip("\n") for ln in
             cifp.read_text(encoding="latin-1").splitlines() if ln.strip()]

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for icao in args.icaos:
        sliced = slice_airport(lines, icao)
        if not sliced:
            print(f"  ! {icao.upper()}: no section-P records found — skipped",
                  file=sys.stderr)
            continue
        out = FIXTURE_DIR / f"{icao.lower()}_cifp.dat"
        out.write_text("\n".join(sliced) + "\n", encoding="latin-1")
        kinds = {"D": 0, "E": 0, "F": 0, "C": 0, "G": 0}
        for ln in sliced:
            sub = ln[12:13] if _f(ln, "section") == "P" else ""
            if sub in kinds:
                kinds[sub] += 1
        print(f"  {icao.upper()}: {len(sliced)} records -> {out} "
              f"(SID {kinds['D']} · STAR {kinds['E']} · IAP {kinds['F']} · "
              f"wp {kinds['C']} · rwy {kinds['G']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
