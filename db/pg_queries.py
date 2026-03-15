"""
PostgreSQL query helpers — Part 1 interface contract
=====================================================
Provides functions consumed by Parts 3 and 5:

    get_active_medications(patient_id)   →  list[dict]
    get_patient_profile(patient_id)     →  dict
    get_medication_history(patient_id, ...)  →  list[dict]  (full history, filters)
    get_patient_timeline(patient_id, ...)   →  list[dict] (chronological events)
    validate_timeline_consistency(timeline_events) →  dict
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime
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
    limit: int = 100,
    years_back: int | None = None,
    since_date: str | None = None,
) -> list[dict]:
    """
    Return every medication the patient has ever been on (not only current).

    Args:
        patient_id: UUID of the patient
        db_url: Optional database URL override
        limit: Maximum number of records to return
        years_back: Only return medications from last N years
        since_date: Only return medications started after this date (ISO: 'YYYY-MM-DD')

    Returns:
        List of medication records ordered by start date (newest first)
        Each record contains:
        - description: Drug name
        - code: Drug code
        - start_ts: When this episode started
        - stop_ts: When it ended (null if still active)
        - reasoncode: Why prescribed
        - reasondescription: Condition description
        - encounter: Associated encounter

    Raises:
        ValueError: If patient_id is invalid or date formats are incorrect
        RuntimeError: If database error occurs
    """
    if not patient_id:
        raise ValueError("patient_id cannot be empty or None")

    if not isinstance(patient_id, str):
        raise ValueError(f"patient_id must be a string, got {type(patient_id).__name__}")

    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    if years_back is not None and years_back <= 0:
        raise ValueError(f"years_back must be positive, got {years_back}")

    if since_date is not None:
        try:
            datetime.fromisoformat(since_date.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise ValueError(
                f"since_date must be in ISO format (YYYY-MM-DD), got {since_date}"
            ) from None

    if years_back is not None and since_date is not None:
        raise ValueError(
            "Cannot specify both years_back and since_date - choose one filter"
        )

    try:
        where_clauses = ["patient = %s"]
        params: list = [patient_id]

        if since_date:
            where_clauses.append("start_ts >= %s::timestamp")
            params.append(since_date)
        elif years_back is not None:
            where_clauses.append("start_ts >= CURRENT_DATE - INTERVAL '1 year' * %s")
            params.append(years_back)

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                description,
                code,
                start_ts,
                stop_ts,
                reasoncode,
                reasondescription,
                encounter
            FROM medications
            WHERE {where_sql}
            ORDER BY start_ts DESC
            LIMIT %s
        """
        params.append(limit)

        with _get_cursor(db_url) as cur:
            cur.execute(sql, params)
            results = [dict(row) for row in cur.fetchall()]

            if not results:
                return []

            for row in results:
                if row["start_ts"] is None:
                    row["start_ts"] = "Unknown"
                elif isinstance(row["start_ts"], (datetime, date)):
                    row["start_ts"] = row["start_ts"].isoformat()

                if row["stop_ts"] is None:
                    row["stop_ts"] = "Present"
                elif isinstance(row["stop_ts"], (datetime, date)):
                    row["stop_ts"] = row["stop_ts"].isoformat()

                if not row.get("description"):
                    row["description"] = f"Unknown medication (code: {row.get('code', 'N/A')})"

                if not row.get("reasondescription") and row.get("reasoncode"):
                    row["reasondescription"] = f"Reason code: {row['reasoncode']}"

            return results

    except psycopg2.Error as e:
        raise RuntimeError(
            f"Database error fetching medication history for patient {patient_id}: {e}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error in get_medication_history: {e}") from e


def get_patient_timeline(
    patient_id: str,
    db_url: str | None = None,
    since_date: str | None = None,
    event_types: list[str] | None = None,
) -> list[dict]:
    """
    One chronological list of all clinical events for a single timeline view.

    Args:
        patient_id: UUID of the patient
        db_url: Optional database URL override
        since_date: Only include events after this date (ISO: 'YYYY-MM-DD')
        event_types: Types to include: ['medication', 'condition', 'encounter', 'procedure']
            Default: all types

    Returns:
        Chronological list of events, each with:
        - date: When the event occurred/started
        - type: Event type (medication, condition, encounter, procedure)
        - description: What happened
        - end_date: For events with duration (null if ongoing)
        - details: Type-specific fields

    Raises:
        ValueError: If patient_id is invalid or event_types contains invalid values
        RuntimeError: If database error occurs
    """
    if not patient_id:
        raise ValueError("patient_id cannot be empty or None")

    if not isinstance(patient_id, str):
        raise ValueError(f"patient_id must be a string, got {type(patient_id).__name__}")

    valid_types = {"medication", "condition", "encounter", "procedure"}

    if event_types is None:
        event_types = list(valid_types)
    else:
        if not isinstance(event_types, list):
            raise ValueError(
                f"event_types must be a list, got {type(event_types).__name__}"
            )

        if not event_types:
            return []

        invalid_types = set(event_types) - valid_types
        if invalid_types:
            raise ValueError(
                f"Invalid event types: {invalid_types}. Valid types are: {valid_types}"
            )

    if since_date is not None:
        try:
            datetime.fromisoformat(since_date.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            raise ValueError(
                f"since_date must be in ISO format (YYYY-MM-DD), got {since_date}"
            ) from None

    try:
        date_filter = ""
        params = [patient_id]
        if since_date:
            date_filter = " AND date_field >= %s::timestamp"
            params.append(since_date)

        queries = []

        if "medication" in event_types:
            queries.append(
                f"""
                SELECT
                    start_ts as date,
                    'medication' as type,
                    COALESCE(description, 'Unknown Medication') as description,
                    stop_ts as end_date,
                    jsonb_build_object(
                        'code', COALESCE(code, 'N/A'),
                        'reason', COALESCE(reasondescription, 'Not specified'),
                        'encounter', COALESCE(encounter::text, 'N/A')
                    ) as details
                FROM medications
                WHERE patient = %s {date_filter.replace('date_field', 'start_ts')}
                """
            )

        if "condition" in event_types:
            queries.append(
                f"""
                SELECT
                    start_date::timestamptz as date,
                    'condition' as type,
                    COALESCE(description, 'Unknown Condition') as description,
                    stop_date::timestamptz as end_date,
                    jsonb_build_object(
                        'code', COALESCE(code, 'N/A')
                    ) as details
                FROM conditions
                WHERE patient = %s {date_filter.replace('date_field', 'start_date')}
                """
            )

        if "encounter" in event_types:
            queries.append(
                f"""
                SELECT
                    start_ts as date,
                    'encounter' as type,
                    COALESCE(description, 'Medical Encounter') as description,
                    NULL::timestamptz as end_date,
                    jsonb_build_object(
                        'code', COALESCE(code, 'N/A'),
                        'cost', COALESCE(total_claim_cost, 0)
                    ) as details
                FROM encounters
                WHERE patient = %s {date_filter.replace('date_field', 'start_ts')}
                """
            )

        if "procedure" in event_types:
            queries.append(
                f"""
                SELECT
                    proc_date as date,
                    'procedure' as type,
                    COALESCE(description, 'Medical Procedure') as description,
                    NULL::timestamptz as end_date,
                    jsonb_build_object(
                        'code', COALESCE(code, 'N/A')
                    ) as details
                FROM procedures
                WHERE patient = %s {date_filter.replace('date_field', 'proc_date')}
                """
            )

        if not queries:
            return []

        union_query = " UNION ALL ".join(queries)
        final_query = f"""
            SELECT * FROM ({union_query}) combined
            ORDER BY date DESC
        """

        all_params = params * len(queries)

        with _get_cursor(db_url) as cur:
            cur.execute(final_query, all_params)
            results = [dict(row) for row in cur.fetchall()]

            if not results:
                return []

            processed_results = []
            for row in results:
                if row.get("date") is None:
                    row["date"] = "Unknown"
                elif isinstance(row["date"], (datetime, date)):
                    row["date"] = row["date"].isoformat()

                if row.get("end_date") is None:
                    if row["type"] in ["medication", "condition"]:
                        row["end_date"] = "Present"
                    else:
                        row["end_date"] = "N/A"
                elif isinstance(row["end_date"], (datetime, date)):
                    row["end_date"] = row["end_date"].isoformat()

                if not row.get("description") or row["description"] == "Unknown":
                    row["description"] = f"{row['type'].title()} (details unavailable)"

                if not isinstance(row.get("details"), dict):
                    row["details"] = {}

                processed_results.append(row)

            def safe_date_key(event: dict) -> str:
                date_val = event.get("date")
                if date_val == "Unknown" or date_val is None:
                    return "0000-00-00"
                return str(date_val)

            processed_results.sort(key=safe_date_key, reverse=True)

            return processed_results

    except psycopg2.Error as e:
        raise RuntimeError(
            f"Database error fetching timeline for patient {patient_id}: {e}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error in get_patient_timeline: {e}") from e


def validate_timeline_consistency(timeline_events: list[dict]) -> dict:
    """
    Validate timeline for data consistency issues.
    Useful for debugging edge cases.

    Args:
        timeline_events: Output from get_patient_timeline()

    Returns:
        Dictionary with validation results and any issues found
    """
    issues = []

    if not timeline_events:
        return {"valid": True, "issues": [], "message": "Empty timeline"}

    dates = []
    for i, event in enumerate(timeline_events):
        date_val = event.get("date")
        if date_val and date_val != "Unknown":
            try:
                date_obj = datetime.fromisoformat(str(date_val).replace("Z", "+00:00"))
                dates.append((i, date_obj))
            except (ValueError, TypeError):
                issues.append(f"Event {i}: Invalid date format - {date_val}")

    for j in range(1, len(dates)):
        if dates[j - 1][1] < dates[j][1]:
            issues.append(
                f"Chronological inconsistency: Event {dates[j-1][0]} before {dates[j][0]}"
            )

    required_fields = ["date", "type", "description"]
    for i, event in enumerate(timeline_events):
        missing = [f for f in required_fields if f not in event]
        if missing:
            issues.append(f"Event {i}: Missing required fields - {missing}")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "event_count": len(timeline_events),
        "date_range": (
            {
                "earliest": dates[-1][1].isoformat() if dates else None,
                "latest": dates[0][1].isoformat() if dates else None,
            }
            if dates
            else None
        ),
    }