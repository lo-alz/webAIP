-- AeroAIP aggregator — AIRAC-versioned PostGIS schema (Stage 4: the structured
-- store for AIXM 5.2 procedure data). Every row is tagged with its 28-day AIRAC
-- cycle so historical cycles are retained and queries are cycle-scoped.
--
-- Geometry is WGS-84 (SRID 4326). Coordinates are ALSO kept as NUMERIC(11,8)
-- (the canonical 8-dp lossless value from the waypoint DB) so exact-equality
-- checks survive the round-trip; the geometry column powers spatial queries and
-- GeoJSON export for the CesiumJS / Google Earth visualisations.

CREATE EXTENSION IF NOT EXISTS postgis;

-- One row per published procedure per AIRAC cycle.
CREATE TABLE IF NOT EXISTS procedure (
    id                    BIGSERIAL PRIMARY KEY,
    airac                 TEXT NOT NULL,
    airport_icao          TEXT NOT NULL,
    record_type           TEXT NOT NULL CHECK (record_type IN ('SID','STAR','IAP')),
    procedure_name        TEXT NOT NULL,
    approach_type         TEXT,              -- IAP only
    runway_designator     TEXT,              -- IAP only (transitions hold their own)
    pbn_nav_spec          TEXT,
    extraction_confidence REAL,
    coverage_source       TEXT,              -- 'miner' | 'sentinel_fallback'
    source_url            TEXT,
    extracted_at          TIMESTAMPTZ,
    ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (airac, airport_icao, record_type, procedure_name)
);
CREATE INDEX IF NOT EXISTS idx_procedure_apt_airac ON procedure (airport_icao, airac);

-- Distinct waypoints referenced in a cycle (spatial dedup target).
CREATE TABLE IF NOT EXISTS waypoint (
    airac        TEXT NOT NULL,
    waypoint_id  TEXT NOT NULL,
    region       TEXT NOT NULL DEFAULT '',
    source_db    TEXT NOT NULL,
    lat          NUMERIC(11,8) NOT NULL,
    lon          NUMERIC(11,8) NOT NULL,
    geom         geometry(Point, 4326) NOT NULL,
    PRIMARY KEY (airac, waypoint_id, region)
);
CREATE INDEX IF NOT EXISTS idx_waypoint_geom ON waypoint USING GIST (geom);

-- One row per procedure leg, in segment + sequence order.
CREATE TABLE IF NOT EXISTS procedure_leg (
    id                BIGSERIAL PRIMARY KEY,
    procedure_id      BIGINT NOT NULL REFERENCES procedure(id) ON DELETE CASCADE,
    segment           TEXT NOT NULL,         -- 'RWY:09R' | 'COMMON' | 'ENR:DET' | 'final_segment' ...
    sequence_number   INT NOT NULL,
    path_terminator   TEXT NOT NULL,         -- ARINC 424 2-letter terminator
    waypoint_id       TEXT,                  -- NULL for heading-terminator legs
    waypoint_type     TEXT,
    source_db         TEXT,                  -- NASR_CIFP | EAD | CAAS_HK | CONSENSUS ...
    waypoint_flag     TEXT,                  -- FLY_BY | FLY_OVER
    alt_type          TEXT,
    alt_lower_ft      INT,
    alt_upper_ft      INT,
    speed_type        TEXT,
    speed_kt          INT,
    course_magnetic   REAL,
    distance_nm       REAL,
    turn_direction    TEXT,
    radius_nm         REAL,
    lat               NUMERIC(11,8),
    lon               NUMERIC(11,8),
    geom              geometry(Point, 4326)  -- NULL for heading legs
);
CREATE INDEX IF NOT EXISTS idx_leg_procedure ON procedure_leg (procedure_id, segment, sequence_number);
CREATE INDEX IF NOT EXISTS idx_leg_geom ON procedure_leg USING GIST (geom);
