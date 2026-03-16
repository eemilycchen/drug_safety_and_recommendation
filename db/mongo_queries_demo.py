"""
MongoDB Query Helpers (Debug / Demo Script)

Interactive version of db/mongo_queries.py.

Use this script to:
- Configure your MongoDB connection (local or Atlas)
- Ping the database to confirm connectivity
- Exercise get_faers_reports_by_ids, log_safety_check, and get_safety_check
  and inspect the returned documents.

Run from repo root:
    python -m db.mongo_queries_demo
Or:
    python db/mongo_queries_demo.py
"""

from __future__ import annotations

import os
import sys
from getpass import getpass
from pathlib import Path

# Allow running as script from any CWD: add repo root to path
if __name__ == "__main__":
    _repo_root = Path(__file__).resolve().parent.parent
    if _repo_root not in sys.path:
        sys.path.insert(0, str(_repo_root))

# Optional: override connection to use Atlas with a prompt (recommended for debugging)
USE_ATLAS = os.getenv("MONGO_USE_ATLAS", "").lower() in ("1", "true", "yes")

if USE_ATLAS:
    user = input("MongoDB username: ")
    password = getpass("MongoDB password: ")
    cluster = input("Cluster host (e.g. mycluster.abcde.mongodb.net): ")
    app_name = "DrugSafetyApp"
    MONGO_URI = (
        f"mongodb+srv://{user}:{password}@{cluster}/"
        f"?retryWrites=true&w=majority&appName={app_name}"
    )
    DB_NAME = input("Database name [drug_safety]: ") or "drug_safety"
    os.environ["MONGO_URI"] = MONGO_URI
    os.environ["MONGO_DB"] = DB_NAME

# Import after env is set so module picks up MONGO_URI / MONGO_DB
from pymongo import MongoClient

from db.mongo_queries import (
    DEFAULT_DB_NAME,
    DEFAULT_MONGO_URI,
    get_faers_reports_by_ids,
    get_safety_check,
    log_safety_check,
)


def _redact_uri(uri: str) -> str:
    """Redact password in mongodb+srv://user:password@host."""
    if "@" in uri and "://" in uri:
        pre, rest = uri.split("://", 1)
        if "@" in rest:
            user_part, host_part = rest.rsplit("@", 1)
            if ":" in user_part:
                user = user_part.split(":")[0]
                return f"{pre}://{user}:***@{host_part}"
    return uri


def main() -> None:
    print("MongoDB URI:", _redact_uri(DEFAULT_MONGO_URI))
    print("DB name:", DEFAULT_DB_NAME)

    # Connectivity check
    client = MongoClient(DEFAULT_MONGO_URI)
    result = client.admin.command("ping")
    print("Ping:", result)

    # Test log_safety_check + get_safety_check
    run_id = log_safety_check({
        "inputs": {"patient_id": "demo-patient", "proposed_drug": "warfarin"},
        "outputs": {"warnings": ["demo warning"], "score": 0.5},
    })
    print("Logged run_id:", run_id)

    doc = get_safety_check(run_id)
    print("get_safety_check(run_id):", doc)

    # Example: fetch FAERS reports by IDs (requires ETL has populated faers_raw/faers_normalized)
    example_ids = ["5801206-7", "5801207-9"]
    reports = get_faers_reports_by_ids(example_ids)
    print(f"get_faers_reports_by_ids({example_ids!r}): {len(reports)} report(s)")
    for r in reports:
        print("  -", r.get("_id"), r.get("safetyreportid"))


if __name__ == "__main__":
    main()
    sys.exit(0)
