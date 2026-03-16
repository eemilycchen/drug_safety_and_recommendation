# Part 4 — Qdrant + openFDA FAERS: What Was Done & How to Run

## What Was Built

Three modules that form the complete Qdrant pipeline for the drug safety project, incorporating DSC 202 course concepts:

```
etl/load_faers_to_qdrant.py      ← ETL: fetches, parses, embeds, loads + payload indexes
db/qdrant_queries.py              ← Query API: similarity search, multi-filter, aspect analysis
analysis/embedding_analysis.py    ← Course concept demos: t-SNE, distance metrics, aspects
```

---

## Data Source: openFDA FAERS

The [FDA Adverse Event Reporting System (FAERS)](https://open.fda.gov/apis/drug/event/) contains real-world reports of adverse drug reactions submitted by healthcare professionals and consumers. Each report includes:

- Patient demographics (age, sex)
- Drugs the patient was taking
- Adverse reactions experienced
- Outcome severity (hospitalization, death, disability, etc.)

We fetch these reports via the openFDA REST API and use them to answer: *"Have patients similar to this one had adverse events with this drug?"*

---

## Pipeline Overview

```
openFDA FAERS API
       │
       ▼
  1. FETCH ──────────── paginate API, collect 5,000 JSON reports
       │
       ▼
  2. PARSE ──────────── extract age, sex, drugs, reactions, outcome
       │                 discard reports missing drugs or reactions
       │                 normalize age units (decades → years, etc.)
       ▼
  3. SERIALIZE ──────── convert each report into a text string:
       │                 "Patient: 65 year old male. Medications: warfarin,
       │                  metformin. Adverse reactions: nausea, dizziness.
       │                  Outcome: hospitalization."
       ▼
  4. EMBED ──────────── encode text with all-MiniLM-L6-v2 (384-dim vectors)
       │
       ▼
  5. LOAD ───────────── upsert vectors + metadata into Qdrant collection
```

---

## How to Set Up

### 1. Install dependencies

```bash
pip3 install qdrant-client sentence-transformers requests python-dotenv
```

### 2. Create the `.env` file

In the project root (`dsc 202/`), create a file called `.env`:

```
OPENFDA_API_KEY=your_api_key_here
QDRANT_PATH=./data/qdrant_storage
```

- `OPENFDA_API_KEY` — your openFDA API key (get one free at https://open.fda.gov/apis/authentication/). Without it you're limited to 240 requests/min; with it you get 120,000/day.
- `QDRANT_PATH` — path for local disk-based Qdrant storage. This means **no Qdrant server is needed** — everything runs locally on disk. Remove this line if you want to connect to a running Qdrant server instead.

### 3. Make sure you're in the project root

All commands below assume you're in the `dsc 202/` directory:

```bash
cd "/Users/sanjana/Downloads/dsc 202"
```

---

## How to Run the ETL

### Full run (fetch from API + embed + load)

```bash
python3 etl/load_faers_to_qdrant.py --limit 5000 --qdrant-path ./data/qdrant_storage
```

This will:
1. Fetch 5,000 adverse event reports from the openFDA API (~2 min)
2. Parse and filter them (removes reports missing drugs/reactions)
3. Cache the raw JSON to `data/faers_raw.json` so you don't re-fetch next time
4. Load the `all-MiniLM-L6-v2` embedding model (~8 sec first time, downloads ~80MB)
5. Embed all 5,000 reports (~3 sec on Apple Silicon with MPS)
6. Upsert into the `adverse_events` Qdrant collection

### Re-run using cached data (no API calls)

```bash
python3 etl/load_faers_to_qdrant.py --use-cache --qdrant-path ./data/qdrant_storage
```

This skips the API fetch and re-uses the JSON saved in `data/faers_raw.json`. Useful when you want to re-embed after changing the serialization format.

### Smaller test run

```bash
python3 etl/load_faers_to_qdrant.py --limit 500 --qdrant-path ./data/qdrant_storage
```

### All CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--limit N` | 5000 | Max number of FAERS reports to fetch from the API |
| `--use-cache` | off | Skip API fetch, use `data/faers_raw.json` instead |
| `--qdrant-path PATH` | from `.env` | Local disk path for Qdrant storage (no server needed) |
| `--qdrant-host HOST` | localhost | Qdrant server host (ignored if `--qdrant-path` is set) |
| `--qdrant-port PORT` | 6333 | Qdrant server port (ignored if `--qdrant-path` is set) |

---

## How to Run Queries

### From Python

```python
from db.qdrant_queries import find_similar_adverse_events, find_similar_patients

# Find adverse events for similar patients who took warfarin
results = find_similar_adverse_events(
    patient_summary="65 year old male, diabetes mellitus type 2, hypertension, taking metformin and lisinopril",
    drug_name="warfarin",
    top_k=5,
)

for r in results:
    print(f"Score: {r['similarity_score']}")
    print(f"  Age: {r['patient_age']}, Sex: {r['patient_sex']}")
    print(f"  Reactions: {', '.join(r['reactions'])}")
    print(f"  Outcome: {r['outcome']}")
    print()
```

### Quick test from the command line

```bash
QDRANT_PATH=./data/qdrant_storage python3 -c "
from db.qdrant_queries import find_similar_adverse_events
results = find_similar_adverse_events(
    '65 year old male, diabetes, hypertension',
    'metformin',
    top_k=3,
)
for r in results:
    print(f'{r[\"similarity_score\"]:.4f} | age={r[\"patient_age\"]}, {r[\"patient_sex\"]} | {r[\"reactions\"][:3]} | {r[\"outcome\"]}')
"
```

---

## Query Functions Reference

### `find_similar_adverse_events(patient_summary, drug_name, top_k=10)`

Searches the `adverse_events` collection for FAERS reports from patients similar to the one described, filtered to only reports involving the specified drug.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `patient_summary` | str | Free-text description of the patient (age, sex, conditions, current medications) |
| `drug_name` | str | Drug to filter adverse events by (matched against the primary drug in each report) |
| `top_k` | int | Number of results to return (default: 10) |

**Returns:** `list[dict]` — each dict contains:

```python
{
    "patient_age": 65,           # int or None
    "patient_sex": "male",       # "male", "female", or None
    "reactions": ["nausea", "dizziness"],  # list of adverse reactions
    "outcome": "hospitalization", # "death", "hospitalization", "life-threatening", "disability", or "non-serious"
    "similarity_score": 0.5802,  # cosine similarity (0 to 1, higher = more similar)
    "raw_text": "Patient: 65 year old male. Medications: ..."  # the text that was embedded
}
```

### `find_similar_adverse_events_multi_filter(patient_summary, drug_names, outcome, serious_only, sex, top_k=10)`

Advanced filtered search combining multiple payload conditions. Demonstrates Qdrant's composable filter model (ref: lecture on Payload in Qdrant).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `patient_summary` | str | Free-text patient description |
| `drug_names` | list[str] or None | Filter to reports involving any of these drugs (MatchAny) |
| `outcome` | str or None | Filter by outcome type (e.g. "hospitalization", "death") |
| `serious_only` | bool | If True, only return serious adverse events |
| `sex` | str or None | Filter by patient sex ("male" or "female") |
| `top_k` | int | Number of results to return (default: 10) |

### `analyze_adverse_event_aspects(results)`

Perform aspect-based analysis on search results (ref: `BERT_rev.py`, `aspect-based-sentiment.py`). Classifies adverse events by severity and organ system aspects.

**Parameters:** `results` — output from `find_similar_adverse_events()`.

**Returns:** Dict with `severity_distribution`, `organ_system_distribution`, `top_reactions`, `outcome_distribution`.

### `compute_drug_similarity(drug1, drug2)`

Compute cosine similarity between two drug names in embedding space (ref: `Word2Vec.py` `calculate_word_similarity()`).

### `compute_pairwise_drug_similarities(drug_names)`

Compute pairwise cosine similarities for a set of drug names. Returns `{(drug_a, drug_b): similarity_score}`.

### `find_similar_patients(patient_summary, top_k=10)`

Searches the `patient_profiles` collection for Synthea patients similar to the one described. No drug filter — pure demographic/clinical similarity.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `patient_summary` | str | Free-text description of the patient |
| `top_k` | int | Number of results to return (default: 10) |

**Returns:** `list[dict]` — each dict contains:

```python
{
    "patient_id": "abc-123-def",
    "conditions": ["diabetes mellitus type 2", "hypertension"],
    "medications": ["metformin", "lisinopril"],
    "similarity_score": 0.92
}
```

**Note:** This collection is empty until Part 1 (PostgreSQL/Synthea) provides patient profiles. The `load_patient_profiles()` utility function in the same file handles loading them.

### `load_patient_profiles(profiles)`

Utility function to embed and load Synthea patient profiles into Qdrant. Called once after Part 1 provides the data.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `profiles` | list[dict] | List of patient profile dicts from `pg_queries.get_patient_profile()`. Each must have: `patient_id`, `age`, `gender`, `conditions` (list), `medications` (list) |

**Returns:** `int` — number of profiles loaded.

**Example:**

```python
from db.qdrant_queries import load_patient_profiles

profiles = [
    {
        "patient_id": "abc-123",
        "age": 65,
        "gender": "male",
        "conditions": ["diabetes mellitus type 2", "hypertension"],
        "medications": ["metformin", "lisinopril"],
    },
    # ... more patients from Part 1's get_patient_profile()
]

count = load_patient_profiles(profiles)
print(f"Loaded {count} patient profiles into Qdrant")
```

---

## Qdrant Collections Schema

### `adverse_events` (populated by ETL)

| Field | Type | Description |
|-------|------|-------------|
| **vector** | float[384] | Cosine-distance embedding from `all-MiniLM-L6-v2` |
| `drug` | string | Primary drug name (lowercase) — used for filtering |
| `all_drugs` | list[string] | All drugs the patient was taking |
| `reactions` | list[string] | Adverse reactions reported |
| `patient_age` | int or null | Patient age in years |
| `patient_sex` | string or null | "male" or "female" |
| `serious` | bool | Whether the event was classified as serious |
| `outcome` | string | "death", "hospitalization", "life-threatening", "disability", or "non-serious" |
| `report_id` | string | openFDA safety report ID |
| `raw_text` | string | The serialized text that was embedded |

### `patient_profiles` (populated by `load_patient_profiles()`)

| Field | Type | Description |
|-------|------|-------------|
| **vector** | float[384] | Cosine-distance embedding from `all-MiniLM-L6-v2` |
| `patient_id` | string | Synthea patient UUID |
| `age` | int | Patient age |
| `gender` | string | Patient gender |
| `conditions` | list[string] | Active conditions |
| `medications` | list[string] | Current medications |

---

## Design Decisions & DSC 202 Course Concept Mapping

### Embedding Model: `all-MiniLM-L6-v2` (ref: Lecture slides 13-14, 20)

- **384 dimensions**, cosine distance
- Fast (~3 sec for 5,000 texts on MPS), small model (~80MB)
- General-purpose sentence embeddings — good enough for structured clinical text
- Alternative considered: `all-mpnet-base-v2` (768-dim, higher quality but 2x slower and 2x storage)

**Why sentence-transformers over Word2Vec/FastText?**
- **Word2Vec** (ref: `Word2Vec.py`, lecture slide 16): Produces one fixed vector per word. Cannot capture the context of a full adverse event report (e.g., "nausea" means different things when caused by chemotherapy vs pregnancy).
- **FastText** (ref: `FastText.py`, lecture slide 19): Handles subwords/OOV medical terms via character n-grams, but still word-level — no sentence-level semantics.
- **BERT** (ref: `BERT_rev.py`, lecture slide 20): Context-aware via bidirectional attention, but requires fine-tuning for good sentence embeddings.
- **Sentence-Transformers**: Built on BERT architecture with contrastive fine-tuning for similarity tasks. Gets the context-awareness of BERT with the ease of use of Word2Vec. This is the approach used by the LinkedIn Unleashed example project (`paraphrase-MiniLM-L6-v2`).

### Distance Metric: Cosine Similarity (ref: Lecture slide 5)

```
cos θ = ⟨x,y⟩ / (||x|| · ||y||)
```

Cosine similarity measures the angle between two vectors, ignoring magnitude. This is appropriate because:
- Sentence-transformer embeddings are L2-normalized, so cosine = dot product
- Longer/shorter reports should not be penalized (Euclidean would penalize magnitude differences)
- Bounded in [-1, 1] for easy interpretation as a similarity score

The analysis script (`analysis/embedding_analysis.py`) demonstrates the monotonic relationship between cosine similarity and Euclidean distance for our normalized embeddings (see `figures/distance_metrics_comparison.png`).

### HNSW Index for ANN Search (ref: Lecture slides 31-32)

Qdrant uses Hierarchical Navigable Small World (HNSW) graphs for approximate nearest neighbor search:
- Multi-layer graph structure: higher layers have fewer connections (highways), lower layers are more dense
- Search starts at the top layer and descends to find the nearest neighbors
- O(log n) query time vs O(n) for brute-force
- Tunable recall/speed tradeoff via `ef` (exploration factor) parameter

### Payload Indexes for Filtered Search (ref: Lecture slide 33)

Created explicit payload indexes on frequently filtered fields:

| Field | Type | Purpose |
|-------|------|---------|
| `drug` | KEYWORD | Filter adverse events by drug name |
| `outcome` | KEYWORD | Filter by outcome severity |
| `serious` | BOOL | Filter serious vs non-serious events |
| `patient_sex` | KEYWORD | Filter by patient sex |

These indexes allow Qdrant's query planner to narrow the candidate set before running the vector comparison, which is critical given the curse of dimensionality (lecture slides 9-11).

### Text Serialization Format (ref: T5.py text preprocessing)

Structured format was chosen over plain concatenation:

```
Patient: 65 year old male. Medications: warfarin, metformin.
Adverse reactions: nausea, dizziness. Outcome: hospitalization.
```

**Why:** Analogous to T5.py's `preprocess_financial_text()` which standardizes financial text before analysis. The field labels ("Patient:", "Medications:", "Adverse reactions:") help the embedding model understand the role of each token — similar to how T5 uses task prefixes.

### Aspect-Based Analysis (ref: BERT_rev.py, aspect-based-sentiment.py)

Instead of simple sentiment analysis, we perform **aspect-based classification** of adverse events by clinical aspects:
- **Severity aspect**: death, life-threatening, hospitalization, disability, non-serious
- **Organ system aspect**: cardiovascular, gastrointestinal, neurological, dermatological, hepatic, renal, respiratory

This mirrors the approach in `BERT_rev.py` where financial statements are analyzed by aspects (Revenue, Expenses, Assets, Liabilities, Risk_Factors) using cosine similarity between text embeddings and aspect-defining anchor texts.

### Curse of Dimensionality Considerations (ref: Lecture slides 9-11)

The analysis script demonstrates that pairwise distance concentration increases with dimensionality:
- At d=2: σ/μ = 0.55 (good distance discrimination)
- At d=384: σ/μ = 0.17 (distances concentrate → harder to distinguish neighbors from non-neighbors)

This justifies our use of:
1. **Payload pre-filtering** to reduce the effective search space before vector comparison
2. **HNSW index** which handles high-dimensional spaces better than tree-based methods
3. **Cosine distance** which is more robust than Euclidean in high dimensions

### Storage Mode

Local disk-based Qdrant (no server). Set `QDRANT_PATH=./data/qdrant_storage` in `.env`. This avoids the need to run a separate Qdrant server process. For production or larger datasets, switch to a running Qdrant server by removing `QDRANT_PATH` and setting `QDRANT_HOST`/`QDRANT_PORT`.

---

## Analysis & Visualizations

Run the analysis script to generate course-concept demonstration figures:

```bash
python3 analysis/embedding_analysis.py
```

### Generated Figures

| Figure | Course Concept | Description |
|--------|---------------|-------------|
| `analysis/figures/tsne_adverse_events.png` | Word2Vec.py `visualize_word_vectors()`, Lecture slide 13 | t-SNE reduction of 1,500 FAERS report embeddings (384D → 2D), colored by outcome severity |
| `analysis/figures/drug_similarity_heatmap.png` | Word2Vec.py `calculate_word_similarity()`, Lecture slide 5 | Pairwise cosine similarity heatmap of top-15 drug embeddings (e.g., atorvastatin ↔ simvastatin = 0.695) |
| `analysis/figures/distance_metrics_comparison.png` | Lecture slides 4-6 (Distance Metrics) | Cosine similarity distribution + cosine vs Euclidean scatter showing monotonic relationship |
| `analysis/figures/curse_of_dimensionality.png` | Lecture slides 9-11 (Curse of Dimensionality) | σ/μ of pairwise distances decreasing from 0.55 (d=2) to 0.17 (d=384) |
| `analysis/figures/aspect_based_classification.png` | BERT_rev.py + aspect-based-sentiment.py | Aspect-based classification of FAERS reports by organ system using cosine similarity to clinical anchor texts |
| `analysis/figures/reaction_embeddings_tsne.png` | Word2Vec.py + FastText.py | t-SNE of top-40 adverse reaction term embeddings showing semantic clustering |

---

## Test Results

Tested with 5,000 FAERS reports. Sample queries:

| Query Patient | Drug | Top Result | Score | Clinically Sensible? |
|---------------|------|------------|-------|---------------------|
| 65yo male, diabetic, hypertension | metformin | 74yo male, reactions: erysipelas, sepsis | 0.58 | Yes — serious outcome in similar demographic |
| 72yo female, arthritis, osteoporosis | ibuprofen | Female, reactions: GI disorder, dyspepsia, anaemia | 0.41 | Yes — classic NSAID side effects |
| 50yo male, atrial fibrillation, heart failure | aspirin | 60yo female, reactions: arteritis | 0.48 | Yes — vascular adverse event |

Query latency: **~58ms** per search (after model warm-up).

---

## Files Generated by the Pipeline

| File | Size | Description |
|------|------|-------------|
| `data/faers_raw.json` | ~25 MB | Cached raw FAERS API responses (5,000 reports) |
| `data/qdrant_storage/` | ~15 MB | Qdrant local database with embedded vectors |

Both are in `.gitignore` and not committed to the repository.

---

## Dependencies

```
qdrant-client
sentence-transformers
requests
python-dotenv
```

All installed via: `pip3 install qdrant-client sentence-transformers requests python-dotenv`

This also installs `torch` (~80MB), `transformers`, and `huggingface-hub` as transitive dependencies of `sentence-transformers`.

---

## What Still Needs the Other Parts

| Dependency | From | Status |
|------------|------|--------|
| Synthea patient profiles for `patient_profiles` collection | Part 1 (`pg_queries.get_patient_profile()`) | Waiting — use `load_patient_profiles()` once available |
| Drug name consistency (Qdrant filter must match Neo4j drug names) | Parts 2 & 3 | Coordinate — currently using lowercase generic names |
| Integration into `drug_safety_check.py` | Part 5 | Ready — functions match the agreed interface contract |
