"""
ETL script: Fetch openFDA FAERS adverse event reports, embed them, and load into Qdrant.

Integrates concepts from DSC 202 Vector Data Model lecture:
- Dense embeddings via BioLORD-2023 (FremyCompany/BioLORD-2023, 768-dim)
- Cosine distance for semantic similarity (normalized embeddings → cos θ = ⟨x,y⟩/(||x|| ||y||))
- HNSW index (Qdrant default) for approximate nearest neighbor search
- Payload indexes on filterable fields for efficient filtered search

Usage:
    python etl/load_faers_to_qdrant.py                      # fetch + embed + load
    python etl/load_faers_to_qdrant.py --use-cache           # skip fetch, use cached JSON
    python etl/load_faers_to_qdrant.py --limit 500            # fetch only 500 reports
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)
from sentence_transformers import SentenceTransformer

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

BASE_URL = "https://api.fda.gov/drug/event.json"
API_KEY = os.getenv("OPENFDA_API_KEY", "")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = DATA_DIR / "faers_raw.json"
MODEL_NAME = "FremyCompany/BioLORD-2023"
VECTOR_DIM = 768
BATCH_SIZE = 50
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
ADVERSE_EVENTS_COLLECTION = "adverse_events"
PATIENT_PROFILES_COLLECTION = "patient_profiles"


# ---------------------------------------------------------------------------
# 1. Fetch FAERS data from openFDA
# ---------------------------------------------------------------------------
YEAR_RANGES = [
    ("20200101", "20201231"),
    ("20210101", "20211231"),
    ("20220101", "20221231"),
    ("20230101", "20231231"),
    ("20240101", "20241231"),
    ("20250101", "20251231"),
]


def fetch_faers(limit: int = 5000) -> list[dict]:
    """
    Fetch FAERS reports from openFDA.
    If limit <= 25000 — single query. If limit > 25000 — slices by year.
    """
    if limit <= 25000:
        return _fetch_single(limit)
    else:
        per_year = limit // len(YEAR_RANGES)
        return _fetch_by_year(per_year)


def _fetch_single(limit: int) -> list[dict]:
    reports = []
    page_size = min(limit, BATCH_SIZE)
    skip = 0
    max_retries = 5

    log.info("Fetching up to %d FAERS reports (single query)…", limit)

    while len(reports) < limit:
        params = {"limit": page_size, "skip": skip}
        if API_KEY:
            params["api_key"] = API_KEY

        for attempt in range(max_retries):
            try:
                resp = requests.get(BASE_URL, params=params, timeout=60)
                resp.raise_for_status()
                break
            except requests.RequestException as exc:
                wait = 2 ** attempt
                log.warning("Attempt %d/%d failed at skip=%d: %s — retrying in %ds",
                            attempt + 1, max_retries, skip, exc, wait)
                time.sleep(wait)
        else:
            log.error("All %d attempts failed at skip=%d — stopping.", max_retries, skip)
            break

        results = resp.json().get("results", [])
        if not results:
            log.info("No more results at skip=%d", skip)
            break

        reports.extend(results)
        skip += page_size
        log.info("  fetched %d / %d", len(reports), limit)
        time.sleep(0.5)

    log.info("Total raw reports fetched: %d", len(reports))
    return reports


def _fetch_by_year(limit_per_year: int) -> list[dict]:
    all_reports = []
    max_retries = 5

    log.info("Fetching up to %d reports/year × %d years …",
             limit_per_year, len(YEAR_RANGES))

    for start_date, end_date in YEAR_RANGES:
        year = start_date[:4]
        reports = []
        skip = 0

        log.info("  Year %s — fetching up to %d reports…", year, limit_per_year)

        while len(reports) < limit_per_year:
            remaining = limit_per_year - len(reports)
            limit = min(BATCH_SIZE, remaining)
            url = (
                f"{BASE_URL}"
                f"?limit={limit}"
                f"&skip={skip}"
                f"&search=receivedate:[{start_date}+TO+{end_date}]"
            )
            if API_KEY:
                url += f"&api_key={API_KEY}"

            for attempt in range(max_retries):
                try:
                    resp = requests.get(url, timeout=60)
                    resp.raise_for_status()
                    break
                except requests.RequestException as exc:
                    wait = 2 ** attempt
                    log.warning("Attempt %d/%d failed at skip=%d year=%s: %s — retrying in %ds",
                                attempt + 1, max_retries, skip, year, exc, wait)
                    time.sleep(wait)
            else:
                log.error("All attempts failed at skip=%d year=%s — moving on.", skip, year)
                break

            results = resp.json().get("results", [])
            if not results:
                break

            reports.extend(results)
            skip += BATCH_SIZE
            log.info("  year=%s fetched %d/%d", year, len(reports), limit_per_year)
            time.sleep(0.4)

        log.info("  Year %s done — got %d reports", year, len(reports))
        all_reports.extend(reports)

    log.info("Total fetched across all years: %d", len(all_reports))
    return all_reports


def save_cache(reports: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(reports, f)
    log.info("Cached %d reports → %s", len(reports), CACHE_FILE)


def load_cache() -> list[dict]:
    with open(CACHE_FILE) as f:
        reports = json.load(f)
    log.info("Loaded %d reports from cache %s", len(reports), CACHE_FILE)
    return reports


# ---------------------------------------------------------------------------
# 2. Parse & filter FAERS reports
# ---------------------------------------------------------------------------

def parse_report(raw: dict) -> dict | None:
    """Extract structured fields from a single FAERS report. Returns None if critical fields missing."""
    patient = raw.get("patient", {})

    age = patient.get("patientonsetage")
    age_unit = patient.get("patientonsetageunit")
    if age is not None:
        try:
            age = float(age)
            if age_unit == "800":
                age *= 10
            elif age_unit == "802":
                age /= 12
            elif age_unit == "803":
                age /= 52
            elif age_unit == "804":
                age /= 365
            age = int(round(age))
        except (ValueError, TypeError):
            age = None

    sex_code = patient.get("patientsex")
    sex_map = {"1": "male", "2": "female"}
    sex = sex_map.get(str(sex_code))

    drugs = []
    for d in patient.get("drug", []):
        names = d.get("openfda", {}).get("generic_name", [])
        if names:
            drugs.append(names[0].lower())
        elif d.get("medicinalproduct"):
            drugs.append(d["medicinalproduct"].lower())
    drugs = list(dict.fromkeys(drugs))

    reactions = []
    for r in patient.get("reaction", []):
        term = r.get("reactionmeddrapt")
        if term:
            reactions.append(term.lower())
    reactions = list(dict.fromkeys(reactions))

    if not drugs or not reactions:
        return None

    serious = raw.get("serious") == "1"

    outcome_parts = []
    if raw.get("seriousnessdeath") == "1":
        outcome_parts.append("death")
    if raw.get("seriousnesshospitalization") == "1":
        outcome_parts.append("hospitalization")
    if raw.get("seriousnesslifethreatening") == "1":
        outcome_parts.append("life-threatening")
    if raw.get("seriousnessdisabling") == "1":
        outcome_parts.append("disability")
    if not outcome_parts:
        outcome_parts.append("non-serious")
    outcome = ", ".join(outcome_parts)

    report_id = raw.get("safetyreportid", "")
    receive_date = raw.get("receivedate", "")

    return {
        "patient_age": age,
        "patient_sex": sex,
        "drugs": drugs,
        "reactions": reactions,
        "serious": serious,
        "outcome": outcome,
        "report_id": report_id,
        "receive_date": receive_date,
    }


def filter_reports(raw_reports: list[dict]) -> list[dict]:
    """Parse all reports, discard those with missing fields."""
    parsed = []
    skipped = 0
    for raw in raw_reports:
        record = parse_report(raw)
        if record is None:
            skipped += 1
            continue
        parsed.append(record)
    log.info("Parsed %d reports, skipped %d (missing drugs/reactions)", len(parsed), skipped)
    return parsed


# ---------------------------------------------------------------------------
# 3. Serialize to text for embedding
# ---------------------------------------------------------------------------

def serialize_report(record: dict) -> str:
    """Convert a parsed report into a text string for embedding."""
    age_str = f"{record['patient_age']} year old" if record["patient_age"] else "Unknown age"
    sex_str = record["patient_sex"] or "unknown sex"
    drugs_str = ", ".join(record["drugs"])
    reactions_str = ", ".join(record["reactions"])
    outcome_str = record["outcome"]

    return (
        f"Patient: {age_str} {sex_str}. "
        f"Medications: {drugs_str}. "
        f"Adverse reactions: {reactions_str}. "
        f"Outcome: {outcome_str}."
    )


# ---------------------------------------------------------------------------
# 4. Embed and load into Qdrant
# ---------------------------------------------------------------------------

def create_collections(client: QdrantClient) -> None:
    """Create Qdrant collections with payload indexes for efficient filtered search."""
    for name in [ADVERSE_EVENTS_COLLECTION, PATIENT_PROFILES_COLLECTION]:
        if not client.collection_exists(name):
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )
            log.info("Created collection '%s'", name)
        else:
            log.info("Collection '%s' already exists", name)

    index_fields = {
        "drug": PayloadSchemaType.KEYWORD,
        "outcome": PayloadSchemaType.KEYWORD,
        "serious": PayloadSchemaType.BOOL,
        "patient_sex": PayloadSchemaType.KEYWORD,
    }
    for field_name, schema_type in index_fields.items():
        try:
            client.create_payload_index(
                collection_name=ADVERSE_EVENTS_COLLECTION,
                field_name=field_name,
                field_schema=schema_type,
            )
            log.info("Created payload index: %s.%s (%s)", ADVERSE_EVENTS_COLLECTION, field_name, schema_type)
        except Exception:
            log.debug("Payload index %s.%s may already exist", ADVERSE_EVENTS_COLLECTION, field_name)


def load_adverse_events(
    client: QdrantClient,
    model: SentenceTransformer,
    records: list[dict],
) -> None:
    """Embed and upsert adverse event records into Qdrant."""
    texts = [serialize_report(r) for r in records]

    log.info("Embedding %d adverse event texts …", len(texts))
    vectors = model.encode(texts, show_progress_bar=True, batch_size=64)

    points = []
    for i, (record, vec, text) in enumerate(zip(records, vectors, texts)):
        primary_drug = record["drugs"][0] if record["drugs"] else ""
        payload = {
            "drug": primary_drug,
            "all_drugs": record["drugs"],
            "reactions": record["reactions"],
            "patient_age": record["patient_age"],
            "patient_sex": record["patient_sex"],
            "serious": record["serious"],
            "outcome": record["outcome"],
            "report_id": record["report_id"],
            "receive_date": record.get("receive_date", ""),
            "raw_text": text,
        }
        points.append(PointStruct(id=i, vector=vec.tolist(), payload=payload))

    upsert_batch_size = 200
    for start in range(0, len(points), upsert_batch_size):
        batch = points[start : start + upsert_batch_size]
        client.upsert(collection_name=ADVERSE_EVENTS_COLLECTION, points=batch)
    log.info("Upserted %d vectors into '%s'", len(points), ADVERSE_EVENTS_COLLECTION)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Load openFDA FAERS data into Qdrant")
    parser.add_argument("--limit", type=int, default=5000, help="Max reports to fetch")
    parser.add_argument("--use-cache", action="store_true", help="Use cached JSON instead of fetching")
    parser.add_argument("--qdrant-host", default=QDRANT_HOST)
    parser.add_argument("--qdrant-port", type=int, default=QDRANT_PORT)
    parser.add_argument("--qdrant-path", default=os.getenv("QDRANT_PATH", ""),
                        help="Path for local disk-based Qdrant (no server needed)")
    args = parser.parse_args()

    if args.use_cache and CACHE_FILE.exists():
        raw_reports = load_cache()
    else:
        raw_reports = fetch_faers(limit=args.limit)
        if raw_reports:
            save_cache(raw_reports)
        else:
            log.error("Fetch returned 0 reports — cache NOT overwritten. Try --use-cache.")
            sys.exit(1)

    records = filter_reports(raw_reports)
    if not records:
        log.error("No valid records after filtering. Exiting.")
        sys.exit(1)

    log.info("Loading embedding model '%s' …", MODEL_NAME)
    model = SentenceTransformer(MODEL_NAME)

    if args.qdrant_path:
        log.info("Using local Qdrant storage at %s", args.qdrant_path)
        client = QdrantClient(path=args.qdrant_path)
    else:
        log.info("Connecting to Qdrant at %s:%d …", args.qdrant_host, args.qdrant_port)
        client = QdrantClient(host=args.qdrant_host, port=args.qdrant_port)

    create_collections(client)
    load_adverse_events(client, model, records)

    log.info("Done. %d adverse events loaded into Qdrant.", len(records))


if __name__ == "__main__":
    main()
