"""
PostgreSQL query helpers — Part 1 interface contract
=====================================================
Provides the two functions consumed by Parts 3 and 5:

    get_active_medications(patient_id)  →  list[dict]
    get_patient_profile(patient_id)     →  dict
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.extras import RealDictCursor

DEFAULT_DB_URL = os.getenv(
    "PG_URL",
    "postgresql://postgres:postgres@localhost:5432/drug_safety",
)


@contextmanager
def _get_cursor(db_url: str | None = None) -> Generator:
    conn = psycopg2.connect(db_url or DEFAULT_DB_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


# ── Interface functions ────────────────────────────────────────────


def get_active_medications(
    patient_id: str,
    db_url: str | None = None,
) -> list[dict]:
    """
    Return every medication the patient is currently taking
    (stop_ts IS NULL  →  still active).

    Each dict contains:
        code, description, start_ts, dispenses, base_cost,
        reasoncode, reasondescription
    """
    sql = """
        SELECT  m.code,
                m.description,
                m.start_ts,
                m.dispenses,
                m.base_cost,
                m.reasoncode,
                m.reasondescription
        FROM    medications m
        WHERE   m.patient = %s
          AND   m.stop_ts IS NULL
        ORDER BY m.start_ts;
    """
    with _get_cursor(db_url) as cur:
        cur.execute(sql, (patient_id,))
        return [dict(row) for row in cur.fetchall()]


def get_patient_profile(
    patient_id: str,
    db_url: str | None = None,
) -> dict:
    """
    Build a rich patient profile for downstream consumers
    (Part 3 embeddings, Part 5 safety report).

    Returns:
        {
            "patient": { ... demographics ... },
            "active_medications": [ ... ],
            "conditions": [ ... active conditions ... ],
            "allergies": [ ... active allergies ... ],
            "recent_observations": [ ... last 20 obs ... ],
        }
    """
    with _get_cursor(db_url) as cur:
        # demographics
        cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
        patient_row = cur.fetchone()
        if patient_row is None:
            raise ValueError(f"Patient {patient_id} not found")

        patient = dict(patient_row)
        # convert dates to strings for JSON serialisation
        for k in ("birthdate", "deathdate"):
            if patient.get(k) is not None:
                patient[k] = patient[k].isoformat()

        # active medications (reuse the public function's logic inline
        # to avoid an extra connection)
        cur.execute(
            """
            SELECT code, description, start_ts, dispenses,
                   base_cost, reasoncode, reasondescription
            FROM   medications
            WHERE  patient = %s AND stop_ts IS NULL
            ORDER BY start_ts
            """,
            (patient_id,),
        )
        active_meds = [dict(r) for r in cur.fetchall()]

        # active conditions (no stop_date → still active)
        cur.execute(
            """
            SELECT code, description, start_date
            FROM   conditions
            WHERE  patient = %s AND stop_date IS NULL
            ORDER BY start_date
            """,
            (patient_id,),
        )
        conditions = [dict(r) for r in cur.fetchall()]

        # active allergies
        cur.execute(
            """
            SELECT code, description, start_date
            FROM   allergies
            WHERE  patient = %s AND stop_date IS NULL
            ORDER BY start_date
            """,
            (patient_id,),
        )
        allergies = [dict(r) for r in cur.fetchall()]

        # most recent observations (last 20)
        cur.execute(
            """
            SELECT code, description, value, units, obs_date
            FROM   observations
            WHERE  patient = %s
            ORDER BY obs_date DESC
            LIMIT 20
            """,
            (patient_id,),
        )
        observations = [dict(r) for r in cur.fetchall()]

    return {
        "patient": patient,
        "active_medications": active_meds,
        "conditions": conditions,
        "allergies": allergies,
        "recent_observations": observations,
    }


# ── convenience helpers for exploration / Part 5 ───────────────────


def list_patients(limit: int = 20, db_url: str | None = None) -> list[dict]:
    """Return a short list of patients (useful for demos)."""
    with _get_cursor(db_url) as cur:
        cur.execute(
            """
            SELECT id, first_name, last_name, birthdate, gender
            FROM   patients
            ORDER BY last_name, first_name
            LIMIT  %s
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_medication_history(
    patient_id: str,
    db_url: str | None = None,
) -> list[dict]:
    """Full medication history (active + past)."""
    with _get_cursor(db_url) as cur:
        cur.execute(
            """
            SELECT code, description, start_ts, stop_ts,
                   dispenses, totalcost, reasoncode, reasondescription
            FROM   medications
            WHERE  patient = %s
            ORDER BY start_ts
            """,
            (patient_id,),
        )
        return [dict(r) for r in cur.fetchall()]
