-- ============================================================
-- PostgreSQL schema for Synthea patient data
-- ============================================================

DROP TABLE IF EXISTS payer_transitions CASCADE;
DROP TABLE IF EXISTS supplies        CASCADE;
DROP TABLE IF EXISTS imaging_studies  CASCADE;
DROP TABLE IF EXISTS devices          CASCADE;
DROP TABLE IF EXISTS immunizations    CASCADE;
DROP TABLE IF EXISTS careplans        CASCADE;
DROP TABLE IF EXISTS observations     CASCADE;
DROP TABLE IF EXISTS procedures       CASCADE;
DROP TABLE IF EXISTS allergies        CASCADE;
DROP TABLE IF EXISTS medications      CASCADE;
DROP TABLE IF EXISTS conditions       CASCADE;
DROP TABLE IF EXISTS encounters       CASCADE;
DROP TABLE IF EXISTS providers        CASCADE;
DROP TABLE IF EXISTS organizations    CASCADE;
DROP TABLE IF EXISTS payers           CASCADE;
DROP TABLE IF EXISTS patients         CASCADE;

-- Reference / lookup tables

CREATE TABLE organizations (
    id          UUID PRIMARY KEY,
    name        TEXT NOT NULL,
    address     TEXT,
    city        TEXT,
    state       TEXT,
    zip         TEXT,
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    phone       TEXT,
    revenue     NUMERIC(14,2),
    utilization INTEGER
);

CREATE TABLE payers (
    id                      UUID PRIMARY KEY,
    name                    TEXT NOT NULL,
    address                 TEXT,
    city                    TEXT,
    state_headquartered     TEXT,
    zip                     TEXT,
    phone                   TEXT,
    amount_covered          NUMERIC(14,2),
    amount_uncovered        NUMERIC(14,2),
    revenue                 NUMERIC(14,2),
    covered_encounters      INTEGER,
    uncovered_encounters    INTEGER,
    covered_medications     INTEGER,
    uncovered_medications   INTEGER,
    covered_procedures      INTEGER,
    uncovered_procedures    INTEGER,
    covered_immunizations   INTEGER,
    uncovered_immunizations INTEGER,
    unique_customers        INTEGER,
    qols_avg                DOUBLE PRECISION,
    member_months           INTEGER
);

CREATE TABLE providers (
    id           UUID PRIMARY KEY,
    organization UUID REFERENCES organizations(id),
    name         TEXT,
    gender       CHAR(1),
    speciality   TEXT,
    address      TEXT,
    city         TEXT,
    state        TEXT,
    zip          TEXT,
    lat          DOUBLE PRECISION,
    lon          DOUBLE PRECISION,
    utilization  INTEGER
);

-- Core patient table

CREATE TABLE patients (
    id                  UUID PRIMARY KEY,
    birthdate           DATE NOT NULL,
    deathdate           DATE,
    ssn                 TEXT,
    drivers             TEXT,
    passport            TEXT,
    prefix              TEXT,
    first_name          TEXT NOT NULL,
    last_name           TEXT NOT NULL,
    suffix              TEXT,
    maiden              TEXT,
    marital             CHAR(1),
    race                TEXT,
    ethnicity           TEXT,
    gender              CHAR(1) NOT NULL,
    birthplace          TEXT,
    address             TEXT,
    city                TEXT,
    state               TEXT,
    county              TEXT,
    zip                 TEXT,
    lat                 DOUBLE PRECISION,
    lon                 DOUBLE PRECISION,
    healthcare_expenses NUMERIC(14,2),
    healthcare_coverage NUMERIC(14,2)
);

-- Clinical event tables

CREATE TABLE encounters (
    id                  UUID PRIMARY KEY,
    start_ts            TIMESTAMPTZ NOT NULL,
    stop_ts             TIMESTAMPTZ,
    patient             UUID NOT NULL REFERENCES patients(id),
    organization        UUID REFERENCES organizations(id),
    provider            UUID REFERENCES providers(id),
    payer               UUID REFERENCES payers(id),
    encounterclass      TEXT,
    code                TEXT,
    description         TEXT,
    base_encounter_cost NUMERIC(14,2),
    total_claim_cost    NUMERIC(14,2),
    payer_coverage      NUMERIC(14,2),
    reasoncode          TEXT,
    reasondescription   TEXT
);

CREATE TABLE conditions (
    id          SERIAL PRIMARY KEY,
    start_date  DATE NOT NULL,
    stop_date   DATE,
    patient     UUID NOT NULL REFERENCES patients(id),
    encounter   UUID NOT NULL REFERENCES encounters(id),
    code        TEXT NOT NULL,
    description TEXT
);

CREATE TABLE medications (
    id                SERIAL PRIMARY KEY,
    start_ts          TIMESTAMPTZ NOT NULL,
    stop_ts           TIMESTAMPTZ,
    patient           UUID NOT NULL REFERENCES patients(id),
    payer             UUID REFERENCES payers(id),
    encounter         UUID NOT NULL REFERENCES encounters(id),
    code              TEXT NOT NULL,
    description       TEXT,
    base_cost         NUMERIC(14,2),
    payer_coverage    NUMERIC(14,2),
    dispenses         INTEGER,
    totalcost         NUMERIC(14,2),
    reasoncode        TEXT,
    reasondescription TEXT
);

CREATE TABLE allergies (
    id          SERIAL PRIMARY KEY,
    start_date  DATE NOT NULL,
    stop_date   DATE,
    patient     UUID NOT NULL REFERENCES patients(id),
    encounter   UUID NOT NULL REFERENCES encounters(id),
    code        TEXT NOT NULL,
    description TEXT
);

CREATE TABLE observations (
    id          SERIAL PRIMARY KEY,
    obs_date    TIMESTAMPTZ NOT NULL,
    patient     UUID NOT NULL REFERENCES patients(id),
    encounter   UUID REFERENCES encounters(id), -- nullable keeps QALY-like patient-level metrics
    code        TEXT NOT NULL,
    description TEXT,
    value       TEXT,
    units       TEXT,
    type        TEXT
);

CREATE TABLE procedures (
    id                SERIAL PRIMARY KEY,
    proc_date         TIMESTAMPTZ NOT NULL,
    patient           UUID NOT NULL REFERENCES patients(id),
    encounter         UUID NOT NULL REFERENCES encounters(id),
    code              TEXT NOT NULL,
    description       TEXT,
    base_cost         NUMERIC(14,2),
    reasoncode        TEXT,
    reasondescription TEXT
);

CREATE TABLE immunizations (
    id          SERIAL PRIMARY KEY,
    imm_date    TIMESTAMPTZ NOT NULL,
    patient     UUID NOT NULL REFERENCES patients(id),
    encounter   UUID NOT NULL REFERENCES encounters(id),
    code        TEXT NOT NULL,
    description TEXT,
    base_cost   NUMERIC(14,2)
);

CREATE TABLE careplans (
    id                UUID PRIMARY KEY,
    start_date        DATE NOT NULL,
    stop_date         DATE,
    patient           UUID NOT NULL REFERENCES patients(id),
    encounter         UUID NOT NULL REFERENCES encounters(id),
    code              TEXT NOT NULL,
    description       TEXT,
    reasoncode        TEXT,
    reasondescription TEXT
);

CREATE TABLE devices (
    id          SERIAL PRIMARY KEY,
    start_ts    TIMESTAMPTZ NOT NULL,
    stop_ts     TIMESTAMPTZ,
    patient     UUID NOT NULL REFERENCES patients(id),
    encounter   UUID NOT NULL REFERENCES encounters(id),
    code        TEXT NOT NULL,
    description TEXT,
    udi         TEXT
);

CREATE TABLE imaging_studies (
    id                   UUID PRIMARY KEY,
    study_date           TIMESTAMPTZ NOT NULL,
    patient              UUID NOT NULL REFERENCES patients(id),
    encounter            UUID NOT NULL REFERENCES encounters(id),
    bodysite_code        TEXT,
    bodysite_description TEXT,
    modality_code        TEXT,
    modality_description TEXT,
    sop_code             TEXT,
    sop_description      TEXT
);

CREATE TABLE supplies (
    id          SERIAL PRIMARY KEY,
    supply_date TIMESTAMPTZ NOT NULL,
    patient     UUID NOT NULL REFERENCES patients(id),
    encounter   UUID NOT NULL REFERENCES encounters(id),
    code        TEXT NOT NULL,
    description TEXT,
    quantity    INTEGER
);

CREATE TABLE payer_transitions (
    id         SERIAL PRIMARY KEY,
    patient    UUID NOT NULL REFERENCES patients(id),
    start_year INTEGER NOT NULL,
    end_year   INTEGER,
    payer      UUID NOT NULL REFERENCES payers(id),
    ownership  TEXT
);


-- Indexes for common query patterns

CREATE INDEX idx_encounters_patient      ON encounters(patient);
CREATE INDEX idx_conditions_patient      ON conditions(patient);
CREATE INDEX idx_medications_patient     ON medications(patient);
CREATE INDEX idx_medications_stop        ON medications(stop_ts);
CREATE INDEX idx_allergies_patient       ON allergies(patient);
CREATE INDEX idx_observations_patient    ON observations(patient);
CREATE INDEX idx_procedures_patient      ON procedures(patient);
CREATE INDEX idx_immunizations_patient   ON immunizations(patient);
CREATE INDEX idx_careplans_patient       ON careplans(patient);
CREATE INDEX idx_payer_transitions_patient ON payer_transitions(patient);
