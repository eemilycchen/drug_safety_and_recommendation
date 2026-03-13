"""
PostgreSQL query helpers — Part 1 interface contract
Two functions for exploring the data:
    get_active_medications(patient_id)  →  list[dict]
    get_patient_profile(patient_id)     →  dict
"""

# or

"""
PostgreSQL query helpers for drug safety monitoring with Synthea data.
Provides comprehensive database interaction for medication safety analysis.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator, Optional, List, Dict, Any
from datetime import datetime, date

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool

DEFAULT_DB_URL = os.getenv(
    "PG_URL",
    "postgresql://postgres:postgres@localhost:5432/drug_safety",
)

# Connection pooling for better performance
class DatabasePool:
    """Manage database connection pool."""
    _pool = None
    
    @classmethod
    def get_pool(cls, db_url: str | None = None, min_conn: int = 1, max_conn: int = 20):
        """Get or create connection pool."""
        if cls._pool is None:
            try:
                cls._pool = pool.SimpleConnectionPool(
                    min_conn, 
                    max_conn, 
                    db_url or DEFAULT_DB_URL
                )
            except psycopg2.Error as e:
                raise RuntimeError(f"Failed to create connection pool: {e}")
        return cls._pool
    
@contextmanager
def _get_cursor(db_url: str | None = None) -> Generator:
    """
    Context manager for database cursor with connection pooling.
    
    Args:
        db_url: Optional database URL override
    
    Yields:
        Database cursor with RealDictCursor factory
    
    Raises:
        RuntimeError: If database connection fails
    """
    pool_instance = DatabasePool.get_pool(db_url)
    conn = None
    try:
        conn = pool_instance.getconn()
        # Set statement timeout for safety (5 seconds)
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '5s'")
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
            conn.commit()
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        raise RuntimeError(f"Database error: {e}")
    finally:
        if conn and pool_instance:
            pool_instance.putconn(conn)

def _convert_dates(obj: Any) -> Any:
    """Convert date/datetime objects to ISO format strings."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj

# Interface functions 


# Core Interface Functions

def get_active_medications(
    patient_id: str,
    db_url: str | None = None,
) -> list[dict]:
    """
    Return every medication the patient is currently taking (stop_ts IS NULL).
    
    Args:
        patient_id: UUID of the patient
        db_url: Optional database URL override
    
    Returns:
        List of medication dictionaries with fields:
        - code: Medication code (e.g., '197361')
        - description: Medication name
        - start_ts: When medication was started
        - dispenses: Number of dispenses
        - base_cost: Cost per dispense
        - reasoncode: Condition code being treated
        - reasondescription: Condition description
    
    Example:
        >>> get_active_medications('fcee8e43-b4dc-40c3-bb1e-836292c5b03f')
        [{'code': '197361', 'description': 'Lisinopril 10mg', ...}]
    
    Note:
        Active medications are those without a stop_ts (NULL). This indicates
        the prescription is still current according to Synthea data.
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
    try:
        with _get_cursor(db_url) as cur:
            cur.execute(sql, (patient_id,))
            return [dict(row) for row in cur.fetchall()]
    except RuntimeError as e:
        print(f"Error fetching active medications for patient {patient_id}: {e}")
        return []

def get_patient_profile(
    patient_id: str,
    db_url: str | None = None,
) -> dict:
    """
    Build a rich patient profile for downstream consumers
    (Part 3 embeddings, Part 5 safety report).

    Args:
        patient_id: UUID of the patient
        db_url: Optional database URL override

    Returns:
        Dictionary containing:
        {
            "patient": { ... demographics ... },
            "active_medications": [ ... ],
            "conditions": [ ... active conditions ... ],
            "allergies": [ ... active allergies ... ],
            "recent_observations": [ ... last 20 obs ... ],
        }
    
    Raises:
        ValueError: If patient not found
        RuntimeError: If database error occurs
    """
    try:
        with _get_cursor(db_url) as cur:
            # demographics
            cur.execute("SELECT * FROM patients WHERE id = %s", (patient_id,))
            patient_row = cur.fetchone()
            if patient_row is None:
                raise ValueError(f"Patient {patient_id} not found")

            patient = dict(patient_row)
            # convert dates to strings for JSON serialisation
            for k, v in patient.items():
                if isinstance(v, (datetime, date)):
                    patient[k] = v.isoformat()

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
    except psycopg2.Error as e:
        raise RuntimeError(f"Database error while fetching patient profile: {e}") from e



def list_patients(
    limit: int = 20, 
    offset: int = 0,
    db_url: str | None = None
) -> list[dict]:
    """
    Return a paginated list of patients.
    
    Args:
        limit: Maximum number of patients to return
        offset: Number of patients to skip (for pagination)
        db_url: Optional database URL override
    
    Returns:
        List of patient dictionaries with basic demographics
    """
    with _get_cursor(db_url) as cur:
        cur.execute(
            """
            SELECT id, first_name, last_name, birthdate, gender
            FROM   patients
            ORDER BY last_name, first_name
            LIMIT  %s
            OFFSET %s
            """,
            (limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]


def get_medication_history(
    patient_id: str,
    db_url: str | None = None,
    limit: int = 100,
    offset: int = 0
) -> list[dict]:
    """
    Full medication history with pagination (active + past).
    
    Args:
        patient_id: UUID of the patient
        db_url: Optional database URL override
        limit: Maximum number of records to return
        offset: Number of records to skip
    
    Returns:
        List of medication records ordered by start date (newest first)
    """
    with _get_cursor(db_url) as cur:
        cur.execute(
            """
            SELECT code, description, start_ts, stop_ts,
                   dispenses, totalcost, reasoncode, reasondescription
            FROM   medications
            WHERE  patient = %s
            ORDER BY start_ts DESC
            LIMIT %s OFFSET %s
            """,
            (patient_id, limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]

#Utility

def search_patients(
    search_term: str,
    limit: int = 20,
    db_url: str | None = None
) -> list[dict]:
    """
    Search patients by name or ID.
    
    Args:
        search_term: Text to search for in patient names or ID
        limit: Maximum number of results
        db_url: Optional database URL override
    
    Returns:
        List of matching patients
    """
    with _get_cursor(db_url) as cur:
        cur.execute(
            """
            SELECT id, first_name, last_name, birthdate, gender
            FROM patients
            WHERE first_name ILIKE %s 
               OR last_name ILIKE %s
               OR id::text ILIKE %s
            ORDER BY last_name, first_name
            LIMIT %s
            """,
            (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%', limit)
        )
        return [dict(r) for r in cur.fetchall()]