"""
OpenFDA-based drug alternatives (same pharmacologic class).

openFDA does not provide an explicit "alternatives" field. Two options:

1. EVENT API (FAERS) — Extract from adverse event reports we already fetch.
   - Each report's patient.drug[] can have openfda.generic_name and openfda.pharm_class_epc.
   - Build: class -> set(drugs), then alternatives(drug) = other drugs in same class.
   - Pros: Same pipeline as load_faers_to_qdrant; no extra API. Cons: Only drugs that
     appear in reports get a class; not all reports have openfda (harmonization gaps).

2. NDC API — Product directory with structured pharm_class per product.
   - Count pharm_class.exact, then for each class fetch products and collect generic_name.
   - Pros: Authoritative, comprehensive; one class list for all drugs in that class.
   - Cons: Different API (rate limits, max 100/request); NDC is product-level (many rows per drug).

RECOMMENDATION: Use NDC for a complete, structured same-class alternatives list.
Use Event when you want alternatives derived only from drugs that appear in FAERS (e.g. for consistency with adverse-event data).
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event API (FAERS) — extract from raw reports
# ---------------------------------------------------------------------------

def get_alternatives_from_events(raw_reports: list[dict]) -> dict[str, list[str]]:
    """
    Build drug -> [alternatives] from FAERS event reports using openfda.pharm_class_epc.

    Only drugs that appear in reports with harmonized openfda data get entries.
    Alternatives = other generic_name in the same pharm_class_epc.
    """
    # (generic_name, pharm_class_epc) from each drug in each report
    drug_classes: dict[str, set[str]] = defaultdict(set)  # drug -> set of class names

    for report in raw_reports:
        for drug in report.get("patient", {}).get("drug", []):
            openfda = drug.get("openfda", {})
            names = openfda.get("generic_name", [])
            classes = openfda.get("pharm_class_epc", [])
            if not names or not classes:
                continue
            gname = names[0].strip().lower()
            for c in classes:
                drug_classes[gname].add(c.strip())

    # class -> set of drugs
    class_to_drugs: dict[str, set[str]] = defaultdict(set)
    for drug, classes in drug_classes.items():
        for c in classes:
            class_to_drugs[c].add(drug)

    # drug -> list of other drugs in any of its classes (excluding self)
    out: dict[str, list[str]] = {}
    for drug, classes in drug_classes.items():
        others = set()
        for c in classes:
            others |= class_to_drugs[c]
        others.discard(drug)
        out[drug] = sorted(others)

    return out


# ---------------------------------------------------------------------------
# NDC API — fetch by pharm_class
# ---------------------------------------------------------------------------

NDC_BASE = "https://api.fda.gov/drug/ndc.json"
NDC_LIMIT = 100  # openFDA max per request


def _ndc_request(params: dict, api_key: str = "") -> dict:
    if api_key:
        params.setdefault("api_key", api_key)
    r = requests.get(NDC_BASE, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_alternatives_from_ndc(
    max_classes: int = 150,
    max_products_per_class: int = 500,
    api_key: str = "",
) -> dict[str, list[str]]:
    """
    Build drug -> [alternatives] from NDC API using pharm_class.

    Fetches top pharmacologic classes by count, then for each class collects
    all unique generic_name (deduped). Alternatives = other drugs in same class.
    """
    import os
    api_key = api_key or os.getenv("OPENFDA_API_KEY", "")

    # 1) Count pharm_class to get class names
    log.info("Fetching NDC pharm_class counts…")
    try:
        data = _ndc_request({"count": "pharm_class.exact", "limit": max_classes}, api_key)
    except requests.RequestException as e:
        log.error("NDC count request failed: %s", e)
        return {}

    results = data.get("results", [])
    if not results:
        log.warning("NDC returned no pharm_class counts.")
        return {}

    class_to_drugs: dict[str, set[str]] = defaultdict(set)

    for i, item in enumerate(results):
        term = item.get("term", "")
        if not term or term == "NA":
            continue
        log.info("  [%d/%d] %s", i + 1, len(results), term[:60] + ("…" if len(term) > 60 else ""))

        skip = 0
        seen = set()
        while skip < max_products_per_class:
            try:
                # NDC search: pharm_class is the field
                data = _ndc_request({
                    "search": f'pharm_class:"{term}"',
                    "limit": NDC_LIMIT,
                    "skip": skip,
                }, api_key)
            except requests.RequestException as e:
                log.warning("    NDC search failed at skip=%d: %s", skip, e)
                break

            for rec in data.get("results", []):
                gname = rec.get("generic_name")
                if isinstance(gname, list):
                    gname = gname[0] if gname else ""
                if not gname:
                    of = rec.get("openfda") or {}
                    gn = of.get("generic_name")
                    gname = gn[0] if isinstance(gn, list) and gn else (gn or "")
                gname = (gname or "").strip().lower()
                if gname and gname not in seen:
                    seen.add(gname)
                    class_to_drugs[term].add(gname)

            if len(data.get("results", [])) < NDC_LIMIT:
                break
            skip += NDC_LIMIT
            time.sleep(0.2)  # be nice to API

    # drug -> list of other drugs in same class(es)
    all_drugs = set()
    for drugs in class_to_drugs.values():
        all_drugs |= drugs

    out: dict[str, list[str]] = {}
    for drug in all_drugs:
        others = set()
        for c, drugs in class_to_drugs.items():
            if drug in drugs:
                others |= drugs
        others.discard(drug)
        out[drug] = sorted(others)

    log.info("NDC: %d drugs with same-class alternatives.", len(out))
    return out


def _extract_generic_name(rec: dict) -> str | None:
    """Get generic_name from NDC result (top-level or openfda)."""
    gname = rec.get("generic_name")
    if isinstance(gname, list):
        gname = gname[0] if gname else None
    if not gname:
        of = rec.get("openfda") or {}
        gn = of.get("generic_name")
        gname = gn[0] if isinstance(gn, list) and gn else (gn or None)
    return (gname or "").strip().lower() or None


def _extract_pharm_class(rec: dict) -> list[str]:
    """Get pharm_class from NDC result (top-level or openfda)."""
    pc = rec.get("pharm_class")
    if isinstance(pc, str) and pc.strip():
        return [pc.strip()]
    if isinstance(pc, list):
        return [str(x).strip() for x in pc if str(x).strip()]
    of = rec.get("openfda") or {}
    pc = of.get("pharm_class_epc") or of.get("pharm_class")
    if isinstance(pc, str) and pc.strip():
        return [pc.strip()]
    if isinstance(pc, list):
        return [str(x).strip() for x in pc if str(x).strip()]
    return []


def get_alternatives_for_drug_from_ndc(drug_name: str, api_key: str = "") -> list[str]:
    """
    Fetch same-class alternatives for a single drug from NDC API.

    Looks up one product with this generic_name to get pharm_class, then
    returns all other generic_name in that class. Empty if drug or class not found.
    """
    import os
    api_key = api_key or os.getenv("OPENFDA_API_KEY", "")
    drug_name = (drug_name or "").strip().lower()
    if not drug_name:
        return []

    try:
        # Find one product with this generic_name to get its pharm_class
        data = _ndc_request({
            "search": f'generic_name:"{drug_name}"',
            "limit": 5,
        }, api_key)
    except requests.RequestException as e:
        log.debug("NDC lookup for %s failed: %s", drug_name, e)
        return []

    results = data.get("results", [])
    classes: list[str] = []
    for rec in results:
        classes = _extract_pharm_class(rec)
        if classes:
            break

    if not classes:
        return []

    # Fetch all drugs in the first matching class
    class_name = classes[0]
    seen: set[str] = set()
    skip = 0
    limit = NDC_LIMIT

    try:
        while True:
            data = _ndc_request({
                "search": f'pharm_class:"{class_name}"',
                "limit": limit,
                "skip": skip,
            }, api_key)
            for rec in data.get("results", []):
                g = _extract_generic_name(rec)
                if g:
                    seen.add(g)
            if len(data.get("results", [])) < limit:
                break
            skip += limit
            time.sleep(0.15)
    except requests.RequestException as e:
        log.debug("NDC class lookup for %s failed: %s", class_name, e)

    seen.discard(drug_name)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Local-first lookup with NDC fallback
# ---------------------------------------------------------------------------

def _default_cache_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "openfda_alternatives.json"


# Common salt/suffix strings in NDC product names — same drug, not an alternative
_NDC_SALT_SUFFIXES = frozenset({
    "sodium", "potassium", "calcium", "magnesium", "hydrochloride", "hcl", "hci",
    "maleate", "succinate", "acetate", "citrate", "phosphate", "sulfate", "besylate",
    "hydrobromide", "mesylate", "fumarate", "tartrate", "dihydrate", "monohydrate",
    "hemihydrate", "trihydrate", "stearate", "oleate", "palmitate", "teoclate",
})


def _should_exclude_ndc_alternative(query_drug: str, alternative: str) -> bool:
    """
    Return True if the NDC alternative should be excluded: same drug (e.g. salt form)
    or antidote (e.g. digoxin immune fab for digoxin).
    """
    q = (query_drug or "").strip().lower()
    a = (alternative or "").strip().lower()
    if not q or not a:
        return True
    # Antidote: e.g. "digoxin immune fab", "ovine digoxin immune fab"
    if "immune fab" in a or "antidote" in a:
        return True
    # Exact match
    if a == q:
        return True
    # Same drug, salt form: "warfarin sodium" for query "warfarin"
    if a.startswith(q + " "):
        remainder = a[len(q):].strip()
        # remainder should be a single token or known combo (e.g. "sodium", "and chloride")
        parts = remainder.split()
        if all(p in _NDC_SALT_SUFFIXES or p in ("and", "injection", "oral", "tablet") for p in parts):
            return True
        # Single token that is a salt
        if len(parts) == 1 and parts[0] in _NDC_SALT_SUFFIXES:
            return True
    # Query is contained and rest looks like salt: "warfarin" in "warfarin sodium"
    if q in a and a != q:
        rest = a.replace(q, "").strip().strip("-").strip()
        if rest and all(p in _NDC_SALT_SUFFIXES for p in rest.split()):
            return True
    return False


def get_alternatives_local_first(
    drug_name: str,
    local_lookup: dict[str, list[str]] | None = None,
    cache_path: str | Path | None = None,
    merge_cache_path: str | Path | None = None,
    fetch_from_ndc_if_missing: bool = True,
    min_count: int = 10,
    return_sources: bool = False,
    api_key: str = "",
) -> list[str] | list[tuple[str, str]]:
    """
    Get alternatives for one drug: use local first; if fewer than min_count, fetch from NDC and merge.

    - If local has >= min_count alternatives: return the first min_count (no API call).
    - If local has < min_count (or none): fetch from NDC, merge with local (local first, then
      fill from NDC until min_count). If merge_cache_path is set, NDC merges are written there
      (never to cache_path / DrugBank file); if not set, write to cache_path.

    - min_count: target number of alternatives (default 10). Return up to this many.
    - return_sources: if True, return list of (drug_name, "drugbank"|"ndc"); if False, return list[str].
    - merge_cache_path: optional path for persisting NDC merges; use so DrugBank cache is never overwritten.
    """
    import os
    api_key = api_key or os.getenv("OPENFDA_API_KEY", "")
    drug_name = (drug_name or "").strip().lower()
    if not drug_name:
        return [] if not return_sources else []

    path = Path(cache_path) if cache_path is not None else _default_cache_path()
    lookup = local_lookup
    if lookup is None:
        if path.exists():
            try:
                with open(path) as f:
                    lookup = json.load(f)
            except (json.JSONDecodeError, OSError):
                lookup = {}
        else:
            lookup = {}

    local_alts = lookup.get(drug_name)
    if isinstance(local_alts, list):
        local_alts = list(local_alts)
    else:
        local_alts = []

    # Data-driven fallback: if missing or few alts, use a related cache key (e.g. "penicillin" -> "ampicillin"'s list).
    # Exclude antidote-like keys so "digoxin" does not get "digoxin immune fab"'s list.
    _ANTIDOTE_KEY_SUBSTRINGS = ("immune fab", "antidote")
    if (not local_alts or len(local_alts) < min_count) and lookup:
        related = [
            (k, v) for k, v in lookup.items()
            if drug_name != k
            and drug_name in k
            and isinstance(v, list)
            and len(v) > len(local_alts)
            and not any(sub in k.lower() for sub in _ANTIDOTE_KEY_SUBSTRINGS)
        ]
        if related:
            best = max(related, key=lambda x: len(x[1]))
            local_alts = list(best[1])

    # Enough in local: return first min_count (all "drugbank")
    if len(local_alts) >= min_count:
        out = local_alts[:min_count]
        if return_sources:
            return [(a, "drugbank") for a in out]
        return out

    if not fetch_from_ndc_if_missing:
        out = local_alts[:min_count]
        if return_sources:
            return [(a, "drugbank") for a in out]
        return out

    # Fetch from NDC and merge: DrugBank first, then fill from NDC up to min_count.
    # Remove duplicates (exact and case-insensitive); filter same-drug and antidotes.
    ndc_alts = get_alternatives_for_drug_from_ndc(drug_name, api_key)
    # Dedupe local: keep first occurrence
    local_deduped = list(dict.fromkeys(local_alts))
    seen = set(local_deduped)
    seen_lower = set(x.lower() for x in local_deduped)
    merged = list(local_deduped)
    merged_sources: list[tuple[str, str]] = [(a, "drugbank") for a in local_deduped]
    for a in ndc_alts:
        if a.lower() in seen_lower:
            continue
        if (
            a not in seen
            and len(merged) < min_count
            and not _should_exclude_ndc_alternative(drug_name, a)
        ):
            seen.add(a)
            seen_lower.add(a.lower())
            merged.append(a)
            merged_sources.append((a, "ndc"))

    if merged != local_alts:
        write_path = Path(merge_cache_path) if merge_cache_path else path
        write_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if write_path.exists():
                with open(write_path) as f:
                    cache = json.load(f)
            else:
                cache = {}
            cache[drug_name] = merged
            with open(write_path, "w") as f:
                json.dump(cache, f, indent=2)
            log.debug("Updated merge cache for %s (%d alts) at %s", drug_name, len(merged), write_path)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not update merge cache %s: %s", write_path, e)

    out = merged[:min_count]
    if return_sources:
        return merged_sources[:min_count]
    return out


# ---------------------------------------------------------------------------
# Unified entrypoint + cache
# ---------------------------------------------------------------------------

def get_alternatives_from_openfda(
    source: str = "ndc",
    raw_reports: list[dict] | None = None,
    cache_path: str | Path | None = None,
    **ndc_kwargs,
) -> dict[str, list[str]]:
    """
    Get drug -> [alternatives] from openFDA.

    source:
      - "ndc" (default): NDC API by pharm_class. Best coverage and structure.
      - "event": From FAERS raw reports (pass raw_reports or they’re loaded from cache).

    If cache_path is set and file exists, load from cache instead of calling API.
    If source is "ndc" and cache_path is set, save result to cache after fetch.
    """
    if cache_path is not None:
        cache_path = Path(cache_path)
        if cache_path.exists():
            with open(cache_path) as f:
                return json.load(f)

    if source == "event":
        if raw_reports is None:
            data_dir = Path(__file__).resolve().parent.parent / "data"
            faers_cache = data_dir / "faers_raw.json"
            if not faers_cache.exists():
                log.warning("No raw reports and no cache at %s. Run load_faers_to_qdrant first or pass raw_reports.", faers_cache)
                return {}
            with open(faers_cache) as f:
                raw_reports = json.load(f)
        return get_alternatives_from_events(raw_reports)

    if source == "ndc":
        result = get_alternatives_from_ndc(**ndc_kwargs)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(result, f, indent=2)
            log.info("Cached to %s", cache_path)
        return result

    raise ValueError('source must be "ndc" or "event"')


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Build same-class drug alternatives from openFDA")
    p.add_argument("--source", choices=("ndc", "event"), default="ndc", help="NDC (recommended) or Event (FAERS)")
    p.add_argument("--cache", type=Path, default=None, help="Load/save JSON cache of alternatives")
    p.add_argument("--max-classes", type=int, default=80, help="NDC: max classes to fetch (default 80)")
    p.add_argument("--event-cache", action="store_true", help="Use data/faers_raw.json for event source")
    args = p.parse_args()

    cache = args.cache or (Path(__file__).resolve().parent.parent / "data" / "openfda_alternatives.json" if args.source == "ndc" else None)
    raw = None
    if args.source == "event" and args.event_cache:
        data_dir = Path(__file__).resolve().parent.parent / "data"
        c = data_dir / "faers_raw.json"
        if c.exists():
            with open(c) as f:
                raw = json.load(f)
            log.info("Loaded %d reports from %s", len(raw), c)

    alt = get_alternatives_from_openfda(
        source=args.source,
        raw_reports=raw,
        cache_path=cache,
        max_classes=args.max_classes,
    )
    print(json.dumps({k: v for k, v in sorted(alt.items()) if v}, indent=2)[:2000], "...")
    print(f"\nTotal drugs with ≥1 alternative: {sum(1 for v in alt.values() if v)}")
