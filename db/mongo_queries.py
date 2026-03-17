"""
MongoDB query helpers

    get_faers_reports_by_ids(faers_ids)  →  list[dict]   (raw evidence)
    log_safety_check(run)                 →  str         (run_id)
    get_safety_check(run_id)              →  dict | None
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, date, timezone
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


# Helpers


def _make_mongo_safe(obj: Any) -> Any:
    """
    Recursively convert values into types MongoDB can encode.

    - datetime/date → ISO 8601 strings
    - tuples       → lists
    """
    if isinstance(obj, dict):
        return {k: _make_mongo_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_mongo_safe(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


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
    safe_run = _make_mongo_safe(run)
    doc = {
        "_id": run_id,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **safe_run,
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
    out = dict(doc)
    if hasattr(out.get("_id"), "binary"):
        out["_id"] = str(out["_id"])
    return out


def search_safety_checks_by_patient(
    name: str,
    limit: int = 20,
    mongo_uri: str | None = None,
    db_name: str | None = None,
) -> list[dict]:
    """Return audit records whose patient_name contains *name* (case-insensitive)."""
    db = _get_db(mongo_uri, db_name)
    query = {"inputs.patient_name": {"$regex": name.strip(), "$options": "i"}}
    docs = list(db[AUDIT_COLLECTION].find(query).sort("timestamp", -1).limit(limit))
    out = []
    for doc in docs:
        d = dict(doc)
        if hasattr(d.get("_id"), "binary"):
            d["_id"] = str(d["_id"])
        out.append(d)
    return out


def list_safety_checks(
    limit: int = 20,
    mongo_uri: str | None = None,
    db_name: str | None = None,
) -> list[dict]:
    """Return the most recent safety-check audit records (newest first)."""
    db = _get_db(mongo_uri, db_name)
    docs = list(db[AUDIT_COLLECTION].find().sort("timestamp", -1).limit(limit))
    out = []
    for doc in docs:
        d = dict(doc)
        if hasattr(d.get("_id"), "binary"):
            d["_id"] = str(d["_id"])
        out.append(d)
    return out


def sample_faers_ids(
    limit: int = 5,
    mongo_uri: str | None = None,
    db_name: str | None = None,
) -> list[str]:
    """Return a random sample of safetyreportid strings from faers_raw."""
    db = _get_db(mongo_uri, db_name)
    docs = list(db["faers_raw"].aggregate([{"$sample": {"size": limit}}]))
    return [str(d["_id"]) for d in docs]
