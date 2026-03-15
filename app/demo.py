"""
Streamlit demo: Drug Safety & Recommendation
=============================================
Single demo for all databases: PostgreSQL (patients), Neo4j (interactions),
Qdrant (similar adverse events), MongoDB (evidence & audit).
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

try:
    from db import qdrant_queries
    HAS_QDRANT = True
except Exception as e:
    HAS_QDRANT = False
    _QDRANT_ERR = str(e)


# ── Config (env or defaults) ─────────────────────────────────────────────
def _pg_url():
    return os.getenv("PG_URL", "postgresql://postgres:postgres@localhost:5432/drug_safety")


def _neo4j_kw():
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "password"),
    }


# ── Sample data for immediate use (no DB required for drug/summary defaults) ──
SAMPLE_PATIENT_SUMMARY = (
    "65 year old male, type 2 diabetes and hypertension, on metformin and aspirin."
)
SAMPLE_DRUGS = ["Warfarin", "Aspirin", "Metformin", "Ibuprofen", "Lisinopril", "Amlodipine"]


def _get_sample_patients():
    """Load first N patients from PostgreSQL once per session for dropdowns."""
    if "sample_patients" not in st.session_state:
        st.session_state.sample_patients = []
        if HAS_PG:
            try:
                st.session_state.sample_patients = pg_queries.list_patients(
                    limit=15, db_url=_pg_url()
                )
            except Exception:
                pass
    return st.session_state.sample_patients


def _patient_summary_from_profile(profile: dict) -> str:
    """Build a short text summary from get_patient_profile() for Qdrant similarity search."""
    p = profile.get("patient", {})
    age = p.get("birthdate", "Unknown age")
    if age and age != "Unknown age":
        try:
            from datetime import date
            birth = date.fromisoformat(age[:10]) if isinstance(age, str) else age
            age = (date.today() - birth).days // 365
        except Exception:
            age = "Unknown age"
    gender = p.get("gender", "unknown")
    conditions = profile.get("conditions", [])
    cond_str = ", ".join(
        c.get("description", c.get("code", "")) if isinstance(c, dict) else str(c)
        for c in (conditions or [])
    ) or "none"
    meds = profile.get("active_medications", [])
    med_str = ", ".join(
        m.get("description", m.get("code", "")) if isinstance(m, dict) else str(m)
        for m in (meds or [])
    ) or "none"
    return (
        f"Patient: {age} year old {gender}. "
        f"Conditions: {cond_str}. "
        f"Medications: {med_str}."
    )


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


def _render_network_graph(net: dict, height: int = 420) -> None:
    """Render nodes/edges from get_interaction_network as an interactive pyvis graph."""
    nodes = net.get("nodes") or []
    edges = net.get("edges") or []
    if not nodes and not edges:
        return
    try:
        from pyvis.network import Network
        g = Network(height=f"{height}px", width="100%", directed=False)
        for n in nodes:
            name = n.get("name") or str(n)
            g.add_node(name, label=name, title=name)
        seen_edges = set()
        for e in edges:
            src, tgt = e.get("source"), e.get("target")
            if not src or not tgt:
                continue
            key = (min(src, tgt), max(src, tgt))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            g.add_edge(src, tgt, title=e.get("description") or "interaction")
        html = g.generate_html()
        st.components.v1.html(html, height=height + 20, scrolling=False)
    except ImportError:
        st.caption("Install `pyvis` for graph visualization: pip install pyvis")


# ── Page: Drug knowledge (Neo4j) ───────────────────────────────────────────
def page_drug_knowledge():
    st.header("Drug knowledge (Neo4j)")
    st.caption("Load sample graph data, then explore interactions, side effects, and visualize the network.")
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
            "Load sample graph data",
            "Check interactions (current meds vs proposed drug)",
            "Side effects for a drug",
            "Interaction path between two drugs",
            "Shared side effects (two drugs)",
            "Safer alternatives",
            "Interaction network (neighborhood)",
        ],
        key="neo4j_sub",
    )

    if "Load sample graph data" in sub:
        st.markdown("Create a small **sample graph** in Neo4j (Drugs, SideEffects, INTERACTS_WITH, HAS_SIDE_EFFECT) so you can try the queries and visualization without running the full SIDER ETL.")
        if st.button("Load sample graph into Neo4j", key="neo4j_seed_btn"):
            try:
                counts = neo4j_queries.seed_sample_graph(**conn)
                st.success(
                    f"Loaded sample graph: **{counts.get('drugs', 0)}** drugs, "
                    f"**{counts.get('side_effects', 0)}** side effects, "
                    f"**{counts.get('interactions', 0)}** interactions, "
                    f"**{counts.get('side_effect_links', 0)}** drug–side-effect links."
                )
                st.caption("Try **Interaction network (neighborhood)** below to visualize the graph.")
            except Exception as e:
                st.error(f"Neo4j: {e}")

    if "Check interactions" in sub:
        current_meds = st.text_area(
            "Current medications (one per line or comma-separated)",
            value="Aspirin",
            key="neo4j_check_meds",
        )
        proposed = st.selectbox("Proposed drug", SAMPLE_DRUGS, index=0, key="neo4j_check_proposed")
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
        drug_a = st.selectbox("Drug A", SAMPLE_DRUGS, index=1, key="path_a")
        drug_b = st.selectbox("Drug B", SAMPLE_DRUGS, index=3, key="path_b")
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
        drug_a = st.selectbox("Drug A", SAMPLE_DRUGS, index=1, key="shared_a")
        drug_b = st.selectbox("Drug B", SAMPLE_DRUGS, index=3, key="shared_b")
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
        proposed = st.selectbox("Proposed drug", SAMPLE_DRUGS, index=0, key="alt_proposed")
        current_meds = st.text_area(
            "Current medications (one per line or comma-separated)",
            value="Aspirin",
            key="alt_meds",
        )
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
        drug = st.selectbox("Drug name", SAMPLE_DRUGS, index=0, key="net_drug")
        depth = st.slider("Depth (hops)", 1, 4, 2, key="net_depth")
        if st.button("Get network"):
            if drug:
                try:
                    net = neo4j_queries.get_interaction_network(drug, depth=depth, **conn)
                    st.write(f"Nodes: {len(net['nodes'])}, Edges: {len(net['edges'])}")
                    if net["nodes"] or net["edges"]:
                        st.markdown("**Graph visualization** (drag nodes to rearrange; hover edges for description)")
                        _render_network_graph(net)
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
    st.caption("Load sample FAERS reports, log safety checks, retrieve by run_id, or fetch reports by ID.")
    if not HAS_MONGO:
        st.error(f"MongoDB module unavailable: {_MONGO_ERR}")
        return

    sub = st.radio(
        "Action",
        [
            "Load sample FAERS reports",
            "Log a safety check",
            "Retrieve safety check by run_id",
            "Fetch FAERS reports by IDs",
        ],
        key="mongo_sub",
    )

    if "Load sample FAERS reports" in sub:
        st.markdown("Fetch adverse event reports from the **openFDA** API and store them in MongoDB (raw + normalized). Use a small limit for a quick demo.")
        limit = st.number_input(
            "Number of reports to load",
            min_value=10,
            max_value=500,
            value=25,
            step=10,
            key="mongo_load_limit",
        )
        if st.button("Load reports into MongoDB", key="mongo_load_btn"):
            try:
                from etl.load_faers_to_mongo import load_faers_to_mongo
            except ImportError as e:
                st.error(f"Could not import ETL: {e}")
            else:
                mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
                db_name = os.getenv("MONGO_DB", "drug_safety")
                with st.spinner(f"Fetching up to {limit} reports from openFDA and writing to MongoDB…"):
                    raw_count, norm_count = load_faers_to_mongo(
                        mongo_uri=mongo_uri,
                        db_name=db_name,
                        max_reports=limit,
                        dry_run=False,
                    )
                st.success(f"Loaded **{raw_count}** raw and **{norm_count}** normalized reports into MongoDB.")
                # Show a few sample IDs so user can try "Fetch FAERS reports by IDs"
                if raw_count > 0 and HAS_MONGO:
                    try:
                        from pymongo import MongoClient
                        client = MongoClient(mongo_uri)
                        cursor = client[db_name]["faers_raw"].find({}, {"_id": 1}).limit(5)
                        sample_ids = [str(doc["_id"]) for doc in cursor]
                        if sample_ids:
                            st.session_state.mongo_sample_report_ids = sample_ids
                            st.caption("Sample report IDs to try in **Fetch FAERS reports by IDs**:")
                            st.code(" ".join(sample_ids))
                    except Exception:
                        pass

    elif "Log a safety check" in sub:
        sample_patients = _get_sample_patients()
        if sample_patients:
            labels = [
                f"{p.get('last_name', '')}, {p.get('first_name', '')} ({p['id'][:8]}…)"
                for p in sample_patients
            ]
            choice = st.selectbox(
                "Patient (sample)",
                range(len(sample_patients)),
                format_func=lambda i: labels[i],
                key="log_patient_choice",
            )
            patient_id = sample_patients[choice]["id"]
        else:
            patient_id = st.text_input("Patient ID", key="log_patient", placeholder="Patient UUID")
        proposed_drug = st.selectbox("Proposed drug", SAMPLE_DRUGS, index=0, key="log_drug")
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
        default_ids = "\n".join(st.session_state.get("mongo_sample_report_ids", []))
        ids_text = st.text_area(
            "FAERS report IDs (safetyreportid), one per line or comma-separated",
            value=default_ids,
            placeholder="Paste IDs from a Qdrant search, or load reports above first.",
            key="mongo_fetch_ids",
        )
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


# ── Page: Similar adverse events (Qdrant + MongoDB) ───────────────────────
def page_similar_adverse_events():
    st.header("Similar adverse events (Qdrant)")
    st.caption("Find FAERS reports similar to a patient on a drug. Evidence is fetched from MongoDB.")
    if not HAS_QDRANT:
        st.error(f"Qdrant module unavailable: {_QDRANT_ERR}")
        return

    use_patient_id = st.radio(
        "Input",
        ["By patient ID (use PostgreSQL profile)", "By free-text summary"],
        key="qdrant_input",
    )

    patient_summary = ""
    if "patient ID" in use_patient_id:
        if not HAS_PG:
            st.warning("PostgreSQL is required to load profile by patient ID.")
        else:
            sample_patients = _get_sample_patients()
            if sample_patients:
                labels = [
                    f"{p.get('last_name', '')}, {p.get('first_name', '')} ({p['id'][:8]}…)"
                    for p in sample_patients
                ]
                choice = st.selectbox(
                    "Patient (sample data)",
                    range(len(sample_patients)),
                    format_func=lambda i: labels[i],
                    key="qdrant_patient_choice",
                )
                patient_id = sample_patients[choice]["id"]
            else:
                patient_id = st.text_input("Patient ID", key="qdrant_patient_id", placeholder="Patient UUID")
            if patient_id:
                try:
                    profile = pg_queries.get_patient_profile(patient_id, db_url=_pg_url())
                    patient_summary = _patient_summary_from_profile(profile)
                    st.text_area("Patient summary (from PostgreSQL)", value=patient_summary, height=100, disabled=True, key="qdrant_summary_from_pg")
                except Exception as e:
                    st.error(f"Could not load profile: {e}")
    else:
        patient_summary = st.text_area(
            "Patient summary (sample below — edit as needed)",
            value=SAMPLE_PATIENT_SUMMARY,
            key="qdrant_summary_free",
            height=100,
        )

    proposed_drug = st.selectbox(
        "Proposed drug (sample options)",
        [d.lower() for d in SAMPLE_DRUGS],
        index=0,
        key="qdrant_drug",
    )
    top_k = st.slider("Number of similar reports", 3, 20, 5, key="qdrant_topk")
    fetch_evidence = st.checkbox("Fetch full evidence from MongoDB for these reports", value=True, key="qdrant_fetch_evidence")

    if not st.button("Find similar adverse events", key="qdrant_btn"):
        return

    if not patient_summary or not proposed_drug:
        st.warning("Enter both a patient summary and a proposed drug.")
        return

    try:
        results = qdrant_queries.find_similar_adverse_events(
            patient_summary, proposed_drug, top_k=top_k
        )
    except Exception as e:
        st.error(f"Qdrant: {e}")
        return

    if not results:
        st.info("No similar adverse events found. Load FAERS into Qdrant first (see README).")
        return

    st.subheader("Similar reports (from Qdrant)")
    display_cols = ["report_id", "similarity_score", "reactions", "outcome", "serious"]
    rows = [{k: r.get(k) for k in display_cols if k in r} for r in results]
    st.dataframe(rows, use_container_width=True)

    if fetch_evidence and HAS_MONGO:
        report_ids = [r.get("report_id") for r in results if r.get("report_id")]
        if report_ids:
            try:
                docs = mongo_queries.get_faers_reports_by_ids(report_ids, raw=True)
                st.subheader("Full evidence from MongoDB")
                st.caption(f"Fetched {len(docs)} of {len(report_ids)} reports from MongoDB.")
                for d in docs:
                    with st.expander(f"Report {d.get('safetyreportid', d.get('_id', '?'))}"):
                        st.json(d)
            except Exception as e:
                st.error(f"MongoDB: {e}")


# ── Page: Full safety check ───────────────────────────────────────────────
def page_full_safety_check():
    st.header("Full safety check")
    st.caption("Pick a patient and a proposed drug. Uses PostgreSQL → Neo4j → Qdrant (similar events) → MongoDB (evidence & audit).")

    sample_patients = _get_sample_patients()
    if sample_patients:
        labels = [
            f"{p.get('last_name', '')}, {p.get('first_name', '')} ({p['id'][:8]}…)"
            for p in sample_patients
        ]
        choice = st.selectbox(
            "Patient (sample data from your database)",
            range(len(sample_patients)),
            format_func=lambda i: labels[i],
            key="full_check_patient_choice",
        )
        patient_id = sample_patients[choice]["id"]
    else:
        patient_id = st.text_input(
            "Patient ID",
            placeholder="e.g. patient UUID from PostgreSQL",
            key="full_check_patient_id",
        )

    proposed_drug = st.selectbox(
        "Proposed drug (sample options)",
        SAMPLE_DRUGS,
        index=0,
        key="full_check_drug",
    )
    include_qdrant = st.checkbox("Include similar adverse events (Qdrant)", value=True)
    log_to_mongo = st.checkbox("Log this check to MongoDB (audit)", value=True)

    if not st.button("Run safety check"):
        return

    run_outputs = {}
    run_inputs = {"patient_id": patient_id, "proposed_drug": proposed_drug}
    profile = None

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

    # 3) Similar adverse events (Qdrant) and evidence from MongoDB
    if include_qdrant and HAS_QDRANT and proposed_drug:
        patient_summary = ""
        if profile:
            patient_summary = _patient_summary_from_profile(profile)
        else:
            patient_summary = f"Patient on proposed drug {proposed_drug}."
        try:
            similar = qdrant_queries.find_similar_adverse_events(
                patient_summary, proposed_drug, top_k=5
            )
            run_outputs["similar_adverse_events"] = [
                {"report_id": r.get("report_id"), "similarity_score": r.get("similarity_score"), "reactions": r.get("reactions"), "outcome": r.get("outcome")}
                for r in similar
            ]
            if similar and HAS_MONGO:
                ids = [r.get("report_id") for r in similar if r.get("report_id")]
                if ids:
                    evidence = mongo_queries.get_faers_reports_by_ids(ids, raw=True)
                    run_outputs["similar_events_evidence_count"] = len(evidence)
        except Exception as e:
            st.error(f"Qdrant: {e}")

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

    if run_outputs.get("similar_adverse_events"):
        st.subheader("Similar adverse events (Qdrant)")
        st.dataframe(run_outputs["similar_adverse_events"], use_container_width=True)
        if run_outputs.get("similar_events_evidence_count") is not None:
            st.caption(f"Fetched {run_outputs['similar_events_evidence_count']} full report(s) from MongoDB.")

    # 4) Log to MongoDB
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
    st.markdown("Single demo: **PostgreSQL** (patients), **Neo4j** (interactions & side effects), **Qdrant** (similar adverse events), **MongoDB** (evidence & audit).")
    st.info("**Sample data ready** — Each section starts with example patients and drugs you can run immediately. Use the dropdowns or enter your own.")

    page = st.sidebar.radio(
        "Section",
        [
            "Patient data (PostgreSQL)",
            "Drug knowledge (Neo4j)",
            "Similar adverse events (Qdrant)",
            "Evidence & audit (MongoDB)",
            "Full safety check",
        ],
    )

    if page == "Patient data (PostgreSQL)":
        page_patient_data()
    elif page == "Drug knowledge (Neo4j)":
        page_drug_knowledge()
    elif page == "Similar adverse events (Qdrant)":
        page_similar_adverse_events()
    elif page == "Evidence & audit (MongoDB)":
        page_evidence_audit()
    else:
        page_full_safety_check()


if __name__ == "__main__":
    main()
