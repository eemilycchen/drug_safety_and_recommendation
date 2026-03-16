"""
View FAERS raw sample (data/faers_raw.json) in a readable format.

Run from project root: python scripts/view_faers_sample.py
"""

import json
import sys
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent.parent
    path = root / "data" / "faers_raw.json"
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    with open(path) as f:
        reports = json.load(f)

    if not isinstance(reports, list):
        reports = [reports]

    print(f"Total reports: {len(reports)}\n")
    print("=" * 60)

    for i, r in enumerate(reports, 1):
        print(f"\n--- Report {i} ---")
        print(f"  Report ID:    {r.get('safetyreportid', '?')}")
        print(f"  Receive date: {r.get('receivedate', '?')}")
        print(f"  Serious:      {r.get('serious') == '1'}")
        outcome = []
        if r.get("seriousnessdeath") == "1": outcome.append("death")
        if r.get("seriousnesshospitalization") == "1": outcome.append("hospitalization")
        if r.get("seriousnesslifethreatening") == "1": outcome.append("life-threatening")
        if r.get("seriousnessdisabling") == "1": outcome.append("disability")
        if outcome:
            print(f"  Outcome:      {', '.join(outcome)}")

        patient = r.get("patient") or {}
        age = patient.get("patientonsetage")
        unit = patient.get("patientonsetageunit")
        if age is not None:
            u = "years" if unit == "801" else "?"
            print(f"  Patient age:  {age} {u}")
        sex = patient.get("patientsex")
        if sex == "1": print(f"  Patient sex:  male")
        elif sex == "2": print(f"  Patient sex:  female")

        drugs = patient.get("drug") or []
        print(f"  Drugs ({len(drugs)}):")
        for d in drugs:
            name = d.get("medicinalproduct") or d.get("openfda", {}).get("generic_name", ["?"])[0]
            ind = d.get("drugindication", "")
            print(f"    - {name}" + (f"  (indication: {ind})" if ind else ""))

        reactions = patient.get("reaction") or []
        print(f"  Reactions ({len(reactions)}):")
        for reac in reactions:
            term = reac.get("reactionmeddrapt", "?")
            print(f"    - {term}")

    print("\n" + "=" * 60)
    print("\nFull JSON (pretty) saved to: data/faers_sample_pretty.json")
    out = root / "data" / "faers_sample_pretty.json"
    with open(out, "w") as f:
        json.dump(reports, f, indent=2)
    print(f"  Open {out} to view raw structure.\n")

if __name__ == "__main__":
    main()
