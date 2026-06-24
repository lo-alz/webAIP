"""
NASR CIFP (ARINC 424-18) — the US ground-truth profile.

The parser itself is the shared, source-neutral ARINC 424-18 implementation in
``miner/extractor/arinc424.py`` (the catalog's ``parser_method == "arinc424"``
tier). This module is the thin US wrapper kept for the existing import path
``from validator.ground_truth.nasr_cifp import parse_sid, parse_waypoints`` used
by the validator, the waypoint-index builder, and the Phase-0 PoC. ``source_url``
/ ``source_db`` default to the FAA NASR CIFP, so behaviour is unchanged.

See docs/SOURCES.md §1/§7 for the verified column map.
"""
from __future__ import annotations

# Re-export the shared parser so all existing call sites keep working verbatim.
from miner.extractor.arinc424 import (  # noqa: F401
    CIFP_FIELDS,
    is_enroute_waypoint,
    is_sid_record,
    is_terminal_waypoint,
    iter_lines,
    parse_sid,
    parse_waypoints,
)

__all__ = [
    "CIFP_FIELDS",
    "iter_lines",
    "is_terminal_waypoint",
    "is_enroute_waypoint",
    "is_sid_record",
    "parse_waypoints",
    "parse_sid",
]
