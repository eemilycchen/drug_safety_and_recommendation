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
    mongo_queries.py         # Part 4: get_faers_reports_by_ids(), log_safety_check(), etc.
    # qdrant_queries.py      # TODO part 3
  etl/
    __init__.py
    load_synthea_to_pg.py    # Load all Synthea CSVs into PostgreSQL 
    load_synthea_to_pg.ipynb # Notebook for running the ETL
    load_sider_to_neo4j.py   # Part 2: SIDER side-effect TSV → Neo4j (SideEffect, HAS_SIDE_EFFECT)
    load_faers_to_mongo.py   # Part 4: openFDA FAERS → MongoDB (raw + normalized)
    # load_rxnav_to_neo4j.py # Part 2: RxNav API → Drug nodes + INTERACTS_WITH (if implemented)
    # load_faers_to_qdrant.py# (Part 3)
  app/                       # (Part 5, to be implemented)
    # config.py              # Central DB config
    # drug_safety_check.py   # Main orchestration & reporting
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

Additional dependencies for Qdrant and the app will be added as those parts are implemented.

### 2. Databases

You will need running instances of:

- **PostgreSQL** — Part 1 (required for patient data)
- **Neo4j** — Part 2 (drug interactions and side effects)
- **MongoDB** — Part 4 (FAERS evidence store and audit)
- **Qdrant** — Part 3 (vector search; when implemented)

For **PostgreSQL**, create a database `drug_safety` and ensure the connection URL matches what the code expects.

Default PostgreSQL URL:

```text
postgresql://postgres:postgres@localhost:5432/drug_safety
```

You can override this via the `PG_URL` environment variable.

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

| Function | Purpose |
|----------|---------|
| `check_interactions(current_med_names, proposed_drug)` | Interactions between current meds and proposed drug (severity, description). |
| `get_side_effects(drug_name)` | Known side effects for a drug (name, frequency). |
| `find_interaction_path(drug_a, drug_b)` | Shortest path of interactions between two drugs. |
| `find_shared_side_effects(drug_a, drug_b)` | Side effects common to both drugs. |
| `find_safer_alternatives(drug_name, current_meds)` | Alternatives that share indications but avoid interactions with current meds. |
| `get_interaction_network(drug_name, depth)` | Nodes and edges around a drug up to `depth` hops. |
| `get_drug_stats()` | Graph counts (drugs, interactions, side-effect links). |

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

| Function | Purpose |
|----------|---------|
| `get_faers_reports_by_ids(faers_ids, raw=True)` | Fetch FAERS reports by `safetyreportid`; used to attach evidence to Qdrant matches. |
| `log_safety_check(run)` | Persist one safety-check run (inputs, outputs, timestamp); returns `run_id`. |
| `get_safety_check(run_id)` | Retrieve an audit record by `run_id`. |

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

## Planned Parts 3 & 5 (High Level)

Implementation follows the design in `PROJECT_SPLIT.md`:

- **Part 3 (Qdrant + openFDA FAERS)**  
  - Embed FAERS and/or patient summaries and store vectors in Qdrant (fed from MongoDB normalized docs).  
  - Functions: `find_similar_adverse_events(patient_summary, drug_name, top_k)`, `find_similar_patients(patient_summary, top_k)`.
- **Part 5 (Application & Integration)**  
  - Central `config.py` for DB connection settings.  
  - Main orchestrator `drug_safety_check.py` that:
    1. Reads patient profile from PostgreSQL (Part 1)
    2. Checks interactions and side effects via Neo4j (Part 2)
    3. Queries Qdrant for similar FAERS cases (Part 3)
    4. Retrieves raw evidence + logs the run in MongoDB (Part 4)
    5. Produces a unified safety report for the user

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

