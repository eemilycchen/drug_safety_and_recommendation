# Qdrant / Vector DB — What’s Implemented

This document describes the **Qdrant / vector database** part of the drug safety and recommendation project: what data is stored, how it’s loaded, and how it’s queried.

---

## Overview

Qdrant is used as a **vector database** for:

1. **Adverse event reports** — FAERS (openFDA) reports embedded and stored so we can find “similar” cases by semantic similarity.
2. **Patient profiles** — Synthea patient summaries embedded so we can find clinically similar patients.

Everything uses **dense 384‑dimensional vectors** from a sentence‑transformer model. Search is **cosine similarity** over these vectors, with optional **payload filters** (e.g. by drug, outcome, sex) so the app can ask “similar to this patient, but only reports involving drug X” or “only serious outcomes.”

---

## What Goes Into Qdrant

### 1. Adverse events (`adverse_events` collection)

- **Source:** openFDA FAERS API (`https://api.fda.gov/drug/event.json`).
- **Processing:**
  - Raw JSON reports are **parsed** into structured records (patient age/sex, drugs, reactions, outcome, seriousness, report ID).
  - Reports **without** both drugs and reactions are **dropped** (not stored).
  - Each kept report is **serialized to text** (e.g. “Patient: 45 year old male. Medications: … Adverse reactions: … Outcome: …”) and **embedded** with the sentence‑transformer into a 384‑dim vector.
- **Stored in Qdrant:**
  - **Vector:** 384 floats (one per report).
  - **Payload:** `drug` (primary drug), `all_drugs`, `reactions`, `patient_age`, `patient_sex`, `serious`, `outcome`, `report_id`, `raw_text`.

### 2. Patient profiles (`patient_profiles` collection)

- **Source:** Patient profile dicts from PostgreSQL/Synthea (e.g. from `get_patient_profile()`): `patient_id`, `age`, `gender`, `conditions`, `medications`.
- **Processing:**
  - Each profile is turned into a short **text** (e.g. “Patient: 50 year old female. Conditions: … Medications: …”). Empty lists become `"none"`.
  - That text is **embedded** with the same model → 384‑dim vector.
- **Stored in Qdrant:**
  - **Vector:** 384 floats per patient.
  - **Payload:** `patient_id`, `age`, `gender`, `conditions`, `medications`.

---

## Embedding Model and Vector Config

- **Model:** `all-MiniLM-L6-v2` (sentence‑transformers). Same model for adverse events and patient profiles so they live in the same semantic space.
- **Vector size:** 384.
- **Distance:** **Cosine**. Collections are created with `Distance.COSINE`; the model’s embeddings are normalized, so cosine similarity is used for search.
- **Index:** Qdrant’s default **HNSW** index for approximate nearest‑neighbor search.

Payload indexes are created on the adverse‑events collection for fast filtering:

- `drug` (keyword)
- `outcome` (keyword)
- `serious` (bool)
- `patient_sex` (keyword)

---

## ETL: Loading Data Into Qdrant

**Script:** `etl/load_faers_to_qdrant.py`

**Steps:**

1. **Fetch** — Call openFDA FAERS API (paginated). Optional `OPENFDA_API_KEY` in env for higher rate limits.
2. **Cache** — Raw JSON can be written to `data/faers_raw.json` and reused with `--use-cache`.
3. **Parse and filter** — Each report is parsed; reports with no drugs or no reactions are skipped and not loaded.
4. **Serialize** — Each kept report is converted to a single text string for embedding.
5. **Embed** — All texts are encoded with the sentence‑transformer in batches.
6. **Create collections** — If missing, `adverse_events` and `patient_profiles` are created (384‑dim, cosine); payload indexes are created on `adverse_events`.
7. **Upsert** — Vectors and payloads are written to the `adverse_events` collection in batches.

**Usage:**

```bash
# Fetch from API, then embed and load (default: up to 5000 reports)
python etl/load_faers_to_qdrant.py

# Use cached raw JSON (no API call)
python etl/load_faers_to_qdrant.py --use-cache

# Limit number of reports fetched
python etl/load_faers_to_qdrant.py --limit 500

# Local disk-backed Qdrant (no server)
python etl/load_faers_to_qdrant.py --qdrant-path /path/to/qdrant_storage
```

Patient profiles are **not** loaded by this script. They are loaded via the function `load_patient_profiles()` in `db/qdrant_queries.py`, which expects a list of profile dicts (e.g. from Part 1’s `get_patient_profile()`).

---

## Query API (`db/qdrant_queries.py`)

### Search

| Function | Purpose |
|----------|--------|
| `find_similar_adverse_events(patient_summary, drug_name, top_k=10)` | Embed `patient_summary`, search `adverse_events` with a **payload filter** so only reports that mention `drug_name` are considered; return the top‑k most similar with scores and payload (age, sex, drugs, reactions, outcome, serious, raw_text). |
| `find_similar_adverse_events_multi_filter(patient_summary, drug_names=None, outcome=None, serious_only=False, sex=None, top_k=10)` | Same idea, but filters are optional and composable: by one or more drugs, outcome type, serious‑only, and/or patient sex. |
| `find_similar_patients(patient_summary, top_k=10)` | Embed `patient_summary`, search `patient_profiles` (no filter); return top‑k similar profiles with payload (patient_id, conditions, medications) and similarity score. |

All of these:

- Turn the given `patient_summary` (free text) into a 384‑dim vector with the same sentence‑transformer.
- Run a vector search (cosine similarity) in the right collection, with optional payload filters where applicable.
- Return lists of dicts with similarity scores and the stored payload fields.

### Utilities

| Function | Purpose |
|----------|--------|
| `load_patient_profiles(profiles)` | Take a list of profile dicts (e.g. from `get_patient_profile()`), build text summaries, embed them, and upsert into the `patient_profiles` collection. Returns the number of profiles loaded. |
| `analyze_adverse_event_aspects(results)` | Take the list of dicts returned by `find_similar_adverse_events` (or multi‑filter) and summarize by severity, organ system (from keyword rules), top reactions, and outcome distribution. Used for aspect‑oriented analysis of search results. |
| `compute_drug_similarity(drug1, drug2)` | Embed both drug names and return their cosine similarity (single float). |
| `compute_pairwise_drug_similarities(drug_names)` | Embed all drug names and return pairwise cosine similarities as a dict of `(drug_a, drug_b): score`. |

---

## Environment and Setup

- **Qdrant**
  - Default: `QDRANT_HOST=localhost`, `QDRANT_PORT=6333`.
  - Or use a **local path** (no server): set `QDRANT_PATH` to a directory; the client uses `QdrantClient(path=...)`.
- **openFDA (ETL only)**
  - Optional: `OPENFDA_API_KEY` for higher rate limits when fetching FAERS.
- **Python**
  - Dependencies: `qdrant-client`, `sentence-transformers`, `numpy`, `scikit-learn`, `python-dotenv` (and any transitive deps). No sparse vector usage; only dense 384‑dim vectors.

---

## Design Choices (plain English)

- **Dense vectors only** — No sparse vectors in Qdrant; everything is 384‑dim dense from the sentence‑transformer.
- **Cosine distance** — Fits normalized embeddings; “closest” means highest cosine similarity.
- **Payload filters** — Filter by drug, outcome, serious, sex **before** vector comparison so we don’t search the whole collection when we only care about one drug or outcome type.
- **Same model for events and patients** — Adverse event text and patient summary text are embedded with the same model so “similar patient” and “similar adverse event” use the same notion of similarity in one shared 384‑dim space.
- **Reports without drugs or reactions** — Dropped in ETL so only “usable” adverse events are stored; sparse or incomplete FAERS reports never get a vector.

---

## Summary

| Item | Detail |
|------|--------|
| **Data in Qdrant** | (1) FAERS adverse event reports → `adverse_events`. (2) Synthea patient profiles → `patient_profiles`. |
| **Vectors** | 384‑dim dense, from `all-MiniLM-L6-v2`; cosine similarity. |
| **ETL** | `etl/load_faers_to_qdrant.py` fetches/parses/filters FAERS, serializes to text, embeds, and upserts into `adverse_events`. Patient profiles are loaded via `load_patient_profiles()` in code. |
| **Query API** | `db/qdrant_queries.py`: similar adverse events (with optional drug/outcome/serious/sex filters), similar patients, load profiles, aspect analysis, drug–drug similarity. |

This is the full Qdrant/vector DB part: what is stored, how it’s loaded, and what the app can do with it at query time.
