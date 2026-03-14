"""
test_qdrant_queries.py
----------------------
Quick sanity-check for your local Qdrant data.
Calls your existing db/qdrant_queries.py functions directly.

Run from your project root:
    QDRANT_PATH=./qdrant_local python test_qdrant_queries.py

No new dependencies — uses whatever you already have installed.
"""

import os
import sys

# ---------------------------------------------------------------------------
# Make sure Python can find your db/ package
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QDRANT_PATH", "./qdrant_local")

# ---------------------------------------------------------------------------
# Import YOUR functions (not a new client — your actual code)
# ---------------------------------------------------------------------------
try:
    from db.qdrant_queries import (
        find_similar_adverse_events,
        find_similar_adverse_events_multi_filter,
        analyze_adverse_event_aspects,
        compute_drug_similarity,
    )
except ImportError as e:
    print(f"\nERROR: Could not import from db/qdrant_queries.py: {e}")
    print("Make sure you're running this from your project root directory.")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Test cases — realistic clinical scenarios for your system
# ---------------------------------------------------------------------------
TEST_CASES = [
    {
        "label": "Elderly patient on warfarin with GI bleed",
        "summary": "75 year old male on warfarin presenting with melena and GI bleeding",
        "drug": "warfarin",
    },
    {
        "label": "Young female on SSRI with bleeding",
        "summary": "28 year old female taking sertraline with unusual bruising and prolonged bleeding time",
        "drug": "sertraline",
    },
    {
        "label": "Diabetic patient with kidney issues",
        "summary": "60 year old diabetic patient on metformin with elevated creatinine and reduced kidney function",
        "drug": "metformin",
    },
    {
        "label": "Cardiac patient with palpitations",
        "summary": "55 year old male on aspirin and atorvastatin with chest pain and palpitations",
        "drug": "aspirin",
    },
]

SEPARATOR = "=" * 65


def score_to_quality(score: float) -> str:
    """Turn a cosine similarity score into a human-readable label.

    BioLORD is more discriminating than MiniLM — it spreads scores further
    apart so thresholds are lower than you'd expect from a general model.
    A 0.65 from BioLORD is genuinely strong clinical signal.
    """
    if score >= 0.65:
        return "excellent"
    elif score >= 0.45:
        return "good"
    elif score >= 0.25:
        return "fair"
    else:
        return "weak — check drug name spelling or add more data"


def print_hit(rank: int, hit: dict) -> None:
    score = hit.get("similarity_score", 0)
    quality = score_to_quality(score)
    reactions = ", ".join(hit.get("reactions", [])[:4]) or "none listed"
    drugs = ", ".join(hit.get("drugs", [])[:3]) or "none"
    age = hit.get("patient_age", "?")
    sex = hit.get("patient_sex", "?")
    outcome = hit.get("outcome", "?")
    serious = "YES" if hit.get("serious") else "no"

    print(f"  [{rank}] score={score:.4f} ({quality})")
    print(f"       patient : {age}yr {sex}")
    print(f"       drugs   : {drugs}")
    print(f"       reactions: {reactions}")
    print(f"       outcome : {outcome} | serious: {serious}")


# ---------------------------------------------------------------------------
# Test 1: Basic similarity search per drug
# ---------------------------------------------------------------------------
def test_basic_search():
    print(f"\n{SEPARATOR}")
    print("TEST 1: find_similar_adverse_events() — basic search")
    print(SEPARATOR)

    all_scores = []

    for tc in TEST_CASES:
        print(f"\n  Query : {tc['label']}")
        print(f"  Drug  : {tc['drug']}")

        results = find_similar_adverse_events(
            patient_summary=tc["summary"],
            drug_name=tc["drug"],
            top_k=3,
        )

        if not results:
            print(f"  RESULT: No matches found for '{tc['drug']}'")
            print(f"          (This drug may not be in your dataset — try a more common drug)")
            continue

        for i, hit in enumerate(results, 1):
            print_hit(i, hit)
            all_scores.append(hit["similarity_score"])

    return all_scores


# ---------------------------------------------------------------------------
# Test 2: Multi-filter search (serious only)
# ---------------------------------------------------------------------------
def test_multi_filter():
    print(f"\n{SEPARATOR}")
    print("TEST 2: find_similar_adverse_events_multi_filter() — serious only")
    print(SEPARATOR)

    summary = "65 year old male patient with multiple medications experiencing severe adverse reaction"

    print(f"\n  Query  : {summary}")
    print(f"  Filter : serious=True, sex=male")

    results = find_similar_adverse_events_multi_filter(
        patient_summary=summary,
        serious_only=True,
        sex="male",
        top_k=5,
    )

    if not results:
        print("  RESULT: No serious male adverse events found in sample.")
        print("          Try running with --limit 5000 for more data.")
        return

    print(f"  Found {len(results)} serious male adverse event(s):\n")
    for i, hit in enumerate(results, 1):
        print_hit(i, hit)


# ---------------------------------------------------------------------------
# Test 3: Aspect analysis on results
# ---------------------------------------------------------------------------
def test_aspect_analysis():
    print(f"\n{SEPARATOR}")
    print("TEST 3: analyze_adverse_event_aspects() — organ system breakdown")
    print(SEPARATOR)

    summary = "patient experiencing adverse drug reaction with multiple symptoms"
    results = find_similar_adverse_events_multi_filter(
        patient_summary=summary,
        top_k=20,
    )

    if not results:
        print("  No results to analyze.")
        return

    analysis = analyze_adverse_event_aspects(results)

    print(f"\n  Analyzed {analysis['total_reports']} reports\n")

    print("  Severity distribution:")
    for sev, count in analysis["severity_distribution"].items():
        bar = "█" * count
        print(f"    {sev:<25} {bar} ({count})")

    print("\n  Organ system distribution:")
    for organ, count in analysis["organ_system_distribution"].items():
        bar = "█" * count
        print(f"    {organ:<25} {bar} ({count})")

    print("\n  Top 5 reactions:")
    top5 = list(analysis["top_reactions"].items())[:5]
    for reaction, count in top5:
        print(f"    {reaction:<35} {count}x")


# ---------------------------------------------------------------------------
# Test 4: Drug-drug semantic similarity
# ---------------------------------------------------------------------------
def test_drug_similarity():
    print(f"\n{SEPARATOR}")
    print("TEST 4: compute_drug_similarity() — semantic drug relationships")
    print(SEPARATOR)

    pairs = [
        ("warfarin", "heparin"),       # both anticoagulants — should be HIGH
        ("aspirin", "ibuprofen"),       # both NSAIDs — should be HIGH
        ("metformin", "insulin"),       # both diabetes drugs — should be MODERATE
        ("aspirin", "metformin"),       # unrelated — should be LOW
        ("sertraline", "fluoxetine"),   # both SSRIs — should be HIGH
    ]

    print()
    for drug1, drug2 in pairs:
        score = compute_drug_similarity(drug1, drug2)
        quality = score_to_quality(score)
        print(f"  {drug1:<15} vs {drug2:<15} → {score:.4f}  ({quality})")

    print()
    print("  BioLORD interpretation guide:")
    print("    same drug class  → expect 0.50-0.70  (warfarin/heparin, aspirin/ibuprofen)")
    print("    related classes  → expect 0.30-0.55  (metformin/insulin)")
    print("    unrelated drugs  → expect 0.00-0.15  (aspirin/metformin) ← BioLORD superpower")


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------
def print_scorecard(all_scores: list[float]):
    print(f"\n{SEPARATOR}")
    print("OVERALL SCORECARD")
    print(SEPARATOR)

    if not all_scores:
        print("\n  No scores collected — your sample may not contain the test drugs.")
        print("  Try: python etl/load_faers_to_qdrant.py --limit 5000 --use-cache")
        return

    import statistics
    avg = statistics.mean(all_scores)
    best = max(all_scores)
    worst = min(all_scores)

    print(f"\n  Queries with results : {len(all_scores)} hits")
    print(f"  Avg similarity       : {avg:.4f}")
    print(f"  Best match           : {best:.4f}")
    print(f"  Worst match          : {worst:.4f}")

    print()
    if avg >= 0.55:
        verdict = "EXCELLENT — BioLORD clinical matching is working great!"
        tip = "Ready to connect to FastAPI and integrate with Neo4j + PostgreSQL."
    elif avg >= 0.40:
        verdict = "GOOD — BioLORD working well (lower raw scores are normal and expected)"
        tip = "Check top hits are clinically relevant — scores matter less than result quality."
    elif avg >= 0.25:
        verdict = "FAIR — Results returning but some queries have weak matches"
        tip = "Check drug name spelling in queries matches payload (lowercase, generic names)."
    else:
        verdict = "WEAK — Very few relevant results"
        tip = "Check VECTOR_DIM=768 in both ETL and qdrant_queries.py, and delete qdrant_local/ and re-ingest."

    print(f"  Verdict : {verdict}")
    print(f"  Tip     : {tip}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"\n{SEPARATOR}")
    print(" QDRANT QUERY TEST")
    print(f" QDRANT_PATH = {os.environ.get('QDRANT_PATH', 'not set!')}")
    print(f" MODEL      = {os.environ.get('MODEL_NAME', 'FremyCompany/BioLORD-2023')}")
    print(f" Note: BioLORD scores lower than MiniLM — that is expected and correct!")
    print(SEPARATOR)

    scores = test_basic_search()
    test_multi_filter()
    test_aspect_analysis()
    test_drug_similarity()
    print_scorecard(scores)

    print(f"{SEPARATOR}\n")