# Improving drug alternatives: Why DrugBank and how it’s used

**Current implementation:** `drug_alternatives.py` uses **DrugBank** as the primary source (`etl.drugbank_alternatives` → `data/drugbank_alternatives.json`). When a drug has &lt;10 alternatives, **NDC** is used as fallback; merges go to `data/ndc_merge.json`. Results are **ranked by BioLORD similarity** and filtered to **≥0.40** (quality gate).

OpenFDA/NDC alone returns **product-level** data: combo drugs, salt forms, and brand variants (“warfarin sodium”, “glipizide and metformin hydrochloride”, etc.). **DrugBank** gives drug-level, clean generics and is used first.

---

## Why DrugBank is better for A

| Aspect | openFDA/NDC (current A) | DrugBank |
|--------|------------------------|----------|
| **Granularity** | NDC = one row per **product** (many per drug) | Drug-level: one entity per **drug** (generic/molecule) |
| **Same drug** | “warfarin sodium” appears as separate from “warfarin” | Normalized names; salt forms linked to same drug |
| **Combos** | “X and metformin” listed as “alternatives” to metformin | Approved/experimental **single** drugs; combos are separate |
| **Classification** | NDC `pharm_class` per product (noisy, product-specific) | **Drug groups**, **ATC**, **categories** at drug level |
| **Alternatives** | Same pharm_class → all products in that class | Same ATC 4th level / same drug group → clean list of other drugs |

So: **yes, DrugBank data is better** for building a clean “alternative drugs” list for Approach A.

---

## What DrugBank offers

From [DrugBank releases](https://go.drugbank.com/releases/latest) (version 5.1.15 as of 2026):

- **Full database (XML, 175 MB)**  
  - Requires a **free Academic license** (students, professors, academic researchers; apply after creating an account).  
  - Contains: drug names, synonyms, **drug groups / categories**, **ATC codes**, indications, targets, **drug–drug interactions**, pathways.  
  - Ideal for: “same therapeutic class” or “same ATC level 4” → list of alternative drugs (generics only, no product/NDC noise).

- **Open Data (CC0, no license)**  
  - **DrugBank Vocabulary** (CSV, ~1.12 MB): identifiers, names, synonyms — good for **normalizing names** and linking.  
  - **DrugBank Structures** (SDF, ~5.16 MB): structures, names, synonyms.  
  - Does **not** include drug class/ATC in the open CSV; you’d need the full DB (or another source) for “same class” alternatives.

So for **improving A** you have two practical options:

1. **Use full DrugBank (academic)**  
   Parse XML → extract drug ID, name, **group/category/ATC** → build `drug → [same-class drugs]` → save as `data/openfda_alternatives.json` (or a new `data/drugbank_alternatives.json`) and have A read that **instead of or in addition to** NDC.

2. **Use Open Data only**  
   Use the Vocabulary CSV to **normalize** names (e.g. “warfarin sodium” → “warfarin”) and optionally merge with NDC: keep NDC for “same class”, but dedupe/normalize with DrugBank names so A returns generics only.

---

## How to improve A (two paths)

### Path 1: Keep openFDA/NDC, add filtering (no new data source)

- **Normalize to generic**: Map product names to a single generic (e.g. “warfarin sodium” → “warfarin”; “acetaminophen and ibuprofen” → drop or list as “acetaminophen”, “ibuprofen” separately).  
- **Filter out**:  
  - Same drug (query drug name contained in “alternative”, or known salt list).  
  - Antidotes (e.g. “digoxin immune fab” for query “digoxin”).  
  - Combination products when you want “alternatives to X” (e.g. exclude “Y and metformin” when query is “metformin”).  
- **Prefer single-ingredient**: When building the list, prefer NDC entries that have a single generic_name; or aggregate NDC by generic and return one row per generic.

This improves A with **no new data**, but NDC will still be product-heavy; you’ll never get as clean as DrugBank at drug level.

### Path 2: Use DrugBank to build the cache (recommended for quality)

1. **Get data**  
   - Create an account at [DrugBank](https://go.drugbank.com/releases/latest).  
   - Apply for the **free Academic license** if you qualify.  
   - Download the **full database (XML)** or the **Open Data** files as needed.

2. **Parse and build “alternatives”**  
   - From the full XML: for each drug, read **drug group** or **ATC code** (e.g. ATC level 4).  
   - Build a map: `(group or ATC4) → [drug_id/name]`.  
   - For each drug, alternatives = other drugs in the same group/ATC4 (excluding self).  
   - Export to JSON: `{"warfarin": ["rivaroxaban", "apixaban", "dabigatran", ...], ...}`.

3. **Wire in (done)**  
   - Built by `etl.drugbank_alternatives` → `data/drugbank_alternatives.json`.  
   - `drug_alternatives.py` loads DrugBank first, then `data/ndc_merge.json` (DrugBank overwrites shared keys). When a drug has &lt;10 alts, NDC is fetched and written to `ndc_merge.json` only.  
   - BioLORD similarity **≥0.40** filters wrong-class hits; results are ranked by similarity.

4. **Open Data only (no academic license)**  
   - Use **DrugBank Vocabulary** CSV to build a **name → canonical name** map.  
   - When displaying or storing A’s list, normalize each name (e.g. “warfarin sodium” → “warfarin”) so the output looks like “generic-only” alternatives even if the source is still NDC.

---

## Summary

- **Is DrugBank data better for A?** **Yes.** Drug-level data and proper classification (groups, ATC) give you clean, generic-only, same-class alternatives and avoid the product/NDC noise.
- **Best improvement for A:** Use **DrugBank (full DB, academic)** to build a `drug → [alternatives]` cache from same group/ATC, and use that as the primary source for A; keep NDC as optional fallback.
- **Without DrugBank:** Improve A by **filtering and normalizing** NDC (same-drug, antidotes, combos, prefer single generic); optionally use **DrugBank Vocabulary (Open Data)** for name normalization only.

Reference: [DrugBank Release Version 5.1.15](https://go.drugbank.com/releases/latest) — Academic (free) and Open Data (CC0) options.
