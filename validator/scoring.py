"""
Scoring logic (master spec §6).

Coordinate scoring is **exact database-lookup match** — a waypoint either equals
the authoritative NASR/EAD record or it does not. Equality is on the canonical
8-dp Decimal value, so there is no proximity tolerance and no float drift (the
index stores coordinates as text; see ``miner.waypoint_db``).
"""
from __future__ import annotations

from typing import Callable, Optional

from miner.schemas import ProcedureLeg, all_legs


def segmented_legs(record) -> list[tuple[tuple, ProcedureLeg]]:
    """
    Yield ``(segment_key, leg)`` for every leg. Sequence numbers repeat across
    transitions in real procedures, so legs must be matched within their segment
    (runway transition / common route / enroute transition / approach segment),
    not by sequence number alone.
    """
    out: list[tuple[tuple, ProcedureLeg]] = []
    for t in getattr(record, "runway_transitions", []) or []:
        for leg in t.legs:
            out.append((("RWY", t.runway_designator), leg))
    for leg in getattr(record, "common_route", []) or []:
        out.append((("COMMON",), leg))
    for t in getattr(record, "enroute_transitions", []) or []:
        for leg in t.legs:
            out.append((("ENR", t.transition_fix), leg))
    for attr in ("initial_approach_segments", "intermediate_segment",
                 "final_segment", "missed_approach_segment"):
        for leg in getattr(record, attr, []) or []:
            out.append(((attr,), leg))
    return out


def _gt_index(gt) -> dict[tuple, ProcedureLeg]:
    return {(seg, leg.sequence_number): leg for seg, leg in segmented_legs(gt)}


def extracted_legs(record) -> list[ProcedureLeg]:
    """All legs of a record, flattened (spec helper)."""
    return all_legs(record)


def find_leg(sequence_number: int, gt) -> Optional[ProcedureLeg]:
    """Find the ground-truth leg with a given sequence number (spec helper)."""
    for leg in all_legs(gt):
        if leg.sequence_number == sequence_number:
            return leg
    return None


def score_vs_ground_truth(extracted, gt) -> float:
    """
    Coordinate scoring: exact database lookup match only.
    A waypoint either matches the NASR/EAD record or it does not.
    Legs are matched within their segment (transition-aware).
    """
    gt_index = _gt_index(gt)
    scores: list[float] = []
    for seg, leg_e in segmented_legs(extracted):
        leg_gt = gt_index.get((seg, leg_e.sequence_number))
        if not leg_gt:
            continue

        # Waypoint: exact match against database record.
        if leg_e.waypoint and leg_gt.waypoint:
            lat_match = leg_e.waypoint.lat == leg_gt.waypoint.lat
            lon_match = leg_e.waypoint.lon == leg_gt.waypoint.lon
            id_match = leg_e.waypoint.waypoint_id == leg_gt.waypoint.waypoint_id
            scores.append(1.0 if (lat_match and lon_match and id_match) else 0.0)

        # Path terminator: exact match.
        scores.append(1.0 if leg_e.path_terminator == leg_gt.path_terminator else 0.0)

        # Altitude constraint.
        if leg_e.alt_constraint and leg_gt.alt_constraint:
            alt_match = (
                leg_e.alt_constraint.type == leg_gt.alt_constraint.type
                and leg_e.alt_constraint.lower_ft == leg_gt.alt_constraint.lower_ft
            )
            scores.append(1.0 if alt_match else 0.0)

        # Speed constraint.
        if leg_e.speed_constraint and leg_gt.speed_constraint:
            spd_match = (
                leg_e.speed_constraint.type == leg_gt.speed_constraint.type
                and leg_e.speed_constraint.value_kt == leg_gt.speed_constraint.value_kt
            )
            scores.append(1.0 if spd_match else 0.0)

    # Field completeness.
    required = ["procedure_name", "runway_transitions", "pbn_nav_spec"]
    completeness = sum(1 for f in required if getattr(extracted, f, None)) / len(required)
    scores.append(completeness)

    return sum(scores) / len(scores) if scores else 0.0


def coordinate_match_rate(extracted, gt) -> float:
    """
    Fraction of comparable legs whose waypoint matches the DB record exactly.
    This is the headline Phase 0 metric (target: 1.0).
    """
    gt_index = _gt_index(gt)
    total = 0
    matched = 0
    for seg, leg_e in segmented_legs(extracted):
        leg_gt = gt_index.get((seg, leg_e.sequence_number))
        if not leg_gt or not (leg_e.waypoint and leg_gt.waypoint):
            continue
        total += 1
        if (
            leg_e.waypoint.lat == leg_gt.waypoint.lat
            and leg_e.waypoint.lon == leg_gt.waypoint.lon
            and leg_e.waypoint.waypoint_id == leg_gt.waypoint.waypoint_id
        ):
            matched += 1
    return matched / total if total else 0.0


def constraint_match_rate(extracted, gt) -> float:
    """
    Fraction of comparable structure items (path terminator + alt + speed) that
    match exactly. This is the secondary Phase 0 metric (target: > 0.90).
    """
    gt_index = _gt_index(gt)
    total = 0
    matched = 0
    for seg, leg_e in segmented_legs(extracted):
        leg_gt = gt_index.get((seg, leg_e.sequence_number))
        if not leg_gt:
            continue

        total += 1
        if leg_e.path_terminator == leg_gt.path_terminator:
            matched += 1

        if leg_e.alt_constraint and leg_gt.alt_constraint:
            total += 1
            if (
                leg_e.alt_constraint.type == leg_gt.alt_constraint.type
                and leg_e.alt_constraint.lower_ft == leg_gt.alt_constraint.lower_ft
            ):
                matched += 1

        if leg_e.speed_constraint and leg_gt.speed_constraint:
            total += 1
            if (
                leg_e.speed_constraint.type == leg_gt.speed_constraint.type
                and leg_e.speed_constraint.value_kt == leg_gt.speed_constraint.value_kt
            ):
                matched += 1

    return matched / total if total else 0.0


def score_miner_cycle(
    miner_uid: int,
    cycle: str,
    assigned: list[str],
    submissions: dict,
    get_ground_truth: Callable[[str, str], object],
    *,
    validate: Callable[[object], list[str]] | None = None,
    score_by_consensus: Callable[[object, str, str], float] | None = None,
    log_omission: Callable[[int, str, set], None] | None = None,
) -> float:
    """Hard coverage gate then accuracy scoring (spec §6)."""
    from validator.validation_rules import validate_submission

    validate = validate or validate_submission

    # Gate 1: Coverage — zero tolerance.
    missing = set(assigned) - set(submissions.keys())
    if missing:
        if log_omission:
            log_omission(miner_uid, cycle, missing)
        return 0.0

    scores: list[float] = []
    for icao, procedures in submissions.items():
        for record in procedures:
            # Gate 2: Schema + hard validation rules.
            errors = validate(record)
            if errors:
                scores.append(0.0)
                continue

            gt = get_ground_truth(icao, cycle)
            if gt:
                scores.append(score_vs_ground_truth(record, gt))
            elif score_by_consensus:
                scores.append(score_by_consensus(record, icao, cycle))

    return sum(scores) / len(scores) if scores else 0.0
