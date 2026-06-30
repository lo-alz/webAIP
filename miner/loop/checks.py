"""
Loop 1 verification harness — the self-checks that make a census row trustworthy.

Three layers, cheapest first:

  1. ``sanity_checks``    — internal consistency of a single row (ICAO shape,
     counts match the manifest, no duplicate procedure ids).
  2. ``denominator_check`` — OurAirports as the *completeness denominator*: is the
     airport in the spine, and does our runway figure agree? (NOT a procedure
     authority — OurAirports has no procedures.)
  3. ``dtpp_reconcile``   — the authoritative cross-check: the CIFP procedure
     manifest vs the independent d-TPP chart inventory. CIFP counts coded
     procedures and d-TPP counts charts, so this tests *coverage* (a type present
     in one but absent in the other) and *order-of-magnitude* agreement — never
     exact equality.

Each function returns a list of flag strings; an empty list means "passed".

NOTE — US ground-truth regression (check #1 in the design): in this US-only
skeleton the CIFP *is* the authoritative source, so there is no separate generic
parser to regress against yet. ``dtpp_reconcile`` is the authoritative,
independent-lineage check for the US. The regression slot activates in Loop 2
(extracted records scored vs CIFP) and for any future generic US eAIP parser.
"""
from __future__ import annotations

_CLASSES = ("sids", "stars", "apps")
_DIVERGENCE_RATIO = 3.0   # both sources non-zero but differ by >3× → flag


def sanity_checks(row: dict, allowed_prefixes: tuple[str, ...] = ("K",)) -> list[str]:
    """Internal consistency of one census row.

    ``allowed_prefixes`` are the leading ICAO letters valid for the country shard
    (e.g. the US family is K plus the FAA-served territories P/T/N).
    """
    flags: list[str] = []
    icao = row.get("icao", "")
    if len(icao) != 4 or not icao.isalpha():
        flags.append("icao_malformed")
    if icao[:1] not in allowed_prefixes:
        flags.append("icao_prefix_unexpected")
    if row.get("runways", 0) < 0:
        flags.append("runways_negative")

    manifest = row.get("manifest", {})
    counts = row.get("counts", {})
    for cls in _CLASSES:
        ids = manifest.get(cls, [])
        if counts.get(cls, 0) != len(ids):
            flags.append(f"{cls}_count_mismatch")        # count must equal manifest length
        if len(ids) != len(set(ids)):
            flags.append(f"{cls}_duplicate_ids")          # distinct ids only
    return flags


def denominator_check(row: dict, oa_runways: dict[str, int]) -> list[str]:
    """OurAirports completeness/runway cross-check (denominator only)."""
    flags: list[str] = []
    icao = row.get("icao", "")
    if icao not in oa_runways:
        flags.append("absent_from_ourairports")
        return flags
    if oa_runways[icao] != row.get("runways", 0):
        flags.append("runway_count_mismatch_vs_ourairports")
    # A large airport with published procedures but zero runways in the spine is
    # implausible — surfaces a spine gap or an ident mismatch.
    if row.get("runways", 0) == 0 and any(row.get("counts", {}).get(c) for c in _CLASSES):
        flags.append("procedures_without_runways")
    return flags


def dtpp_reconcile(counts: dict[str, int], dtpp: dict[str, int] | None) -> list[str]:
    """Reconcile the CIFP manifest counts against the d-TPP chart inventory.

    Coverage: a procedure class present in one source but absent in the other.
    Magnitude: both present but diverging by more than ``_DIVERGENCE_RATIO``.
    """
    if dtpp is None:
        return ["no_dtpp_record"]          # airport coded in CIFP but absent from d-TPP
    flags: list[str] = []
    for cls in _CLASSES:
        c = counts.get(cls, 0)
        d = dtpp.get(cls, 0)
        if (c > 0) != (d > 0):
            flags.append(f"{cls}_coverage_gap")           # one source has it, the other doesn't
        elif c > 0 and d > 0 and max(c, d) / min(c, d) > _DIVERGENCE_RATIO:
            flags.append(f"{cls}_count_divergent")        # same type, wildly different magnitude
    return flags
