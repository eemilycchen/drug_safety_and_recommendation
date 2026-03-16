# Part 4 — Qdrant + openFDA FAERS: Task List

## Files to Deliver

- [ ] `etl/load_faers_to_qdrant.py`
- [ ] `db/qdrant_queries.py`

---

## Phase 1: Understand the Data (Week 1)

### 1.1 Explore the openFDA FAERS API
- [ ] Read the API docs: https://open.fda.gov/apis/drug/event/
- [ ] Make test calls and study the JSON response structure
  ```
  curl "https://api.fda.gov/drug/event.json?limit=1" | python -m json.tool
  ```
- [ ] Identify the fields you'll use from each report:
  - `patient.patientonsetage` — patient age
  - `patient.patientsex` — patient sex (1=male, 2=female)
  - `patient.drug[]` — list of drugs the patient was taking
  - `patient.drug[].openfda.generic_name` — standardized drug name
  - `patient.drug[].drugindication` — why the drug was prescribed
  - `patient.reaction[]` — adverse reactions experienced
  - `patient.reaction[].reactionmeddrapt` — MedDRA preferred term
  - `serious` — whether the event was serious (1=yes, 2=no)
  - `seriousnessdeath`, `seriousnesshospitalization`, etc. — outcome flags
- [ ] Note API rate limits: 240 requests/min without key, 120,000/day with key
- [ ] Store your API key in a `.env` file (never commit this to git):
  ```
  OPENFDA_API_KEY=your_key_here
  ```
- [ ] Add `.env` to `.gitignore`
- [ ] Use the key in API calls by adding `api_key=` before other parameters:
  ```
  https://api.fda.gov/drug/event.json?api_key=${OPENFDA_API_KEY}&search=...
  ```

### 1.2 Explore Qdrant
- [ ] Install and run Qdrant locally (pip install qdrant-client, or run the server)
- [ ] Create a test collection, insert a few vectors, run a search — get familiar with the API
- [ ] Read Qdrant docs on filtering: https://qdrant.tech/documentation/concepts/filtering/

### 1.3 Explore Sentence-Transformers
- [ ] Install sentence-transformers: `pip install sentence-transformers`
- [ ] Load `all-MiniLM-L6-v2` and embed a few test sentences
- [ ] Try `all-mpnet-base-v2` (768-dim) — compare speed and quality
- [ ] Check if any clinical/biomedical models exist (e.g., `pritamdeka/BioBERT-mnli-snli-scinli-scitail-mednli-stsb`)
- [ ] Pick a model and document why

---

## Phase 2: Build the ETL Pipeline (Week 2)

### 2.1 Fetch FAERS Data
- [ ] Write the openFDA fetcher in `etl/load_faers_to_qdrant.py`
- [ ] Paginate using `skip` parameter to collect ~5,000–10,000 reports
  ```
  /drug/event.json?limit=100&skip=0
  /drug/event.json?limit=100&skip=100
  ...
  ```
- [ ] Handle API errors (rate limits, timeouts, empty responses)
- [ ] Filter out reports with missing critical fields (no age, no drug name, no reaction)
- [ ] Save raw JSON to `data/faers_raw.json` as a cache so you don't re-fetch every run
- [ ] Log stats: how many reports fetched, how many passed filtering, how many discarded and why

### 2.2 Design Text Serialization
- [ ] **This is the most important design decision.** The embedding quality depends entirely on what text you feed the model.
- [ ] Try multiple formats and compare — examples:

  **Format A (simple):**
  ```
  65 year old male, taking warfarin metformin lisinopril, experienced nausea dizziness, serious
  ```

  **Format B (structured):**
  ```
  Patient: 65 year old male. Medications: warfarin, metformin, lisinopril. 
  Adverse reactions: nausea, dizziness. Outcome: hospitalization.
  ```

  **Format C (clinical):**
  ```
  Age 65, male, concomitant drugs: warfarin (anticoagulant), metformin (diabetes), 
  lisinopril (hypertension). Reported adverse events: nausea, dizziness. 
  Serious event: yes, resulted in hospitalization.
  ```
- [ ] Embed 50 reports in each format, run the same 5 test queries, compare which format returns more clinically sensible results
- [ ] Document your choice and reasoning

### 2.3 Embed and Load into Qdrant
- [ ] Serialize each FAERS report into chosen text format
- [ ] Batch-embed (sentence-transformers supports batch encoding for speed)
- [ ] Create `adverse_events` collection in Qdrant:
  - Vector size: 384 (or 768 if using mpnet)
  - Distance: Cosine
- [ ] Upsert vectors with payload fields:
  - `drug` (string) — primary drug name
  - `reactions` (list[string]) — adverse reactions
  - `patient_age` (int)
  - `patient_sex` (string)
  - `serious` (bool)
  - `outcome` (string) — e.g., "hospitalization", "death", "recovered"
  - `report_id` (string) — openFDA safety report ID
  - `raw_text` (string) — the serialized text used for embedding
- [ ] Verify: run a test search and check results make sense

### 2.4 Build Patient Profile Collection
- [ ] Coordinate with Part 1 to get `get_patient_profile()` output format
- [ ] For each Synthea patient, serialize their profile into text:
  ```
  72 year old female. Conditions: diabetes mellitus type 2, hypertension, 
  chronic kidney disease. Medications: metformin, lisinopril, amlodipine.
  ```
- [ ] Embed and upsert into `patient_profiles` collection with payload:
  - `patient_id` (string)
  - `age` (int)
  - `gender` (string)
  - `conditions` (list[string])
  - `medications` (list[string])

---

## Phase 3: Build the Query Functions (Week 3)

### 3.1 Implement `find_similar_adverse_events()`
- [ ] Write the function in `db/qdrant_queries.py`
- [ ] Accept `patient_summary` (text string) and `drug_name` (string)
- [ ] Embed the patient summary at query time
- [ ] Use Qdrant payload filter: `{"must": [{"key": "drug", "match": {"value": drug_name}}]}`
- [ ] Return top_k results with similarity scores
- [ ] Map results into the agreed output format:
  ```python
  {
      "patient_age": 65,
      "patient_sex": "male",
      "reactions": ["nausea", "dizziness"],
      "outcome": "hospitalization",
      "similarity_score": 0.87
  }
  ```

### 3.2 Implement `find_similar_patients()`
- [ ] Accept `patient_summary` (text string)
- [ ] Search `patient_profiles` collection (no drug filter, just pure similarity)
- [ ] Return top_k results with similarity scores
- [ ] Map results into the agreed output format:
  ```python
  {
      "patient_id": "abc-123",
      "conditions": ["diabetes", "hypertension"],
      "medications": ["metformin", "lisinopril"],
      "similarity_score": 0.92
  }
  ```

### 3.3 Test the Query Functions
- [ ] Test with at least 5 different patient profiles (vary age, gender, conditions)
- [ ] Test with at least 3 different drug names
- [ ] Verify that similarity scores decrease as patients get less similar
- [ ] Test edge cases:
  - [ ] Drug name not in the collection (should return empty list, not crash)
  - [ ] Very short patient summary
  - [ ] Patient with no conditions or no medications

---

## Phase 4: Validate and Document (Week 3–4)

### 4.1 Validate Clinical Meaningfulness
- [ ] For 10 sample queries, manually inspect the top-5 results:
  - Are the returned patients actually similar (same age range, same conditions)?
  - Are the returned adverse events plausible for the drug?
  - Are there obvious false positives (completely unrelated patients ranked high)?
- [ ] Calculate basic stats:
  - Average similarity score for top-10 results
  - How many results have similarity > 0.8? > 0.5?
- [ ] If results are poor, revisit text serialization or try a different embedding model

### 4.2 Benchmark Performance
- [ ] Time the embedding step (how long to embed 5,000 reports?)
- [ ] Time query latency (how long does a single similarity search take?)
- [ ] Document collection size (number of vectors, storage size)

### 4.3 Write Report Section
- [ ] Explain why a vector database is needed (can't do similarity in SQL or Cypher)
- [ ] Describe the data source (openFDA FAERS — what it is, what it contains)
- [ ] Describe design decisions made:
  - Text serialization format chosen and why
  - Embedding model chosen and why
  - Similarity metric (cosine) and why
  - Filtering strategy and why
- [ ] Show example queries and results
- [ ] Include the Qdrant schema (collections, vector dimensions, payload fields)
- [ ] Include validation results (are similar patients actually similar?)

---

## Coordination Points

| With | What | When |
|------|------|------|
| **Part 1** (PostgreSQL) | Get the `get_patient_profile()` output format so you can build `patient_profiles` embeddings from the same Synthea data | Week 2 |
| **Part 5** (Integration) | Confirm `find_similar_adverse_events()` and `find_similar_patients()` function signatures and return format | Week 1 |
| **Parts 2 & 3** (Neo4j) | Agree on drug naming convention — the `drug_name` filter in Qdrant must match the drug names used in Neo4j | Week 1 |

---

## Quick Reference: Key Commands

```bash
# Install dependencies
pip install qdrant-client sentence-transformers requests

# Test openFDA API (with API key from .env)
curl "https://api.fda.gov/drug/event.json?api_key=$OPENFDA_API_KEY&search=patient.drug.openfda.generic_name:warfarin&limit=5"

# Run Qdrant locally
pip install qdrant-client
# Or run the server:
# docker run -p 6333:6333 qdrant/qdrant

# Run ETL
python etl/load_faers_to_qdrant.py

# Test queries
python -c "from db.qdrant_queries import find_similar_adverse_events; print(find_similar_adverse_events('65 year old male, diabetes, hypertension', 'warfarin'))"
```
