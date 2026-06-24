#!/usr/bin/env python3
"""
Backfill the parsing-method catalog into data/aip_sources_seed.json.

For every country, derive ``acquisition_method`` and ``parser_method`` from its
``(source_type, access, aixm_version)`` setting via the single source of truth,
``miner.extractor.registry.resolve_methods``. This is a deterministic, free,
reproducible pass — no agent re-run. A human ``parser_method_override`` (set by
AIPhunter when it has high-confidence evidence) is preserved and NOT overwritten;
the derived ``parser_method`` remains the materialized cache of the mapping so the
two can be diffed for drift (see tests/test_registry.py).

Usage:
    python scripts/backfill_parser_methods.py        # write back to the seed
    python scripts/backfill_parser_methods.py --check # report drift, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miner.extractor.registry import resolve_methods  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "aip_sources_seed.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report entries whose cache differs from resolve_methods; write nothing")
    args = ap.parse_args()

    seed = json.loads(SEED.read_text())
    countries = seed.get("countries", {})

    changed = 0
    drift = []
    for iso, entry in countries.items():
        acq, parser = resolve_methods(
            entry.get("source_type", "unknown"),
            entry.get("access", "unknown"),
            entry.get("aixm_version", "nil"),
        )
        if entry.get("acquisition_method") != acq or entry.get("parser_method") != parser:
            drift.append((iso, entry.get("parser_method"), parser))
            entry["acquisition_method"] = acq
            entry["parser_method"] = parser
            changed += 1

    if args.check:
        print(f"{len(drift)} entries differ from resolve_methods():")
        for iso, was, now in drift[:50]:
            print(f"  {iso}: parser_method {was!r} -> {now!r}")
        return 1 if drift else 0

    if changed:
        SEED.write_text(json.dumps(seed, indent=2, ensure_ascii=False) + "\n")

    # Distribution summary.
    from collections import Counter
    pc = Counter(e.get("parser_method") for e in countries.values())
    ac = Counter(e.get("acquisition_method") for e in countries.values())
    print(f"backfilled {changed} entries across {len(countries)} countries")
    print("parser_method:", dict(sorted(pc.items(), key=lambda kv: -kv[1])))
    print("acquisition_method:", dict(sorted(ac.items(), key=lambda kv: -kv[1])))
    print(f"\nNext: python scripts/build_country_table.py  (regenerate dashboard/countries.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
