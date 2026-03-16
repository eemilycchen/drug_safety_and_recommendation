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

try:
    from db.qdrant_queries import (
        find_similar_adverse_events_multi_filter,
        analyze_adverse_event_aspects,
        get_drug_faers_summary,
        compute_drug_similarity,
    )
    HAS_QDRANT = True
except Exception as e:
    HAS_QDRANT = False
    _QDRANT_ERR = str(e)

try:
    from drug_alternatives import get_alternatives
    HAS_ALTS = True
except Exception as e:
    HAS_ALTS = False
    _ALTS_ERR = str(e)


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


# ── Page: FAERS similarity (Qdrant) ───────────────────────────────────────
def page_qdrant_faers():
    st.header("FAERS similarity (Qdrant)")
    st.markdown(
        """
        This section searches **FAERS adverse event reports** stored in Qdrant.

        - Reports come from openFDA FAERS (loaded via `etl/load_faers_to_qdrant.py`)  
        - Each report is embedded with **BioLORD‑2023** and stored with payload fields:
          age, sex, drugs, reactions, outcome, serious flag, and raw text  
        - You provide a **patient summary** and **drug name**, and we find the most similar FAERS reports
        - You can optionally filter by **serious only**, **outcome type**, and **patient sex**

        Use this to answer questions like:

        > “Show me real FAERS cases similar to this patient on this drug, and summarize how severe they were.”
        """
    )
    if not HAS_QDRANT:
        st.error(f"Qdrant query module unavailable: {_QDRANT_ERR}")
        st.info("Ensure db/qdrant_queries.py and qdrant-client / sentence-transformers are installed.")
        return

    summary = st.text_area(
        "Patient summary (free text)",
        value="65 year old male on warfarin with gastrointestinal bleeding.",
        height=80,
    )
    drug = st.text_input("Drug name (generic or brand)", value="warfarin")
    serious_only = st.checkbox("Serious only (FAERS serious flag)", value=False)
    outcome = st.selectbox(
        "Outcome filter (optional)",
        ["", "death", "hospitalization", "life-threatening", "disability", "non-serious"],
    )
    sex = st.selectbox("Patient sex filter (optional)", ["", "male", "female"])
    top_k = st.slider("Number of similar FAERS reports (Top K)", min_value=3, max_value=20, value=10)

    if st.button("Search FAERS in Qdrant"):
        if not summary or not drug:
            st.warning("Enter both a patient summary and a drug name.")
            return
        try:
            results = find_similar_adverse_events_multi_filter(
                patient_summary=summary,
                drug_names=[drug],
                outcome=outcome or None,
                serious_only=serious_only,
                sex=sex or None,
                top_k=top_k,
            )
        except Exception as e:
            st.error(f"Qdrant search failed: {e}")
            return

        if not results:
            st.info("No matching FAERS reports found in Qdrant.")
            return

        st.subheader("Top similar FAERS reports")
        # Rename keys for nicer table headers
        table_rows = []
        for r in results:
            table_rows.append(
                {
                    "Age": r.get("patient_age"),
                    "Sex": r.get("patient_sex"),
                    "Drugs": ", ".join(r.get("drugs", [])),
                    "Reactions": ", ".join(r.get("reactions", [])),
                    "Outcome": r.get("outcome", ""),
                    "Serious": r.get("serious", False),
                    "Similarity": r.get("similarity_score"),
                    "Report text": r.get("raw_text", ""),
                }
            )
        st.dataframe(table_rows, use_container_width=True)

        st.subheader("Aspect-based summary (across these reports)")
        aspects = analyze_adverse_event_aspects(results)
        st.json(aspects)


# ── Page: Drug alternatives (DrugBank + NDC + FAERS) ──────────────────────
def page_drug_alternatives():
    st.header("Drug alternatives (DrugBank + NDC + FAERS)")
    st.markdown(
        """
        This section shows **same‑class alternatives** for a drug using:

        - **DrugBank cache** (`data/drugbank_alternatives.json`) as the primary source  
        - **NDC fallback** (`data/ndc_merge.json`) when DrugBank has fewer than the requested number of alternatives  
        - **BioLORD-2023** to measure how clinically similar each alternative is to the original drug  
        - Optional **FAERS safety annotation** from Qdrant (number of reports, % serious per alternative)

        Results are shown as a table so you can quickly scan:

        - which alternatives came from DrugBank vs NDC  
        - which ones have more FAERS evidence and lower % serious outcomes
        """
    )
    if not HAS_ALTS:
        st.error(f"Alternatives module unavailable: {_ALTS_ERR}")
        st.info("Ensure drug_alternatives.py is importable from the project root.")
        return

    drug = st.text_input("Drug name", value="warfarin")
    use_faers = st.checkbox("Use FAERS safety annotation (requires Qdrant)", value=False)
    min_count = st.slider("Target number of alternatives", min_value=3, max_value=15, value=10)

    if st.button("Find alternatives"):
        if not drug:
            st.warning("Enter a drug name.")
            return

        try:
            results, elapsed = get_alternatives(
                drug,
                local_lookup=None,
                min_count=min_count,
                return_sources=True,
                fetch_from_ndc=True,
            )
        except Exception as e:
            st.error(f"Alternatives error: {e}")
            return

        st.caption(f"Lookup time: {elapsed*1000:.1f} ms (DrugBank first; NDC only if needed)")

        rows = []
        for name, source in results:
            rows.append({"Alternative": name, "Source": source})

        if use_faers:
            if not HAS_QDRANT:
                st.warning("Qdrant not available; cannot fetch FAERS summaries.")
            else:
                for row in rows:
                    try:
                        summary = get_drug_faers_summary(row["alternative"], top_k=50)
                    except Exception:
                        summary = None
                    if summary:
                        row["FAERS reports"] = summary.get("total_reports", 0)
                        row["% serious (FAERS)"] = summary.get("pct_serious", 0) * 100
                    else:
                        row["FAERS reports"] = 0
                        row["% serious (FAERS)"] = None

        st.subheader("Alternatives")
        st.dataframe(rows, use_container_width=True)


# ── Page: Qdrant + Alternatives combined ───────────────────────────────────
def page_qdrant_and_alternatives():
    st.header("FAERS risk + alternatives (Qdrant + DrugBank/NDC)")
    st.markdown(
        """
        This view combines:

        - **FAERS risk (Qdrant)** — similar adverse event reports for a patient summary + drug  
        - **Drug alternatives (DrugBank + NDC)** — same-class alternatives ranked by BioLORD, optionally annotated with FAERS safety

        Use it to see **how risky a drug looks in FAERS** and what **nearby alternatives** exist in the same class.
        """
    )

    col1, col2 = st.columns(2)
    with col1:
        summary = st.text_area(
            "Patient summary (free text)",
            value=(
                "55 year old female with type 2 diabetes and hypertension taking metformin and lisinopril. "
                "Doctor proposes adding ibuprofen daily for chronic back pain."
            ),
            height=100,
        )
    with col2:
        drug = st.text_input("Drug name (generic or brand)", value="ibuprofen")

    serious_only = st.checkbox("Serious only (FAERS serious flag)", value=False)
    outcome = st.selectbox(
        "Outcome filter (optional)",
        ["", "death", "hospitalization", "life-threatening", "disability", "non-serious"],
    )
    sex = st.selectbox("Patient sex filter (optional)", ["", "male", "female"])
    top_k = st.slider("Number of similar FAERS reports (Top K)", min_value=3, max_value=20, value=10)

    st.markdown("---")

    col_left, col_right = st.columns(2)

    # Left: FAERS similarity
    with col_left:
        st.subheader("FAERS similarity (Qdrant)")
        if not HAS_QDRANT:
            st.error(f"Qdrant query module unavailable: {_QDRANT_ERR}")
            st.info("Ensure db/qdrant_queries.py and qdrant-client / sentence-transformers are installed.")
        elif st.button("Search FAERS in Qdrant"):
            if not summary or not drug:
                st.warning("Enter both a patient summary and a drug name.")
            else:
                try:
                    results = find_similar_adverse_events_multi_filter(
                        patient_summary=summary,
                        drug_names=[drug],
                        outcome=outcome or None,
                        serious_only=serious_only,
                        sex=sex or None,
                        top_k=top_k,
                    )
                except Exception as e:
                    st.error(f"Qdrant search failed: {e}")
                    results = []

                if results:
                    table_rows = []
                    for r in results:
                        reactions = r.get("reactions", []) or []
                        top_rx = reactions[:3]
                        if len(reactions) > 3:
                            top_rx.append("…")
                        table_rows.append(
                            {
                                "Similarity": r.get("similarity_score"),
                                "Outcome": r.get("outcome", ""),
                                "Serious": r.get("serious", False),
                                "Age": r.get("patient_age"),
                                "Sex": r.get("patient_sex"),
                                "Drugs": ", ".join(r.get("drugs", [])),
                                "Top reactions": ", ".join(top_rx),
                                "FAERS report_id": r.get("report_id", ""),
                            }
                        )
                    st.dataframe(table_rows, use_container_width=True)

                    # Simple BioLORD verdict based on similarity range
                    sims = [row["Similarity"] for row in table_rows if row["Similarity"] is not None]
                    if sims:
                        avg_sim = sum(sims) / len(sims)
                        min_sim = min(sims)
                        max_sim = max(sims)
                        st.caption(
                            f"BioLORD similarity range: min={min_sim:.3f}, max={max_sim:.3f}, avg={avg_sim:.3f}. "
                            "Values around 0.50–0.70 usually indicate same or very close clinical class; "
                            "values below ~0.20 indicate unrelated drugs/patterns."
                        )

                    # Serious outcome verdict
                    total = len(results)
                    serious_count = sum(1 for r in results if r.get("serious"))
                    if total:
                        pct_serious = serious_count / total
                        pct_display = pct_serious * 100
                        if pct_serious >= 0.5:
                            st.markdown(
                                f"**FAERS verdict:** Among the top {total} similar FAERS cases, "
                                f"**{pct_display:.0f}% involved serious outcomes** "
                                "(death, hospitalization, life-threatening, or disability). "
                                "It is recommended to **consider an alternative drug** and review the options on the right."
                            )
                        else:
                            st.markdown(
                                f"**FAERS verdict:** Among the top {total} similar FAERS cases, "
                                f"{pct_display:.0f}% involved serious outcomes. "
                                "Serious events appear less common in this neighborhood, "
                                "but alternatives may still be considered based on clinical judgment."
                            )

                    with st.expander("Aspect-based & case-level summary"):
                        aspects = analyze_adverse_event_aspects(results)
                        st.subheader("Aspect-based summary")
                        st.json(aspects)

                        st.subheader("Top 3 FAERS cases (detailed view)")
                        for idx, r in enumerate(results[:3], start=1):
                            rx = ", ".join(r.get("reactions", []) or [])
                            drugs_str = ", ".join(r.get("drugs", []) or [])
                            st.markdown(
                                f"**[{idx}] similarity={r.get('similarity_score', 0):.4f}**  \n"
                                f"  patient: {r.get('patient_age','?')}yr {r.get('patient_sex','?')}  \n"
                                f"  drugs: {drugs_str or '—'}  \n"
                                f"  reactions: {rx or '—'}  \n"
                                f"  outcome: {r.get('outcome','')} | serious: {r.get('serious')}"
                            )
                else:
                    st.info("No matching FAERS reports found in Qdrant.")

    # Right: alternatives
    with col_right:
        st.subheader("Drug alternatives (DrugBank + NDC + FAERS)")
        if not HAS_ALTS:
            st.error(f"Alternatives module unavailable: {_ALTS_ERR}")
            st.info("Ensure drug_alternatives.py is importable from the project root.")
        else:
            use_faers = st.checkbox("Use FAERS safety annotation (requires Qdrant)", value=False)
            min_count = st.slider("Target number of alternatives", min_value=3, max_value=15, value=10)

            if st.button("Find alternatives"):
                if not drug:
                    st.warning("Enter a drug name.")
                else:
                    try:
                        results, elapsed = get_alternatives(
                            drug,
                            local_lookup=None,
                            min_count=min_count,
                            return_sources=True,
                            fetch_from_ndc=True,
                        )
                    except Exception as e:
                        st.error(f"Alternatives error: {e}")
                        results = []

                    if results:
                        st.caption(f"Lookup time: {elapsed*1000:.1f} ms (DrugBank first; NDC only if needed)")
                        rows = []
                        for name, source in results:
                            try:
                                sim = compute_drug_similarity(drug, name)
                            except Exception:
                                sim = None
                            rows.append(
                                {
                                    "Alternative": name,
                                    "Source": source,
                                    "BioLORD similarity": sim,
                                }
                            )

                        # Rank by similarity (descending)
                        rows.sort(
                            key=lambda r: (r["BioLORD similarity"] is not None, r["BioLORD similarity"] or 0.0),
                            reverse=True,
                        )

                        if use_faers:
                            if not HAS_QDRANT:
                                st.warning("Qdrant not available; cannot fetch FAERS summaries.")
                            else:
                                for row in rows:
                                    try:
                                        summary_alt = get_drug_faers_summary(row["Alternative"], top_k=50)
                                    except Exception:
                                        summary_alt = None
                                    if summary_alt:
                                        row["FAERS reports"] = summary_alt.get("total_reports", 0)
                                        row["% serious (FAERS)"] = summary_alt.get("pct_serious", 0) * 100
                                    else:
                                        row["FAERS reports"] = 0
                                        row["% serious (FAERS)"] = None

                        st.dataframe(rows, use_container_width=True)
                        st.caption(
                            "Alternatives are **ranked by BioLORD similarity** to the original drug "
                            "(higher = more similar clinical class). "
                            "When FAERS annotation is enabled, you can also compare how often each alternative "
                            "appears in FAERS and what % of those reports were serious."
                        )
                    else:
                        st.info("No alternatives found.")

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
                        st.subheader("Safety check summary")
                        summary = {
                            "run_id": str(doc.get("_id", "")),
                            "patient_id": doc.get("inputs", {}).get("patient_id"),
                            "proposed_drug": doc.get("inputs", {}).get("proposed_drug"),
                            "created_at": doc.get("created_at", doc.get("timestamp")),
                        }
                        st.table([summary])

                        with st.expander("Full details"):
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
                    if docs:
                        rows = []
                        for d in docs:
                            patient = d.get("patient", {}) or {}
                            drugs = patient.get("drug") or []
                            drug_names = [(x.get("medicinalproduct") or "").lower() for x in drugs]
                            reactions = patient.get("reaction") or []
                            rx_names = [(x.get("reactionmeddrapt") or "").lower() for x in reactions]

                            rows.append(
                                {
                                    "safetyreportid": d.get("safetyreportid"),
                                    "receivedate": d.get("receivedate"),
                                    "serious": d.get("serious") == "1",
                                    "drug_count": len(drugs),
                                    "drugs": ", ".join(drug_names[:3]) + ("…" if len(drug_names) > 3 else ""),
                                    "reactions": ", ".join(rx_names[:3]) + ("…" if len(rx_names) > 3 else ""),
                                }
                            )

                        st.subheader("FAERS reports (summary)")
                        st.dataframe(rows, use_container_width=True)

                        with st.expander("Raw documents"):
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
    st.markdown(
        "Experience what each database can do: "
        "**PostgreSQL** (patients), **Neo4j** (interactions & side effects), "
        "**Qdrant** (FAERS similarity), **MongoDB** (evidence & audit), "
        "plus **DrugBank + NDC** alternatives."
    )

    page = st.sidebar.radio(
        "Section",
        [
            "Patient data (PostgreSQL)",
            "Drug knowledge (Neo4j)",
            "FAERS + alternatives (Qdrant + DrugBank/NDC)",
            "Evidence & audit (MongoDB)",
            "Full safety check",
        ],
    )

    if page == "Patient data (PostgreSQL)":
        page_patient_data()
    elif page == "Drug knowledge (Neo4j)":
        page_drug_knowledge()
    elif page == "FAERS + alternatives (Qdrant + DrugBank/NDC)":
        page_qdrant_and_alternatives()
    elif page == "Evidence & audit (MongoDB)":
        page_evidence_audit()
    else:
        page_full_safety_check()


if __name__ == "__main__":
    main()
