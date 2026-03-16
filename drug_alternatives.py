"""
drug_alternatives.py
--------------------
Find safer drug alternatives from DrugBank (same ATC level 4, approved only).
Uses data/drugbank_alternatives.json; build with etl.drugbank_alternatives (default: approved only).

Optional: use --faers to consider FAERS reactions/outcomes when giving alternatives:
  - Annotate each alternative with FAERS summary (reports, % serious, top reactions).
  - Re-rank so alternatives with lower % serious are preferred when BioLORD similarity is close.
  Requires Qdrant with adverse_events loaded (python -m etl.load_faers_to_qdrant).

Run from project root:
    python drug_alternatives.py
    python drug_alternatives.py --faers
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QDRANT_PATH", "")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

import logging
import warnings
warnings.filterwarnings("ignore")
try:
    warnings.filterwarnings("ignore", module="huggingface_hub")
except Exception:
    pass
# Suppress HuggingFace / HTTP noise when loading BioLORD
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)

from db.qdrant_queries import compute_drug_similarity, get_drug_faers_summary
from etl.openfda_alternatives import get_alternatives_local_first
from pathlib import Path

# ── colours ──────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[91m"
RESET  = "\033[0m"
W      = 65

def sep():   print(f"\n{BOLD}{'═' * W}{RESET}")
def thin():  print(f"{DIM}{'─' * W}{RESET}")

# ─────────────────────────────────────────────────────────────────────────────
# Drug alternatives — DrugBank only (same ATC level 4)
# ─────────────────────────────────────────────────────────────────────────────
def get_alternatives(
    drug: str,
    local_lookup: dict | None = None,
    min_count: int = 10,
    return_sources: bool = False,
    fetch_from_ndc: bool = False,
) -> tuple[list[str] | list[tuple[str, str]], float]:
    """Return up to min_count alternatives: DrugBank first; if <10, fill from NDC (duplicates removed). If return_sources=True, result is list of (name, 'drugbank'|'ndc')."""
    data_dir = Path(__file__).resolve().parent / "data"
    start = time.perf_counter()
    result = get_alternatives_local_first(
        drug,
        local_lookup=local_lookup,
        cache_path=data_dir / "drugbank_alternatives.json",
        merge_cache_path=data_dir / "ndc_merge.json",
        min_count=min_count,
        return_sources=return_sources,
        fetch_from_ndc_if_missing=fetch_from_ndc,
    )
    elapsed = time.perf_counter() - start
    return result, elapsed


# ─────────────────────────────────────────────────────────────────────────────
# Test drugs
# ─────────────────────────────────────────────────────────────────────────────
TEST_DRUGS = [
    ("warfarin",    "anticoagulant — well known alternatives exist"),
    ("ibuprofen",   "NSAID — common, many alternatives"),
    ("sertraline",  "SSRI — psychiatric drug"),
    ("metformin",   "antidiabetic — well defined class"),
    ("penicillin",  "antibiotic — classic drug"),
    ("digoxin",     "cardiac — obscure, tests edge cases"),
    ("omeprazole",  "proton pump inhibitor"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────
def run_alternatives(use_faers: bool = False):
    sep()
    print(f"\n{BOLD}{CYAN}  Drug Alternatives (DrugBank + NDC if <10){RESET}")
    print(f"  {DIM}DrugBank first (approved); if <10 alts, fill from NDC. Duplicates removed.{RESET}")
    if use_faers:
        print(f"  {DIM}FAERS: using reactions/outcomes to annotate and prefer safer alternatives (Qdrant).{RESET}")
    sep()

    # Load DrugBank first; overlay ndc_merge (DrugBank wins so we never show polluted data for keys in both).
    data_dir = Path(__file__).resolve().parent / "data"
    drugbank_path = data_dir / "drugbank_alternatives.json"
    ndc_merge_path = data_dir / "ndc_merge.json"
    local_lookup = {}
    if ndc_merge_path.exists():
        try:
            with open(ndc_merge_path) as f:
                local_lookup = json.load(f)
        except Exception:
            pass
    if drugbank_path.exists():
        try:
            with open(drugbank_path) as f:
                drugbank = json.load(f)
            for k, v in drugbank.items():
                local_lookup[k] = v
        except Exception:
            pass
    if not local_lookup:
        local_lookup = None

    total_time = 0.0
    for drug, description in TEST_DRUGS:
        thin()
        print(f"\n  {BOLD}Drug: {drug}{RESET}  {DIM}({description}){RESET}\n")

        results, elapsed = get_alternatives(
            drug,
            local_lookup=local_lookup,
            min_count=10,
            return_sources=True,
            fetch_from_ndc=True,
        )
        total_time += elapsed

        # Quality gate: BioLORD similarity >= 0.40 filters wrong-class / low-confidence alternatives
        SIMILARITY_THRESHOLD = 0.40
        print(f"  {CYAN}Alternatives (ranked by BioLORD similarity, ≥{SIMILARITY_THRESHOLD}):{RESET}")
        if results:
            scored = [(item[0], item[1], compute_drug_similarity(drug, item[0])) for item in results]
            scored = [c for c in scored if c[2] >= SIMILARITY_THRESHOLD]
            if not scored:
                print(f"    {YELLOW}No alternatives above similarity threshold (≥{SIMILARITY_THRESHOLD}).{RESET}")

            # Optionally add FAERS reaction/outcome summary and re-rank by safety
            elif use_faers:
                faers_list = []
                for alt, source, sim in scored:
                    summary = get_drug_faers_summary(alt, top_k=50)
                    pct = summary["pct_serious"] if summary else None
                    top_r = list((summary or {}).get("top_reactions", {}).keys())[:3]
                    faers_list.append((alt, source, sim, summary, pct, top_r))
                # Re-rank: primary by similarity desc, secondary by pct_serious asc (prefer safer)
                faers_list.sort(key=lambda x: (x[2], -(x[4] if x[4] is not None else 1)), reverse=True)
                for alt, source, sim, summary, pct, top_r in faers_list:
                    colour = GREEN if sim >= 0.45 else YELLOW if sim >= 0.25 else DIM
                    src_label = f" {DIM}(DrugBank){RESET}" if source == "drugbank" else f" {DIM}(NDC){RESET}"
                    print(f"    {alt:<20} BioLORD similarity: {colour}{sim:.4f}{RESET}{src_label}")
                    if summary:
                        n_rep = summary.get("total_reports", 0)
                        pct_s = summary.get("pct_serious", 0) * 100
                        reactions_str = ", ".join(top_r) if top_r else "—"
                        print(f"      {DIM}FAERS: {n_rep} reports, {pct_s:.0f}% serious; top: {reactions_str}{RESET}")
            else:
                scored.sort(key=lambda x: x[2], reverse=True)
                for alt, source, sim in scored:
                    colour = GREEN if sim >= 0.45 else YELLOW if sim >= 0.25 else DIM
                    src_label = f" {DIM}(DrugBank){RESET}" if source == "drugbank" else f" {DIM}(NDC){RESET}"
                    print(f"    {alt:<20} BioLORD similarity: {colour}{sim:.4f}{RESET}{src_label}")
        else:
            print(f"    {RED}No alternatives for '{drug}' (DrugBank or NDC).{RESET}")
            print(f"    {DIM}Run: python -m etl.drugbank_alternatives --xml \"data/full database.xml\" (approved only).{RESET}")
        print(f"    {DIM}Time: {elapsed*1000:.2f}ms{RESET}")

    sep()
    n = len(TEST_DRUGS)
    print(f"\n  {BOLD}Summary{RESET}\n")
    print(f"  Drugs: {n}  |  Avg time: {total_time/n*1000:.2f}ms per drug")
    print(f"  {DIM}Build cache (approved only): python -m etl.drugbank_alternatives --xml \"data/full database.xml\"{RESET}")
    if use_faers:
        print(f"  {DIM}FAERS data from Qdrant adverse_events. Load with: python -m etl.load_faers_to_qdrant --use-cache{RESET}")
    sep()
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drug alternatives (DrugBank + NDC), optional FAERS reaction/outcome awareness")
    parser.add_argument("--faers", action="store_true", help="Annotate with FAERS reactions/outcomes and prefer safer alternatives (requires Qdrant)")
    args = parser.parse_args()
    run_alternatives(use_faers=args.faers)
