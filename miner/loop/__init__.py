"""
Sequenceable pipeline loops for AeroAIP.

Loop 1 (``census``) builds the per-airport coverage manifest — what procedures
each airport has, cross-checked across independent sources. Loop 2 (extraction →
AIXM 5.2 → self-check, to follow) consumes that manifest. Both ride the shared
``framework.LoopRunner`` so they run in sequence, idempotently, per AIRAC cycle.
"""
