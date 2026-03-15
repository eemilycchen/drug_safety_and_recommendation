## Clinical Decision-Support: Drug Safety & Recommendation

This project implements a **multi-database clinical decision-support tool** that helps assess the safety of a proposed medication for a given patient. It combines:

- **PostgreSQL**: Structured patient and prescription data from **Synthea**
- **Neo4j**: Graph of drug–drug interactions and side effects from **RxNav** + **SIDER**
- **Qdrant**: Vector similarity search over adverse event reports from **openFDA FAERS**
- **MongoDB**: Evidence store + audit trail for traceability and reproducibility

Given a **patient ID** and a **proposed drug**, the system retrieves the patient’s current state, checks for unsafe interactions, looks for similar real-world adverse events, and returns a unified safety report with links back to the underlying evidence.

---

## Repository Structure

```text
drug_safety_and_recommendation/
  data/
    synthea/                 # Synthea CSV exports (patients, medications, encounters, etc.)
  db/
    __init__.py
    pg_schema.sql            # PostgreSQL DDL for Synthea data 
    pg_queries.py            # get_active_medications(), get_patient_profile(), get_medication_history(), get_patient_timeline(), validate_timeline_consistency()
    pg_queries.ipynb         # Notebook for exploring pg_queries
    neo4j_queries.py         # Part 2: check_interactions(), get_side_effects(), etc.
    qdrant_queries.py        # Part 3: find_similar_adverse_events(), load_patient_profiles(), etc.
    mongo_queries.py         # Part 4: get_faers_reports_by_ids(), log_safety_check(), etc.
  etl/
    __init__.py
    load_synthea_to_pg.py    # Load all Synthea CSVs into PostgreSQL 
    load_synthea_to_pg.ipynb # Notebook for running the ETL
    load_sider_to_neo4j.py   # Part 2: SIDER side-effect TSV → Neo4j (SideEffect, HAS_SIDE_EFFECT)
    load_faers_to_mongo.py   # Part 4: openFDA FAERS → MongoDB (raw + normalized)
    load_faers_to_qdrant.py # Part 3: openFDA FAERS → Qdrant (embed + vector store)
    # load_rxnav_to_neo4j.py # Part 2: RxNav API → Drug nodes + INTERACTS_WITH (if implemented)
  app/                       # Part 5 — demo and orchestration
    demo.py                  # Streamlit demo: single app for all four databases
    # config.py              # Central DB config (to be implemented)
    # drug_safety_check.py   # Main orchestration & reporting (to be implemented)
  docs/
    database_diagrams.md     # Mermaid diagrams for all four databases
    database_diagrams.html   # Browser-viewable version of the diagrams
  .gitignore
  PROJECT_SPLIT.md           # Detailed split of parts 1–5, responsibilities, contracts
  plan.md                    # High-level goals
  README.md
  requirements.txt           # Python dependencies
```

For details on what each part must implement and the function-level interface contracts, see `PROJECT_SPLIT.md`.

---

## Databases and Roles

- **PostgreSQL**  
Stores **structured EHR-like data** from Synthea: patients, encounters, medications, conditions, labs, etc. This is the source of truth for a patient’s current active medications and overall clinical profile.
- **Neo4j**  
Stores a **knowledge graph** of drug–drug interactions and side effects, built from **RxNav** and **SIDER**. Enables graph queries such as “does this proposed drug interact with any of the patient’s current medications?” and “what serious side effects are associated with this drug?”.
- **Qdrant**  
Stores **vector embeddings** of adverse event reports and/or patient profiles derived from **openFDA FAERS**. Enables similarity search such as “find FAERS cases most similar to this patient on this drug”.
- **MongoDB**  
Serves as an **evidence store and audit trail**:
  - Raw FAERS JSON documents for traceability
  - Normalized documents used to build Qdrant embeddings
  - Audit log of each safety check (inputs, outputs, data/embedding versions)
- **Application Layer**  
Orchestrates all four databases to produce a **unified safety report** and expose a CLI or notebook-based interface.

---

## Setup

### 1. Python Environment

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

`requirements.txt` includes:

- `psycopg2-binary` — PostgreSQL driver (Part 1)
- `pandas` — CSV handling for ETL
- `neo4j` — Neo4j driver (Part 2)
- `pymongo`, `requests` — MongoDB and openFDA API (Part 4)
- `streamlit` — Web demo (run `streamlit run app/demo.py`)
- `qdrant-client`, `sentence-transformers`, `numpy`, `scikit-learn`, `python-dotenv` — Part 3 (Qdrant vector search)

### 2. Databases

You will need running instances of:

- **PostgreSQL** — Part 1 (required for patient data)
- **Neo4j** — Part 2 (drug interactions and side effects)
- **Qdrant** — Part 3 (vector similarity over FAERS adverse events)
- **MongoDB** — Part 4 (FAERS evidence store and audit)

For **PostgreSQL**, create a database `drug_safety` and ensure the connection URL matches what the code expects.

Default PostgreSQL URL:

```text
postgresql://postgres:postgres@localhost:5432/drug_safety
```

You can override this via the `PG_URL` environment variable.

Neo4j and MongoDB use defaults (`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `MONGO_URI`, `MONGO_DB`); set these if your instances differ. Start Docker Desktop first, then proceed below.

**Qdrant** (Part 3): default is Docker at `localhost:6333`. Start with:

```bash
docker compose -f qdrant/docker-compose.yml up -d
```

Then load FAERS into Qdrant (from project root):

```bash
python etl/load_faers_to_qdrant.py --limit 5000
# or use cached openFDA data: python etl/load_faers_to_qdrant.py --use-cache
```

Use `QDRANT_HOST`, `QDRANT_PORT` if Qdrant is elsewhere; or set `QDRANT_PATH` to a directory for on-disk storage (no server).

### 3. Run the Streamlit demo

From the project root (with your venv activated and databases running):

Export all of your commands:

```bash
# PostgreSQL
export PG_URL="postgresql://postgres:<your_password>@localhost:5432/drug_safety"

# Neo4j
export NEO4J_URI="neo4j://127.0.0.1:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="<your_password>"

# MongoDB
export MONGO_URI="mongodb+srv://YOUR_USER:YOUR_PASSWORD@cluster0.qizimgq.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
export MONGO_DB="drug_safety"
```

Load data into PostgreSQL and MongoDB first using

```bash
python etl/load_synthea_to_pg.py
python etl/load_faers_to_mongo.py --limit 100
```

Set up Qdrant using the above commands.

```bash
streamlit run app/demo.py
```

The **single demo** uses all four databases:

- **Patient data (PostgreSQL)** — List patients, view profile, active medications, medication history, and timeline.
- **Drug knowledge (Neo4j)** — Check interactions (current meds vs proposed drug), side effects, interaction paths, shared side effects, safer alternatives, and graph statistics.
- **Similar adverse events (Qdrant)** — Find FAERS reports similar to a patient on a proposed drug; optionally **fetch full evidence from MongoDB** for those report IDs (MongoDB + vector DB together).
- **Evidence & audit (MongoDB)** — Log safety-check runs and retrieve them by `run_id`; fetch FAERS reports by ID.
- **Full safety check** — Patient ID + proposed drug → PostgreSQL (profile) → Neo4j (interactions, side effects) → Qdrant (similar adverse events) → MongoDB (evidence for those events + audit log).

If a database is unreachable, the app shows a clear error and continues for other sections.

---

## Part 1 — PostgreSQL + Synthea (Patient Data)

Part 1 is responsible for:

- Designing the relational schema for Synthea data
- Loading all Synthea CSVs into PostgreSQL
- Exposing a well-defined interface for other parts:
  - `get_active_medications(patient_id)` — current medications only
  - `get_patient_profile(patient_id)` — current state (demographics, active meds, conditions, allergies, recent observations)
  - `get_medication_history(patient_id, limit=100, years_back=None, since_date=None)` — full medication history with optional filters
  - `get_patient_timeline(patient_id, since_date=None, event_types=None)` — chronological clinical events (medication, condition, encounter, procedure)
  - `validate_timeline_consistency(timeline_events)` — optional validation helper for timeline data

### 1. Load Synthea CSVs into PostgreSQL

Ensure the Synthea CSVs are present in `data/synthea/` (or set `SYNTHEA_DATA_DIR`). 

Then run:

```bash
export PG_URL="postgresql://<your_username>:<your_password>@localhost:5432/drug_safety"  # adjust as needed

python etl/load_synthea_to_pg.py
```

This will:

- Apply the schema from `db/pg_schema.sql` (drop & recreate tables)
- Bulk-load all 16 Synthea CSV files into PostgreSQL

To skip re-creating the schema (if tables already exist), use:

```bash
python etl/load_synthea_to_pg.py --no-schema
```

### 2. Query Helpers

The core Part 1 interface lives in `db/pg_queries.py`. There is also a convenience helper `list_patients(limit=20)` for demos.

- **Get active medications for a patient**

```python
from db.pg_queries import get_active_medications

meds = get_active_medications(patient_id="some-uuid")
```

Returns a list of dicts with fields like `code`, `description`, `start_ts`, `dispenses`, `base_cost`, etc., where `stop_ts IS NULL` (still active).

- **Get a rich patient profile**

```python
from db.pg_queries import get_patient_profile

profile = get_patient_profile(patient_id="some-uuid")
```

The profile dict includes:

- `patient`: demographics and basic attributes
- `active_medications`: medications with no stop timestamp
- `conditions`: active conditions
- `allergies`: active allergies
- `recent_observations`: most recent observations (e.g., vitals, labs)
- **Get full medication history** (for richer context with MongoDB / vector DB)

```python
from db.pg_queries import get_medication_history

history = get_medication_history(patient_id="some-uuid", limit=100, years_back=5)  # or since_date="2020-01-01"
```

Returns all medication records (current and past), newest first. Optional filters: `limit`, `years_back`, or `since_date` (ISO date). Cannot use both `years_back` and `since_date`.

- **Get a single chronological timeline** (medications, conditions, encounters, procedures)

```python
from db.pg_queries import get_patient_timeline

timeline = get_patient_timeline(patient_id="some-uuid", since_date="2022-01-01", event_types=["medication", "condition"])
```

Each event has `date`, `type`, `description`, `end_date`, and `details`. Default `event_types` is all four.

- **Validate timeline data** (optional, for debugging)

```python
from db.pg_queries import validate_timeline_consistency

result = validate_timeline_consistency(timeline)
# result: { "valid": True/False, "issues": [...], "event_count": N, "date_range": {...} }
```

`get_active_medications` and `get_patient_profile` are the main contract used by **Part 3 (Qdrant)** and **Part 5 (Application)**. `get_medication_history` and `get_patient_timeline` support richer patient context for embeddings, evidence, and audit.

---

## Part 2 — Neo4j (Drug Interactions & Side Effects)

Part 2 provides a **knowledge graph** of drugs, drug–drug interactions, and side effects for safety checks.

### 1. Prerequisites

- **Neo4j** running (default: `bolt://127.0.0.1:7687`).
- **Drug nodes** must exist in the graph before loading SIDER side effects. Create them with your RxNav or DrugBank ETL (e.g. `load_rxnav_to_neo4j.py` if implemented), which should create `Drug` nodes and `INTERACTS_WITH` edges.

### 2. Load SIDER Side Effects into Neo4j

After Drug nodes exist, load side-effect data from the SIDER TSV:

```bash
python etl/load_sider_to_neo4j.py --file /path/to/meddra_all_se.tsv --uri bolt://127.0.0.1:7687 --user neo4j --password YOUR_PASSWORD
```

- **6-column SIDER file** (`meddra_all_se.tsv`): use optional `--drug-mapping` and `--drug-atc` TSVs to link STITCH IDs to Drug names/ATC for matching.
- **Simplified 3-column TSV** (drug_name, side_effect_name, frequency): use `--simple`.

This creates `SideEffect` nodes and `HAS_SIDE_EFFECT` relationships from drugs to side effects.

### 3. Query Interface

The Part 2 interface lives in `db/neo4j_queries.py`.


| Function                                               | Purpose                                                                       |
| ------------------------------------------------------ | ----------------------------------------------------------------------------- |
| `check_interactions(current_med_names, proposed_drug)` | Interactions between current meds and proposed drug (severity, description).  |
| `get_side_effects(drug_name)`                          | Known side effects for a drug (name, frequency).                              |
| `find_interaction_path(drug_a, drug_b)`                | Shortest path of interactions between two drugs.                              |
| `find_shared_side_effects(drug_a, drug_b)`             | Side effects common to both drugs.                                            |
| `find_safer_alternatives(drug_name, current_meds)`     | Alternatives that share indications but avoid interactions with current meds. |
| `get_interaction_network(drug_name, depth)`            | Nodes and edges around a drug up to `depth` hops.                             |
| `get_drug_stats()`                                     | Graph counts (drugs, interactions, side-effect links).                        |


### 4. Run the Neo4j Queries Script

From the project root (with Neo4j running and data loaded):

```bash
python db/neo4j_queries.py --uri bolt://127.0.0.1:7687 --user neo4j --password YOUR_PASSWORD
```

Optional arguments: `--drug` (default `Warfarin`), `--current-meds` (comma-separated, default `Aspirin`), `--alt-drug` (for path and shared-side-effect queries).

Use in Python:

```python
from db.neo4j_queries import check_interactions, get_side_effects

interactions = check_interactions(["Aspirin", "Metformin"], "Warfarin")
effects = get_side_effects("Warfarin")
```

---

## Part 3 — Qdrant (Vector Similarities over FAERS Adverse Events)

Part 3 provides **vector similarity search** over adverse event reports from **openFDA FAERS**. A patient summary and proposed drug are embedded with the **BioLORD-2023** model (768-dim); Qdrant returns the most similar FAERS cases. The app then fetches full evidence for those report IDs from MongoDB (Part 4).

### 1. Prerequisites

- **Qdrant** running (default: `localhost:6333`). Start with Docker:
  ```bash
  docker compose -f qdrant/docker-compose.yml up -d
  ```
  Or use on-disk storage (no server): set `QDRANT_PATH` to a directory (e.g. `./qdrant_local`).
- Optional: `OPENFDA_API_KEY` in the environment for higher openFDA rate limits when fetching FAERS.

### 2. Load FAERS into Qdrant

From the project root (with Qdrant running):

```bash
# Fetch from openFDA, embed, and load (default: up to 5000 reports)
python etl/load_faers_to_qdrant.py

# Use cached raw JSON (no API call; requires data/faers_raw.json from a previous run)
python etl/load_faers_to_qdrant.py --use-cache

# Limit number of reports (e.g. quick test)
python etl/load_faers_to_qdrant.py --limit 500
```

Options:

- `--limit N` — Max reports to fetch from openFDA (default: 5000).
- `--use-cache` — Read from `data/faers_raw.json` instead of calling the API.
- `--qdrant-host`, `--qdrant-port` — Qdrant server (default: localhost:6333).
- `--qdrant-path PATH` — Use on-disk Qdrant at `PATH` instead of a server.

The script parses and filters FAERS (keeps only reports with drugs and reactions), serializes each to text, embeds with BioLORD, and upserts into the `adverse_events` collection. Payload indexes are created for filtered search (drug, outcome, serious, patient_sex).

### 3. Query Interface

The Part 3 interface lives in `db/qdrant_queries.py`.


| Function                                                                  | Purpose                                                                                        |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `find_similar_adverse_events(patient_summary, drug_name, top_k=10)`       | FAERS reports similar to the summary, filtered by drug; returns report_id, reactions, outcome. |
| `find_similar_adverse_events_multi_filter(..., drug_names, outcome, ...)` | Same with optional filters (drugs, outcome, serious_only, sex).                                |
| `find_similar_patients(patient_summary, top_k=10)`                        | Similar patient profiles (requires `load_patient_profiles()` to have been run).                |
| `load_patient_profiles(profiles)`                                         | Embed and upsert Synthea profiles (e.g. from `get_patient_profile()`) into `patient_profiles`. |
| `analyze_adverse_event_aspects(results)`                                  | Summarize results by severity, organ system, top reactions, outcome distribution.              |


Use in Python:

```python
from db.qdrant_queries import find_similar_adverse_events, find_similar_patients

# Similar adverse events for a patient on a proposed drug
results = find_similar_adverse_events(
    "65 year old male, diabetes, hypertension, on metformin",
    "warfarin",
    top_k=5
)
# Each result has report_id, similarity_score, reactions, outcome, etc.
# Use report_id with mongo_queries.get_faers_reports_by_ids() for full evidence.
```

### 4. Environment Variables

- **QDRANT_HOST**, **QDRANT_PORT** — Qdrant server (default: `localhost`, `6333`). Leave unset when using `QDRANT_PATH`.
- **QDRANT_PATH** — Directory for on-disk Qdrant; if set, the client uses local storage instead of host/port.

---

## Part 4 — MongoDB (FAERS Evidence Store & Audit Trail)

Part 4 stores **openFDA FAERS** adverse-event reports and an **audit log** of safety-check runs for traceability.

### 1. Prerequisites

- **MongoDB** running (default: `mongodb://localhost:27017`).
- Override via `MONGO_URI` and `MONGO_DB` (default database: `drug_safety`).

### 2. Load FAERS into MongoDB

Fetch reports from the openFDA Drug Event API and write raw + normalized documents:

```bash
python etl/load_faers_to_mongo.py
```

If you use **MongoDB Atlas** (as in `etl/load_faers_to_mongo.ipynb`), set `MONGO_URI` and `MONGO_DB` first, or pass `--mongo-uri` and `--db`, so the script connects to the same cluster as the notebook:

```bash
export MONGO_URI="mongodb+srv://user:password@cluster.mongodb.net/?retryWrites=true&w=majority"
export MONGO_DB="drug_safety"
python etl/load_faers_to_mongo.py --limit 100
```

Options:

- `--mongo-uri` — MongoDB connection URI (default: `mongodb://localhost:27017`).
- `--db` — Database name (default: `drug_safety`).
- `--limit N` — Max number of reports to fetch (default: 500).
- `--search 'query'` — openFDA search filter (e.g. `patient.patientsex:1`).
- `--dry-run` — Fetch from API but do not write to MongoDB.

**Collections:**

- `faers_raw` — Raw API response per report; `_id` = `safetyreportid`.
- `faers_normalized` — Flattened summary (drugs, reactions, summary text) for embedding and evidence display; same `_id` for lookup.
- `safety_check_audit` — Audit records written by `log_safety_check()` (Part 5).

### 3. Query Interface

The Part 4 interface lives in `db/mongo_queries.py`.


| Function                                        | Purpose                                                                             |
| ----------------------------------------------- | ----------------------------------------------------------------------------------- |
| `get_faers_reports_by_ids(faers_ids, raw=True)` | Fetch FAERS reports by `safetyreportid`; used to attach evidence to Qdrant matches. |
| `log_safety_check(run)`                         | Persist one safety-check run (inputs, outputs, timestamp); returns `run_id`.        |
| `get_safety_check(run_id)`                      | Retrieve an audit record by `run_id`.                                               |


Use in Python:

```python
from db.mongo_queries import get_faers_reports_by_ids, log_safety_check, get_safety_check

reports = get_faers_reports_by_ids(["12345", "67890"], raw=True)
run_id = log_safety_check({"patient_id": "uuid", "proposed_drug": "Warfarin", "interactions": []})
record = get_safety_check(run_id)
```

---

## Relational Database Visualization (PostgreSQL)

### 1. High-Level Entity Layout

The PostgreSQL schema is a **star around the patient**: `patients` is the hub, and most other tables reference it.

```text
                         ┌───────────────┐
                         │ organizations │
                         └───────┬───────┘
                                 │
                         ┌───────▼───────┐
                         │   providers   │
                         └───────┬───────┘
                                 │
                               (FK)
                                 │
                    ┌────────────▼────────────┐
                    │       encounters        │
                    └─────────┬───────┬───────┘
                              │       │
                            (FK)    (FK)
                              │       │
                        ┌─────▼─┐   ┌─▼─────┐
                        │patients│  │ payers│
                        └─┬───┬──┘   └──────┘
                          │   │
             ┌────────────┘   └───────────────────────────────┐
             │                                                │
        (FK to patient)                                  (FK to patient)
             │                                                │
   ┌─────────▼────────┐   ┌─────────▼────────┐   ┌────────────▼─────────┐
   │   medications    │   │   conditions    │   │       allergies       │
   └────────┬─────────┘   └────────┬────────┘   └────────────┬──────────┘
            │                      │                        │
            │                      │                        │
      ┌─────▼─────────┐   ┌────────▼────────┐   ┌────────────▼─────────┐
      │ observations  │   │  procedures    │   │    immunizations      │
      └───────────────┘   └────────────────┘   └──────────────────────┘
```

- `patients` — core demographics and IDs.
- `encounters` — each visit, linking patient, provider, organization, and payer.
- `medications`, `conditions`, `allergies`, `observations`, `procedures`, `immunizations` — clinical facts, each with a foreign key to `patients` (and usually `encounters`).
- `payers`, `organizations`, `providers` — reference tables describing institutions and insurance.

Indexes (e.g. `idx_medications_patient`, `idx_conditions_patient`) are created on `patient` foreign keys to support fast per-patient queries.

Note: FK means foreign key

### 2. Data Flow: From Synthea CSVs to Tables

```text
Synthea CSVs in data/synthea/
   │
   │  (pandas)
   ▼
etl/load_synthea_to_pg.py
   │
   │  1. Apply db/pg_schema.sql  (DROP + CREATE all tables)
   │  2. For each CSV:
   │        - read CSV
   │        - normalize column names
   │        - bulk INSERT with psycopg2.execute_values
   ▼
PostgreSQL database `drug_safety`
   └── fully populated relational schema
```

This ensures that:

- CSV column names (e.g. `START`, `STOP`) are renamed to the schema’s names (`start_ts`, `stop_ts`, etc.).
- All relationships (patient → encounter → medications/conditions/…) are preserved via foreign keys.

### 3. Query Flow: How `get_patient_profile()` Uses the Schema

```text
Input: patient_id
   │
   ▼
db.pg_queries.get_patient_profile(patient_id)
   │
   ├─ SELECT * FROM patients
   │      → demographics
   │
   ├─ SELECT ... FROM medications
   │      WHERE patient = :id AND stop_ts IS NULL
   │      → active_medications
   │
   ├─ SELECT ... FROM conditions
   │      WHERE patient = :id AND stop_date IS NULL
   │      → active conditions
   │
   ├─ SELECT ... FROM allergies
   │      WHERE patient = :id AND stop_date IS NULL
   │      → active allergies
   │
   └─ SELECT ... FROM observations
          WHERE patient = :id
          ORDER BY obs_date DESC LIMIT 20
          → recent_observations
```

The result is a **single nested JSON-like structure** that summarizes the patient’s state, ready to be:

- Embedded into vectors for Qdrant (Part 3),
- Combined with graph and evidence data in the final safety report (Part 5).

---

---

## Why This Multi-Database Design?

- **PostgreSQL**: Strong consistency, relational integrity, and powerful SQL for core EHR-like data (patients, encounters, prescriptions).
- **Neo4j**: Natural fit for **graph-shaped biomedical knowledge** (drug–drug interactions, drug–side-effect edges) and complex traversals.
- **Qdrant**: Efficient **vector similarity search** over high-dimensional embeddings of patients and adverse event narratives.
- **MongoDB**: Flexible schema for **raw JSON evidence and audit logs**, making it easy to trace every warning back to its source data and model versions.

Together, these systems support an application that can answer:

- *“Is this new prescription safe for this specific patient?”*  
- *“What interactions and side effects should I be worried about?”*  
- *“Have we seen similar real-world cases, and what happened?”*  
- *“Exactly which data and model versions produced this recommendation?”*

