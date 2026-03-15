# Qdrant Code: What It Does & How to Merge With Your Current Databases

**Status:** The merge is complete. Qdrant lives in `db/qdrant_queries.py` and `etl/load_faers_to_qdrant.py` at project root; the single Streamlit demo (`app/demo.py`) uses all four databases and wires MongoDB to fetch evidence for Qdrant results. The guide below is kept for reference.

## What the Qdrant code does in the project

The code in the **`qdrant/`** folder implements **Part 3** of the clinical decision-support tool: **vector similarity search** over adverse event reports and patient/drug profiles.

### Role in the multi-database design

| Database   | Role in the project |
|-----------|----------------------|
| **PostgreSQL** | Patient data (Synthea): active meds, profile, timeline. |
| **Neo4j**      | Drug–drug interactions and side effects (graph). |
| **Qdrant**     | **Similar cases**: “Find FAERS reports similar to this patient on this drug” and “Find similar patients.” |
| **MongoDB**    | Raw FAERS evidence + audit log of safety checks. |

The **application flow** (Part 5) is:

1. **PostgreSQL** → patient profile and current medications.
2. **Neo4j** → check interactions and side effects for the proposed drug.
3. **Qdrant** → find similar adverse events (and optionally similar patients / drug alternatives).
4. **MongoDB** → fetch full FAERS evidence for Qdrant matches and log the safety check.

So Qdrant does **not** replace your current databases; it **adds** semantic search that uses the same patient/drug context and ties back to MongoDB for evidence.

### What the Qdrant code actually does

1. **ETL (`qdrant/etl/load_faers_to_qdrant.py`)**
   - Fetches adverse event reports from the **openFDA FAERS API** (or uses cached JSON).
   - Parses and filters reports (keeps only those with drugs and reactions).
   - Serializes each report to text (patient age/sex, drugs, reactions, outcome, seriousness).
   - Embeds text with **BioLORD-2023** (768-dim) and **upserts into Qdrant** in the `adverse_events` collection.
   - Creates collections and payload indexes for filtered search (e.g. by drug, outcome, serious, sex).

2. **Query API (`qdrant/db/qdrant_queries.py`)**
   - **`find_similar_adverse_events(patient_summary, drug_name, top_k)`** — embeds the summary, searches `adverse_events` with a payload filter for `drug_name`, returns top-k similar reports (scores + payload: reactions, outcome, report_id, etc.).
   - **`find_similar_patients(patient_summary, top_k)`** — same idea over the `patient_profiles` collection (profiles loaded separately via `load_patient_profiles()`).
   - **`load_patient_profiles(profiles)`** — takes list of profile dicts (same shape as `pg_queries.get_patient_profile()`), builds text, embeds, upserts into `patient_profiles`.
   - Optional: **`find_similar_drugs`** / **`find_safe_alternatives`** (use a `drug_profiles` collection if you add `load_drugs_to_qdrant` later).

3. **Integration with your existing DBs**
   - **PostgreSQL**: Qdrant does **not** import `pg_queries`. The app (or a small script) calls `get_patient_profile()`, then either builds a text summary and calls `find_similar_adverse_events(profile_text, drug_name)` or first calls `load_patient_profiles([profile])` and then uses Qdrant for similar patients.
   - **MongoDB**: After Qdrant returns matches, the app uses **`mongo_queries.get_faers_reports_by_ids(faers_ids)`** to fetch full evidence for those report IDs (already documented in your README and `mongo_queries.py`).

So: **Qdrant = vector store and similarity API; PostgreSQL/Neo4j/MongoDB stay the source of truth and evidence.**

---

## Step-by-step guide to merge Qdrant with your current databases

Follow these steps so that Qdrant lives in the main project and works with PostgreSQL, Neo4j, and MongoDB.

### Step 1: Copy Qdrant code into the main project tree

The Qdrant implementation currently lives under **`qdrant/`** (separate folder). The main app and README expect Part 3 under **`db/`** and **`etl/`** at the **project root**.

1. **Copy the query module**
   - Copy **`qdrant/db/qdrant_queries.py`** → **`db/qdrant_queries.py`** (project root).

2. **Copy the ETL script**
   - Copy **`qdrant/etl/load_faers_to_qdrant.py`** → **`etl/load_faers_to_qdrant.py`** (project root).

3. **Paths**
   - In `etl/load_faers_to_qdrant.py`, `DATA_DIR = Path(__file__).resolve().parent.parent / "data"` is already correct once the file is in `etl/`: it will point to the project root **`data/`** directory (e.g. `data/faers_raw.json` for cache). No change needed.

4. **Optional: tests/demos**
   - You can copy **`qdrant/test_qdrant_queries.py`** and **`qdrant/demo_qdrant.py`** to the project root if you want to run them from there (they import `from db.qdrant_queries import ...`). If you keep them only in `qdrant/`, run them from project root with `python qdrant/test_qdrant_queries.py` and ensure the project root is on `sys.path` so `db` resolves to `db/` at root.

### Step 2: Add Qdrant dependencies

Add to **`requirements.txt`**:

```text
# Part 3 — Qdrant + embeddings
qdrant-client
sentence-transformers
numpy
scikit-learn
python-dotenv
```

Then:

```bash
pip install -r requirements.txt
```

### Step 3: Run Qdrant (Docker or local)

- **Docker (recommended):** Use the compose file in `qdrant/docker-compose.yml` from project root:
  - `docker compose -f qdrant/docker-compose.yml up -d`
  - Do **not** set `QDRANT_PATH`; use default `QDRANT_HOST=localhost`, `QDRANT_PORT=6333`.
- **Local (on-disk):** Set `QDRANT_PATH` to a directory (e.g. `./qdrant_local`). No server needed; data persists in that folder.

Check that Qdrant is up (Docker):

```bash
curl http://localhost:6333
```

### Step 4: Load data into Qdrant (order relative to existing DBs)

Your **current** databases are:

- **PostgreSQL** — already loaded via `etl/load_synthea_to_pg.py`.
- **Neo4j** — already loaded via `etl/load_sider_to_neo4j.py` (and RxNav if you have it).
- **MongoDB** — already loaded via `etl/load_faers_to_mongo.py` (FAERS raw + normalized).

Merge order:

1. **Keep using PostgreSQL and Neo4j as-is** — no change.
2. **FAERS for Qdrant**
   - Either:
     - **Option A:** Run **`python etl/load_faers_to_qdrant.py`** (fetches from openFDA, parses, embeds, loads into Qdrant). Use `--use-cache` if you have `data/faers_raw.json`; use `--limit N` for a smaller test.
     - **Option B (future):** Add a path that reads from MongoDB’s **`faers_normalized`** and feeds the same serialization + embedding pipeline so Qdrant is populated from MongoDB instead of (or in addition to) openFDA. The current script does not do this yet; it only uses openFDA + optional cache.
3. **Patient profiles in Qdrant (optional)**
   - From your app or a small script: get a list of patient IDs (e.g. from `pg_queries.list_patients()`), call `pg_queries.get_patient_profile(id)` for each, collect the profile dicts, then call **`qdrant_queries.load_patient_profiles(profiles)`**. This fills the `patient_profiles` collection so **`find_similar_patients`** works.
4. **MongoDB**
   - Continue to use **`etl/load_faers_to_mongo.py`** as you do now. The app will use **`mongo_queries.get_faers_reports_by_ids(ids)`** to attach evidence to Qdrant results; no need to load MongoDB from Qdrant or vice versa for the merge.

So the **merge with current DBs** is: run the existing PG/Neo4j/Mongo ETL as today; add running Qdrant + `load_faers_to_qdrant.py` (and optionally `load_patient_profiles`); then wire the app to call Qdrant and MongoDB together.

### Step 5: Wire the Streamlit app to Qdrant

In **`app/demo.py`**:

1. **Optional import and flag** (same pattern as PG/Neo4j/Mongo):

```python
try:
    from db import qdrant_queries
    HAS_QDRANT = True
except Exception as e:
    HAS_QDRANT = False
    _QDRANT_ERR = str(e)
```

2. **Config for Qdrant** (env or defaults):

```python
def _qdrant_path():
    return os.getenv("QDRANT_PATH", "")  # "" = use Docker host/port
# QDRANT_HOST, QDRANT_PORT are already read inside db/qdrant_queries.py
```

3. **New sidebar page**, e.g. **“Similar adverse events (Qdrant)”**:
   - Input: patient ID (or free-text patient summary) and proposed drug.
   - If patient ID given: call `pg_queries.get_patient_profile(patient_id)`, build a short text summary (e.g. “Patient: age X, gender, conditions: …, medications: …”), then call `qdrant_queries.find_similar_adverse_events(summary, proposed_drug, top_k=10)`.
   - Display the list of similar reports (reactions, outcome, report_id, score).
   - Optionally: for each report_id, call **`mongo_queries.get_faers_reports_by_ids([report_id], raw=True)`** and show a link or expandable evidence.

4. **Full safety check** (existing page):
   - After step 2 (Neo4j interactions/side effects), add step 3:
     - Build patient summary from `get_patient_profile()` (or from run_outputs).
     - If `HAS_QDRANT` and proposed_drug: call **`qdrant_queries.find_similar_adverse_events(patient_summary, proposed_drug, top_k=5)`**, put results in `run_outputs["similar_adverse_events"]`, and optionally fetch evidence with **`mongo_queries.get_faers_reports_by_ids(ids)`**.
   - Then keep step 4 as today: log the run to MongoDB with **`mongo_queries.log_safety_check(...)`**.

That way the **current databases** (PostgreSQL, Neo4j, MongoDB) stay unchanged; Qdrant is an additional step that consumes the same patient/drug context and writes back into the same audit payload you already log.

### Step 6: (Optional) Drug naming consistency

- **Neo4j** and **Qdrant** both use drug **names** (e.g. “Warfarin”, “Aspirin”). For the “safe alternatives” pipeline (if you later add `load_drugs_to_qdrant` and use `find_safe_alternatives`), use the same naming convention in both so that Qdrant payload filters and Neo4j nodes align (e.g. same strings for drug names in SIDER/RxNav and in FAERS/Qdrant).

### Step 7: Update README and docs

- In the main **README.md**:
  - Uncomment or add **`db/qdrant_queries.py`** and **`etl/load_faers_to_qdrant.py`** in the repo structure.
  - Under “Databases and Roles”, describe Qdrant as in this guide (vector similarity over FAERS; optional patient/drug profiles).
  - In “Setup”, add: start Qdrant (Docker or local), run `python etl/load_faers_to_qdrant.py` (and optionally load patient profiles), and list the new env vars: `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_PATH`.
- In **PROJECT_SPLIT.md**, Part 3 is already described; you can add a short note that the implementation now lives under `db/qdrant_queries.py` and `etl/load_faers_to_qdrant.py` at project root and is merged with the other parts as above.

---

## Summary

- **What Qdrant does:** Provides vector similarity search over FAERS adverse events (and optionally patient/drug profiles) so the app can answer “similar cases to this patient on this drug” and attach evidence from MongoDB.
- **How it fits:** It sits **next to** PostgreSQL (patient data), Neo4j (interactions/side effects), and MongoDB (evidence + audit). The app orchestrates all four: PG + Neo4j + Qdrant + MongoDB.
- **Merge steps:** (1) Copy `qdrant/db/qdrant_queries.py` and `qdrant/etl/load_faers_to_qdrant.py` into root `db/` and `etl/`. (2) Add deps to `requirements.txt`. (3) Start Qdrant. (4) Run `load_faers_to_qdrant.py` (and optionally load patient profiles from PG). (5) Wire `app/demo.py` to call Qdrant and use `get_faers_reports_by_ids` for evidence. (6) Optionally align drug naming with Neo4j; (7) update README and PROJECT_SPLIT. After that, your current databases are unchanged and Qdrant is integrated into the same workflow and evidence/audit trail.
