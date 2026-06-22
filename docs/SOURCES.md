# AeroAIP — Authoritative Data Sources (validated 2026-06)

This document records the **correct, verified** authoritative sources for the AeroAIP pipeline,
the access model and update cadence for each, and the known integrity caveats. It exists because
several URLs in the master spec (`CLAUDE.md`) were approximate and **404 as written** — the
corrections below were verified live (HTTP 200 / index parsed) before being baked into the code.

> Scope note: AeroAIP targets **flight-prep / simulation / visualization / analytics** use.
> It is explicitly **not** DO-200B/ED-76A certified and **not** for safety-of-flight navigation.

---

## 1. United States — FAA CIFP (Coded Instrument Flight Procedures)

**Role:** Primary ground truth. Authoritative coordinates *and* procedure coding for US airports.

| Property | Value |
|---|---|
| Landing page | `https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/download/` |
| Direct file | `https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_<YYMMDD>.zip` |
| `<YYMMDD>` | AIRAC **effective date**, not the cycle number (e.g. `CIFP_260122.zip` → HTTP 200) |
| Format | ARINC 424-18, fixed-length **132-byte** records |
| Cadence | 28 days (AIRAC) |
| Access | Free, no registration |

**Correction:** the spec's `https://aeronav.faa.gov/NASR_Subscription/CIFP/CIFP_<cycle>.zip`
returns 404. The cycle *number* (`2606`) is **not** the filename; the effective **date** is.
`scripts/download_waypoint_dbs.sh` resolves the date for the active cycle.

**Record types used** (see `validator/ground_truth/nasr_cifp.py`):

| Subsection | Meaning |
|---|---|
| `PD` | SID legs |
| `PE` | STAR legs |
| `PF` | Approach (IAP) legs |
| `PG` | Runway records |
| `PC` / `EA` | Terminal / enroute waypoints (lat-lon) |
| `PN` | Terminal NDB navaids |

---

## 2. United States — FAA d-TPP (digital Terminal Procedures Publication)

**Role:** Source PDF charts the LLM reads for procedure **structure** (never coordinates).

| Property | Value |
|---|---|
| Portal | `https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/dtpp/` |
| Metadata index | `https://nfdc.faa.gov/webContent/dtpp/current.xml` (cycle `2606`, KLAX confirmed) |
| Chart PDF | `https://aeronav.faa.gov/d-tpp/<cycle>/<FILE>.PDF` |
| Cadence | 28 days (AIRAC); new edition ~20 days before effective |

**Correction:** the spec's hard-coded `.../d-tpp/2606/00397LOOP.PDF` 404s — the 5-digit prefix is
chart-specific. Resolve the real filename for an airport+procedure from `current.xml`; do not guess.

---

## 3. Europe — EUROCONTROL EAD (Phase 1)

| Property | Value |
|---|---|
| Portal | `https://www.ead.eurocontrol.int/` |
| Access | Registration; EAD Basic (non-operational) / MY EAD B2B (AIMSL) |
| Native format | **AIXM 5.1** (and 4.5) — **not 5.2** |

**Caveat:** EAD supplies AIXM **5.1**. Our output target is **5.2**, so a documented
**5.1 → 5.2 mapping** step is required before EU data can be scored. Out of scope for Phase 0.

---

## 4. Canada — NAV CANADA (Phase 1)

| Property | Value |
|---|---|
| Portal | `https://www.navcanada.ca/en/aeronautical-information/aip-canada.aspx` |
| Format | **PDF AIP** (GEN/ENR/AD) — **no public ARINC 424 coordinate database** |
| Cadence | 56-day publication / 28-day AIRAC alignment |

**Caveat:** Unlike the FAA CIFP, there is no machine-readable Canadian coordinate DB to look up
against. Canada therefore needs **chart/PDF extraction** for coordinates (or a commercial data
arrangement), which weakens the "exact DB-lookup" guarantee. Out of scope for Phase 0.

---

## 5. Output format — AIXM 5.2

| Property | Value |
|---|---|
| Spec | `https://aixm.aero/page/aixm-52-specification` |
| Governance | **EUROCONTROL / FAA Change Control Board** (CCB) — *not* published directly by ICAO |
| Encoding | **GML 3.2** based (ISO 19100 family) |
| ICAO relationship | The encoding used to satisfy **ICAO Annex 15** and **Doc 10066 (PANS-AIM)** digital datasets |

**Naming precision:** "ICAO AIXM 5.2" is shorthand. AIXM is the de-facto exchange model for AIP
digital data and is how Annex 15 / PANS-AIM is implemented, but it is governed by the AIXM CCB.

**Correct AIXM 5.2 feature/attribute names** used by `miner/schemas/`:

| Concept | AIXM 5.2 class / attribute |
|---|---|
| SID | `StandardInstrumentDeparture` |
| STAR | `StandardTerminalArrival` |
| Approach | `InstrumentApproachProcedure` |
| Leg (base) | `SegmentLeg` → `DepartureLeg` / `ArrivalLeg` / `ApproachLeg` |
| Path terminator | `SegmentLeg.legTypeARINC` (IF, TF, CF, DF, RF, VA, CA, …) |
| Named point | `DesignatedPoint` (designator ≤ 5 chars, lat/lon) |

---

## 6. Pipeline integrity — why exact coordinate match is sound

1. **Single source of truth.** Both the miner's coordinate lookup *and* the validator's ground
   truth read the **same CIFP value** and apply the **same** `arinc424_to_decimal()` conversion.
   The LLM supplies only the waypoint *identifier*; the coordinate is never extracted from a chart.
   Equality is therefore deterministic — there is no proximity tolerance and no comparison drift.
2. **Lossless storage.** Coordinates are stored as the canonical **8-decimal-place text** string
   (not a float), so the SQLite round-trip cannot perturb the value.
3. **Shared datum.** ARINC 424 and AIXM both use **WGS-84**; no datum shift is applied.

## 7. Known limitations (documented, not hidden)

- **Conditional altitude termination.** AIXM 5.2 has no single leg type for "terminate at altitude
  *or* at the ARINC terminator, whichever first" (cf. change request AIXM-524). We encode the
  altitude as a constraint on the leg and accept the minor semantic loss.
- **Magnetic vs true course.** ARINC 424 course fields may be magnetic; AIXM requires the reference
  to be explicit. The schema keeps `course_magnetic` and `course_true` as separate fields.
- **CIFP offsets.** The fixed-width column map in `validator/ground_truth/nasr_cifp.py`
  (`CIFP_FIELDS`) follows ARINC 424-18 for the subset of fields we parse. It is validated against
  the committed fixtures; re-validating against a live CIFP drop is a Phase 1 task.
