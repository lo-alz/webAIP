#!/usr/bin/env python3
"""
Loop 2 — Extract → AIXM 5.2 → self-check (second of the two sequenceable loops).

Consumes Loop 1's census manifest and, for every SID it counted, produces a typed
record from the authoritative coded CIFP, serialises AIXM 5.2 GML, self-checks it
(schema/units · XSD · unresolved waypoints · leg-sequence sanity), and grades the
result against the manifest (exhaustivity). US-only, offline; STAR/IAP coded
parsers and Europe (EAD) follow the same shape.

Run in sequence (Loop 1 must have produced the manifest first):
    python scripts/census_loop.py  --airac 2607      # Loop 1
    python scripts/extract_loop.py --airac 2607      # Loop 2 (this script)

Options:
    --airac CYCLE     AIRAC cycle tag (default 2607)
    --country US      country shard (US only in this increment)
    --limit N         process only the first N airports (smoke test)
    --force           ignore the checkpoint and recompute every airport
    --no-gml          skip writing the per-procedure .gml files
    --no-xsd          skip XSD validation (faster; schema gate still runs)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miner.loop.extract import run_extract


def main() -> int:
    ap = argparse.ArgumentParser(description="Loop 2 — Extract → AIXM 5.2 → self-check")
    ap.add_argument("--airac", default="2607", help="AIRAC cycle tag (e.g. 2607)")
    ap.add_argument("--country", default="US", help="country shard (US only for now)")
    ap.add_argument("--limit", type=int, default=None, help="process only first N airports")
    ap.add_argument("--force", action="store_true", help="ignore checkpoint; recompute all")
    ap.add_argument("--no-gml", action="store_true", help="don't write .gml artifacts")
    ap.add_argument("--no-xsd", action="store_true", help="skip XSD validation (faster)")
    args = ap.parse_args()

    if args.country.upper() != "US":
        print(f"country {args.country!r} not yet implemented — only US in this increment.",
              file=sys.stderr)
        return 2

    out = run_extract(args.airac, limit=args.limit, force=args.force,
                      write_gml=not args.no_gml, validate_xsd=not args.no_xsd)
    s = out["summary"]
    print(f"\nExtract complete · {s['total']} airports · {s['by_status']}")
    for cls, t in s["procedures"].items():
        print(f"  {cls}: {t['produced']}/{t['expected']} produced")
    if s["flags"]:
        top = sorted(s["flags"].items(), key=lambda kv: -kv[1])[:8]
        print("top flags: " + ", ".join(f"{k}×{v}" for k, v in top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
