# Project Split: Clinical Decision-Support Tool for Drug Safety

This document outlines how the project is divided into **5 parts**: **one part per database** plus **one integration part**.

---

## Overview

```
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                                5-PART PROJECT STRUCTURE                                     │
├──────────────┬──────────────┬──────────────┬──────────────┬────────────────────────────────┤
│   PART 1     │   PART 2     │   PART 3     │   PART 4     │             PART 5             │
│ PostgreSQL   │   Neo4j      │   Qdrant     │  MongoDB     │    Application & Integration   │
│ + Synthea    │ RxNav+SIDER  │ + openFDA    │ Evidence+    │                                │
│              │ (graph KB)   │   FAERS      │ Audit trail  │                                │
└──────────────┴──────────────┴──────────────┴──────────────┴────────────────────────────────┘
```

---

## PART 1 — PostgreSQL + Synthea (Patient Data)

**Owner:** Person 1  
**Focus:** Structured patient and prescription data

### Responsibilities
- Generate synthetic patients using Synthea
- Design and implement PostgreSQL schema
- ETL: Load Synthea CSVs into PostgreSQL
- Expose query functions for other parts

### Deliverables
| File | Description |
|------|-------------|
| `etl/load_synthea_to_pg.py` | Parse Synthea CSVs, bulk-load into PostgreSQL |
| `db/pg_schema.sql` | DDL for all tables |
| `db/pg_queries.py` | `get_active_medications()`, `get_patient_profile()`, `get_medication_history()`, `get_patient_timeline()`, `validate_timeline_consistency()`, `list_patients()` |

### Data Source
- **Synthea** — Synthetic EHR (patients, medications, conditions, encounters, etc.)

### Key Functions (Interface Contract)
```python
def get_active_medications(patient_id: str) -> list[dict]
def get_patient_profile(patient_id: str) -> dict
def get_medication_history(patient_id: str, db_url=None, limit=100, years_back=None, since_date=None) -> list[dict]
def get_patient_timeline(patient_id: str, db_url=None, since_date=None, event_types=None) -> list[dict]
def validate_timeline_consistency(timeline_events: list[dict]) -> dict
def list_patients(limit=20, db_url=None) -> list[dict]
```

### Dependencies
- **From:** None (Part 1 is the source of patient data)
- **To:** Part 3 needs `get_patient_profile()` for embeddings; Part 5 needs both core functions; `get_medication_history` and `get_patient_timeline` support richer context for MongoDB evidence and vector DB (Qdrant)

---

## PART 2 — Neo4j + RxNav + SIDER (Drug Interactions & Side Effects)

**Owner:** Person 2  
**Focus:** Knowledge graph of drug–drug interactions and drug–side-effect relationships

### Responsibilities
- Fetch drug interactions from RxNav API
- Parse SIDER side-effect data
- Design and implement Neo4j schema for Drug and SideEffect nodes + relationships
- ETL: Load RxNav + SIDER into Neo4j
- Expose query functions used by the application

### Deliverables
| File | Description |
|------|-------------|
| `etl/load_rxnav_to_neo4j.py` | Fetch RxNav API, create `Drug` nodes and `INTERACTS_WITH` edges |
| `etl/load_sider_to_neo4j.py` | Parse SIDER TSV, create `SideEffect` nodes and `HAS_SIDE_EFFECT` edges |
| `db/neo4j_queries.py` | `check_interactions()`, `get_side_effects()` |

### Data Sources
- **NLM RxNav Interaction API** — Drug–drug interactions (severity, description)
- **SIDER** — Drug–side-effect pairs (frequency)

### Key Functions (Interface Contract)
```python
def check_interactions(current_med_names: list[str], proposed_drug: str) -> list[dict]
def get_side_effects(drug_name: str) -> list[dict]
```

### Dependencies
- **From:** Part 1 — medication codes from Synthea for RxNav lookups
- **To:** Part 5 needs `check_interactions()` and `get_side_effects()`

---

## PART 3 — Qdrant + openFDA FAERS (Adverse Events & Similarity)

**Owner:** Person 3  
**Focus:** Vector similarity search over embedded patient profiles and adverse event reports

### Responsibilities
- Fetch adverse event reports from openFDA FAERS API (or read them from MongoDB if Part 4 is run first)
- Create text summaries, embed with sentence-transformers, and upsert vectors into Qdrant
- Expose query functions for similarity retrieval

### Deliverables
| File | Description |
|------|-------------|
| `etl/load_faers_to_qdrant.py` | Embed FAERS summaries and upsert into Qdrant |
| `db/qdrant_queries.py` | `find_similar_adverse_events()`, `find_similar_patients()` |

### Data Source
- **openFDA FAERS API** — Real-world adverse event reports

### Key Functions (Interface Contract)
```python
def find_similar_adverse_events(patient_summary: str, drug_name: str, top_k: int = 10) -> list[dict]
def find_similar_patients(patient_summary: str, top_k: int = 10) -> list[dict]
```

### Dependencies
- **From:** Part 1 — `get_patient_profile()` to build the patient embedding text
- **To:** Part 5 needs `find_similar_adverse_events()`

---

## PART 4 — MongoDB (Evidence Store + Audit Trail)

**Owner:** Person 4  
**Focus:** Document storage for traceability, evidence, and reproducibility

### Responsibilities
- Store **raw FAERS JSON** (as returned by openFDA) for traceability
- Store **normalized FAERS documents** used to create embeddings
- Store an **audit log** of each drug safety check (inputs + outputs + versions)
- Expose query functions for “show evidence behind this warning” and for audit/history

### Deliverables
| File | Description |
|------|-------------|
| `etl/load_faers_to_mongo.py` | Fetch FAERS and store raw + normalized documents in MongoDB |
| `db/mongo_queries.py` | Fetch evidence docs by id; write/read audit logs |

### Data Source
- **openFDA FAERS API** — Real-world adverse event reports (stored as documents)

### Key Functions (Interface Contract)
```python
def get_faers_reports_by_ids(faers_ids: list[str]) -> list[dict]
def log_safety_check(run: dict) -> str
def get_safety_check(run_id: str) -> dict | None
```

### Dependencies
- **From:** None required (can fetch from openFDA directly)
- **To:** Part 5 uses MongoDB to (a) attach raw evidence to Qdrant matches, (b) persist audit logs
- **Optional:** Part 3 can embed from MongoDB’s normalized docs instead of calling openFDA directly

---

## PART 5 — Application & Integration (Unified Drug Safety Check)

**Owner:** Person 5  
**Focus:** Orchestrate all four databases and produce the final safety report

### Responsibilities
- Define project dependencies (`requirements.txt`)
- Configure database connections (`config.py`)
- Build main application that calls all query modules
- Combine results into unified safety report
- Coordinate documentation and presentation

### Deliverables
| File | Description |
|------|-------------|
| `app/config.py` | Connection settings for PostgreSQL, Neo4j, Qdrant, MongoDB |
| `app/drug_safety_check.py` | Main app: orchestrates drug safety check + evidence + logging |
| `requirements.txt` | Python dependencies |

### Dependencies
- **From:** Parts 1, 2, 3, 4 — all query functions
- **To:** None (Part 5 is the consumer)

---

## Query Flow (How Parts Connect)

```
Input: patient_id + proposed_drug
                    │
    ┌───────────────┼───────────────────────┬───────────────────────┐
    ▼               ▼                       ▼                       ▼
┌────────┐   ┌────────────┐          ┌────────────┐          ┌────────────┐
│ PART 1 │   │   PART 2   │          │   PART 3   │          │   PART 4   │
│   PG   │   │   Neo4j    │          │   Qdrant   │          │  MongoDB   │
│ Step 1 │   │ Step 2     │          │ Step 3     │          │ Step 4     │
│ patient│   │ interactions│         │ similar    │          │ evidence + │
│ state  │   │ + sidefx   │          │ FAERS cases│          │ audit log  │
└────────┘   └────────────┘          └────────────┘          └────────────┘
    │               │                       │                       │
    └───────────────┼───────────────────────┼───────────────────────┘
                    ▼
          ┌──────────────────────────┐
          │          PART 5          │
          │   app/drug_safety_check  │
          │   combined safety report │
          └──────────────────────────┘
```

---

## File Ownership Summary

| Part | Owned Files |
|------|-------------|
| **Part 1** | `etl/load_synthea_to_pg.py`, `db/pg_schema.sql`, `db/pg_queries.py` |
| **Part 2** | `etl/load_rxnav_to_neo4j.py`, `etl/load_sider_to_neo4j.py`, `db/neo4j_queries.py` |
| **Part 3** | `etl/load_faers_to_qdrant.py`, `db/qdrant_queries.py` |
| **Part 4** | `etl/load_faers_to_mongo.py`, `db/mongo_queries.py` |
| **Part 5** | `app/config.py`, `app/drug_safety_check.py`, `requirements.txt` |

---

## Execution Order

1. **Start databases:** PostgreSQL, Neo4j, Qdrant, MongoDB (Docker)
2. **Run ETL (order matters):**
   - Part 1: `python etl/load_synthea_to_pg.py`
   - Part 2: `python etl/load_rxnav_to_neo4j.py` + `python etl/load_sider_to_neo4j.py`
   - Part 4: `python etl/load_faers_to_mongo.py` *(for raw/normalized evidence; optional but recommended)*
   - Part 3: `python etl/load_faers_to_qdrant.py` *(can embed from openFDA directly or from MongoDB-normalized docs)*
3. **Run app (Part 5):** `python app/drug_safety_check.py --patient-id <UUID> --proposed-drug "Warfarin"`
