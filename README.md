<div align="center">

# 💊 Drug Safety & Recommendation System

**A production-grade, polyglot clinical decision-support system built on four specialized databases.**  
Ask: *"Is this drug safe for this patient?"* Get a traceable answer grounded in real-world evidence.

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-EHR_Store-336791?style=flat&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Neo4j](https://img.shields.io/badge/Neo4j-Knowledge_Graph-008CC1?style=flat&logo=neo4j&logoColor=white)](https://neo4j.com)
[![Qdrant](https://img.shields.io/badge/Qdrant-Vector_Search-DC244C?style=flat&logo=qdrant&logoColor=white)](https://qdrant.tech)
[![MongoDB](https://img.shields.io/badge/MongoDB-Evidence_Store-47A248?style=flat&logo=mongodb&logoColor=white)](https://mongodb.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-Demo_App-FF4B4B?style=flat&logo=streamlit&logoColor=white)](https://streamlit.io)

*Graduate Data Science Project · UC San Diego · Built on real open biomedical data*

</div>

---

## The Problem

Medication errors and adverse drug reactions are among the leading causes of preventable harm in healthcare. A clinician prescribing a new drug needs to instantly know: does it interact with the patient's current medications? What side effects are documented? Have similar patients experienced serious adverse events on this drug?

No single database handles all of these query shapes well. This system uses four.

---

## What It Does

> Input: a **patient ID** + a **proposed drug**. Output: a traceable safety verdict backed by real-world evidence.

<br>

| Step | What Happens | How |
|------|-------------|-----|
| 🏥 **1. Build Patient Context** | Pull active medications, conditions, allergies, and recent observations | PostgreSQL · star schema · `stop_ts IS NULL` filter |
| 🕸️ **2. Check Drug Interactions** | Traverse the drug knowledge graph for interaction chains and known side effects | Neo4j · Cypher · multi-hop polypharmacy traversal |
| 🔍 **3. Search Real-World Cases** | Find the most similar adverse event reports from 5,000+ FAERS submissions | Qdrant · BioLORD-2023 · 768-dim ANN search |
| 🗂️ **4. Fetch Evidence + Log Run** | Retrieve raw FAERS source documents and persist full audit record | MongoDB · raw + normalized collections |
| 📋 **5. Unified Safety Report** | Every interaction, side effect, and adverse case linked back to its source | Structured JSON · fully reproducible |

---

## Architecture

Four databases, each chosen for the query pattern it handles best:

| Layer | Database | What It Stores | Why This Database |
|-------|----------|----------------|-------------------|
| EHR Store | **PostgreSQL** | Patients, encounters, medications, conditions, allergies, observations (Synthea) | Relational integrity, FK constraints, indexed per-patient SQL on a star schema |
| Knowledge Graph | **Neo4j** | `Drug` nodes, `INTERACTS_WITH` edges (severity, mechanism), `SideEffect` nodes, `HAS_SIDE_EFFECT` edges (RxNav + SIDER) | Multi-hop Cypher traversals, polypharmacy cluster detection, shortest interaction paths |
| Vector Store | **Qdrant** | 768-dim BioLORD-2023 embeddings of FAERS adverse event reports; payload indexes on `drug`, `outcome`, `serious`, `patient_sex` | ANN search over clinical embeddings with filtered retrieval |
| Evidence + Audit | **MongoDB** | Raw + normalized FAERS JSON; append-only safety check audit log with full data lineage | Flexible schema for heterogeneous JSON; immutable audit records |

This is **polyglot persistence** in practice, the same architectural pattern used in production pharmacovigilance and clinical decision-support systems.

---

## Key Technical Decisions

**Why BioLORD-2023?**  
General-purpose sentence transformers (e.g. `all-MiniLM`) underperform on biomedical text. BioLORD-2023 is trained on biomedical concept pairs and clinical ontologies, producing more meaningful similarity scores between drug names, patient summaries, and adverse event narratives.

**Why graph for interactions, not a join table?**  
Drug interaction queries are inherently multi-hop: Drug A interacts with Drug B, which shares a metabolic pathway with Drug C. A relational join table can answer pairwise queries but cannot efficiently traverse polypharmacy clusters or detect indirect interaction chains. Neo4j Cypher traversals handle this natively.

**Why Qdrant over pgvector?**  
Qdrant supports payload-level filtering (e.g. restrict search to reports where `serious=true` and `drug="Warfarin"`) applied at the HNSW index level, not as a post-filter. This keeps retrieval both fast and precise at scale.

**Why MongoDB for the audit trail?**  
Each safety check run produces a heterogeneous document: structured inputs, nested interaction results, a list of FAERS report IDs, embedding model metadata, and data version timestamps. MongoDB's flexible schema stores this as a single document without requiring schema migrations as the system evolves.

---

## Features

**Full safety check**  
Combines patient profile, Neo4j interaction and side effect queries, and Qdrant FAERS similarity search into a single structured report with source links.

**Polypharmacy-aware interaction detection**  
Cypher traversal checks the proposed drug against all active medications simultaneously, detects full interaction clusters, and returns interaction paths, not just binary flags.

**Semantic adverse event retrieval**  
Patient summary is serialized and embedded with BioLORD-2023. Qdrant returns the top-k most similar real-world FAERS cases with optional filters on outcome, patient sex, and serious flag. `analyze_adverse_event_aspects()` then summarizes results by severity distribution, organ-system involvement, and top reaction terms.

**Drug alternatives ranking**  
Candidates are scored by BioLORD cosine similarity against the proposed drug (threshold >= 0.40), then re-ranked using aggregated FAERS outcome data from `get_drug_faers_summary()` to prefer safer options when similarity scores are close.

**Reproducible audit trail**  
Every run persisted to MongoDB includes `patient_id`, `patient_name`, `proposed_drug`, `current_meds`, interaction results, embedding model version, and data version. Searchable by patient name for full traceability.

---

## Data Sources

| Source | What It Provides |
|--------|-----------------|
| [Synthea](https://synthetichealth.github.io/synthea/) | Realistic synthetic EHR data: 16 CSV files covering patients, encounters, medications, conditions, allergies, observations, procedures, immunizations |
| [RxNav (NIH)](https://rxnav.nlm.nih.gov/) | Drug interaction data for Neo4j `INTERACTS_WITH` edges |
| [SIDER](http://sideeffects.embl.de/) | Drug side effect frequency data for Neo4j `HAS_SIDE_EFFECT` edges |
| [openFDA FAERS](https://open.fda.gov/data/faers/) | Real-world adverse event reports for MongoDB and Qdrant |
| [DrugBank](https://go.drugbank.com/) | ATC level-4 approved drug alternatives for safer replacement ranking |

---

## Core Query Interface

**PostgreSQL — patient context**
```python
from db.pg_queries import get_patient_profile, get_active_medications, get_patient_timeline

profile  = get_patient_profile(patient_id="<uuid>")     # demographics, active meds, conditions, allergies, recent obs
meds     = get_active_medications(patient_id="<uuid>")   # WHERE stop_ts IS NULL
timeline = get_patient_timeline(patient_id="<uuid>", event_types=["medication", "condition"])
```

**Neo4j — drug safety graph**
```python
from db.neo4j_queries import check_interactions, get_side_effects, find_interaction_path

interactions = check_interactions(["Aspirin", "Metformin"], "Warfarin")  # severity + mechanism per edge
effects      = get_side_effects("Warfarin")                              # side effect name + frequency
path         = find_interaction_path("Warfarin", "Ibuprofen")            # shortest Cypher path
```

**Qdrant — semantic adverse event search**
```python
from db.qdrant_queries import find_similar_adverse_events, compute_drug_similarity

results    = find_similar_adverse_events(patient_summary="...", drug_name="Warfarin", top_k=10)
similarity = compute_drug_similarity("Warfarin", "Apixaban")  # BioLORD-2023 cosine similarity
```

**MongoDB — evidence + audit**
```python
from db.mongo_queries import log_safety_check, get_safety_check, get_faers_reports_by_ids

run_id  = log_safety_check({"patient_id": "...", "proposed_drug": "Warfarin", "interactions": [...]})
record  = get_safety_check(run_id)
reports = get_faers_reports_by_ids(["12345", "67890"], raw=True)
```

---

## Streamlit Demo

A five-tab hospital-themed UI covering all four databases:

```bash
export PG_URL="postgresql://postgres:<password>@localhost:5432/drug_safety"
export NEO4J_URI="neo4j://127.0.0.1:7687"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="<password>"
streamlit run app/demo.py
```

Tabs: **Full Safety Check** · **Patient Data** · **Drug Knowledge** · **FAERS + Alternatives** · **Evidence & Audit**

---

## Setup

```bash
# 1. Environment
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# key deps: psycopg2-binary, neo4j, pymongo, qdrant-client, sentence-transformers, scikit-learn, streamlit

# 2. Load data (requires running PostgreSQL, Neo4j, MongoDB, Qdrant instances)
python etl/load_synthea_to_pg.py                                         # 16 Synthea CSVs via psycopg2.execute_values
python etl/load_sider_to_neo4j.py --file /path/to/meddra_all_se.tsv     # SideEffect nodes + HAS_SIDE_EFFECT edges
python etl/load_faers_to_mongo.py --limit 500                            # raw + normalized FAERS collections
python -m etl.load_faers_to_qdrant --limit 5000                          # BioLORD embeddings → adverse_events collection
```

Default connection strings:

```
PostgreSQL:  postgresql://postgres:postgres@localhost:5432/drug_safety
Neo4j:       bolt://127.0.0.1:7687
MongoDB:     mongodb://localhost:27017
Qdrant:      http://localhost:6333
```

Override via: `PG_URL`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `MONGO_URI`, `MONGO_DB`, `QDRANT_URL`

---

## Repository Structure

```
drug_safety_and_recommendation/
  db/
    pg_schema.sql            # PostgreSQL DDL: star schema around patients
    pg_queries.py            # get_patient_profile(), get_active_medications(), get_patient_timeline()
    neo4j_queries.py         # check_interactions(), get_side_effects(), find_interaction_path()
    mongo_queries.py         # get_faers_reports_by_ids(), log_safety_check(), search_by_patient()
    qdrant_queries.py        # find_similar_adverse_events(), compute_drug_similarity(), get_drug_faers_summary()
  etl/
    load_synthea_to_pg.py    # Synthea CSV → PostgreSQL (bulk insert, 16 files)
    load_sider_to_neo4j.py   # SIDER TSV → Neo4j (SideEffect nodes + HAS_SIDE_EFFECT edges)
    load_faers_to_mongo.py   # openFDA FAERS API → MongoDB (raw + normalized)
    load_faers_to_qdrant.py  # FAERS → BioLORD embeddings → Qdrant adverse_events collection
    drugbank_alternatives.py # DrugBank ATC level-4 approved alternatives cache
    openfda_alternatives.py  # NDC/event-based fallback alternatives
  app/
    demo.py                  # Streamlit demo (five tabs)
  docs/
    database_diagrams.md     # Mermaid ER + graph diagrams for all four databases
```

---

## Tech Stack

`Python 3.10+` · `PostgreSQL` · `Neo4j + Cypher` · `Qdrant` · `MongoDB` · `BioLORD-2023` · `sentence-transformers` · `scikit-learn` · `Streamlit` · `psycopg2` · `openFDA API` · `Synthea` · `RxNav` · `SIDER` · `DrugBank`
