"""
MongoDB query helpers

    get_faers_reports_by_ids(faers_ids)  →  list[dict]   (raw evidence)
    log_safety_check(run)                 →  str         (run_id)
    get_safety_check(run_id)              →  dict | None
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from pymongo import MongoClient
    from pymongo.database import Database
    HAS_PYMONGO = True
except ImportError:
    HAS_PYMONGO = False

# defaults 

DEFAULT_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DEFAULT_DB_NAME = os.getenv("MONGO_DB", "drug_safety")
AUDIT_COLLECTION = "safety_check_audit"


def _get_db(uri: str | None = None, db_name: str | None = None) -> Database:
    if not HAS_PYMONGO:
        raise RuntimeError("pymongo is required. Install with: pip install pymongo")
    client = MongoClient(uri or DEFAULT_MONGO_URI)
    return client[db_name or DEFAULT_DB_NAME]


# Interface functions 


def get_faers_reports_by_ids(
    faers_ids: list[str],
    mongo_uri: str | None = None,
    db_name: str | None = None,
    *,
    raw: bool = True,
) -> list[dict]:
    """
    Fetch FAERS reports by their IDs (safetyreportid).

    Used by Part 5 to attach raw evidence to Qdrant matches.

    Args:
        faers_ids: List of safetyreportid strings (e.g. from Qdrant payload).
        mongo_uri: Optional MongoDB URI.
        db_name: Optional database name.
        raw: If True (default), return raw API-shaped docs; else normalized.

    Returns:
        List of documents in the same order as faers_ids where found;
        missing IDs are omitted (no placeholder).
    """
    if not faers_ids:
        return []

    db = _get_db(mongo_uri, db_name)
    collection = db["faers_raw"] if raw else db["faers_normalized"]
    cursor = collection.find({"_id": {"$in": list(faers_ids)}})
    by_id = {doc["_id"]: doc for doc in cursor}
    # Preserve order of faers_ids; omit missing IDs
    return [by_id[id_] for id_ in faers_ids if id_ in by_id]


def log_safety_check(
    run: dict,
    mongo_uri: str | None = None,
    db_name: str | None = None,
) -> str:
    """
    Persist one drug-safety-check run (inputs + outputs + versions) for audit.

    Args:
        run: Dict with at least inputs (e.g. patient_id, proposed_drug) and
             any outputs (e.g. interactions, similar_events, warnings).
             Timestamp and run_id can be omitted; they will be added.

    Returns:
        The assigned run_id (UUID string).
    """
    run_id = str(uuid.uuid4())
    doc = {
        "_id": run_id,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **run,
    }
    db = _get_db(mongo_uri, db_name)
    db[AUDIT_COLLECTION].insert_one(doc)
    return run_id


def get_safety_check(
    run_id: str,
    mongo_uri: str | None = None,
    db_name: str | None = None,
) -> dict | None:
    """
    Retrieve a single safety-check audit record by run_id.

    Returns:
        The audit document, or None if not found.
    """
    db = _get_db(mongo_uri, db_name)
    doc = db[AUDIT_COLLECTION].find_one({"_id": run_id})
    if doc is None:
        return None
    # _id is ObjectId or str; return as plain dict, convert ObjectId for JSON
    out = dict(doc)
    if hasattr(out.get("_id"), "binary"):
        out["_id"] = str(out["_id"])
    return out
