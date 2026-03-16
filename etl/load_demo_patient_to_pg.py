"""
Load demo patient with 2-cluster medications into PostgreSQL.
Use this patient ID in Full Safety Check to search by patient instead of manual meds.

Usage:
    python etl/load_demo_patient_to_pg.py
    # or from project root:
    cd drug_safety_and_recommendation && python etl/load_demo_patient_to_pg.py
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

DEFAULT_DB_URL = os.getenv(
    "PG_URL",
    "postgresql://postgres:postgres@localhost:5432/drug_safety",
)

DEMO_PATIENT_ID = "a2b3c4d5-0000-4e00-8000-000000000001"
DEMO_MEDICATIONS = [
    "Amphotericin B",
    "Baclofen",
    "Bumetanide",
    "Buthiazide",
    "Alpha-1-proteinase inhibitor",
    "Aminocaproic acid",
    "Aminomethylbenzoic acid",
]

DEMO_CONDITIONS = [
    ("44054006", "Diabetes"),
    ("38341003", "Hypertension"),
]

DEMO_ALLERGIES = [
    ("7980", "Penicillin"),
    ("1191", "Aspirin"),
]


def main():
    db_url = DEFAULT_DB_URL
    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get existing org, payer, provider (from any existing row)
            cur.execute("SELECT id FROM organizations LIMIT 1")
            org_row = cur.fetchone()
            cur.execute("SELECT id FROM payers LIMIT 1")
            payer_row = cur.fetchone()
            cur.execute("SELECT id FROM providers LIMIT 1")
            provider_row = cur.fetchone()

            org_id = str(org_row["id"]) if org_row else None
            payer_id = str(payer_row["id"]) if payer_row else None
            provider_id = str(provider_row["id"]) if provider_row else None

            if not org_id or not payer_id or not provider_id:
                print("Run load_synthea_to_pg.py first to load organizations, payers, providers.")
                conn.rollback()
                return 1

            # Remove existing demo patient data (so we can re-run)
            # Delete dependent rows first to satisfy encounter FKs
            cur.execute("DELETE FROM medications WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM conditions WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM allergies WHERE patient = %s", (DEMO_PATIENT_ID,))
            # Optional: clear common encounter-linked tables if present
            cur.execute("DELETE FROM procedures WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM immunizations WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM observations WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM careplans WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM devices WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM imaging_studies WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM supplies WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM payer_transitions WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM encounters WHERE patient = %s", (DEMO_PATIENT_ID,))
            cur.execute("DELETE FROM patients WHERE id = %s", (DEMO_PATIENT_ID,))

            # Insert demo patient
            cur.execute(
                """
                INSERT INTO patients (
                    id, birthdate, deathdate, gender, first_name, last_name,
                    address, city, state, zip
                ) VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    DEMO_PATIENT_ID,
                    "1985-03-15",
                    "M",
                    "Demo",
                    "Patient",
                    "123 Demo St",
                    "Boston",
                    "MA",
                    "02101",
                ),
            )

            # Create one encounter for all meds
            encounter_id = str(uuid.uuid4())
            start_ts = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

            cur.execute(
                """
                INSERT INTO encounters (
                    id, start_ts, stop_ts, patient, organization, provider, payer,
                    encounterclass, code, description
                ) VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    encounter_id,
                    start_ts,
                    DEMO_PATIENT_ID,
                    org_id,
                    provider_id,
                    payer_id,
                    "ambulatory",
                    "DEMO001",
                    "Demo safety check encounter",
                ),
            )

            # Insert a couple of active conditions (stop_date NULL)
            for code, desc in DEMO_CONDITIONS:
                cur.execute(
                    """
                    INSERT INTO conditions (
                        start_date, stop_date, patient, encounter, code, description
                    ) VALUES (%s, NULL, %s, %s, %s, %s)
                    """,
                    (
                        start_ts.date(),
                        DEMO_PATIENT_ID,
                        encounter_id,
                        code,
                        desc,
                    ),
                )

            # Insert a couple of active allergies (stop_date NULL)
            for code, desc in DEMO_ALLERGIES:
                cur.execute(
                    """
                    INSERT INTO allergies (
                        start_date, stop_date, patient, encounter, code, description
                    ) VALUES (%s, NULL, %s, %s, %s, %s)
                    """,
                    (
                        start_ts.date(),
                        DEMO_PATIENT_ID,
                        encounter_id,
                        code,
                        desc,
                    ),
                )

            # Insert medications (all active: stop_ts NULL)
            for i, med_name in enumerate(DEMO_MEDICATIONS):
                cur.execute(
                    """
                    INSERT INTO medications (
                        start_ts, stop_ts, patient, payer, encounter,
                        code, description, base_cost, payer_coverage,
                        dispenses, totalcost
                    ) VALUES (%s, NULL, %s, %s, %s, %s, %s, 0, 0, 1, 0)
                    """,
                    (
                        start_ts,
                        DEMO_PATIENT_ID,
                        payer_id,
                        encounter_id,
                        f"DEMO{i:03d}",
                        med_name,
                    ),
                )

        conn.commit()
        print(f"Loaded demo patient {DEMO_PATIENT_ID} with {len(DEMO_MEDICATIONS)} medications.")
        print("Use this Patient ID in Full Safety Check.")
        return 0

    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    exit(main())
