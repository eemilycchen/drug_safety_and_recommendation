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
    pg_queries.py            # get_active_medications(), get_patient_profile(), helpers
    pg_queries.ipynb         # Notebook for exploring pg_queries
    # neo4j_queries.py       # TODO part 2
    # qdrant_queries.py      # TODO part 3
    # mongo_queries.py       # TODO part 4
  etl/
    __init__.py
    load_synthea_to_pg.py    # Load all Synthea CSVs into PostgreSQL 
    load_synthea_to_pg.ipynb # Notebook for running the ETL
    # load_rxnav_to_neo4j.py # (Part 2)
    # load_sider_to_neo4j.py # (Part 2)
    # load_faers_to_qdrant.py# (Part 3)
    # load_faers_to_mongo.py # (Part 4)
  app/                       # (Part 5, to be implemented)
    # config.py              # Central DB config
    # drug_safety_check.py   # Main orchestration & reporting
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
- **Qdrant **  
Stores **vector embeddings** of adverse event reports and/or patient profiles derived from **openFDA FAERS**. Enables similarity search such as “find FAERS cases most similar to this patient on this drug”.
- **MongoDB **  
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

`requirements.txt` (current) includes:

- `psycopg2-binary` — PostgreSQL driver
- `pandas` — CSV handling for ETL

Additional dependencies for Neo4j, Qdrant, MongoDB, and the app will be added as those parts are implemented.

### 2. Databases

You will ultimately need running instances of:

- PostgreSQL
- Neo4j
- Qdrant
- MongoDB

For now, **Part 1** only requires **PostgreSQL**.

Create a database, `drug_safety`, and ensure the connection URL matches what the code expects.

Default connection URL:

```text
postgresql://postgres:postgres@localhost:5432/drug_safety
```

You can override this via the `PG_URL` environment variable.

---

## Part 1 — PostgreSQL + Synthea (Patient Data)

Part 1 is responsible for:

- Designing the relational schema for Synthea data
- Loading all Synthea CSVs into PostgreSQL
- Exposing a minimal, well-defined interface for other parts:
  - `get_active_medications(patient_id)`
  - `get_patient_profile(patient_id)`

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

The core Part 1 interface lives in `db/pg_queries.py`.

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

These two functions are the main contract used by **Part 3 (Qdrant)** and **Part 5 (Application)**.

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

## Planned Parts 2–5 (High Level)

Implementation for Parts 2–5 follows the design in `PROJECT_SPLIT.md`:

- **Part 2 (Neo4j + RxNav + SIDER)**  
  - ETL from RxNav and SIDER into a Neo4j drug graph  
  - Functions: `check_interactions(current_med_names, proposed_drug)`, `get_side_effects(drug_name)`
- **Part 3 (Qdrant + openFDA FAERS)**  
  - Embed FAERS and/or patient summaries and store vectors in Qdrant  
  - Functions: `find_similar_adverse_events(patient_summary, drug_name, top_k)`, `find_similar_patients(patient_summary, top_k)`
- **Part 4 (MongoDB Evidence Store + Audit Trail)**  
  - Store raw and normalized FAERS docs  
  - Store safety check audit logs  
  - Functions: `get_faers_reports_by_ids(faers_ids)`, `log_safety_check(run)`, `get_safety_check(run_id)`
- **Part 5 (Application & Integration)**  
  - Central `config.py` for DB connection settings  
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

