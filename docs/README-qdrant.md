# Part 4 — Qdrant Vector Database (Adverse Events & Similarity Search)

Qdrant stores **768-dimensional dense embeddings** of real-world adverse event reports from the FDA and synthetic patient profiles, enabling **semantic similarity search** to surface clinically relevant safety signals that keyword-based queries would miss.

---

## What This Component Does

When a clinician proposes a new medication, the Qdrant layer answers:

> *"Have patients similar to this one experienced adverse reactions with this drug — and how severe were they?"*

It does this by embedding a free-text patient summary into the same vector space as thousands of real FDA adverse event reports, then retrieving the nearest neighbors filtered by drug name, severity, or outcome.

---

## Architecture & Connections to Other Components

```text
                    ┌──────────────────────┐
                    │   openFDA FAERS API   │
                    │  (adverse event JSON) │
                    └──────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │ etl/load_faers_to_     │
                  │       qdrant.py        │
                  │                        │
                  │  1. Fetch/paginate API  │
                  │  2. Parse & filter      │
                  │  3. Serialize to text   │
                  │  4. Embed (BioLORD)     │
                  │  5. Upsert to Qdrant    │
                  └────────────┬───────────┘
                               │
                               ▼
                  ┌────────────────────────┐
                  │        Qdrant          │
                  │                        │
                  │  adverse_events        │
                  │    vec(768) + payload   │
                  │                        │
                  │  patient_profiles      │
                  │    vec(768) + payload   │
                  └─────┬──────────┬───────┘
                        │          │
              ┌─────────┘          └──────────┐
              ▼                               ▼
  ┌───────────────────────┐     ┌──────────────────────────┐
  │  db/qdrant_queries.py │     │ analysis/embedding_      │
  │                       │     │        analysis.py       │
  │  Public API consumed  │     │                          │
  │  by Part 5 (app) and  │     │  t-SNE, heatmaps,       │
  │  demo/test scripts    │     │  curse-of-dim, aspect    │
  └───────────┬───────────┘     └──────────────────────────┘
              │
    ┌─────────┼─────────────────┐
    ▼         ▼                 ▼
┌────────┐ ┌──────────────┐ ┌──────────────────────────────┐
│  Part  │ │ demo_qdrant  │ │  test_qdrant_queries.py      │
│  5 App │ │     .py      │ │  (sanity checks)             │
└────────┘ └──────────────┘ └──────────────────────────────┘
```

### How Qdrant connects to the other databases

| Connection | Direction | What happens |
|---|---|---|
| **PostgreSQL → Qdrant** | `pg_queries.get_patient_profile()` feeds `qdrant_queries.load_patient_profiles()` | Patient demographics, conditions, and medications from Synthea are serialized to text, embedded with BioLORD, and stored in the `patient_profiles` collection for similarity search. |
| **Qdrant → Part 5 (App)** | `drug_safety_check.py` calls `find_similar_adverse_events()` | The application layer passes a patient summary + proposed drug, receives ranked adverse event matches with similarity scores. |
| **Qdrant ↔ Neo4j** | Indirect (via Part 5) | Drug names returned by Qdrant results can be cross-checked against Neo4j's interaction graph for a comprehensive safety picture. |

---

## Data Source: openFDA FAERS

The [openFDA FAERS API](https://open.fda.gov/apis/drug/event.json) provides real-world adverse event reports submitted to the FDA. Each report contains patient demographics, drugs taken, adverse reactions experienced, and clinical outcomes.

```bash
# Public API — no key required for <1000 requests/day
curl "https://api.fda.gov/drug/event.json?limit=10"

# Higher throughput with a free API key
curl "https://api.fda.gov/drug/event.json?api_key=$OPENFDA_API_KEY&limit=100"
```

**Why FAERS?** Unlike structured drug interaction databases (handled by Neo4j), FAERS captures the messy reality of clinical practice — polypharmacy, off-label use, variable patient demographics. Vector similarity search is the right tool to query this unstructured, high-dimensional data.

---

## Embedding Model: BioLORD-2023

| Property | Value |
|---|---|
| Model | `FremyCompany/BioLORD-2023` |
| Dimensions | **768** |
| Type | Sentence-transformer (biomedical domain) |
| Distance metric | Cosine similarity |

**Why BioLORD over general-purpose models?** BioLORD was trained on biomedical literature and clinical ontologies, so it understands that "aspirin" and "ibuprofen" are semantically close (both NSAIDs) while "aspirin" and "metformin" are distant (painkiller vs. diabetes drug). A general-purpose model like `all-MiniLM-L6-v2` would miss these clinical relationships.

**Cosine similarity** measures the angle between two vectors:

```
cos θ = ⟨x, y⟩ / (‖x‖ · ‖y‖)
```

For normalized embeddings (which BioLORD produces), cosine similarity equals the dot product and is bounded in \[0, 1\].

---

## Qdrant Collections

### `adverse_events`

Stores embedded FAERS reports. Each point is one adverse event report.

| Field | Type | Description |
|---|---|---|
| **vector** | `float[768]` | BioLORD embedding of the serialized report text |
| `drug` | keyword (indexed) | Primary drug name (lowercase, generic) |
| `all_drugs` | keyword[] | All drugs the patient was taking |
| `reactions` | keyword[] | MedDRA adverse reaction terms |
| `patient_age` | integer | Patient age in years |
| `patient_sex` | keyword (indexed) | `"male"` or `"female"` |
| `serious` | bool (indexed) | Whether the event was classified as serious |
| `outcome` | keyword (indexed) | `"death"`, `"hospitalization"`, `"life-threatening"`, `"disability"`, `"non-serious"` |
| `report_id` | keyword | FDA safety report ID |
| `raw_text` | text | The serialized text that was embedded |

**Payload indexes** are created on `drug`, `outcome`, `serious`, and `patient_sex` to speed up filtered queries via Qdrant's inverted index, narrowing the candidate set *before* the HNSW vector comparison.

### `patient_profiles`

Stores embedded patient profiles from Synthea (loaded via PostgreSQL).

| Field | Type | Description |
|---|---|---|
| **vector** | `float[768]` | BioLORD embedding of the patient summary |
| `patient_id` | keyword | Synthea patient UUID |
| `age` | integer | Patient age |
| `gender` | keyword | `"M"` or `"F"` |
| `conditions` | keyword[] | Active medical conditions |
| `medications` | keyword[] | Current medications |

### Text Serialization Format

Reports are converted to structured text before embedding, optimized for BioLORD's training distribution:

```text
Patient: 65 year old male. Medications: aspirin, metformin. 
Adverse reactions: gastrointestinal haemorrhage, nausea. Outcome: hospitalization.
```

---

## File Overview

```text
.
├── etl/
│   └── load_faers_to_qdrant.py    # ETL: openFDA → parse → embed → Qdrant
├── db/
│   └── qdrant_queries.py          # Query layer: similarity search + analysis
├── analysis/
│   └── embedding_analysis.py      # Visualizations: t-SNE, heatmaps, curse of dim
├── demo_qdrant.py                 # Interactive 4-part clinical demo
├── test_qdrant_queries.py         # Sanity checks against live Qdrant data
└── .env                           # OPENFDA_API_KEY, QDRANT_PATH
```

---

## ETL Pipeline (`etl/load_faers_to_qdrant.py`)

### Pipeline stages

```text
openFDA FAERS API
       │
       ▼
  1. FETCH ─────── Paginate API (50 reports/page, retry with backoff)
       │            Cache raw JSON to data/faers_raw.json
       ▼
  2. PARSE ─────── Extract: age, sex, drugs, reactions, outcome, seriousness
       │            Normalize age units (decades → years, months → years)
       │            Deduplicate drug/reaction lists
       │            Discard reports missing drugs or reactions
       ▼
  3. SERIALIZE ─── Convert each record to structured text for BioLORD
       │
       ▼
  4. EMBED ─────── Encode with BioLORD-2023 (batch_size=64, 768-dim vectors)
       │
       ▼
  5. UPSERT ────── Create collections + payload indexes
                   Batch upsert (200 points/batch) into Qdrant
```

### Usage

```bash
# Full pipeline: fetch from API + embed + load
python etl/load_faers_to_qdrant.py --limit 5000

# Use cached data (skip API calls)
python etl/load_faers_to_qdrant.py --use-cache

# Qdrant in Docker (default): ensure docker compose up -d, then run without QDRANT_PATH
python etl/load_faers_to_qdrant.py --limit 5000

# On-disk Qdrant (no Docker)
python etl/load_faers_to_qdrant.py --qdrant-path ./qdrant_local
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENFDA_API_KEY` | *(empty)* | Free API key for higher rate limits |
| `QDRANT_HOST` | `localhost` | Qdrant server host |
| `QDRANT_PORT` | `6333` | Qdrant server port |
| `QDRANT_PATH` | *(empty)* | Path for local disk-based Qdrant (no Docker) |

---

## Query Layer (`db/qdrant_queries.py`)

### Public API

#### `find_similar_adverse_events(patient_summary, drug_name, top_k=10)`

Core function — finds FAERS reports from patients similar to the query who experienced adverse events with the specified drug.

```python
from db.qdrant_queries import find_similar_adverse_events

results = find_similar_adverse_events(
    patient_summary="65 year old male with heart disease taking aspirin daily",
    drug_name="aspirin",
    top_k=5,
)
# Returns: [{patient_age, patient_sex, drugs, reactions, outcome, serious, similarity_score, raw_text}, ...]
```

**How it works:** Embeds the patient summary with BioLORD, applies a payload filter on `drug == drug_name`, then runs HNSW approximate nearest-neighbor search over the `adverse_events` collection.

#### `find_similar_adverse_events_multi_filter(patient_summary, drug_names=None, outcome=None, serious_only=False, sex=None, top_k=10)`

Advanced search with composable filters. Combines multiple payload conditions before HNSW traversal.

```python
results = find_similar_adverse_events_multi_filter(
    patient_summary="55 year old female diabetic with kidney issues",
    serious_only=True,
    sex="female",
    top_k=20,
)
```

#### `find_similar_patients(patient_summary, top_k=10)`

Searches the `patient_profiles` collection (Synthea patients) for clinically similar profiles.

```python
results = find_similar_patients(
    patient_summary="70 year old male with diabetes and hypertension",
    top_k=5,
)
# Returns: [{patient_id, conditions, medications, similarity_score}, ...]
```

#### `analyze_adverse_event_aspects(results)`

Aspect-based analysis inspired by BERT aspect-based sentiment. Classifies adverse event results by clinical aspects (severity, organ system) and returns distributions.

```python
from db.qdrant_queries import analyze_adverse_event_aspects

analysis = analyze_adverse_event_aspects(results)
# Returns:
# {
#   total_reports: 20,
#   severity_distribution: {"high": 8, "moderate": 5, ...},
#   organ_system_distribution: {"cardiovascular": 6, "gastrointestinal": 4, ...},
#   top_reactions: {"nausea": 7, "headache": 5, ...},
#   outcome_distribution: {"hospitalization": 10, "death": 3, ...},
# }
```

#### `compute_drug_similarity(drug1, drug2)`

Cosine similarity between two drug names in BioLORD embedding space.

```python
from db.qdrant_queries import compute_drug_similarity

score = compute_drug_similarity("aspirin", "ibuprofen")   # ~0.55 (same class)
score = compute_drug_similarity("aspirin", "metformin")    # ~0.05 (unrelated)
```

#### `compute_pairwise_drug_similarities(drug_names)`

Batch pairwise cosine similarities for a list of drugs.

#### `load_patient_profiles(profiles)`

Embeds and upserts Synthea patient profiles into Qdrant. Takes the output of `pg_queries.get_patient_profile()`.

---

## Demo (`demo_qdrant.py`)

An interactive terminal demo with four sections showcasing real clinical scenarios:

```bash
docker compose up -d    # if not already running
python demo_qdrant.py   # uses Docker at localhost:6333 by default
```

| Demo | What it shows |
|---|---|
| **Demo 1 — Semantic Patient Matching** | For 3 realistic patients (aspirin + stomach pain, ibuprofen in a diabetic, paracetamol + alcohol), finds the top FAERS matches by semantic similarity. |
| **Demo 2 — Drug Safety Signal Analysis** | Analyzes 20 serious adverse events for a high-risk patient, showing severity distribution, affected organ systems, and top reactions as bar charts. |
| **Demo 3 — BioLORD Drug Intelligence** | Shows BioLORD correctly groups same-class drugs (aspirin/ibuprofen → high similarity) and separates unrelated ones (aspirin/metformin → near zero). |
| **Demo 4 — Live Safety Check** | End-to-end: finds similar cases, checks drug similarity for current medications, and produces a risk summary with a recommendation. |

---

## Testing (`test_qdrant_queries.py`)

Sanity checks against live Qdrant data with 4 clinical test scenarios:

```bash
python test_qdrant_queries.py   # Docker default
```

| Test | What it validates |
|---|---|
| **Test 1** — Basic search | `find_similar_adverse_events()` returns results for warfarin, sertraline, metformin, aspirin queries |
| **Test 2** — Multi-filter | `find_similar_adverse_events_multi_filter()` with `serious_only=True, sex="male"` returns appropriately filtered results |
| **Test 3** — Aspect analysis | `analyze_adverse_event_aspects()` produces severity/organ system breakdowns |
| **Test 4** — Drug similarity | `compute_drug_similarity()` scores same-class pairs higher than unrelated pairs |
| **Scorecard** | Aggregates similarity scores and rates overall data quality (excellent / good / fair / weak) |

---

## Analysis & Visualizations (`analysis/embedding_analysis.py`)

Generates figures demonstrating DSC 202 course concepts applied to the FAERS data:

```bash
python analysis/embedding_analysis.py
# Outputs saved to analysis/figures/
```

| Analysis | Course concept | Output |
|---|---|---|
| **t-SNE of adverse events** | Word2Vec `visualize_word_vectors` | `tsne_adverse_events.png` — 768D → 2D, colored by outcome severity |
| **Drug similarity heatmap** | Word2Vec `cosine_similarity` | `drug_similarity_heatmap.png` — pairwise cosine sim for top 15 drugs |
| **Cosine vs. Euclidean** | Lecture slides 4–6 (distance metrics) | `distance_metrics_comparison.png` — verifies monotonic relationship |
| **Curse of dimensionality** | Lecture slides 9–11 | `curse_of_dimensionality.png` — σ/μ of pairwise distances vs. dimension |
| **Aspect-based classification** | `BERT_rev.py`, `aspect-based-sentiment.py` | `aspect_based_classification.png` — reports classified by organ system |
| **Reaction embeddings t-SNE** | Word2Vec, FastText word-level analysis | `reaction_embeddings_tsne.png` — clustering of top 40 reaction terms |

---

## DSC 202 Concepts Demonstrated

| Concept | Where it appears |
|---|---|
| **Cosine similarity** — `cos θ = ⟨x,y⟩ / (‖x‖·‖y‖)` | All similarity queries, drug pair comparisons, aspect classification |
| **HNSW index** — O(log n) approximate nearest neighbor | Qdrant's default index; used in every `query_points()` call |
| **Payload-filtered search** — narrow candidates before vector comparison | `FieldCondition` filters on drug, outcome, serious, sex |
| **Dense vector embeddings** — sentence-transformers | BioLORD-2023 encodes clinical text to 768-dim vectors |
| **Word2Vec / FastText analogy** — word similarity via vectors | `compute_drug_similarity()` mirrors `Word2Vec.py calculate_word_similarity()` |
| **Aspect-based analysis** — BERT aspect extraction | `analyze_adverse_event_aspects()` classifies by severity & organ system |
| **t-SNE visualization** — dimensionality reduction | Adverse event and reaction embedding plots |
| **Curse of dimensionality** — distance concentration in high-D | σ/μ analysis across projected dimensions |
| **Distance metrics** — cosine vs. Euclidean vs. Manhattan | Empirical comparison on FAERS embeddings |

---

## Quick Start

### 1. Install dependencies

```bash
pip install qdrant-client sentence-transformers python-dotenv requests numpy scikit-learn matplotlib
```

### 2. Start Qdrant (Docker)

```bash
docker compose up -d
```

### 3. Set up environment (optional)

```bash
# .env — optional
OPENFDA_API_KEY=your_key_here   # for higher FDA API rate limits
# Leave QDRANT_PATH unset to use Docker (localhost:6333)
```

### 4. Run the ETL

```bash
# Fetch 5000 FAERS reports, embed, and load into Qdrant (connects to Docker)
python etl/load_faers_to_qdrant.py --limit 5000
```

### 5. Verify the data

```bash
python test_qdrant_queries.py
```

### 6. Run the demo

```bash
python demo_qdrant.py
```

### 7. Generate analysis figures

```bash
python analysis/embedding_analysis.py
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Embedding model** | BioLORD-2023 (768-dim) | Biomedical domain model trained on clinical ontologies; understands drug class relationships without lookup tables |
| **Distance metric** | Cosine | Standard for sentence-transformer embeddings; magnitude-invariant; bounded \[0,1\] |
| **Indexing** | HNSW (Qdrant default) | O(log n) approximate nearest neighbor with tunable recall/speed |
| **Payload indexes** | `drug`, `outcome`, `serious`, `patient_sex` | Pre-filtering narrows HNSW search space; critical for performance |
| **Text serialization** | Structured template (`"Patient: X. Medications: Y. Adverse reactions: Z. Outcome: W."`) | Consistent format improves embedding quality for retrieval |
| **Local vs. remote** | Support both via `QDRANT_PATH` / `QDRANT_HOST` | Local disk mode for development/demo; remote server for production |
| **Batch size** | 64 (embedding), 200 (upsert) | Balance between throughput and memory usage |
