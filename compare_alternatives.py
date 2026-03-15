"""
compare_alternatives.py
-----------------------
Compare two approaches for finding safer drug alternatives:

Approach A — Hardcoded lookup table (simple, fast, limited)
Approach B — Qdrant drug_profiles semantic search (dynamic, scalable)

Run from project root:
    QDRANT_PATH=./qdrant_local python compare_alternatives.py

Helps you decide which approach to use in Part 5.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QDRANT_PATH", "")

import logging
import warnings
warnings.filterwarnings("ignore")
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

from db.qdrant_queries import compute_drug_similarity, find_similar_drugs

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
# APPROACH A — hardcoded lookup table
# ─────────────────────────────────────────────────────────────────────────────
HARDCODED_ALTERNATIVES = {
    "warfarin":    ["apixaban", "heparin", "rivaroxaban", "dabigatran"],
    "ibuprofen":   ["paracetamol", "naproxen", "celecoxib", "aspirin"],
    "sertraline":  ["fluoxetine", "citalopram", "escitalopram"],
    "metformin":   ["insulin", "glipizide", "sitagliptin"],
    "amoxicillin": ["azithromycin", "ciprofloxacin", "penicillin"],
    "aspirin":     ["paracetamol", "ibuprofen", "naproxen"],
    "lisinopril":  ["amlodipine", "losartan", "ramipril"],
}

def approach_a(drug: str) -> tuple[list[str], float]:
    """Hardcoded lookup — returns alternatives + time taken."""
    start = time.perf_counter()
    result = HARDCODED_ALTERNATIVES.get(drug.lower(), [])
    elapsed = time.perf_counter() - start
    return result, elapsed


# ─────────────────────────────────────────────────────────────────────────────
# APPROACH B — Qdrant drug_profiles semantic search
# ─────────────────────────────────────────────────────────────────────────────
def approach_b(drug: str, top_k: int = 5) -> tuple[list[dict], float]:
    """Qdrant semantic search — returns alternatives + time taken."""
    start = time.perf_counter()
    result = find_similar_drugs(drug, top_k=top_k)
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
    ("omeprazole",  "proton pump inhibitor — not in hardcoded list!"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Run comparison
# ─────────────────────────────────────────────────────────────────────────────
def run_comparison():
    sep()
    print(f"\n{BOLD}{CYAN}  APPROACH A vs B — Drug Alternatives Comparison{RESET}")
    print(f"  {DIM}A = hardcoded lookup table{RESET}")
    print(f"  {DIM}B = Qdrant drug_profiles semantic search{RESET}")
    sep()

    scorecard = {
        "a_wins":        0,
        "b_wins":        0,
        "tie":           0,
        "a_total_time":  0.0,
        "b_total_time":  0.0,
        "b_missing":     0,
    }

    for drug, description in TEST_DRUGS:
        thin()
        print(f"\n  {BOLD}Drug: {drug}{RESET}  {DIM}({description}){RESET}\n")

        # --- Approach A ---
        a_results, a_time = approach_a(drug)
        print(f"  {CYAN}A — Hardcoded:{RESET}")
        if a_results:
            for alt in a_results:
                sim = compute_drug_similarity(drug, alt)
                colour = GREEN if sim >= 0.45 else YELLOW if sim >= 0.25 else DIM
                print(f"    {alt:<20} BioLORD similarity: {colour}{sim:.4f}{RESET}")
        else:
            print(f"    {RED}No hardcoded alternatives for '{drug}'!{RESET}")
            print(f"    {DIM}This drug is not in the lookup table.{RESET}")
        print(f"    {DIM}Time: {a_time*1000:.2f}ms{RESET}")

        print()

        # --- Approach B ---
        b_results, b_time = approach_b(drug, top_k=5)
        print(f"  {CYAN}B — Qdrant semantic:{RESET}")
        if b_results:
            for r in b_results:
                colour = GREEN if r["similarity_score"] >= 0.45 else YELLOW if r["similarity_score"] >= 0.25 else DIM
                print(
                    f"    {r['name']:<20} "
                    f"Qdrant score: {colour}{r['similarity_score']:.4f}{RESET}  "
                    f"{DIM}({r.get('drug_class', '?')}){RESET}"
                )
        else:
            print(f"    {YELLOW}drug_profiles collection empty or drug not found.{RESET}")
            print(f"    {DIM}Run: python etl/load_drugs_to_qdrant.py first.{RESET}")
            scorecard["b_missing"] += 1

        print(f"    {DIM}Time: {b_time*1000:.2f}ms{RESET}")

        # --- winner for this drug ---
        print()
        a_count = len(a_results)
        b_count = len(b_results)
        a_has   = a_count > 0
        b_has   = b_count > 0

        if a_has and not b_has:
            print(f"  winner: {GREEN}A wins{RESET} — B has no data yet")
            scorecard["a_wins"] += 1
        elif b_has and not a_has:
            print(f"  winner: {GREEN}B wins{RESET} — A has no entry for this drug")
            scorecard["b_wins"] += 1
        elif b_has and a_has:
            # compare quality — B wins if it finds similar drugs A doesn't have
            a_set = set(a_results)
            b_set = set(r["name"] for r in b_results)
            b_only = b_set - a_set
            if b_only:
                print(f"  winner: {GREEN}B wins{RESET} — found extra: {', '.join(b_only)}")
                scorecard["b_wins"] += 1
            else:
                print(f"  winner: {YELLOW}tie{RESET} — same alternatives found")
                scorecard["tie"] += 1
        else:
            print(f"  winner: {RED}neither — no data in either approach!{RESET}")

        scorecard["a_total_time"] += a_time
        scorecard["b_total_time"] += b_time

    # ── Final scorecard ───────────────────────────────────────────────────────
    sep()
    print(f"\n{BOLD}  FINAL SCORECARD{RESET}\n")

    total = len(TEST_DRUGS)
    print(f"  Approach A wins : {scorecard['a_wins']}/{total}")
    print(f"  Approach B wins : {scorecard['b_wins']}/{total}")
    print(f"  Ties            : {scorecard['tie']}/{total}")
    print()
    print(f"  Avg time A : {scorecard['a_total_time']/total*1000:.2f}ms per drug")
    print(f"  Avg time B : {scorecard['b_total_time']/total*1000:.2f}ms per drug")
    print()

    # verdict
    print(f"  {BOLD}Verdict:{RESET}")
    if scorecard["b_missing"] >= 3:
        print(f"  {YELLOW}⚠  B needs drug_profiles loaded first — run load_drugs_to_qdrant.py{RESET}")
        print(f"  {DIM}Then re-run this comparison for a fair result.{RESET}")
    elif scorecard["b_wins"] > scorecard["a_wins"]:
        print(f"  {GREEN}Use Approach B (Qdrant){RESET} — more flexible, handles unknown drugs")
        print(f"  {DIM}Scales to any drug automatically. Worth the extra setup.{RESET}")
    elif scorecard["a_wins"] > scorecard["b_wins"]:
        print(f"  {GREEN}Use Approach A (hardcoded){RESET} — simpler, fast, good enough")
        print(f"  {DIM}Add more drugs to the lookup table as needed.{RESET}")
    else:
        print(f"  {YELLOW}Use BOTH — A as fallback when B returns empty{RESET}")
        print(f"  {DIM}B for known drugs in drug_profiles, A as safety net.{RESET}")

    print()
    print(f"  {BOLD}Key insight:{RESET}")
    print(f"  {DIM}Approach A fails silently for unknown drugs (omeprazole, digoxin){RESET}")
    print(f"  {DIM}Approach B works for ANY drug BioLORD was trained on{RESET}")
    print(f"  {DIM}Best architecture: try B first, fall back to A if empty{RESET}")
    sep()
    print()


if __name__ == "__main__":
    run_comparison()