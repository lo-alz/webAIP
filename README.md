# AeroAIP Subnet — Phase 0 Proof of Concept

Open, globally-complete AIP database of instrument flight procedures (SIDs, STARs,
IAPs) in **AIXM 5.2**. This repository contains the **Phase 0 PoC** (master spec:
[`CLAUDE.md`](CLAUDE.md)), which proves the core thesis on authentic FAA data:

> A vision LLM extracts only the procedure **structure** (leg order, ARINC 424
> path terminators, altitude/speed constraints, PBN spec) from a chart, while
> every waypoint **coordinate** comes from an authoritative database lookup
> (FAA NASR CIFP). They combine into a schema-valid AIXM 5.2 record that scores
> **100% coordinate match, > 90% constraint match**.

The PoC runs end-to-end on the **KLAX DOTSS2 RNAV SID** using real FAA CIFP
records, and is fully deterministic offline (no GPU/LLM/network required).

## Why coordinates are exact, not approximate

The miner's lookup **and** the validator's ground truth read the *same* CIFP value
through the *same* `arinc424_to_decimal()` conversion, stored as canonical 8-dp
text (no float drift). Equality is deterministic — there is no proximity
tolerance. The LLM never contributes a coordinate (and any coordinate-like keys
it returns are stripped). See [`docs/SOURCES.md`](docs/SOURCES.md).

## Quick start

```bash
pip install -r requirements.txt

# (optional) fetch real FAA data — best-effort, non-fatal if offline:
bash scripts/download_waypoint_dbs.sh        # resolves the live CIFP + d-TPP index

# build the waypoint index (uses live CIFP if present, else committed fixtures):
python scripts/build_waypoint_index.py

# run the end-to-end PoC (KLAX DOTSS2):
python scripts/poc_extraction.py             # -> 100% coords, 100% constraints, AIXM 5.2 GML valid

# tests:
pytest -q

# project dashboard (clickable checklist + Gantt, progress + dependencies):
open dashboard/index.html
```

## Pipeline

```
 d-TPP PDF chart ─► LLM (structure only) ─┐
                                          ├─► assemble ─► AIXM 5.2 SIDRecord
 NASR CIFP ─► waypoint index ─► coords ───┘                    │
                                                               ├─► AIXM 5.2 GML  (XSD-validated)
 NASR CIFP PD records ─► ground truth ─────────────────────────┴─► exact-match scoring
```

| Stage | Module |
|---|---|
| ARINC 424 coordinate conversion | `miner/coordinates.py` |
| AIXM 5.2 Pydantic schemas | `miner/schemas/aixm52_procedure.py` |
| AIXM 5.2 GML exporter | `miner/schemas/aixm52_gml.py` |
| Waypoint index (SQLite) | `miner/waypoint_db.py` |
| LLM router (+ fixture fallback) | `miner/extractor/llm_router.py` |
| d-TPP chart fetch/render | `miner/extractor/faa_dtpp.py` |
| Structure → record assembler | `miner/assemble.py` |
| Hard validation rules (§5) | `validator/validation_rules.py` |
| Exact-match scoring (§6) | `validator/scoring.py` |
| AIXM 5.2 XSD conformance | `validator/aixm_xsd.py` |
| NASR CIFP parser | `validator/ground_truth/nasr_cifp.py` |

## Data sources

Authoritative sources, verified URLs, access models, and known caveats (EAD is
AIXM 5.1; NAV CANADA has no ARINC 424 DB; AIXM governance) are documented in
[`docs/SOURCES.md`](docs/SOURCES.md). The parser is verified against the **live**
FAA CIFP (`FAACIFP18`, ~70k waypoints) — see `tests/test_cifp_real_format.py`.

## Scope

Phase 0 only. Out of scope here (Phase 1+): Bittensor/subtensor, PostGIS
aggregator, CesiumJS globe, EAD (AIXM 5.1→5.2 mapping), NAV CANADA, and the
non-US shards. AeroAIP is for flight-prep / simulation / visualization /
analytics — **not** DO-200B/ED-76A certified, not for safety-of-flight navigation.
