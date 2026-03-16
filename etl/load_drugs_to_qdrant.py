"""
Load drug profiles into Qdrant for semantic search (e.g. test_qdrant_queries, demo_qdrant).

Builds a DRUG_CATALOG with name, class, and conditions, embeds via BioLORD,
and upserts into the drug_profiles collection.

Run from project root. If Qdrant is in Docker (default), no env needed:
    python -m etl.load_drugs_to_qdrant
For on-disk Qdrant: QDRANT_PATH=./qdrant_local python -m etl.load_drugs_to_qdrant
"""

import argparse
import os
import sys

# Ensure project root is on path when run as script
if __name__ == "__main__":
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)

# Drug catalog: list of dicts with name, drug_class, mechanism, conditions, side_effects.
# Covers drugs used in drug_alternatives.py (e.g. penicillin, digoxin, omeprazole) and common classes.
DRUG_CATALOG = [
    # Anticoagulants
    {"name": "warfarin", "drug_class": "anticoagulant", "mechanism": "vitamin K antagonist",
     "conditions": ["atrial fibrillation", "DVT", "PE", "thrombosis"], "side_effects": ["bleeding", "bruising"]},
    {"name": "apixaban", "drug_class": "anticoagulant", "mechanism": "factor Xa inhibitor",
     "conditions": ["atrial fibrillation", "DVT", "PE"], "side_effects": ["bleeding"]},
    {"name": "rivaroxaban", "drug_class": "anticoagulant", "mechanism": "factor Xa inhibitor",
     "conditions": ["atrial fibrillation", "DVT", "PE"], "side_effects": ["bleeding"]},
    {"name": "dabigatran", "drug_class": "anticoagulant", "mechanism": "direct thrombin inhibitor",
     "conditions": ["atrial fibrillation", "DVT", "PE"], "side_effects": ["bleeding", "dyspepsia"]},
    {"name": "heparin", "drug_class": "anticoagulant", "mechanism": "antithrombin activator",
     "conditions": ["DVT", "PE", "acute coronary syndrome"], "side_effects": ["bleeding", "HIT"]},
    # NSAIDs
    {"name": "ibuprofen", "drug_class": "NSAID", "mechanism": "COX inhibitor",
     "conditions": ["pain", "inflammation", "fever"], "side_effects": ["GI bleeding", "renal"]},
    {"name": "naproxen", "drug_class": "NSAID", "mechanism": "COX inhibitor",
     "conditions": ["pain", "inflammation", "arthritis"], "side_effects": ["GI", "cardiovascular"]},
    {"name": "celecoxib", "drug_class": "NSAID", "mechanism": "COX-2 selective inhibitor",
     "conditions": ["arthritis", "pain"], "side_effects": ["cardiovascular", "GI"]},
    {"name": "aspirin", "drug_class": "NSAID", "mechanism": "antiplatelet, COX inhibitor",
     "conditions": ["pain", "fever", "cardiovascular prevention"], "side_effects": ["bleeding", "GI"]},
    {"name": "paracetamol", "drug_class": "analgesic", "mechanism": "central COX inhibition",
     "conditions": ["pain", "fever"], "side_effects": ["hepatotoxicity in overdose"]},
    # SSRIs
    {"name": "sertraline", "drug_class": "SSRI", "mechanism": "serotonin reuptake inhibitor",
     "conditions": ["depression", "anxiety", "OCD", "PTSD"], "side_effects": ["nausea", "insomnia", "sexual"]},
    {"name": "fluoxetine", "drug_class": "SSRI", "mechanism": "serotonin reuptake inhibitor",
     "conditions": ["depression", "anxiety", "bulimia"], "side_effects": ["nausea", "insomnia"]},
    {"name": "citalopram", "drug_class": "SSRI", "mechanism": "serotonin reuptake inhibitor",
     "conditions": ["depression", "anxiety"], "side_effects": ["QT prolongation", "nausea"]},
    {"name": "escitalopram", "drug_class": "SSRI", "mechanism": "serotonin reuptake inhibitor",
     "conditions": ["depression", "anxiety"], "side_effects": ["nausea", "fatigue"]},
    # Antidiabetics
    {"name": "metformin", "drug_class": "antidiabetic", "mechanism": "biguanide, decreases hepatic glucose",
     "conditions": ["type 2 diabetes", "PCOS"], "side_effects": ["GI", "lactic acidosis rare"]},
    {"name": "insulin", "drug_class": "antidiabetic", "mechanism": "hormone, glucose uptake",
     "conditions": ["type 1 diabetes", "type 2 diabetes"], "side_effects": ["hypoglycemia", "weight gain"]},
    {"name": "glipizide", "drug_class": "sulfonylurea", "mechanism": "insulin secretion",
     "conditions": ["type 2 diabetes"], "side_effects": ["hypoglycemia", "weight gain"]},
    {"name": "sitagliptin", "drug_class": "DPP-4 inhibitor", "mechanism": "incretin enhancement",
     "conditions": ["type 2 diabetes"], "side_effects": ["pancreatitis rare", "nasopharyngitis"]},
    # Antibiotics
    {"name": "amoxicillin", "drug_class": "antibiotic", "mechanism": "beta-lactam, cell wall",
     "conditions": ["bacterial infection", "UTI", "respiratory"], "side_effects": ["allergy", "diarrhea"]},
    {"name": "penicillin", "drug_class": "antibiotic", "mechanism": "beta-lactam, cell wall",
     "conditions": ["bacterial infection", "strep", "syphilis"], "side_effects": ["allergy", "anaphylaxis"]},
    {"name": "azithromycin", "drug_class": "antibiotic", "mechanism": "macrolide, protein synthesis",
     "conditions": ["respiratory infection", "chlamydia"], "side_effects": ["GI", "QT prolongation"]},
    {"name": "ciprofloxacin", "drug_class": "fluoroquinolone", "mechanism": "DNA gyrase inhibitor",
     "conditions": ["UTI", "respiratory", "anthrax"], "side_effects": ["tendon rupture", "CNS"]},
    # ACEi / ARB / cardiovascular
    {"name": "lisinopril", "drug_class": "ACE inhibitor", "mechanism": "RAAS blockade",
     "conditions": ["hypertension", "heart failure", "post-MI"], "side_effects": ["cough", "hyperkalemia"]},
    {"name": "amlodipine", "drug_class": "calcium channel blocker", "mechanism": "vascular smooth muscle",
     "conditions": ["hypertension", "angina"], "side_effects": ["edema", "flushing"]},
    {"name": "losartan", "drug_class": "ARB", "mechanism": "angiotensin receptor blockade",
     "conditions": ["hypertension", "heart failure", "diabetic nephropathy"], "side_effects": ["hyperkalemia"]},
    {"name": "ramipril", "drug_class": "ACE inhibitor", "mechanism": "RAAS blockade",
     "conditions": ["hypertension", "heart failure", "CV prevention"], "side_effects": ["cough", "hyperkalemia"]},
    {"name": "digoxin", "drug_class": "cardiac glycoside", "mechanism": "Na/K-ATPase, inotrope",
     "conditions": ["heart failure", "atrial fibrillation"], "side_effects": ["arrhythmia", "nausea", "vision"]},
    # PPI
    {"name": "omeprazole", "drug_class": "proton pump inhibitor", "mechanism": "H+/K+ ATPase inhibitor",
     "conditions": ["GERD", "ulcer", "erosive esophagitis"], "side_effects": ["headache", "GI", "C.diff risk"]},
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Load drug profiles into Qdrant for semantic alternative search")
    parser.add_argument("--qdrant-path", default=os.getenv("QDRANT_PATH", ""),
                        help="Path for local Qdrant storage (default: env QDRANT_PATH)")
    parser.add_argument("--qdrant-host", default=os.getenv("QDRANT_HOST", "localhost"), help="Qdrant host")
    parser.add_argument("--qdrant-port", type=int, default=int(os.getenv("QDRANT_PORT", "6333")), help="Qdrant port")
    args = parser.parse_args()

    if args.qdrant_path:
        os.environ["QDRANT_PATH"] = args.qdrant_path
    if "QDRANT_PATH" not in os.environ and not args.qdrant_path:
        # Allow default host/port via env
        os.environ.setdefault("QDRANT_HOST", args.qdrant_host)
        os.environ.setdefault("QDRANT_PORT", str(args.qdrant_port))

    from db.qdrant_queries import load_drug_profiles

    n = load_drug_profiles(DRUG_CATALOG)
    print(f"Loaded {n} drug profiles into Qdrant collection 'drug_profiles'.")
    print("Run: python drug_alternatives.py for DrugBank/OpenFDA alternatives; or test_qdrant_queries.py / demo_qdrant.py for Qdrant demos.")


if __name__ == "__main__":
    main()
