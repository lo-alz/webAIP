#!/usr/bin/env python3
"""
Loop 1 — Coverage Census (the first of the two sequenceable pipeline loops).

Builds a per-airport procedure manifest for a country and self-checks it across
three independent sources (OurAirports denominator · FAA CIFP procedures ·
FAA d-TPP charts). This first increment covers the **US** only, offline against
committed data; Europe (EAD) and the rest follow the same shape once their
sources are wired.

Run in sequence:
    python scripts/census_loop.py --airac 2607          # Loop 1 (this script)
    # python scripts/extract_loop.py --airac 2607       # Loop 2 (to follow)

Options:
    --airac CYCLE   AIRAC cycle tag (default 2607)
    --country US    country shard (only US implemented in this increment)
    --limit N       process only the first N airports (smoke test)
    --force         ignore the checkpoint and recompute every airport
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miner.loop.census import run_census


def main() -> int:
    ap = argparse.ArgumentParser(description="Loop 1 — Coverage Census")
    ap.add_argument("--airac", default="2607", help="AIRAC cycle tag (e.g. 2607)")
    ap.add_argument("--country", default="US", help="country shard (US only for now)")
    ap.add_argument("--limit", type=int, default=None, help="process only first N airports")
    ap.add_argument("--force", action="store_true", help="ignore checkpoint; recompute all")
    args = ap.parse_args()

    if args.country.upper() != "US":
        print(f"country {args.country!r} not yet implemented — only US in this increment.",
              file=sys.stderr)
        return 2

    out = run_census(args.airac, limit=args.limit, force=args.force)
    s = out["summary"]
    print(f"\nCensus complete · {s['total']} airports · {s['by_status']}")
    if s["flags"]:
        top = sorted(s["flags"].items(), key=lambda kv: -kv[1])[:8]
        print("top flags: " + ", ".join(f"{k}×{v}" for k, v in top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
