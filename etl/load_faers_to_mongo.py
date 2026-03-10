"""
ETL — Load openFDA FAERS into MongoDB (raw + normalized)
========================================================
Fetches adverse event reports from the openFDA Drug Event API,
stores raw JSON for traceability and normalized documents for
embedding (e.g. Part 3 Qdrant) and evidence lookup.

Collections:
  - faers_raw:         raw API response per report (by safetyreportid)
  - faers_normalized:  flattened summary + fields used for embedding

Usage:
    python etl/load_faers_to_mongo.py
    python etl/load_faers_to_mongo.py --limit 500 --search 'patient.patientsex:1'
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import requests

# Optional: use pymongo if available (allow running without Mongo for dry-run)
try:
    from pymongo import MongoClient
    from pymongo.collection import Collection
    HAS_PYMONGO = True
except ImportError:
    HAS_PYMONGO = False

# defaults 

OPENFDA_BASE = "https://api.fda.gov/drug/event.json"
DEFAULT_MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DEFAULT_DB_NAME = os.getenv("MONGO_DB", "drug_safety")
DEFAULT_LIMIT = 1000  # openFDA max per request
DEFAULT_SKIP = 0
# Rate limit: openFDA asks for max 240 requests per minute (4 per second)
REQUEST_DELAY_SEC = 0.35


def _fetch_page(search: str | None, limit: int, skip: int) -> dict[str, Any]:
    params: dict[str, str | int] = {"limit": limit, "skip": skip}
    if search:
        params["search"] = search
    r = requests.get(OPENFDA_BASE, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _report_id(report: dict[str, Any]) -> str:
    """Extract canonical FAERS report ID (safetyreportid)."""
    return report.get("safetyreportid") or report.get("safetyreportidstring") or ""


def _normalize_report(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Build a normalized document for embedding and evidence display.
    Includes a text summary and key structured fields; same _id as raw for lookup.
    """
    report_id = _report_id(raw)
    patient = raw.get("patient") or {}
    drugs = patient.get("drug") or []
    reactions = patient.get("reaction") or []

    drug_names = [
        d.get("medicinalproduct") or d.get("activesubstance", {}).get("activesubstancename")
        for d in drugs
        if isinstance(d, dict)
    ]
    drug_names = [n for n in drug_names if n]

    reaction_pts = [
        r.get("reactionmeddrapt") if isinstance(r, dict) else None
        for r in reactions
    ]
    reaction_pts = [x for x in reaction_pts if x]

    summary_parts = []
    if drug_names:
        summary_parts.append("Drugs: " + "; ".join(drug_names))
    if reaction_pts:
        summary_parts.append("Reactions: " + "; ".join(reaction_pts))
    summary = " | ".join(summary_parts) if summary_parts else "(no drugs or reactions)"

    return {
        "_id": report_id,
        "faers_id": report_id,
        "summary": summary,
        "drugs": drug_names,
        "reactions": reaction_pts,
        "receivedate": raw.get("receivedate"),
        "serious": raw.get("serious"),
        "seriousnessdeath": raw.get("seriousnessdeath"),
        "transmissiondate": raw.get("transmissiondate"),
    }


def _ensure_index(collection: Collection, key: str, unique: bool = True) -> None:
    try:
        collection.create_index(key, unique=unique)
    except Exception:
        pass


def load_faers_to_mongo(
    mongo_uri: str = DEFAULT_MONGO_URI,
    db_name: str = DEFAULT_DB_NAME,
    search: str | None = None,
    limit_per_request: int = DEFAULT_LIMIT,
    max_reports: int | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Fetch FAERS from openFDA and write raw + normalized docs to MongoDB.

    Returns:
        (raw_count, normalized_count) of documents written.
    """
    if not HAS_PYMONGO and not dry_run:
        print("pymongo is required. Install with: pip install pymongo", file=sys.stderr)
        sys.exit(1)

    total_raw = 0
    total_norm = 0
    skip = DEFAULT_SKIP

    if not dry_run:
        client = MongoClient(mongo_uri)
        db = client[db_name]
        raw_coll = db["faers_raw"]
        norm_coll = db["faers_normalized"]
        _ensure_index(raw_coll, "_id")
        _ensure_index(norm_coll, "_id")

    while True:
        if max_reports is not None and total_raw >= max_reports:
            break
        fetch_limit = limit_per_request
        if max_reports is not None:
            fetch_limit = min(fetch_limit, max_reports - total_raw)

        data = _fetch_page(search, limit=fetch_limit, skip=skip)
        results = data.get("results") or []
        meta = data.get("meta") or {}
        total_available = meta.get("results", {}).get("total", 0)

        if not results:
            break

        for report in results:
            report_id = _report_id(report)
            if not report_id:
                continue

            # Store raw with _id = safetyreportid for get_faers_reports_by_ids
            raw_doc = dict(report)
            raw_doc["_id"] = report_id

            norm_doc = _normalize_report(report)

            if dry_run:
                total_raw += 1
                total_norm += 1
                continue

            raw_coll.replace_one({"_id": report_id}, raw_doc, upsert=True)
            norm_coll.replace_one({"_id": report_id}, norm_doc, upsert=True)
            total_raw += 1
            total_norm += 1

        skip += len(results)
        if len(results) < fetch_limit or (max_reports is not None and total_raw >= max_reports):
            break
        time.sleep(REQUEST_DELAY_SEC)

    return total_raw, total_norm


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load openFDA FAERS into MongoDB (raw + normalized)."
    )
    parser.add_argument(
        "--mongo-uri",
        default=DEFAULT_MONGO_URI,
        help=f"MongoDB connection URI (default: {DEFAULT_MONGO_URI})",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_NAME,
        help=f"Database name (default: {DEFAULT_DB_NAME})",
    )
    parser.add_argument(
        "--search",
        default=None,
        help="openFDA search query (e.g. 'patient.patientsex:1')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        metavar="N",
        help="Max number of reports to fetch (default: 500)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch from API but do not write to MongoDB",
    )
    args = parser.parse_args()

    raw_count, norm_count = load_faers_to_mongo(
        mongo_uri=args.mongo_uri,
        db_name=args.db,
        search=args.search,
        max_reports=args.limit,
        dry_run=args.dry_run,
    )
    print(f"Written: {raw_count} raw, {norm_count} normalized" + (" (dry run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
