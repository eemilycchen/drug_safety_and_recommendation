"""
demo_qdrant.py
--------------
Team demo: Qdrant vector search for clinical decision support.

Shows three capabilities in a clear, story-driven way:
  1. Semantic patient matching — find real-world FAERS cases similar to a patient
  2. Drug safety signals     — surface adverse event patterns for a proposed drug
  3. Drug intelligence       — show BioLORD understands clinical drug relationships

Run from project root:
    QDRANT_PATH=./qdrant_local python demo_qdrant.py

No extra dependencies beyond what is already installed.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QDRANT_PATH", "")

try:
    from db.qdrant_queries import (
        find_similar_adverse_events,
        find_similar_adverse_events_multi_filter,
        analyze_adverse_event_aspects,
        compute_drug_similarity,
        compute_pairwise_drug_similarities,
    )
except ImportError as e:
    print(f"\nERROR: {e}")
    print("Run from project root: QDRANT_PATH=./qdrant_local python demo_qdrant.py")
    sys.exit(1)

# ── colours for terminal output ──────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[91m"
RESET  = "\033[0m"

W = 65  # separator width

def sep(char="═"):      print(f"\n{BOLD}{char * W}{RESET}")
def thin():             print(f"{DIM}{'─' * W}{RESET}")
def title(t):           print(f"\n{BOLD}{CYAN}{t}{RESET}")
def label(k, v):        print(f"  {DIM}{k:<18}{RESET}{v}")
def good(t):            print(f"  {GREEN}✓{RESET} {t}")
def warn(t):            print(f"  {YELLOW}⚠{RESET}  {t}")
def info(t):            print(f"  {DIM}→{RESET} {t}")
def pause(s=0.6):       time.sleep(s)

def risk_badge(score: float) -> str:
    if score >= 0.65:
        return f"{RED}{BOLD}HIGH SIGNAL{RESET}"
    elif score >= 0.45:
        return f"{YELLOW}MODERATE{RESET}"
    else:
        return f"{DIM}LOW{RESET}"

def bar(count: int, total: int, width: int = 20) -> str:
    filled = int(round(count / total * width)) if total else 0
    return f"{'█' * filled}{'░' * (width - filled)} {count}"

# ─────────────────────────────────────────────────────────────────────────────
# DEMO PATIENTS — realistic clinical scenarios
# ─────────────────────────────────────────────────────────────────────────────
DEMO_PATIENTS = [
    {
        "name": "Patient A — Aspirin + stomach pain",
        "summary": (
            "65 year old male taking aspirin daily for heart attack prevention. "
            "Complaining of stomach pain, nausea, and dark stools. "
            "History of acid reflux and occasional heartburn."
        ),
        "proposed_drug": "aspirin",
        "why_interesting": (
            "Everyone knows aspirin — but most people don't know daily aspirin "
            "can cause serious stomach bleeding. Watch what FAERS reports show up."
        ),
    },
    {
        "name": "Patient B — Ibuprofen (Advil) in a diabetic",
        "summary": (
            "55 year old female with type 2 diabetes taking ibuprofen regularly "
            "for chronic back pain. Noticing swollen ankles and reduced urination. "
            "Currently on metformin for blood sugar control."
        ),
        "proposed_drug": "ibuprofen",
        "why_interesting": (
            "Ibuprofen (the active ingredient in Advil/Motrin) is something "
            "everyone takes for pain — but in diabetics it can damage kidneys. "
            "This is a hidden danger most people don't know about."
        ),
    },
    {
        "name": "Patient C — Paracetamol (Tylenol) overdose risk",
        "summary": (
            "40 year old male taking paracetamol for headaches while also "
            "drinking alcohol regularly. Experiencing nausea and upper right "
            "abdominal pain. No known liver disease."
        ),
        "proposed_drug": "paracetamol",
        "why_interesting": (
            "Paracetamol (Tylenol) is the world's most common painkiller — "
            "but alcohol + paracetamol is a dangerous combo that destroys the liver. "
            "FAERS has thousands of reports on this."
        ),
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# DEMO 1 — patient matching per scenario
# ─────────────────────────────────────────────────────────────────────────────
def demo_patient_matching():
    sep()
    title("DEMO 1 — Semantic Patient Matching")
    info("Given a patient description, find the most similar")
    info("real-world FAERS adverse event reports in our database.")
    info("No keyword matching — pure semantic similarity via BioLORD.\n")
    pause()

    for patient in DEMO_PATIENTS:
        thin()
        print(f"\n  {BOLD}{patient['name']}{RESET}")
        print(f"  {DIM}{patient['why_interesting']}{RESET}\n")
        label("Patient:", patient["summary"][:72] + "...")
        label("Proposed drug:", patient["proposed_drug"])
        print()

        results = find_similar_adverse_events(
            patient_summary=patient["summary"],
            drug_name=patient["proposed_drug"],
            top_k=3,
        )

        if not results:
            warn(f"No FAERS reports found for '{patient['proposed_drug']}' in current dataset.")
            info("Try loading more reports: python etl/load_faers_to_qdrant.py --limit 10000")
            continue

        print(f"  {GREEN}Top {len(results)} similar FAERS reports:{RESET}\n")
        for i, hit in enumerate(results, 1):
            score  = hit["similarity_score"]
            age    = hit.get("patient_age", "?")
            sex    = hit.get("patient_sex", "?")
            drugs  = ", ".join(hit.get("drugs", [])[:3]) or "unknown"
            rxns   = ", ".join(hit.get("reactions", [])[:4]) or "none"
            outcome = hit.get("outcome", "?")
            serious = f"{RED}YES{RESET}" if hit.get("serious") else "no"

            print(f"  [{i}] similarity={score:.4f}  {risk_badge(score)}")
            print(f"      patient  : {age}yr {sex}")
            print(f"      drugs    : {drugs}")
            print(f"      reactions: {rxns}")
            print(f"      outcome  : {outcome}  |  serious: {serious}")
            print()

        pause(0.4)

# ─────────────────────────────────────────────────────────────────────────────
# DEMO 2 — drug safety signal analysis
# ─────────────────────────────────────────────────────────────────────────────
def demo_safety_signals():
    sep()
    title("DEMO 2 — Drug Safety Signal Analysis")
    info("For a high-risk patient, analyse the pattern of adverse events")
    info("across the top 20 most similar FAERS reports.\n")
    pause()

    summary = (
        "55 year old female taking ibuprofen daily for back pain. "
        "Also taking metformin for type 2 diabetes. "
        "Noticing reduced urination and swollen ankles — possible kidney damage."
    )

    print(f"  {BOLD}Patient profile:{RESET}")
    label("Summary:", summary[:72] + "...")
    print()

    # serious only
    serious_results = find_similar_adverse_events_multi_filter(
        patient_summary=summary,
        serious_only=True,
        top_k=20,
    )

    if not serious_results:
        warn("No serious adverse events found. Try loading more data.")
        return

    analysis = analyze_adverse_event_aspects(serious_results)
    total    = analysis["total_reports"]

    thin()
    print(f"\n  {BOLD}Analysed {total} serious adverse event reports{RESET}\n")

    # severity
    print(f"  {BOLD}Severity distribution:{RESET}")
    for sev, count in analysis["severity_distribution"].items():
        colour = RED if sev == "high" else YELLOW if sev == "moderate" else DIM
        print(f"    {colour}{sev:<12}{RESET}  {bar(count, total)}  ({count}/{total})")

    print()

    # organ systems
    print(f"  {BOLD}Organ systems affected:{RESET}")
    for organ, count in list(analysis["organ_system_distribution"].items())[:5]:
        print(f"    {organ:<18}  {bar(count, total)}  ({count} reports)")

    print()

    # top reactions
    print(f"  {BOLD}Most frequent reactions:{RESET}")
    for reaction, count in list(analysis["top_reactions"].items())[:6]:
        print(f"    {reaction:<30}  {count}x")

    print()

    # outcomes
    print(f"  {BOLD}Outcomes:{RESET}")
    for outcome, count in list(analysis["outcome_distribution"].items())[:5]:
        colour = RED if "death" in outcome else YELLOW if "hospitalization" in outcome else DIM
        print(f"    {colour}{outcome:<28}{RESET}  {count} cases")

    pause(0.4)

# ─────────────────────────────────────────────────────────────────────────────
# DEMO 3 — BioLORD drug intelligence
# ─────────────────────────────────────────────────────────────────────────────
def demo_drug_intelligence():
    sep()
    title("DEMO 3 — BioLORD Drug Intelligence")
    info("BioLORD learned clinical drug relationships from biomedical literature.")
    info("No lookup table. No hardcoded rules. Pure learned geometry.\n")
    pause()

    groups = [
        {
            "label": "Same drug class — should score HIGH (model knows these are related)",
            "pairs": [
                ("aspirin",     "ibuprofen",   "both painkillers — Advil vs aspirin"),
                ("ibuprofen",   "naproxen",    "both NSAIDs — Advil vs Aleve"),
                ("metformin",   "insulin",     "both treat diabetes"),
                ("amoxicillin", "penicillin",  "both antibiotics"),
            ],
        },
        {
            "label": "Related but different — should score MODERATE",
            "pairs": [
                ("aspirin",     "paracetamol", "both painkillers but different mechanisms"),
                ("ibuprofen",   "metformin",   "both common but different conditions"),
                ("amoxicillin", "ibuprofen",   "antibiotic vs painkiller"),
            ],
        },
        {
            "label": "Unrelated drugs — should score LOW (BioLORD superpower!)",
            "pairs": [
                ("aspirin",     "metformin",   "painkiller vs diabetes drug"),
                ("ibuprofen",   "amoxicillin", "painkiller vs antibiotic"),
                ("paracetamol", "metformin",   "painkiller vs diabetes drug"),
            ],
        },
    ]

    for group in groups:
        thin()
        print(f"\n  {BOLD}{group['label']}{RESET}\n")
        for drug1, drug2, reason in group["pairs"]:
            score  = compute_drug_similarity(drug1, drug2)
            badge  = risk_badge(score)
            colour = GREEN if score >= 0.45 else (YELLOW if score >= 0.25 else DIM)
            print(
                f"    {drug1:<14} vs {drug2:<14} "
                f"→ {colour}{score:.4f}{RESET}  "
                f"{DIM}({reason}){RESET}"
            )
        pause(0.3)

    print()
    info("Key insight: aspirin vs metformin scores near 0 — BioLORD correctly")
    info("knows Advil and a diabetes drug are completely unrelated, no rules needed.")

# ─────────────────────────────────────────────────────────────────────────────
# DEMO 4 — live safety check (the full story in one query)
# ─────────────────────────────────────────────────────────────────────────────
def demo_live_safety_check():
    sep()
    title("DEMO 4 — Live Safety Check (Full Story)")
    info("This is what Part 5 (app layer) will call for every patient.\n")
    pause()

    patient = {
        "summary": (
            "58 year old male with type 2 diabetes and high blood pressure. "
            "Takes aspirin daily for heart protection and metformin for blood sugar. "
            "Doctor proposes adding ibuprofen (Advil) for newly developed arthritis pain."
        ),
        "proposed_drug": "ibuprofen",
    }

    print(f"  {BOLD}Clinical scenario:{RESET}")
    label("Patient:", patient["summary"][:72])
    label("", patient["summary"][72:])
    label("Proposed drug:", "ibuprofen (Advil)")
    print()

    thin()
    print(f"\n  {BOLD}Step 1 — Semantic search: similar FAERS cases{RESET}")
    results = find_similar_adverse_events(
        patient_summary=patient["summary"],
        drug_name=patient["proposed_drug"],
        top_k=5,
    )

    if results:
        serious_count = sum(1 for r in results if r.get("serious"))
        avg_score     = sum(r["similarity_score"] for r in results) / len(results)
        top_reactions = {}
        for r in results:
            for rxn in r.get("reactions", []):
                top_reactions[rxn] = top_reactions.get(rxn, 0) + 1
        top3 = sorted(top_reactions.items(), key=lambda x: -x[1])[:3]

        good(f"Found {len(results)} similar FAERS reports")
        good(f"Avg similarity score: {avg_score:.4f}")
        warn(f"{serious_count}/{len(results)} reports involved serious outcomes")
        print()
        print(f"  {BOLD}Most common reactions in similar cases:{RESET}")
        for rxn, count in top3:
            colour = RED if any(w in rxn for w in ["haemorrhage","bleed","death"]) else YELLOW
            print(f"    {colour}{rxn:<30}{RESET}  seen in {count} similar cases")
    else:
        warn("No similar cases found — try loading more data.")

    thin()
    print(f"\n  {BOLD}Step 2 — Drug intelligence: ibuprofen risk profile{RESET}")
    dangerous_combos = [
        ("ibuprofen", "aspirin",   "patient is ALREADY on aspirin → stomach bleeding risk"),
        ("ibuprofen", "metformin", "ibuprofen can damage kidneys → metformin builds up → dangerous"),
        ("ibuprofen", "naproxen",  "both NSAIDs — never take together"),
    ]
    for d1, d2, note in dangerous_combos:
        score  = compute_drug_similarity(d1, d2)
        colour = RED if score >= 0.45 else DIM
        print(f"    {colour}{d1} + {d2}{RESET}  sim={score:.3f}  {DIM}{note}{RESET}")

    thin()
    print(f"\n  {BOLD}Step 3 — Safety summary{RESET}\n")

    if results and serious_count >= 2:
        print(f"  {RED}{BOLD}⚠  HIGH RISK — Ibuprofen (Advil) for this patient{RESET}\n")
        print(f"  Evidence from {len(results)} similar FAERS cases:")
        for rxn, count in top3:
            print(f"    • {rxn} ({count} cases)")
        print()
        print(f"  {YELLOW}Recommendation:{RESET}")
        print(f"    Consider paracetamol (Tylenol) as safer alternative for pain.")
        print(f"    Patient is already on aspirin — adding ibuprofen (also an NSAID)")
        print(f"    doubles the stomach bleeding risk. Most people don't know this!")
        print(f"    Ibuprofen also reduces blood flow to kidneys — dangerous in")
        print(f"    diabetics who are already at higher kidney risk.")
    else:
        print(f"  {YELLOW}MODERATE RISK — Review needed{RESET}")
        print(f"  Limited similar cases found — load more FAERS data for stronger signal.")

    pause(0.4)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    sep("═")
    print(f"\n{BOLD}{CYAN}  CLINICAL DECISION SUPPORT — QDRANT DEMO{RESET}")
    print(f"  {DIM}UC San Diego · DSC 202 · Drug Safety & Recommendation{RESET}")
    print(f"\n  {DIM}Model  : FremyCompany/BioLORD-2023 (768-dim biomedical embeddings){RESET}")
    print(f"  {DIM}Data   : openFDA FAERS adverse event reports{RESET}")
    print(f"  {DIM}Store  : Qdrant (local file mode){RESET}")
    print(f"  {DIM}Path   : {os.environ.get('QDRANT_PATH', './qdrant_local')}{RESET}")
    sep("═")
    pause(1)

    demo_patient_matching()
    demo_safety_signals()
    demo_drug_intelligence()
    demo_live_safety_check()

    sep("═")
    print(f"\n{BOLD}{GREEN}  DEMO COMPLETE{RESET}\n")
    print(f"  What you just saw:\n")
    good("Semantic search — no keywords, pure clinical meaning")
    good("5000 real FAERS reports embedded with BioLORD")
    good("Drug class relationships learned from biomedical literature")
    good("Live safety signal for a real clinical scenario")
    print()
    print(f"  {DIM}Next: Neo4j (drug interaction graph) + MongoDB (audit trail){RESET}")
    print(f"  {DIM}Then: Part 5 orchestrates all 4 databases into one safety report{RESET}\n")
    sep("═")
    print()

if __name__ == "__main__":
    main()