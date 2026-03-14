"""
Streamlit demo: Drug Safety & Recommendation
=============================================
Experience what each database can do — patient data (PostgreSQL),
drug interactions & side effects (Neo4j), and evidence/audit (MongoDB).
Run from project root: streamlit run app/demo.py
"""

from __future__ import annotations

import os
import sys

# Ensure project root is on path when running streamlit run app/demo.py
if __name__ == "__main__" or "streamlit" in sys.modules:
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)

import streamlit as st

# Optional DB imports — we catch and show friendly messages if missing
try:
    from db import pg_queries
    HAS_PG = True
except Exception as e:
    HAS_PG = False
    _PG_ERR = str(e)

try:
    from db import neo4j_queries
    HAS_NEO4J = True
except Exception as e:
    HAS_NEO4J = False
    _NEO4J_ERR = str(e)

try:
    from db import mongo_queries
    HAS_MONGO = True
except Exception as e:
    HAS_MONGO = False
    _MONGO_ERR = str(e)


# ── Config (env or defaults) ─────────────────────────────────────────────
def _pg_url():
    return os.getenv("PG_URL", "postgresql://postgres:postgres@localhost:5432/drug_safety")


def _neo4j_kw():
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "password"),
    }


# ── Page: Patient data (PostgreSQL) ───────────────────────────────────────
def page_patient_data():
    st.header("Patient data (PostgreSQL)")
    st.caption("Synthea EHR-like data: demographics, medications, conditions, allergies, timeline.")
    if not HAS_PG:
        st.error(f"PostgreSQL module unavailable: {_PG_ERR}")
        return

    limit = st.sidebar.number_input("Patients to list", min_value=5, max_value=100, value=20)
    try:
        patients = pg_queries.list_patients(limit=limit, db_url=_pg_url())
    except Exception as e:
        st.error(f"Cannot connect to PostgreSQL: {e}")
        st.info("Set PG_URL if your database is elsewhere.")
        return

    if not patients:
        st.warning("No patients in the database. Load Synthea data first (see README).")
        return

    ids = [p["id"] for p in patients]
    labels = [f"{p.get('last_name','')}, {p.get('first_name','')} ({p['id'][:8]}…)" for p in patients]
    choice = st.selectbox("Select a patient", range(len(ids)), format_func=lambda i: labels[i])
    patient_id = ids[choice]

    tab_profile, tab_meds, tab_history, tab_timeline = st.tabs([
        "Profile", "Active medications", "Medication history", "Timeline",
    ])

    with tab_profile:
        try:
            profile = pg_queries.get_patient_profile(patient_id, db_url=_pg_url())
        except ValueError as e:
            st.warning(str(e))
        except Exception as e:
            st.error(str(e))
        else:
            p = profile["patient"]
            st.subheader("Demographics")
            st.json({
                "id": p.get("id"), "first_name": p.get("first_name"), "last_name": p.get("last_name"),
                "birthdate": p.get("birthdate"), "gender": p.get("gender"), "race": p.get("race"),
            })
            st.subheader("Active conditions")
            st.dataframe(profile["conditions"] if profile["conditions"] else [{"message": "None"}], use_container_width=True)
            st.subheader("Allergies")
            st.dataframe(profile["allergies"] if profile["allergies"] else [{"message": "None"}], use_container_width=True)
            st.subheader("Recent observations (last 20)")
            st.dataframe(profile["recent_observations"] if profile["recent_observations"] else [{"message": "None"}], use_container_width=True)

    with tab_meds:
        try:
            meds = pg_queries.get_active_medications(patient_id, db_url=_pg_url())
        except Exception as e:
            st.error(str(e))
        else:
            st.dataframe(meds if meds else [{"message": "No active medications"}], use_container_width=True)

    with tab_history:
        years = st.number_input("Years back (optional)", min_value=0, value=0, key="years")
        limit_h = st.number_input("Max records", min_value=10, value=50, key="limit_h")
        try:
            history = pg_queries.get_medication_history(
                patient_id, db_url=_pg_url(),
                limit=limit_h,
                years_back=years if years else None,
            )
        except Exception as e:
            st.error(str(e))
        else:
            st.dataframe(history if history else [{"message": "No history"}], use_container_width=True)

    with tab_timeline:
        event_types = st.multiselect(
            "Event types",
            ["medication", "condition", "encounter", "procedure"],
            default=["medication", "condition"],
            key="event_types",
        )
        try:
            timeline = pg_queries.get_patient_timeline(
                patient_id, db_url=_pg_url(),
                event_types=event_types or None,
            )
        except Exception as e:
            st.error(str(e))
        else:
            if timeline:
                validation = pg_queries.validate_timeline_consistency(timeline)
                st.caption(f"Validation: valid={validation['valid']}, events={validation['event_count']}")
            st.dataframe(timeline if timeline else [{"message": "No events"}], use_container_width=True)


# ── Page: Drug knowledge (Neo4j) ───────────────────────────────────────────
def page_drug_knowledge():
    st.header("Drug knowledge (Neo4j)")
    st.caption("Interactions, side effects, paths, shared effects, safer alternatives, graph stats.")
    if not HAS_NEO4J:
        st.error(f"Neo4j module unavailable: {_NEO4J_ERR}")
        return

    conn = _neo4j_kw()
    try:
        stats = neo4j_queries.get_drug_stats(**conn)
    except Exception as e:
        st.error(f"Cannot connect to Neo4j: {e}")
        st.info("Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD if needed.")
        return

    st.subheader("Graph statistics")
    st.json(stats)

    sub = st.radio(
        "What to run",
        [
            "Check interactions (current meds vs proposed drug)",
            "Side effects for a drug",
            "Interaction path between two drugs",
            "Shared side effects (two drugs)",
            "Safer alternatives",
            "Interaction network (neighborhood)",
        ],
        key="neo4j_sub",
    )

    if "Check interactions" in sub:
        current_meds = st.text_area("Current medications (one per line or comma-separated)")
        proposed = st.text_input("Proposed drug")
        if st.button("Check interactions"):
            meds = [m.strip() for m in current_meds.replace(",", "\n").split() if m.strip()]
            if not proposed:
                st.warning("Enter a proposed drug.")
            else:
                try:
                    out = neo4j_queries.check_interactions(meds, proposed, **conn)
                    if out:
                        st.dataframe(out, use_container_width=True)
                    else:
                        st.success("No interactions found between current meds and the proposed drug.")
                except Exception as e:
                    st.error(str(e))

    elif "Side effects" in sub:
        drug = st.text_input("Drug name", key="se_drug", value="Warfarin")
        if st.button("Get side effects"):
            try:
                out = neo4j_queries.get_side_effects(drug, **conn)
                if out:
                    st.dataframe(out, use_container_width=True)
                else:
                    st.info("No side effects found for this drug.")
            except Exception as e:
                st.error(str(e))

    elif "Interaction path" in sub:
        drug_a = st.text_input("Drug A", key="path_a")
        drug_b = st.text_input("Drug B", key="path_b")
        max_hops = st.slider("Max hops", 1, 5, 3)
        if st.button("Find path"):
            if drug_a and drug_b:
                try:
                    out = neo4j_queries.find_interaction_path(drug_a, drug_b, max_hops=max_hops, **conn)
                    if out:
                        for p in out:
                            st.write(" → ".join(p["path_drugs"]), f"(length: {p['path_length']})")
                    else:
                        st.info("No path found.")
                except Exception as e:
                    st.error(str(e))
            else:
                st.warning("Enter both drugs.")

    elif "Shared side effects" in sub:
        drug_a = st.text_input("Drug A", key="shared_a")
        drug_b = st.text_input("Drug B", key="shared_b")
        if st.button("Find shared side effects"):
            if drug_a and drug_b:
                try:
                    out = neo4j_queries.find_shared_side_effects(drug_a, drug_b, **conn)
                    if out:
                        st.dataframe(out, use_container_width=True)
                    else:
                        st.info("No shared side effects.")
                except Exception as e:
                    st.error(str(e))
            else:
                st.warning("Enter both drugs.")

    elif "Safer alternatives" in sub:
        proposed = st.text_input("Proposed drug", key="alt_proposed")
        current_meds = st.text_area("Current medications (one per line or comma-separated)", key="alt_meds")
        if st.button("Find safer alternatives"):
            meds = [m.strip() for m in current_meds.replace(",", "\n").split() if m.strip()]
            if not proposed:
                st.warning("Enter the proposed drug.")
            else:
                try:
                    out = neo4j_queries.find_safer_alternatives(proposed, meds, **conn)
                    if out:
                        st.dataframe(out, use_container_width=True)
                    else:
                        st.info("No alternatives found.")
                except Exception as e:
                    st.error(str(e))

    elif "Interaction network" in sub:
        drug = st.text_input("Drug name", key="net_drug")
        depth = st.slider("Depth (hops)", 1, 4, 2, key="net_depth")
        if st.button("Get network"):
            if drug:
                try:
                    net = neo4j_queries.get_interaction_network(drug, depth=depth, **conn)
                    st.write(f"Nodes: {len(net['nodes'])}, Edges: {len(net['edges'])}")
                    if net["nodes"]:
                        st.dataframe(net["nodes"], use_container_width=True)
                    if net["edges"]:
                        st.dataframe(net["edges"], use_container_width=True)
                except Exception as e:
                    st.error(str(e))
            else:
                st.warning("Enter a drug name.")


# ── Page: Evidence & audit (MongoDB) ──────────────────────────────────────
def page_evidence_audit():
    st.header("Evidence & audit (MongoDB)")
    st.caption("Log safety checks and retrieve them by run_id. Optionally fetch FAERS reports by ID.")
    if not HAS_MONGO:
        st.error(f"MongoDB module unavailable: {_MONGO_ERR}")
        return

    sub = st.radio(
        "Action",
        ["Log a safety check", "Retrieve safety check by run_id", "Fetch FAERS reports by IDs"],
        key="mongo_sub",
    )

    if "Log a safety check" in sub:
        patient_id = st.text_input("Patient ID", key="log_patient")
        proposed_drug = st.text_input("Proposed drug", key="log_drug")
        notes = st.text_area("Notes / summary (optional)", key="log_notes")
        if st.button("Log run"):
            run = {
                "inputs": {"patient_id": patient_id or None, "proposed_drug": proposed_drug or None},
                "outputs": {},
                "notes": notes or None,
            }
            try:
                run_id = mongo_queries.log_safety_check(run)
                st.success(f"Logged. Run ID: `{run_id}`")
            except Exception as e:
                st.error(str(e))

    elif "Retrieve safety check" in sub:
        run_id = st.text_input("Run ID (UUID)")
        if st.button("Retrieve"):
            if run_id:
                try:
                    doc = mongo_queries.get_safety_check(run_id)
                    if doc:
                        st.json(doc)
                    else:
                        st.warning("No record found for this run_id.")
                except Exception as e:
                    st.error(str(e))
            else:
                st.warning("Enter a run_id.")

    elif "Fetch FAERS" in sub:
        ids_text = st.text_area("FAERS report IDs (safetyreportid), one per line or comma-separated")
        raw = st.checkbox("Raw documents (else normalized)", value=True)
        if st.button("Fetch FAERS reports"):
            ids_list = [x.strip() for x in ids_text.replace(",", "\n").split() if x.strip()]
            if not ids_list:
                st.warning("Enter at least one ID.")
            else:
                try:
                    docs = mongo_queries.get_faers_reports_by_ids(ids_list, raw=raw)
                    st.write(f"Found {len(docs)} of {len(ids_list)} reports.")
                    for d in docs:
                        st.json(d)
                except Exception as e:
                    st.error(str(e))


# ── Page: Full safety check ───────────────────────────────────────────────
def page_full_safety_check():
    st.header("Full safety check")
    st.caption("Pick a patient and a proposed drug. We use PostgreSQL + Neo4j and optionally log to MongoDB.")
    patient_id = st.text_input("Patient ID")
    proposed_drug = st.text_input("Proposed drug")
    log_to_mongo = st.checkbox("Log this check to MongoDB (audit)", value=True)

    if not st.button("Run safety check"):
        return

    run_outputs = {}
    run_inputs = {"patient_id": patient_id, "proposed_drug": proposed_drug}

    # 1) Patient profile and current meds (PG)
    if HAS_PG and patient_id:
        try:
            profile = pg_queries.get_patient_profile(patient_id, db_url=_pg_url())
            run_outputs["patient_profile"] = {
                "name": f"{profile['patient'].get('first_name')} {profile['patient'].get('last_name')}",
                "active_medications_count": len(profile["active_medications"]),
                "conditions_count": len(profile["conditions"]),
                "allergies_count": len(profile["allergies"]),
            }
            current_med_names = [m.get("description") or m.get("code", "") for m in profile["active_medications"]]
        except Exception as e:
            st.error(f"PostgreSQL: {e}")
            current_med_names = []
    else:
        current_med_names = []
        if not patient_id:
            st.warning("Enter a patient ID to use PostgreSQL.")
        elif not HAS_PG:
            st.warning("PostgreSQL module not available.")

    # 2) Interactions and side effects (Neo4j)
    if HAS_NEO4J and proposed_drug:
        try:
            interactions = neo4j_queries.check_interactions(current_med_names, proposed_drug, **_neo4j_kw())
            side_effects = neo4j_queries.get_side_effects(proposed_drug, **_neo4j_kw())
            run_outputs["interactions"] = interactions
            run_outputs["side_effects_count"] = len(side_effects)
            run_outputs["side_effects_sample"] = side_effects[:15]
        except Exception as e:
            st.error(f"Neo4j: {e}")
    elif not proposed_drug:
        st.warning("Enter a proposed drug to check interactions and side effects.")

    # Show report
    st.subheader("Report")
    st.json({"inputs": run_inputs, "outputs": run_outputs})

    if run_outputs.get("interactions"):
        st.warning("⚠️ Interactions found between current medications and the proposed drug.")
        st.dataframe(run_outputs["interactions"], use_container_width=True)
    else:
        st.success("No drug–drug interactions found for the proposed drug vs current meds.")

    if run_outputs.get("side_effects_sample"):
        st.subheader("Side effects (sample)")
        st.dataframe(run_outputs["side_effects_sample"], use_container_width=True)

    # 3) Log to MongoDB
    if log_to_mongo and HAS_MONGO:
        try:
            run_id = mongo_queries.log_safety_check({"inputs": run_inputs, "outputs": run_outputs})
            st.success(f"Audit log saved. Run ID: `{run_id}`")
        except Exception as e:
            st.error(f"MongoDB: {e}")


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="Drug Safety & Recommendation",
        page_icon="💊",
        layout="wide",
    )
    st.title("Drug Safety & Recommendation")
    st.markdown("Experience what each database can do: **PostgreSQL** (patients), **Neo4j** (interactions & side effects), **MongoDB** (evidence & audit).")

    page = st.sidebar.radio(
        "Section",
        [
            "Patient data (PostgreSQL)",
            "Drug knowledge (Neo4j)",
            "Evidence & audit (MongoDB)",
            "Full safety check",
        ],
    )

    if page == "Patient data (PostgreSQL)":
        page_patient_data()
    elif page == "Drug knowledge (Neo4j)":
        page_drug_knowledge()
    elif page == "Evidence & audit (MongoDB)":
        page_evidence_audit()
    else:
        page_full_safety_check()


if __name__ == "__main__":
    main()
