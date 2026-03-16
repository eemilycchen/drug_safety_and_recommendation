# Qdrant / Vector DB — What’s Implemented

This document describes the **Qdrant / vector database** part of the drug safety and recommendation project: what data is stored, how it’s loaded, and how it’s queried.

---

## Overview

Qdrant is used as a **vector database** for:

1. **Adverse event reports** — FAERS (openFDA) reports embedded and stored so we can find “similar” cases by semantic similarity.
2. **Patient profiles** — Synthea patient summaries embedded so we can find clinically similar patients.

Everything uses **dense 768‑dimensional vectors** from the **BioLORD-2023** biomedical sentence‑transformer. Search is **cosine similarity** over these vectors, with optional **payload filters** (e.g. by drug, outcome, sex) so the app can ask “similar to this patient, but only reports involving drug X” or “only serious outcomes.”

---

## What Goes Into Qdrant

### 1. Adverse events (`adverse_events` collection)

- **Source:** openFDA FAERS API (`https://api.fda.gov/drug/event.json`).
- **Processing:**
  - Raw JSON reports are **parsed** into structured records (patient age/sex, drugs, reactions, outcome, seriousness, report ID).
  - Reports **without** both drugs and reactions are **dropped** (not stored).
  - Each kept report is **serialized to text** (e.g. “Patient: 45 year old male. Medications: … Adverse reactions: … Outcome: …”) and **embedded** with the BioLORD into a 768‑dim vector.
- **Stored in Qdrant:**
  - **Vector:** 768 floats (one per report).
  - **Payload:** `drug` (primary drug), `all_drugs`, `reactions`, `patient_age`, `patient_sex`, `serious`, `outcome`, `report_id`, `raw_text`.

### 2. Patient profiles (`patient_profiles` collection)

- **Source:** Patient profile dicts from PostgreSQL/Synthea (e.g. from `get_patient_profile()`): `patient_id`, `age`, `gender`, `conditions`, `medications`.
- **Processing:**
  - Each profile is turned into a short **text** (e.g. “Patient: 50 year old female. Conditions: … Medications: …”). Empty lists become `"none"`.
  - That text is **embedded** with the same model → 768‑dim vector.
- **Stored in Qdrant:**
  - **Vector:** 768 floats per patient.
  - **Payload:** `patient_id`, `age`, `gender`, `conditions`, `medications`.

---

## Embedding Model and Vector Config

- **Model:** `FremyCompany/BioLORD-2023` (sentence‑transformers, biomedical domain). Same model for adverse events and patient profiles so they live in the same semantic space. BioLORD was trained on biomedical literature and clinical ontologies, so it captures clinical relationships (e.g. aspirin vs ibuprofen as related NSAIDs, aspirin vs metformin as unrelated) that general-purpose models miss.
- **Vector size:** 768.
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
6. **Create collections** — If missing, `adverse_events` and `patient_profiles` are created (768‑dim, cosine); payload indexes are created on `adverse_events`.
7. **Upsert** — Vectors and payloads are written to the `adverse_events` collection in batches.

**Usage (from project root):**

```bash
# Fetch from API, then embed and load (default: up to 5000 reports)
python3 -m etl.load_faers_to_qdrant

# Use cached raw JSON (no API call)
python3 -m etl.load_faers_to_qdrant --use-cache

# Limit number of reports fetched
python3 -m etl.load_faers_to_qdrant --limit 500

# Restrict to one calendar year (receivedate in [YYYY0101, YYYY1231])
python3 -m etl.load_faers_to_qdrant --year 2022 --limit 5000

# Full replace after changing serious/outcome logic (delete + recreate collection)
python3 -m etl.load_faers_to_qdrant --use-cache --recreate

# Local disk-backed Qdrant (no server; use instead of Docker)
python3 -m etl.load_faers_to_qdrant --qdrant-path ./qdrant_local --use-cache
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

- Turn the given `patient_summary` (free text) into a 768‑dim vector with the same sentence‑transformer (BioLORD).
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

## Local vs Docker mode (Qdrant)

- **Docker mode (default):** Qdrant runs in a container. Do **not** set `QDRANT_PATH`. The client uses `QDRANT_HOST` and `QDRANT_PORT` (default `localhost:6333`). All data lives inside the container.
- **Local mode:** Qdrant runs on-disk with no server. Set `QDRANT_PATH` to a directory (e.g. `./qdrant_local`). The client uses `QdrantClient(path=...)`. Use this when you want a persistent folder and no Docker.

**Important:** If you load data with Docker (e.g. `docker compose up` then run the ETL), your data is in the **Docker** instance. Tests and demos must then run **without** `QDRANT_PATH` so they connect to the same Qdrant. If `QDRANT_PATH` is set (e.g. to `./qdrant_local`), the scripts use the local folder, which is empty — so you get no results.

---

## Setup and run

### Run the whole thing 

From the project root (`dsc 202`). Use `python3` if your system doesn’t have `python`.

```bash
# 1) Start Qdrant (Docker)
docker compose up -d

# 2) Load FAERS into Qdrant (pick one)
python3 -m etl.load_faers_to_qdrant --limit 5000                  # small multi-year run
# python3 -m etl.load_faers_to_qdrant --year 2022 --limit 2000    # only 2022
# python3 -m etl.load_faers_to_qdrant --use-cache --recreate      # reuse data/faers_raw.json, full replace

# 3) Build DrugBank alternatives cache — approved drugs only (default)
python3 -m etl.drugbank_alternatives --xml "data/full database.xml" --out data/drugbank_alternatives.json

# 4) Run drug alternatives
python3 drug_alternatives.py                  # DrugBank + NDC if <10; ranked by BioLORD ≥0.40
# python3 drug_alternatives.py --faers        # same, but annotate/re-rank with FAERS safety (requires Qdrant adverse_events)

# 5) Optional — load drug profiles for other Qdrant demos
python3 -m etl.load_drugs_to_qdrant

# 6) Verify & demo
python3 test_qdrant_queries.py
python3 demo_qdrant.py
```

**Quick sample for FAERS (step 2):**  
`python3 -m etl.load_sample_openfda` loads 100 FAERS reports (and optionally NDC) in one go.

**Minimal for drug alternatives only (no Qdrant):** From project root, run step 3 then step 4. Requires `data/full database.xml` (DrugBank full database).

---

### 1. Start Qdrant

- Make sure **Docker Desktop** is running (whale icon in menu bar).
- Start the stack:

```bash
docker compose up -d
```

- Verify Qdrant is up:

```bash
curl http://localhost:6333
```

You should see something like: `{"title":"qdrant","version":"..."}`.

### 2. Load data into Qdrant (step 2 above)

**Option A — use cached data (fast, if `data/faers_raw.json` exists)**

```bash
python3 -m etl.load_faers_to_qdrant --use-cache
```

**Option B — fetch fresh from openFDA (slow, ~1 hour for 150k)**

```bash
python3 -m etl.load_faers_to_qdrant --limit 150000
```

**Option C — small test run (fast, good for verifying setup)**

```bash
python3 -m etl.load_faers_to_qdrant --limit 1000
```

**Local mode:** If you use on-disk Qdrant instead of Docker, pass the path when loading:

```bash
python3 -m etl.load_faers_to_qdrant --qdrant-path ./qdrant_local --use-cache
```

**Load drug profiles (optional — for other Qdrant demos):**

```bash
python3 -m etl.load_drugs_to_qdrant
```

(Use `--qdrant-path ./qdrant_local` only if you use on-disk Qdrant instead of Docker.)

### 3. Run drug alternatives

**DrugBank first, NDC when &lt;10. Build the cache first:**

```bash
python3 -m etl.drugbank_alternatives --xml "data/full database.xml" --out data/drugbank_alternatives.json
python3 drug_alternatives.py
# or, with FAERS-aware annotation / safety preference:
# python3 drug_alternatives.py --faers
```

Uses `data/drugbank_alternatives.json` (never overwritten by the script). When a drug has &lt;10 alternatives, NDC is fetched and merged; merges are stored in `data/ndc_merge.json`. Alternatives are **ranked by BioLORD similarity** and only those **≥0.40** are shown (quality gate). Rebuild DrugBank cache with `etl.drugbank_alternatives` to refresh; approved only by default (`--all` for experimental). Output shows `(DrugBank)` or `(NDC)`. With `--faers`, each alternative is also annotated with FAERS reaction/outcome summary from Qdrant and, when similarity is close, safer alternatives (lower % serious) are preferred.

### 4. Verify (step 6 above)

- Check that collections exist:

```bash
curl http://localhost:6333/collections
```

- Run test queries (requires adverse_events data from step 2):

```bash
python3 test_qdrant_queries.py
```

- Run the demo:

```bash
python3 demo_qdrant.py
```

**If tests find no results:** Your data is in Docker but the test script is still using local mode.

- **Fix 1 — run without `QDRANT_PATH` (use Docker):**

```bash
unset QDRANT_PATH
python3 test_qdrant_queries.py
```

- **Fix 2 — change the default in `test_qdrant_queries.py`:** at the top, use:

```python
# Use Docker (no local path):
os.environ.setdefault("QDRANT_PATH", "")

# Not this (local path — empty folder when data is in Docker):
# os.environ.setdefault("QDRANT_PATH", "./qdrant_local")
```

Same idea for `demo_qdrant.py`: either `unset QDRANT_PATH` before running, or ensure the script defaults to `QDRANT_PATH=""` so it talks to Docker.

---

## Environment and Setup

- **Qdrant**
  - **Docker:** Leave `QDRANT_PATH` unset. Uses `QDRANT_HOST=localhost`, `QDRANT_PORT=6333`.
  - **Local (on-disk):** Set `QDRANT_PATH` to a directory; the client uses `QdrantClient(path=...)`.
- **openFDA (ETL only)**
  - Optional: `OPENFDA_API_KEY` for higher rate limits when fetching FAERS.
- **Python**
  - Dependencies: `qdrant-client`, `sentence-transformers`, `numpy`, `scikit-learn`, `python-dotenv` (and any transitive deps). No sparse vector usage; only dense 768‑dim vectors from BioLORD.

---

## Design Choices

- **Dense vectors only** — No sparse vectors in Qdrant; everything is 768‑dim dense from BioLORD.
- **Cosine distance** — Fits normalized embeddings; “closest” means highest cosine similarity.
- **Payload filters** — Filter by drug, outcome, serious, sex **before** vector comparison so we don’t search the whole collection when we only care about one drug or outcome type.
- **Same model for events and patients** — Adverse event text and patient summary text are embedded with the same model so “similar patient” and “similar adverse event” use the same notion of similarity in one shared 768‑dim space.
- **Reports without drugs or reactions** — Dropped in ETL so only “usable” adverse events are stored; sparse or incomplete FAERS reports never get a vector.

---

## Example: Demo run

**Docker (default):**

```bash
unset QDRANT_PATH   # optional if script already defaults to ""
python demo_qdrant.py
```

**Local on-disk Qdrant:**

```bash
QDRANT_PATH=./qdrant_local python demo_qdrant.py
```

The demo runs four sections:

1. **Semantic patient matching** — Given a patient description (e.g. “65 year old male taking aspirin daily for heart attack prevention”), it finds the most similar FAERS reports by embedding the text and searching the `adverse_events` collection. No keyword matching; similarity is purely from BioLORD embeddings. Example: for “55 year old female with type 2 diabetes taking ibuprofen,” top matches include reports with dyspepsia, renal impairment, and nephrocalcinosis (clinically relevant for diabetics on NSAIDs).

2. **Drug safety signal analysis** — For a high‑risk patient, it analyzes the top 20 similar FAERS reports: severity distribution, organ systems affected, most frequent reactions, and outcomes (e.g. renal 8×, gastrointestinal 2×; type 2 diabetes mellitus 9×, blood creatinine increased 2×).

3. **BioLORD drug intelligence** — Pairwise drug similarity from embeddings: same class (e.g. ibuprofen vs naproxen → 0.63, amoxicillin vs penicillin → 0.66), related but different (aspirin vs paracetamol → 0.49), unrelated (aspirin vs metformin → 0.08). Shows the model correctly separates painkillers from diabetes drugs without rules.

4. **Live safety check** — End‑to‑end for one scenario: e.g. “58 year old male, type 2 diabetes and hypertension, on aspirin and metformin; doctor proposes adding ibuprofen.” Steps: (1) semantic search for similar FAERS cases and common reactions (dyspepsia, renal impairment, tinnitus), (2) drug‑drug similarity (ibuprofen + aspirin, + metformin, + naproxen) to flag stomach bleeding and kidney risk, (3) HIGH RISK summary with a recommendation (e.g. consider paracetamol; avoid doubling NSAID risk with aspirin).

Output is printed to the terminal with clear section headers and similarity scores (LOW / MODERATE / HIGH). Next steps noted in the demo: Neo4j (drug interaction graph) and MongoDB (audit), then Part 5 to orchestrate all databases into one safety report.

---

## Summary

| Item | Detail |
|------|--------|
| **Data in Qdrant** | (1) FAERS adverse event reports → `adverse_events`. (2) Synthea patient profiles → `patient_profiles`. |
| **Vectors** | 768‑dim dense, from `FremyCompany/BioLORD-2023`; cosine similarity. |
| **ETL** | `etl/load_faers_to_qdrant.py` fetches/parses/filters FAERS, serializes to text, embeds, and upserts into `adverse_events`. Patient profiles are loaded via `load_patient_profiles()` in code. |
| **Query API** | `db/qdrant_queries.py`: similar adverse events (with optional drug/outcome/serious/sex filters), similar patients, load profiles, aspect analysis, drug–drug similarity. |

This is the full Qdrant/vector DB part: what is stored, how it’s loaded, and what the app can do with it at query time.
