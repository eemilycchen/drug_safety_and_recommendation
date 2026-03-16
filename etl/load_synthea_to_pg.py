"""
ETL — Load Synthea CSVs into PostgreSQL
========================================
Reads every CSV exported by Synthea from DATA_DIR, applies light
transformations (column renames, empty-string → NULL), and bulk-loads
each table using psycopg2's execute_values for speed.

Usage:
    python etl/load_synthea_to_pg.py            # uses defaults
    python etl/load_synthea_to_pg.py --data-dir /other/path
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# ── defaults (overridable via env vars or CLI) ──────────────────────

DEFAULT_DATA_DIR = os.getenv("SYNTHEA_DATA_DIR", "data/synthea")
DEFAULT_DB_URL = os.getenv(
    "PG_URL",
    "postgresql://postgres:postgres@localhost:5432/drug_safety",
)


# ── helpers ─────────────────────────────────────────────────────────

def _read_csv(path: Path) -> pd.DataFrame:
    """Read a Synthea CSV, converting empty strings to NaN (→ NULL)."""
    df = pd.read_csv(path, dtype=str)
    df = df.replace({"": None})
    return df


def _bulk_insert(cur, table: str, cols: list[str], rows: list[tuple]) -> int:
    """INSERT rows via execute_values; returns count inserted."""
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s ON CONFLICT DO NOTHING"
    execute_values(cur, sql, rows, template=f"({placeholders})", page_size=500)
    return len(rows)


def _df_to_rows(df: pd.DataFrame) -> list[tuple]:
    return [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False)]


# ── per-table loaders ──────────────────────────────────────────────

def load_organizations(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "organizations.csv")
    cols = [
        "id", "name", "address", "city", "state", "zip",
        "lat", "lon", "phone", "revenue", "utilization",
    ]
    df.columns = [c.lower() for c in df.columns]
    return _bulk_insert(cur, "organizations", cols, _df_to_rows(df[cols]))


def load_payers(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "payers.csv")
    df.columns = [c.lower() for c in df.columns]
    cols = [
        "id", "name", "address", "city", "state_headquartered", "zip",
        "phone", "amount_covered", "amount_uncovered", "revenue",
        "covered_encounters", "uncovered_encounters",
        "covered_medications", "uncovered_medications",
        "covered_procedures", "uncovered_procedures",
        "covered_immunizations", "uncovered_immunizations",
        "unique_customers", "qols_avg", "member_months",
    ]
    return _bulk_insert(cur, "payers", cols, _df_to_rows(df[cols]))


def load_providers(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "providers.csv")
    df.columns = [c.lower() for c in df.columns]
    cols = [
        "id", "organization", "name", "gender", "speciality",
        "address", "city", "state", "zip", "lat", "lon", "utilization",
    ]
    return _bulk_insert(cur, "providers", cols, _df_to_rows(df[cols]))


def load_patients(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "patients.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"first": "first_name", "last": "last_name"})
    cols = [
        "id", "birthdate", "deathdate", "ssn", "drivers", "passport",
        "prefix", "first_name", "last_name", "suffix", "maiden",
        "marital", "race", "ethnicity", "gender", "birthplace",
        "address", "city", "state", "county", "zip", "lat", "lon",
        "healthcare_expenses", "healthcare_coverage",
    ]
    return _bulk_insert(cur, "patients", cols, _df_to_rows(df[cols]))


def load_encounters(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "encounters.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"start": "start_ts", "stop": "stop_ts"})
    cols = [
        "id", "start_ts", "stop_ts", "patient", "organization",
        "provider", "payer", "encounterclass", "code", "description",
        "base_encounter_cost", "total_claim_cost", "payer_coverage",
        "reasoncode", "reasondescription",
    ]
    return _bulk_insert(cur, "encounters", cols, _df_to_rows(df[cols]))


def load_conditions(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "conditions.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"start": "start_date", "stop": "stop_date"})
    cols = ["start_date", "stop_date", "patient", "encounter", "code", "description"]
    return _bulk_insert(cur, "conditions", cols, _df_to_rows(df[cols]))


def load_medications(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "medications.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"start": "start_ts", "stop": "stop_ts"})
    cols = [
        "start_ts", "stop_ts", "patient", "payer", "encounter",
        "code", "description", "base_cost", "payer_coverage",
        "dispenses", "totalcost", "reasoncode", "reasondescription",
    ]
    return _bulk_insert(cur, "medications", cols, _df_to_rows(df[cols]))


def load_allergies(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "allergies.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"start": "start_date", "stop": "stop_date"})
    cols = ["start_date", "stop_date", "patient", "encounter", "code", "description"]
    return _bulk_insert(cur, "allergies", cols, _df_to_rows(df[cols]))


def load_observations(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "observations.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"date": "obs_date"})
    cols = ["obs_date", "patient", "encounter", "code", "description", "value", "units", "type"]
    return _bulk_insert(cur, "observations", cols, _df_to_rows(df[cols]))


def load_procedures(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "procedures.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"date": "proc_date"})
    cols = [
        "proc_date", "patient", "encounter", "code", "description",
        "base_cost", "reasoncode", "reasondescription",
    ]
    return _bulk_insert(cur, "procedures", cols, _df_to_rows(df[cols]))


def load_immunizations(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "immunizations.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"date": "imm_date"})
    cols = ["imm_date", "patient", "encounter", "code", "description", "base_cost"]
    return _bulk_insert(cur, "immunizations", cols, _df_to_rows(df[cols]))


def load_careplans(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "careplans.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"start": "start_date", "stop": "stop_date"})
    cols = [
        "id", "start_date", "stop_date", "patient", "encounter",
        "code", "description", "reasoncode", "reasondescription",
    ]
    return _bulk_insert(cur, "careplans", cols, _df_to_rows(df[cols]))


def load_devices(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "devices.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"start": "start_ts", "stop": "stop_ts"})
    cols = ["start_ts", "stop_ts", "patient", "encounter", "code", "description", "udi"]
    return _bulk_insert(cur, "devices", cols, _df_to_rows(df[cols]))


def load_imaging_studies(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "imaging_studies.csv")
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"date": "study_date"})
    cols = [
        "id", "study_date", "patient", "encounter",
        "bodysite_code", "bodysite_description",
        "modality_code", "modality_description",
        "sop_code", "sop_description",
    ]
    return _bulk_insert(cur, "imaging_studies", cols, _df_to_rows(df[cols]))


def load_supplies(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "supplies.csv")
    if df.empty:
        return 0
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"date": "supply_date"})
    cols = ["supply_date", "patient", "encounter", "code", "description", "quantity"]
    return _bulk_insert(cur, "supplies", cols, _df_to_rows(df[cols]))


def load_payer_transitions(cur, data_dir: Path) -> int:
    df = _read_csv(data_dir / "payer_transitions.csv")
    df.columns = [c.lower() for c in df.columns]
    cols = ["patient", "start_year", "end_year", "payer", "ownership"]
    return _bulk_insert(cur, "payer_transitions", cols, _df_to_rows(df[cols]))


# ── orchestrator ────────────────────────────────────────────────────

TABLE_LOADERS = [
    ("organizations",      load_organizations),
    ("payers",             load_payers),
    ("providers",          load_providers),
    ("patients",           load_patients),
    ("encounters",         load_encounters),
    ("conditions",         load_conditions),
    ("medications",        load_medications),
    ("allergies",          load_allergies),
    ("observations",       load_observations),
    ("procedures",         load_procedures),
    ("immunizations",      load_immunizations),
    ("careplans",          load_careplans),
    ("devices",            load_devices),
    ("imaging_studies",    load_imaging_studies),
    ("supplies",           load_supplies),
    ("payer_transitions",  load_payer_transitions),
]


def apply_schema(cur, schema_path: Path) -> None:
    """Execute the DDL script to (re)create all tables."""
    ddl = schema_path.read_text()
    cur.execute(ddl)


def run_etl(db_url: str, data_dir: str, apply_ddl: bool = True) -> dict[str, int]:
    """
    Main entry point.  Returns {table_name: rows_loaded}.
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        sys.exit(f"Data directory not found: {data_path}")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        if apply_ddl:
            schema_file = Path(__file__).resolve().parent.parent / "db" / "pg_schema.sql"
            print(f"Applying schema from {schema_file} ...")
            apply_schema(cur, schema_file)
            conn.commit()

        results: dict[str, int] = {}
        for table_name, loader_fn in TABLE_LOADERS:
            print(f"  Loading {table_name} ...", end=" ", flush=True)
            n = loader_fn(cur, data_path)
            results[table_name] = n
            print(f"{n:,} rows")

        conn.commit()
        print("\nAll tables loaded successfully.")
        return results

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Load Synthea CSVs into PostgreSQL")
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Path to Synthea CSV folder (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--db-url",
        default=DEFAULT_DB_URL,
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--no-schema",
        action="store_true",
        help="Skip DDL — assume tables already exist",
    )
    args = parser.parse_args()
    run_etl(args.db_url, args.data_dir, apply_ddl=not args.no_schema)


if __name__ == "__main__":
    main()
