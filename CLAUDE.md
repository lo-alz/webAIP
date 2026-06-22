# AeroAIP Subnet — Master Project Specification v3

> **Usage**: Save as `CLAUDE.md` (Claude Code) or `.cursorrules` (Cursor) in your project root.
> The AI agent loads this as persistent context every session.
> Start every session: *"Read CLAUDE.md and confirm full project scope before we begin."*

---

## 1. Project Identity

| Field | Value |
|---|---|
| **Project name** | AeroAIP Subnet |
| **Operating entity** | Hong Kong private limited company (independent, personal project) |
| **Goal** | World's first open, globally-complete AIP database: SIDs, STARs, IAPs in AIXM 5.2 format |
| **Funding model** | Bittensor TAO subnet emissions |
| **Primary use cases** | Flight preparation, MSFS/X-Plane simulation, CesiumJS/Google Earth 3D visualization, aviation AI/analytics |
| **Explicitly out of scope** | DO-200B/ED-76A certification, FMS avionics loading, safety-of-flight navigation |
| **Master API node** | Singapore (DigitalOcean) — HK entity, SG server |

---

## 2. Core Design Principles

1. **LLM-agnostic**: The subnet scores only the output (AIXM 5.2 JSON). Miners set `MINER_LLM` to any model — local, cloud, open-weight. `ollama/qwen2.5-vl` is the recommended default (no cloud dependency). No cloud provider is assumed anywhere in the architecture.

2. **Country-sharded**: Sharding by ICAO country prefix (`LF` = France, `ED` = Germany, `K` = USA, `VH` = Hong Kong, etc.). One country = one AIP source + one language + one format = one miner specialises per country.

3. **Exact coordinates required**: Waypoint coordinates must match the authoritative ARINC 424 / NASR CIFP database exactly (see Section 4). There is no tolerance threshold — extracted coordinates are validated by lookup against the canonical waypoint database, not by proximity.

4. **28-day AIRAC cycle alignment**: All tasks trigger on ICAO AIRAC cycle boundaries. Stale data (wrong cycle tag) scores zero.

5. **Guaranteed completeness**: Missing airport = zero score for entire cycle. Sentinel miner (operator-run) fills any gap after 72h, flagged `coverage_source: sentinel_fallback`.

6. **AIXM 5.2 target format**: January 2025 release, purpose-built for IFP/PBN encoding. Build on the latest standard from day one.

---

## 3. Airport Scope

- **Universe**: ~9,300 airports with ICAO 4-letter codes and ≥1 published instrument procedure
- **Excluded**: ~35,000 VFR-only fields (no procedures = no value)
- **Per airport**: All SIDs, STARs, IAPs (ILS/RNAV/RNP/VOR/NDB), runway geometry, threshold coordinates, ATC frequencies
- **Volume**: ~232,500 procedures refreshed every 28-day AIRAC cycle

### Phase 0 Test Airports

| Airport | ICAO | Country Shard | AIP Source | Scoring Method |
|---|---|---|---|---|
| Vancouver Intl | CYVR | C__ (Canada) | NAV CANADA eAIP HTML | Plausibility + NAV CANADA NASR-equivalent |
| Los Angeles Intl | KLAX | K__ (USA) | FAA d-TPP PDF + NASR CIFP | **Primary ground truth** (exact ARINC 424 match) |
| London Heathrow | EGLL | EG__ (UK) | NATS eAIP HTML via EAD | EAD Basic reference |
| Hong Kong Intl | VHHH | VH__ (HK) | CAAS HK eAIP HTML | Plausibility + field completeness |
| Shanghai Pudong | ZSPD | Z__ (China) | CAAC PDF (restricted) | 3-model consensus, flag `confidence: LOW` |

---

## 4. ICAO Data Specification — Exact Format Requirements

This section defines the authoritative specification every miner must comply with. The validator rejects any output that does not conform.

### 4.1 Waypoint Coordinate Standard

**Authority**: ICAO Annex 15 (Aeronautical Information Services), ICAO Doc 8697, ARINC 424-18[cite:237][cite:222]

Waypoint coordinates are **not extracted from charts**. They are **looked up from the authoritative waypoint database** (NASR CIFP for the US; EAD for Europe; equivalent national databases elsewhere). Chart extraction is used only for procedure structure (leg sequence, path terminator, constraints) — never for waypoint coordinates when a database record exists.

**Coordinate format in ARINC 424 (source database)**:
- Latitude: `DDMMSS.SS` with hemisphere suffix (`N`/`S`) — degrees, minutes, decimal seconds to 2dp
- Longitude: `DDDMMSS.SS` with hemisphere suffix (`E`/`W`) — degrees, minutes, decimal seconds to 2dp
- Example: `340337.12N` / `1183159.78W` = 34°03'37.12"N 118°31'59.78"W

**ICAO Annex 15 precision requirement**[cite:249]:
- Named waypoints (5-letter ICAO identifiers): coordinates stored to **nearest 0.01 arc-second** = ~0.31 metres
- Unnamed lat/lon waypoints (oceanic): expressed as `DDMMN/DDDMMW` = whole minutes only
- Runway thresholds: to **nearest 0.1 arc-second** = ~3 metres

**Internal storage in this database**: WGS-84 decimal degrees to **8 decimal places** (sub-centimetre precision, lossless round-trip from ARINC 424 source).

```python
# Coordinate conversion: ARINC 424 DMS → decimal degrees (exact, no rounding)
import decimal
decimal.getcontext().prec = 12

def arinc424_to_decimal(dms_str: str) -> decimal.Decimal:
    """
    Convert ARINC 424 coordinate string to decimal degrees.
    Input formats:
      Latitude:  'DDMMSS.SSN' or 'DDMMSS.SSS'  (9-10 chars)
      Longitude: 'DDDMMSS.SSE' or 'DDDMMSS.SSW' (10-11 chars)
    """
    hem = dms_str[-1]
    nums = dms_str[:-1]
    if hem in ('N', 'S'):
        d = decimal.Decimal(nums[0:2])
        m = decimal.Decimal(nums[2:4])
        s = decimal.Decimal(nums[4:])
    else:  # E or W
        d = decimal.Decimal(nums[0:3])
        m = decimal.Decimal(nums[3:5])
        s = decimal.Decimal(nums[5:])
    result = d + m / 60 + s / 3600
    if hem in ('S', 'W'):
        result = -result
    return result.quantize(decimal.Decimal('0.00000001'))
```

### 4.2 Waypoint Database Sources (Download These First)

**Step 0 before any parsing**: download the canonical waypoint databases. These are the ground truth for all coordinate lookups.

#### United States — FAA NASR CIFP (Free, No Registration)

```bash
# Current AIRAC cycle CIFP (ARINC 424 format)
# Replace YYMM with current cycle (e.g. 2606 for June 2026)
wget "https://aeronav.faa.gov/NASR_Subscription/CIFP/CIFP_$(date +%y%m%d).zip"

# Also download the Fix database (named waypoints only, CSV format)
wget "https://aeronav.faa.gov/Upload_313-d/supplements/NASR_Subscription_$(date +%Y-%m-%d).zip"
```

CIFP file sections relevant to this project:
- `EA` records: Enroute airways
- `PC` records: Terminal procedure (SID) legs — **primary source**
- `PD` records: Terminal procedure (STAR) legs — **primary source**
- `PE` records: Instrument approach procedure legs
- `WP` records: Enroute waypoints with exact WGS-84 coordinates
- `PN` records: Terminal NDB navaids
- `PG` records: Runway geometry

#### Europe — EUROCONTROL EAD Basic (Free, Registration Required)

Register at `https://www.ead.eurocontrol.int/` → request EAD Basic access (approved within 24–48h). EAD Basic provides:
- AIXM 4.5 XML exports of all ECAC state significant points
- Procedure data for 50+ European states
- Updated every AIRAC cycle

```python
# EAD Basic API endpoint (after registration)
EAD_BASE_URL = "https://www.ead.eurocontrol.int/ead/api/v1"
# Auth: username/password → Bearer token
# Endpoint: GET /aip/{state_icao}/procedures?cycle={airac_cycle}
```

#### Canada — NAV CANADA NASR-equivalent

NAV CANADA publishes coded procedure data (ARINC 424 format) via:
- `https://www.navcanada.ca/en/aeronautical-information/flight-planning.aspx`
- Free download, updated every 28 days

#### Hong Kong — CAAS eAIP (Public, No Registration)

```
Base URL: https://eaip.cad.gov.hk/
Structure: /eaip/{AIRAC_DATE}/html/index-en-HK.html
Procedure charts: /eaip/{AIRAC_DATE}/graphics/
```

Waypoint coordinates for HK: cross-reference against ICAO Asia-Pacific regional significant point database. HK procedures use the same 5-letter ICAO waypoint identifiers that appear in the EAD/NASR databases for shared waypoints (e.g. oceanic fixes appear in all databases).

#### China — CAAC (Restricted)

No public API or download portal. Options:
1. **Jeppesen NavData** subscription (commercial, expensive — not for PoC)
2. **CAAC AIP PDF via academic/archived sources** — flag as `confidence: LOW`
3. **Navigraph** subscription data (simulator-grade, not certified — acceptable for this project's scope)

For ZSPD Phase 0 PoC: use Navigraph or publicly archived CAAC charts. Treat as Tier 3, consensus-scored only.

### 4.3 Path Terminator Codes (ARINC 424-18)[cite:222][cite:234]

Every procedure leg has a 2-letter path terminator. The validator rejects any leg with an unrecognised terminator.

| Code | Name | Description |
|---|---|---|
| `IF` | Initial Fix | Start of procedure or transition — fix only, no path |
| `TF` | Track to Fix | Great circle track to a fix (most common) |
| `CF` | Course to Fix | Specified magnetic course to a fix |
| `DF` | Direct to Fix | Direct (unspecified track) to a fix |
| `FA` | Fix to Altitude | From fix, fly heading to crossing altitude |
| `FC` | Track from Fix for Distance | Track from fix for specified distance |
| `FD` | Track from Fix to DME Distance | Track from fix to DME distance |
| `FM` | From Fix to Manual Termination | Fly heading until pilot terminates |
| `CA` | Course to Altitude | Fly course until altitude |
| `CD` | Course to DME Distance | Fly course to DME distance |
| `CI` | Course to Intercept | Fly course to intercept next leg |
| `CR` | Course to Radial | Fly course to intercept VOR radial |
| `RF` | Constant Radius Arc to Fix | Radius turn to fix (RNP AR) |
| `AF` | Arc to Fix | DME arc to fix |
| `VA` | Heading to Altitude | Fly runway heading to altitude |
| `VD` | Heading to DME Distance | Fly heading to DME distance |
| `VI` | Heading to Intercept | Fly heading to intercept next leg |
| `VM` | Heading to Manual Termination | Fly heading until ATC terminates |
| `VR` | Heading to Radial | Fly heading to intercept VOR radial |
| `HA` | Holding to Altitude | Holding pattern to altitude |
| `HF` | Holding to Fix | Single-turn holding pattern |
| `HM` | Holding, Manual Termination | Holding until ATC terminates |
| `PI` | Procedure Turn | 45°/180° procedure turn |

### 4.4 Altitude Constraint Encoding

**ICAO PANS-OPS Vol II, ARINC 424 Section 5.73**[cite:246]:

| ARINC 424 code | Meaning | AIXM 5.2 encoding |
|---|---|---|
| `+` | At or above | `AT_OR_ABOVE` |
| `-` | At or below | `AT_OR_BELOW` |
| `B` | Between (window) | `BETWEEN` |
| ` ` (blank) | At (mandatory crossing) | `AT` |

Altitude values are always in **feet MSL** (AMSL) in ARINC 424 and AIXM 5.2 for IFP data. Flight levels (e.g. FL150) are stored as feet (15000). Never store as metres in the AIXM 5.2 output — the `uom="FT"` attribute must always be present.

```python
class AltConstraint(BaseModel):
    type: Literal["AT", "AT_OR_ABOVE", "AT_OR_BELOW", "BETWEEN"]
    lower_ft: int                    # mandatory — feet MSL
    upper_ft: Optional[int] = None  # only for BETWEEN
    uom: Literal["FT"] = "FT"       # always feet, never metres
```

### 4.5 Speed Constraint Encoding

Speed constraints in IFP data are in **knots indicated airspeed (KIAS)**[cite:246].

| ARINC 424 code | Meaning | AIXM 5.2 encoding |
|---|---|---|
| `+` | At or above | `AT_OR_ABOVE` |
| `-` | At or below (most common) | `AT_OR_BELOW` |
| ` ` (blank) | At (mandatory) | `AT` |

```python
class SpeedConstraint(BaseModel):
    type: Literal["AT", "AT_OR_ABOVE", "AT_OR_BELOW"]
    value_kt: int           # knots IAS
    uom: Literal["KT"] = "KT"
```

### 4.6 PBN Navigation Specification Codes

From ICAO Doc 9613 (PBN Manual) and ARINC 424-18 NavSpec field[cite:246]:

| Code | Specification | Typical use |
|---|---|---|
| `A1` | RNP APCH | RNAV approach (non-AR) |
| `B1` | RNP AR APCH (RF capability not required) | AR approach, no RF legs |
| `B2` | RNP AR APCH (RF capability required) | AR approach with RF legs |
| `C1` | RNAV 2 (GNSS) | SID/STAR |
| `C2` | RNAV 2 (DME/DME) | SID/STAR |
| `C3` | RNAV 2 (DME/DME/IRU) | SID/STAR |
| `D1` | RNAV 1 (GNSS) | SID/STAR — most common |
| `D2` | RNAV 1 (DME/DME) | SID/STAR |
| `D3` | RNAV 1 (DME/DME/IRU) | SID/STAR |
| `E1` | RNP 4 | Oceanic/remote |
| `L1` | ILS | ILS approach |
| `O1` | RNP 1 (GNSS) | Terminal, departure |
| `P1` | RNP 0.3 (GNSS) | Helicopter, short-haul |
| `T1` | RNP/RNAV 10 | Oceanic |

### 4.7 Waypoint Overfly Flag

ICAO PANS-OPS and ARINC 424 distinguish fly-by vs fly-over waypoints[cite:248]:

```python
class WaypointFlag(str, Enum):
    FLY_BY  = "FLY_BY"    # Aircraft may begin turn before reaching waypoint
    FLY_OVER = "FLY_OVER" # Aircraft must cross waypoint before turning
```

This affects RNP/RNAV procedure design and must be preserved in AIXM 5.2 output (`turnDirection` and `overfly` attributes on `InstrumentApproachProcedure/ProcedureLeg`).

### 4.8 Complete Pydantic Schemas

```python
# miner/schemas/aixm52_procedure.py
from pydantic import BaseModel, field_validator
from typing import Optional, Literal
from enum import Enum
import decimal

class WGS84Point(BaseModel):
    """
    WGS-84 coordinates stored to 8 decimal places (lossless from ARINC 424 DMS).
    Source: always the NASR CIFP / EAD waypoint database — NEVER chart extraction.
    """
    lat: decimal.Decimal   # e.g. Decimal('34.06030556')
    lon: decimal.Decimal   # e.g. Decimal('-118.53327222')
    waypoint_id: str       # 5-letter ICAO identifier (e.g. 'SADDE') or lat/lon code
    waypoint_type: Literal["NAMED", "UNNAMED_LATLON", "NAVAID", "AIRPORT", "RUNWAY"]
    source_db: Literal["NASR_CIFP", "EAD", "NAVCANADA", "CAAS_HK", "CONSENSUS"]

    @field_validator('lat')
    @classmethod
    def lat_range(cls, v):
        if not (-90 <= v <= 90):
            raise ValueError(f'Latitude {v} out of range [-90, 90]')
        return v

    @field_validator('lon')
    @classmethod
    def lon_range(cls, v):
        if not (-180 <= v <= 180):
            raise ValueError(f'Longitude {v} out of range [-180, 180]')
        return v

class AltConstraint(BaseModel):
    type: Literal["AT", "AT_OR_ABOVE", "AT_OR_BELOW", "BETWEEN"]
    lower_ft: int
    upper_ft: Optional[int] = None  # only for BETWEEN
    uom: Literal["FT"] = "FT"

class SpeedConstraint(BaseModel):
    type: Literal["AT", "AT_OR_ABOVE", "AT_OR_BELOW"]
    value_kt: int
    uom: Literal["KT"] = "KT"

class ProcedureLeg(BaseModel):
    sequence_number: int
    path_terminator: Literal[
        "IF","TF","CF","DF","FA","FC","FD","FM",
        "CA","CD","CI","CR","RF","AF",
        "VA","VD","VI","VM","VR",
        "HA","HF","HM","PI"
    ]
    waypoint: Optional[WGS84Point] = None  # None for heading-terminator legs (VA, CA, etc.)
    waypoint_flag: Optional[Literal["FLY_BY", "FLY_OVER"]] = None
    alt_constraint: Optional[AltConstraint] = None
    speed_constraint: Optional[SpeedConstraint] = None
    course_magnetic: Optional[float] = None   # degrees magnetic
    course_true: Optional[float] = None        # degrees true
    distance_nm: Optional[float] = None
    turn_direction: Optional[Literal["L", "R"]] = None
    center_fix: Optional[WGS84Point] = None   # RF legs only
    radius_nm: Optional[float] = None          # RF legs only

class RunwayTransition(BaseModel):
    runway_designator: str     # e.g. '24L', '06R'
    transition_id: str         # e.g. 'KLAX6.KARIN'
    legs: list[ProcedureLeg]

class EnrouteTransition(BaseModel):
    transition_fix: str        # 5-letter identifier
    transition_id: str
    legs: list[ProcedureLeg]

class SIDRecord(BaseModel):
    record_type: Literal["SID"] = "SID"
    airport_icao: str              # e.g. 'KLAX'
    procedure_name: str            # e.g. 'LOOP6'
    runway_transitions: list[RunwayTransition]
    common_route: list[ProcedureLeg]
    enroute_transitions: list[EnrouteTransition]
    pbn_nav_spec: Optional[str]    # e.g. 'D1' = RNAV 1 GNSS
    airac_cycle: str               # e.g. '2606'
    extraction_confidence: float   # 0.0–1.0
    source_url: str
    coverage_source: Literal["miner", "sentinel_fallback"] = "miner"
    extracted_at: str              # ISO 8601

class STARRecord(BaseModel):
    record_type: Literal["STAR"] = "STAR"
    airport_icao: str
    procedure_name: str
    enroute_transitions: list[EnrouteTransition]
    common_route: list[ProcedureLeg]
    runway_transitions: list[RunwayTransition]
    pbn_nav_spec: Optional[str]
    airac_cycle: str
    extraction_confidence: float
    source_url: str
    coverage_source: Literal["miner", "sentinel_fallback"] = "miner"
    extracted_at: str

class IAPRecord(BaseModel):
    record_type: Literal["IAP"] = "IAP"
    airport_icao: str
    procedure_name: str            # e.g. 'ILS Z RWY 24L'
    approach_type: Literal[
        "ILS","LOC","LOC_BC","LDA","SDF",
        "RNAV_GNSS","RNAV_RNP","RNP_AR",
        "VOR","VOR_DME","TACAN","NDB","NDB_DME",
        "VISUAL"
    ]
    runway_designator: str
    initial_approach_segments: list[ProcedureLeg]
    intermediate_segment: list[ProcedureLeg]
    final_segment: list[ProcedureLeg]
    missed_approach_segment: list[ProcedureLeg]
    decision_altitude_ft: Optional[int] = None   # DA for precision
    minimum_descent_altitude_ft: Optional[int] = None  # MDA for non-precision
    visibility_rvr_m: Optional[int] = None
    pbn_nav_spec: Optional[str]
    airac_cycle: str
    extraction_confidence: float
    source_url: str
    coverage_source: Literal["miner", "sentinel_fallback"] = "miner"
    extracted_at: str
```

---

## 5. Validation Rules (Hard Reject)

The validator rejects any submission that fails these checks before scoring:

```python
def validate_submission(record: SIDRecord | STARRecord | IAPRecord) -> list[str]:
    """Returns list of errors. Empty list = passes hard gate."""
    errors = []

    # 1. All named waypoints must have source_db != "CONSENSUS" for US airports
    #    (US waypoints are always in NASR CIFP — no guessing allowed)
    if record.airport_icao.startswith('K') or record.airport_icao.startswith('P'):
        for leg in all_legs(record):
            if leg.waypoint and leg.waypoint.waypoint_type == "NAMED":
                if leg.waypoint.source_db == "CONSENSUS":
                    errors.append(f"Leg {leg.sequence_number}: US named waypoint "
                                  f"{leg.waypoint.waypoint_id} must use NASR_CIFP source")

    # 2. Altitude values must be integers (feet MSL)
    for leg in all_legs(record):
        if leg.alt_constraint:
            if leg.alt_constraint.uom != "FT":
                errors.append(f"Leg {leg.sequence_number}: altitude uom must be FT")

    # 3. Speed values must be positive integers
    for leg in all_legs(record):
        if leg.speed_constraint:
            if leg.speed_constraint.value_kt <= 0 or leg.speed_constraint.value_kt > 999:
                errors.append(f"Leg {leg.sequence_number}: invalid speed "
                              f"{leg.speed_constraint.value_kt} kt")

    # 4. RF legs must have center_fix and radius_nm
    for leg in all_legs(record):
        if leg.path_terminator == "RF":
            if not leg.center_fix or not leg.radius_nm:
                errors.append(f"Leg {leg.sequence_number}: RF leg missing center_fix or radius")

    # 5. Waypoint coordinates must be within country bounding box
    bbox = get_country_bbox(record.airport_icao[:2])
    for leg in all_legs(record):
        if leg.waypoint:
            if not bbox.contains(leg.waypoint.lat, leg.waypoint.lon):
                errors.append(f"Waypoint {leg.waypoint.waypoint_id} outside "
                              f"country bounding box for {record.airport_icao}")

    return errors
```

---

## 6. Scoring Logic

```python
def score_miner_cycle(miner_uid: int, cycle: str,
                      assigned: list[str], submissions: dict) -> float:
    """Hard coverage gate then accuracy scoring."""

    # Gate 1: Coverage — zero tolerance
    missing = set(assigned) - set(submissions.keys())
    if missing:
        log_omission(miner_uid, cycle, missing)
        return 0.0

    scores = []
    for icao, procedures in submissions.items():
        for record in procedures:

            # Gate 2: Schema + hard validation rules
            errors = validate_submission(record)
            if errors:
                scores.append(0.0)
                continue

            gt = get_ground_truth(icao, cycle)  # NASR (US) / EAD (EU) / None

            if gt:
                scores.append(score_vs_ground_truth(record, gt))
            else:
                scores.append(score_by_consensus(record, icao, cycle))

    return sum(scores) / len(scores) if scores else 0.0


def score_vs_ground_truth(extracted: dict, gt: dict) -> float:
    """
    Coordinate scoring: exact database lookup match only.
    A waypoint either matches the NASR/EAD record or it does not.
    """
    scores = []
    for leg_e in extracted_legs(extracted):
        leg_gt = find_leg(leg_e.sequence_number, gt)
        if not leg_gt:
            continue

        # Waypoint: exact match against database record
        if leg_e.waypoint and leg_gt.waypoint:
            lat_match = leg_e.waypoint.lat == leg_gt.waypoint.lat
            lon_match = leg_e.waypoint.lon == leg_gt.waypoint.lon
            id_match  = leg_e.waypoint.waypoint_id == leg_gt.waypoint.waypoint_id
            scores.append(1.0 if (lat_match and lon_match and id_match) else 0.0)

        # Path terminator: exact match
        scores.append(1.0 if leg_e.path_terminator == leg_gt.path_terminator else 0.0)

        # Altitude constraint
        if leg_e.alt_constraint and leg_gt.alt_constraint:
            alt_match = (leg_e.alt_constraint.type == leg_gt.alt_constraint.type and
                        leg_e.alt_constraint.lower_ft == leg_gt.alt_constraint.lower_ft)
            scores.append(1.0 if alt_match else 0.0)

        # Speed constraint
        if leg_e.speed_constraint and leg_gt.speed_constraint:
            spd_match = (leg_e.speed_constraint.type == leg_gt.speed_constraint.type and
                        leg_e.speed_constraint.value_kt == leg_gt.speed_constraint.value_kt)
            scores.append(1.0 if spd_match else 0.0)

    # Field completeness
    required = ["procedure_name", "runway_transitions", "pbn_nav_spec"]
    completeness = sum(1 for f in required if getattr(extracted, f, None)) / len(required)
    scores.append(completeness)

    return sum(scores) / len(scores) if scores else 0.0
```

---

## 7. Data Source Pipeline per Airport (Phase 0)

### KLAX — United States (Ground Truth)

```python
# Step 1: Download NASR CIFP
NASR_URL = "https://aeronav.faa.gov/NASR_Subscription/CIFP/CIFP_{cycle}.zip"

# Step 2: Parse CIFP for KLAX waypoints (section WP + PN records)
# Step 3: Build waypoint lookup dict: {identifier: WGS84Point}
# Step 4: Download d-TPP PDF for KLAX procedure chart
DTPP_URL = "https://aeronav.faa.gov/d-tpp/{cycle}/00397{proc_code}.PDF"
# Step 5: Render PDF → images (pymupdf)
# Step 6: LLM extracts: procedure name, leg sequence, path terminators,
#          waypoint identifiers, altitude constraints, speed constraints, PBN spec
#          — LLM is NEVER asked for coordinates
# Step 7: For each waypoint identifier from LLM → lookup coords in NASR CIFP dict
# Step 8: Assemble SIDRecord / STARRecord with exact NASR coordinates
# Step 9: Validate schema, score vs NASR CIFP ground truth
```

### VHHH — Hong Kong

```python
# Step 1: Fetch CAAS HK eAIP HTML
CAAS_BASE = "https://eaip.cad.gov.hk/eaip/{date}/html/"
# Step 2: Parse HTML for procedure tables (BS4/lxml)
# Step 3: LLM extracts procedure structure from HTML text
# Step 4: Waypoint coordinates: lookup in EAD Basic or ICAO APAC regional DB
# Step 5: Assemble records, flag source_db = "CAAS_HK"
```

### EGLL — United Kingdom

```python
# Step 1: Fetch NATS eAIP via EAD Basic API (registration required)
EAD_EGLL = "https://www.nats-uk.ead-it.com/aip/current/html/index-en-GB.html"
# Step 2: Parse eAIP HTML procedure sections
# Step 3: LLM extracts procedure structure
# Step 4: Waypoint coordinates: EAD Basic AIXM export (source_db = "EAD")
# Step 5: Assemble records, score vs EAD reference
```

### CYVR — Canada

```python
# Step 1: Fetch NAV CANADA eAIP
NAVCAN_BASE = "https://www.navcanada.ca/en/aeronautical-information/aip-canada.aspx"
# Step 2: Download CIFP-equivalent (ARINC 424 format)
# Step 3: Same pipeline as KLAX — CIFP lookup for all waypoint coordinates
```

### ZSPD — China (Tier 3, No Ground Truth)

```python
# Step 1: Obtain CAAC PDF (archived source, Navigraph, or simulator data)
# Step 2: Render PDF → images (pymupdf)
# Step 3: Run extraction with 3 different models via LiteLLM:
#   Model A: ollama/qwen2.5-vl (multimodal, Chinese script capable)
#   Model B: gpt-4o (or any capable vision model)
#   Model C: ollama/llama3.3 (text fallback if chart text is OCR'd)
# Step 4: For coordinates — attempt lookup in global ICAO waypoint database
#   (shared waypoints like oceanic fixes appear in NASR CIFP)
# Step 5: Unavailable coordinates: extract from chart text, flag as CONSENSUS
# Step 6: Majority vote across 3 model outputs
# Step 7: Flag entire ZSPD record: confidence: LOW, coverage_source: sentinel_fallback
```

---

## 8. File Structure

```
aeroaip/
├── CLAUDE.md                         ← This file
├── .cursorrules                      ← Same content for Cursor
├── .env.example
│
├── data/
│   ├── nasr/                         ← FAA NASR CIFP downloads (gitignored)
│   ├── ead/                          ← EAD Basic exports (gitignored)
│   ├── navcanada/                    ← NAV CANADA CIFP (gitignored)
│   └── waypoints/
│       └── global_waypoint_index.db  ← SQLite: all named waypoints → WGS84 coords
│
├── miner/
│   ├── miner.py
│   ├── extractor/
│   │   ├── base.py
│   │   ├── eaip_html.py
│   │   ├── faa_dtpp.py
│   │   ├── aixm_upconverter.py
│   │   └── llm_router.py             ← LiteLLM, MINER_LLM env var
│   ├── schemas/
│   │   └── aixm52_procedure.py       ← All Pydantic schemas (Section 4.8)
│   ├── waypoint_db.py                ← Global waypoint lookup (NASR + EAD + regional)
│   └── country_configs/
│       ├── K.yaml                    ← USA: FAA d-TPP + NASR CIFP
│       ├── EG.yaml                   ← UK: NATS eAIP + EAD
│       ├── LF.yaml                   ← France: SIA eAIP + EAD
│       ├── VH.yaml                   ← Hong Kong: CAAS eAIP
│       ├── C.yaml                    ← Canada: NAV CANADA eAIP + CIFP
│       └── Z.yaml                    ← China: CAAC PDF, Tier 3
│
├── validator/
│   ├── validator.py
│   ├── scoring.py                    ← Exact match scoring (Section 6)
│   ├── validation_rules.py           ← Hard reject rules (Section 5)
│   ├── task_assignment.py
│   ├── consensus.py
│   └── ground_truth/
│       ├── nasr_cifp.py
│       └── ead_fetcher.py
│
├── aggregator/
│   ├── db/schema.sql                 ← PostGIS, AIRAC-versioned
│   ├── api/main.py                   ← FastAPI
│   └── archive.py                    ← IPFS snapshots
│
├── visualization/
│   ├── cesium/index.html             ← CesiumJS 3D globe (use Cursor for this)
│   └── kml_export.py
│
├── scripts/
│   ├── bootstrap_testnet.sh
│   ├── download_waypoint_dbs.sh      ← Downloads NASR + EAD + NAV CANADA
│   ├── build_waypoint_index.py       ← Merges all sources → global_waypoint_index.db
│   ├── airac_scheduler.py
│   └── sentinel_coverage.py
│
└── tests/
    ├── test_coordinate_precision.py  ← ARINC 424 DMS → decimal (exact)
    ├── test_schema_validation.py     ← All Pydantic schema edge cases
    ├── test_faa_extraction.py        ← KLAX extraction vs NASR ground truth
    ├── test_scoring.py
    └── test_coverage.py
```

---

## 9. Phase Plan

### Phase 0 — Proof of Concept (Weeks 1–2)

**Goal**: Prove LLM extracts correct procedure *structure* (leg sequence, terminators, constraints) from charts while the waypoint *coordinates* come from the NASR CIFP database — and that these combine into a schema-valid AIXM 5.2 output that scores 100% on coordinate accuracy.

**First build task for coding agent**:

```
BUILD: scripts/download_waypoint_dbs.sh + scripts/build_waypoint_index.py

These two scripts are the foundation of the entire project.

download_waypoint_dbs.sh:
  - Download current FAA NASR CIFP zip from aeronav.faa.gov
  - Download EAD Basic AIXM export (manual step — print instruction to register)
  - Download NAV CANADA CIFP from navcanada.ca
  - Save all to data/nasr/, data/ead/, data/navcanada/

build_waypoint_index.py:
  - Parse NASR CIFP WP + PN records → extract all named waypoints with WGS84 coords
  - Parse EAD AIXM XML → extract all European DesignatedPoint records
  - Merge into SQLite database: data/waypoints/global_waypoint_index.db
  - Schema: (waypoint_id TEXT, region TEXT, lat REAL, lon REAL, source TEXT, airac TEXT)
  - Index on waypoint_id for fast lookup during extraction
  - Print: total waypoints loaded per source

THEN BUILD: poc_extraction.py for KLAX LOOP6 SID:
  - Download FAA d-TPP PDF: https://aeronav.faa.gov/d-tpp/2606/00397LOOP.PDF
  - Render pages to images (pymupdf)
  - Send chart image to MINER_LLM (default: ollama/qwen2.5-vl)
  - LLM task: extract procedure name, all leg sequence numbers, path terminators,
    waypoint IDENTIFIERS (5-letter codes), altitude constraints, speed constraints,
    PBN spec, runway transitions — NEVER ask LLM for coordinates
  - For each waypoint identifier: lookup exact coords in global_waypoint_index.db
  - Assemble SIDRecord with exact NASR coordinates
  - Load NASR CIFP PC records for KLAX LOOP6 as ground truth
  - Score: exact match on waypoint_id + coordinates + path_terminator + constraints
  - Print: score per leg, overall score, any mismatches

Success criterion: 100% coordinate match (exact database lookup), >90% constraint match
```

### Phase 1 — Pipeline Build (Weeks 3–6)

1. Generalise FAA extractor to all US SID/STAR/IAP chart types
2. Build eAIP HTML parsers: EGLL (NATS), VHHH (CAAS), CYVR (NAV CANADA)
3. Build AIXM 5.2 XML serializer from Pydantic records
4. Build PostGIS schema (AIRAC-versioned, spatial indexing)
5. Build validator scoring (Section 6) — exact match for US/EU, consensus for CN
6. Build AIRAC scheduler (28-day cron)

### Phase 2 — Testnet (Weeks 7–10)

1. Run `bootstrap_testnet.sh` → register subnet, wallets, faucet tTAO
2. Run validator + sentinel on single DO droplet (testnet)
3. Process one full US cycle (all K__ airports)
4. Verify coverage, scoring, on-chain weights
5. Recruit 3–5 external testnet miners (Bittensor Discord)

### Phase 3 — Demo (Weeks 11–12)

1. CesiumJS: ICAO input → 3D SID/STAR paths with altitude constraints rendered (use Cursor)
2. KML export endpoint (Google Earth)
3. Demo video: CAAC/FAA chart → AIXM 5.2 JSON → 3D globe in <30 seconds
4. Public URL → approach TAO validator contacts for staking commitments

### Phase 4 — Mainnet (Week 13+)

1. Monitor `taostats.io/subnets` for registration cost trough (<300 TAO target)
2. Recruit 5+ founding miners pre-launch
3. Register subnet on mainnet (HK entity, owner wallet)
4. Begin global production AIRAC operations

---

## 10. Environment Variables

```bash
# ── MINER ────────────────────────────────────────────────────────────────────
MINER_LLM=ollama/qwen2.5-vl         # Any LiteLLM model — miner's choice, no cloud required
MINER_WALLET_NAME=miner
MINER_HOTKEY_NAME=default
NETUID=<your_subnet_netuid>
SUBTENSOR_NETWORK=finney             # "test" for testnet

# ── WAYPOINT DATABASE ────────────────────────────────────────────────────────
WAYPOINT_DB_PATH=data/waypoints/global_waypoint_index.db
NASR_DATA_DIR=data/nasr
EAD_DATA_DIR=data/ead
NAVCAN_DATA_DIR=data/navcanada

# ── VALIDATOR ────────────────────────────────────────────────────────────────
VALIDATOR_WALLET_NAME=validator
VALIDATOR_HOTKEY_NAME=default
EAD_API_KEY=<ead_basic_api_key>      # From free EAD Basic registration

# ── AGGREGATOR ───────────────────────────────────────────────────────────────
DATABASE_URL=postgresql://user:pass@localhost:5432/aeroaip
IPFS_TOKEN=<optional>

# ── SENTINEL MINER ───────────────────────────────────────────────────────────
SENTINEL_LLM=ollama/qwen2.5-vl       # Local — no cloud dependency
SENTINEL_TRIGGER_HOURS=72
SENTINEL_WALLET_NAME=sentinel_miner
```

---

## 11. Economics (HK Entity)

| Item | Value |
|---|---|
| Operating entity | Hong Kong private limited company |
| TAO price (June 2026) | ~$236 |
| Subnet registration | ~1,500 TAO (~$354K, locked/recoverable) |
| Monthly opex | ~$313/mo |
| Base net income (1.2% share) | ~$992K/yr |
| HK profits tax (territorial) | 16.5%, offshore income potentially exempt |
| Target registration trough | <300 TAO — monitor taostats.io |

---

## 12. Key External References

| Resource | URL |
|---|---|
| FAA d-TPP procedures | `https://aeronav.faa.gov/d-tpp/` |
| FAA NASR CIFP | `https://aeronav.faa.gov/NASR_Subscription/` |
| EUROCONTROL EAD Basic | `https://www.ead.eurocontrol.int/` |
| CAAS HK eAIP | `https://eaip.cad.gov.hk/` |
| NAV CANADA AIP | `https://www.navcanada.ca/en/aeronautical-information/` |
| France SIA open data | `https://www.data.gouv.fr/datasets/donnees-despace-aerien-de-la-base-aeronautique-du-sia` |
| OurAirports (spine) | `https://ourairports.com/data/` |
| AIXM 5.2 specification | `https://aixm.aero/page/aixm-52-specification` |
| Bittensor subnet template | `https://github.com/opentensor/bittensor-subnet-template` |
| Bittensor testnet | `wss://test.finney.opentensor.ai:443` |
| Subnet cost monitor | `https://taostats.io/subnets` |
| LiteLLM (model router) | `https://github.com/BerriAI/litellm` |
| aipp Ruby gem (AIP parsers) | `https://github.com/svoop/aipp` |
| pyaixm | `https://github.com/volkerp/pyaixm` |
| ARINC 424-18 shorthand ref | `https://code7700.com/arinc_424_shorthand.htm` |
