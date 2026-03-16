# Drug alternatives from openFDA (NDC fallback)

**Current pipeline:** `drug_alternatives.py` uses **DrugBank** first (`data/drugbank_alternatives.json`). When a drug has &lt;10 alternatives, it fetches from **openFDA NDC** and merges; merges are stored in `data/ndc_merge.json` (the DrugBank file is never overwritten). Alternatives are ranked by BioLORD similarity and filtered to **≥0.40**.

openFDA does **not** expose an explicit “therapeutic alternatives” field. You can derive **same-pharmacologic-class** alternatives from two APIs.

## Options

| Source | API | What it gives | Pros | Cons |
|--------|-----|----------------|------|------|
| **NDC** | `drug/ndc.json` | Products with `pharm_class`, `generic_name` | Structured, one list per class; good coverage | Separate API, 100/request limit, product-level (aggregate by generic_name) |
| **Event (FAERS)** | `drug/event.json` | Reports with `patient.drug[].openfda.pharm_class_epc`, `generic_name` | Reuses data you already fetch for adverse events | Only drugs that appear in reports; not all reports have `openfda` |

## Recommendation: **NDC** for a full alternatives list

- Use **NDC** as fallback when DrugBank has &lt;10 alternatives for a drug (handled automatically by `drug_alternatives.py`).
- Use **Event** when you want alternatives **only for drugs that show up in FAERS** and prefer not to call another API.

## Local-first with NDC fallback (recommended)

Use local cache first; if there are **fewer than 10** alternatives (or none), fetch from NDC and merge. Return up to 10.

```python
from etl.openfda_alternatives import get_alternatives_local_first

# If local has ≥10 alts: return first 10 (no API call). If <10: fetch NDC, merge, cache, return up to 10
alts = get_alternatives_local_first("omeprazole")  # min_count=10 by default
alts = get_alternatives_local_first("warfarin", min_count=5)
# Tag each alternative as from cache or NDC: return_sources=True -> [(name, "drugbank"|"ndc"), ...]
alts_with_sources = get_alternatives_local_first("digoxin", return_sources=True)
# Disable NDC: return whatever is in local (up to min_count)
alts = get_alternatives_local_first("digoxin", fetch_from_ndc_if_missing=False)
```

## Bulk build and usage

```bash
# From project root

# Pre-fill local cache from NDC (optional; then local-first will hit cache for many drugs)
python -m etl.openfda_alternatives --source ndc --cache data/openfda_alternatives.json

# Event: derive from cached FAERS reports (run load_faers_to_qdrant first or pass raw reports)
python -m etl.openfda_alternatives --source event --event-cache
```

```python
from etl.openfda_alternatives import get_alternatives_from_openfda, get_alternatives_local_first

# Full dict (all drugs in cache or from bulk NDC/event run)
alts = get_alternatives_from_openfda("ndc", cache_path="data/openfda_alternatives.json")

# Single drug: local first, then NDC if not found (and cache updated)
alts = get_alternatives_local_first("warfarin")
```

## Label API

The **drug label** API (`drug/label`) has rich text (indications, mechanism) and `openfda` (e.g. `pharm_class_*`) but no direct “alternatives” list. Best used for semantic enrichment (e.g. embedding label text), not for building a same-class table.
