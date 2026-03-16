# Analysis: Qdrant Queries and FAERS ETL

This document describes what is implemented in `db/qdrant_queries.py` and `etl/load_faers_to_qdrant.py`.

---

## `db/qdrant_queries.py`

Qdrant query layer for the Drug Safety Check application. It provides similarity search over adverse event reports and patient profiles in Qdrant, using dense embeddings and cosine similarity (DSC 202 vector/similarity concepts).

### Configuration and infrastructure

- **Embedding model:** `FremyCompany/BioLORD-2023` (768-dimensional), loaded once via `@lru_cache`.
- **Qdrant:** Client from env (`QDRANT_HOST`, `QDRANT_PORT`, or `QDRANT_PATH` for local storage), also cached.
- **Collections:** `adverse_events`, `patient_profiles`, `drug_profiles`.

### Core helpers

- **`_get_model()`** — Returns the cached SentenceTransformer (BioLORD).
- **`_get_client()`** — Returns the cached Qdrant client.
- **`_embed(text)`** — Embeds a single string to a 768-dim list.
- **`_embed_batch(texts)`** — Batch encode texts to numpy array.

### Public query API

1. **`find_similar_adverse_events(patient_summary, drug_name, top_k=10)`**  
   - Embeds `patient_summary`, queries `adverse_events` with a payload filter `drug == drug_name`, returns top-k by cosine similarity.  
   - Each result: `patient_age`, `patient_sex`, `drugs`, `reactions`, `outcome`, `serious`, `similarity_score`, `raw_text`.

2. **`find_similar_adverse_events_multi_filter(patient_summary, drug_names=None, outcome=None, serious_only=False, sex=None, top_k=10)`**  
   - Same idea but with composable filters: optional `MatchAny` on drugs, exact match on `outcome`, `serious`, `patient_sex`.  
   - Returns the same payload shape as above.

3. **`find_similar_patients(patient_summary, top_k=10)`**  
   - Embeds summary, queries `patient_profiles` (no filter), returns top-k similar Synthea-style profiles: `patient_id`, `conditions`, `medications`, `similarity_score`.

4. **`analyze_adverse_event_aspects(results)`**  
   - Takes output of `find_similar_adverse_events` and does aspect-style analysis (severity, system-organ class, top reactions, outcomes) using keyword rules in `CLINICAL_ASPECTS` (severity buckets and organ keywords).

5. **`compute_drug_similarity(drug1, drug2)`**  
   - Cosine similarity between two drug-name embeddings.

6. **`compute_pairwise_drug_similarities(drug_names)`**  
   - Pairwise cosine similarities for a list of drugs; returns dict of `(drug_a, drug_b): score`.

### Loaders (write to Qdrant)

7. **`load_patient_profiles(profiles)`**  
   - Builds text from each profile (age, gender, conditions, medications), embeds with BioLORD, creates `patient_profiles` if missing (COSINE, 768-dim), upserts in batches of 200.  
   - Expects list of dicts with `patient_id`, `age`, `gender`, `conditions`, `medications` (e.g. from `pg_queries.get_patient_profile()`).

8. **`load_drug_profiles(drug_list)`**  
   - Each drug: `name`, optional `drug_class`, `mechanism`, `conditions`, `side_effects`. Builds a rich text description via `_build_drug_profile_text()`, embeds, creates `drug_profiles` if missing, upserts in batches of 200.  
   - Payload stores `name`, `drug_class`, `mechanism`, `conditions`, `side_effects`, `profile_text`.

### Drug similarity and safe-alternatives pipeline

9. **`find_similar_drugs(proposed_drug, top_k=10, exclude_drug=None)`**  
   - Embeds `proposed_drug`, queries `drug_profiles`, returns top-k similar drugs (optionally excluding one by name).  
   - Returns list of dicts with `name`, `drug_class`, `mechanism`, `conditions`, `side_effects`, `similarity_score`.

10. **`find_safe_alternatives_candidates(proposed_drug, top_k=10)`**  
    - Wrapper that checks `drug_profiles` exists, then returns `{ proposed_drug, candidates (from find_similar_drugs), total_found, status, message }`.  
    - Intended for Part 5: get candidates from Qdrant, then filter with Neo4j (e.g. `check_interactions`).

**Separate pipeline:** `drug_alternatives.py` uses **DrugBank** (`data/drugbank_alternatives.json`) plus **NDC** fallback when &lt;10 alts; results are ranked and filtered by **BioLORD similarity ≥0.40**. It does not use `drug_profiles` or `find_similar_drugs`.

---

## `etl/load_faers_to_qdrant.py`

ETL script that pulls openFDA FAERS adverse event data, parses it, embeds it with the same BioLORD model, and loads it into Qdrant. Also creates collections and payload indexes for filtered search, and at the end loads drug profiles from a separate ETL module.

### 1. Fetch FAERS from openFDA

- **`fetch_faers(limit=5000)`**  
  - Paginates over `https://api.fda.gov/drug/event.json` with configurable limit and page size (max 50 per request). Uses optional `OPENFDA_API_KEY`, retries with exponential backoff (2^attempt seconds), and a short delay between pages.  
  - Returns list of raw API report objects.

- **`save_cache(reports)`** — Writes reports to `data/faers_raw.json`.
- **`load_cache()`** — Reads from that file.

### 2. Parse and filter reports

- **`parse_report(raw)`**  
  - From each raw FAERS object: normalizes patient age (handles decade/month/week/day units), maps sex code to male/female, collects drug names (openFDA generic_name or medicinalproduct), dedupes reactions (reactionmeddrapt).  
  - Builds seriousness/outcome from seriousnessdeath, seriousnesshospitalization, etc.  
  - Returns `None` if no drugs or no reactions; otherwise dict with `patient_age`, `patient_sex`, `drugs`, `reactions`, `serious`, `outcome`, `report_id`.

- **`filter_reports(raw_reports)`** — Runs `parse_report` on each, drops failures, logs skip count; returns list of parsed records.

### 3. Text for embedding

- **`serialize_report(record)`**  
  - Turns one parsed record into a single string: “Patient: &lt;age&gt; &lt;sex&gt;. Medications: … Adverse reactions: … Outcome: …” for embedding.

### 4. Qdrant: collections and payload indexes

- **`create_collections(client)`**  
  - Ensures `adverse_events` and `patient_profiles` exist with `VectorParams(size=768, distance=COSINE)`.  
  - Calls `_create_payload_indexes` for `adverse_events`.

- **`_create_payload_indexes(client, collection)`**  
  - Creates payload indexes on `drug` (KEYWORD), `outcome` (KEYWORD), `serious` (BOOL), `patient_sex` (KEYWORD) for faster filtered queries.

- **`load_adverse_events(client, model, records)`**  
  - Serializes each record, encodes with the given SentenceTransformer in batches of 64, builds points with payload: `drug` (first drug), `all_drugs`, `reactions`, `patient_age`, `patient_sex`, `serious`, `outcome`, `report_id`, `raw_text`.  
  - Upserts into `adverse_events` in batches of 200.

### 5. Main entrypoint

- **`main()`**  
  - Parses CLI: `--limit` (default 5000), `--use-cache`, `--qdrant-host`, `--qdrant-port`, `--qdrant-path`.  
  - If not using cache: fetches FAERS, then saves cache. Loads cache or uses fetch result.  
  - Filters to valid records, loads BioLORD (`FremyCompany/BioLORD-2023`), connects to Qdrant (path or host/port), runs `create_collections` and `load_adverse_events`.  
  - Then imports `DRUG_CATALOG` from `etl.load_drugs_to_qdrant` and `load_drug_profiles` from `db.qdrant_queries`, and loads drug profiles into the `drug_profiles` collection.

### Summary

- **`db/qdrant_queries.py`**: Query and loader API for Qdrant — adverse events and patient profiles (similarity + filters), drug profiles and safe-alternatives candidates, aspect-style analysis and drug–drug similarity helpers.  
- **`etl/load_faers_to_qdrant.py`**: End-to-end FAERS ETL (fetch → parse → serialize → embed → upsert) into `adverse_events`, plus collection/index setup and a final step that loads drug profiles into Qdrant from the drug ETL catalog.
