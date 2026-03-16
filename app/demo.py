"""
Streamlit demo: Drug Safety & Recommendation
=============================================
Sections:
  1. Patient data            — PostgreSQL (Synthea EHR)
  2. Drug knowledge          — Neo4j (interactions & side effects)
  3. FAERS risk + alternatives — Qdrant (BioLORD similarity) + DrugBank/NDC
  4. Evidence & audit        — MongoDB (FAERS evidence + audit log)
  5. Full safety check       — all databases orchestrated together

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

# Load .env so PG_URL (port 5433), MONGO_URI, NEO4J_* etc. are always set
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

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
    return os.getenv("PG_URL", "postgresql://postgres:postgres@127.0.0.1:5433/drug_safety")


def _neo4j_kw():
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "password"),
    }


def _extract_drug_name(description: str) -> str:
    """Strip dose/form info from Synthea medication descriptions for Neo4j name matching.

    Synthea uses RxNorm verbose strings like:
      '24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet'
    Neo4j (DrugBank) uses clean names like: 'Metformin hydrochloride'

    Strategy: drop leading time-release prefix (e.g. '24 HR'), then take text
    before the first dose quantity (any digit sequence followed by a unit).
    """
    import re
    desc = description.strip()
    # Strip leading dose-frequency prefix: "24 HR", "12 HR" etc.
    desc = re.sub(r'^\d+\s+HR\s+', '', desc, flags=re.IGNORECASE)
    # Take text before first dose quantity (digit + space + unit like MG, ML, MCG, UNT)
    match = re.match(r'^(.*?)\s+\d', desc)
    return match.group(1).strip() if match else desc


# ── Page: Patient data (PostgreSQL) ───────────────────────────────────────
def _clean_name(name: str) -> str:
    """Strip Synthea's trailing numeric suffix from generated names (e.g. 'Cyril535' → 'Cyril')."""
    import re
    return re.sub(r'\d+$', '', name).strip()


def _age(birthdate) -> int:
    from datetime import date
    today = date.today()
    bd = birthdate if hasattr(birthdate, 'year') else date.fromisoformat(str(birthdate))
    return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))


def _fmt_date(val) -> str:
    """Return just the date portion of a timestamp string or date object."""
    if val is None:
        return ""
    s = str(val)
    return s[:10]  # "YYYY-MM-DD"


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

    def _label(p):
        first = _clean_name(p.get("first_name", ""))
        last  = _clean_name(p.get("last_name", ""))
        gender = p.get("gender", "?")
        try:
            age = _age(p["birthdate"])
            age_str = f"age {age}"
        except Exception:
            age_str = str(p.get("birthdate", ""))
        return f"{first} {last}  —  {gender}, {age_str}"

    ids = [p["id"] for p in patients]
    labels = [_label(p) for p in patients]
    choice = st.selectbox("Select a patient", range(len(ids)), format_func=lambda i: labels[i])
    patient_id = ids[choice]

    tab_profile, tab_meds, tab_history, tab_timeline, tab_trends = st.tabs([
        "Profile", "Active medications", "Medication history", "Timeline", "Analytics",
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
            # ── Demographics card ──────────────────────────────────────
            first = _clean_name(p.get("first_name", ""))
            last  = _clean_name(p.get("last_name", ""))
            st.subheader(f"{first} {last}")
            c1, c2, c3 = st.columns(3)
            c1.metric("Date of birth", _fmt_date(p.get("birthdate")))
            try:
                c2.metric("Age", _age(p["birthdate"]))
            except Exception:
                c2.metric("Age", "—")
            c3.metric("Gender", "Male" if p.get("gender") == "M" else "Female" if p.get("gender") == "F" else p.get("gender", "—"))

            # ── Conditions ────────────────────────────────────────────
            st.subheader("Active conditions")
            if profile["conditions"]:
                cond_rows = [
                    {"Condition": r.get("description", r.get("code", "")),
                     "Since": _fmt_date(r.get("start_date"))}
                    for r in profile["conditions"]
                ]
                st.dataframe(cond_rows, width="stretch")
            else:
                st.info("No active conditions on record.")

            # ── Allergies ─────────────────────────────────────────────
            st.subheader("Allergies")
            if profile["allergies"]:
                allergy_rows = [
                    {"Allergy": r.get("description", r.get("code", "")),
                     "Since": _fmt_date(r.get("start_date"))}
                    for r in profile["allergies"]
                ]
                st.dataframe(allergy_rows, width="stretch")
            else:
                st.info("No known allergies.")

            # ── Recent observations ───────────────────────────────────
            st.subheader("Recent observations (last 20)")
            if profile["recent_observations"]:
                obs_rows = [
                    {"Observation": r.get("description", r.get("code", "")),
                     "Value": f"{r.get('value', '')} {r.get('units', '')}".strip(),
                     "Date": _fmt_date(r.get("obs_date"))}
                    for r in profile["recent_observations"]
                ]
                st.dataframe(obs_rows, width="stretch")
            else:
                st.info("No recent observations.")

    with tab_meds:
        try:
            meds = pg_queries.get_active_medications(patient_id, db_url=_pg_url())
        except Exception as e:
            st.error(str(e))
        else:
            if meds:
                med_rows = [
                    {"Medication": _extract_drug_name(r.get("description", r.get("code", ""))),
                     "Full name": r.get("description", ""),
                     "Since": _fmt_date(r.get("start_ts")),
                     "Reason": r.get("reasondescription") or r.get("reasoncode", "")}
                    for r in meds
                ]
                st.dataframe(med_rows, width="stretch")
            else:
                st.info("No active medications.")

    with tab_history:
        col_y, col_n = st.columns(2)
        years = col_y.number_input("Years back (0 = all)", min_value=0, value=0, key="years")
        limit_h = col_n.number_input("Max records", min_value=10, value=50, key="limit_h")
        try:
            history = pg_queries.get_medication_history(
                patient_id, db_url=_pg_url(),
                limit=limit_h,
                years_back=years if years else None,
            )
        except Exception as e:
            st.error(str(e))
        else:
            if history:
                hist_rows = [
                    {"Medication": _extract_drug_name(r.get("description", r.get("code", ""))),
                     "Start": _fmt_date(r.get("start_ts")),
                     "Stop": _fmt_date(r.get("stop_ts")) or "Present",
                     "Reason": r.get("reasondescription") or ""}
                    for r in history
                ]
                st.dataframe(hist_rows, width="stretch")
            else:
                st.info("No medication history found.")

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
                st.caption(f"{validation['event_count']} events — valid={validation['valid']}")
                tl_rows = [
                    {"Date": _fmt_date(r.get("date")),
                     "Type": (r.get("type") or "").title(),
                     "Description": r.get("description", "")}
                    for r in timeline
                ]
                st.dataframe(tl_rows, width="stretch")
            else:
                st.info("No timeline events for the selected types.")

    # ── Tab: Analytics ────────────────────────────────────────────────────
    with tab_trends:
        import pandas as pd

        st.caption(
            "PostgreSQL window functions — rolling averages, cumulative totals, LAG comparisons. "
            "**Best demo patients:** Lola Abernathy (107 visits, 16 conditions, 164 meds) "
            "or Laine Abbott (34 visits, 14 meds)."
        )

        section = st.radio(
            "View",
            ["Lab trends", "Procedure costs", "Medication burden", "Condition accumulation"],
            horizontal=True,
            key="analytics_section",
        )
        st.divider()

        # ── Lab trends ────────────────────────────────────────────────
        if section == "Lab trends":
            try:
                obs_codes = pg_queries.list_observation_codes(patient_id, min_readings=3, db_url=_pg_url())
            except Exception as e:
                st.error(str(e))
                obs_codes = []

            if not obs_codes:
                st.info("No clinical lab observations found for this patient. Try selecting Lola Abernathy or Laine Abbott for richer lab data.")
            else:
                col_sel, col_win = st.columns([3, 1])
                code_labels = [f"{c['description']} ({c['readings']} readings)" for c in obs_codes]
                code_choice = col_sel.selectbox("Observation", range(len(obs_codes)), format_func=lambda i: code_labels[i], key="trend_code")
                window = col_win.number_input("Rolling window", min_value=2, max_value=10, value=3, key="trend_window")

                selected = obs_codes[code_choice]
                try:
                    trends = pg_queries.get_observation_trends(patient_id, selected["code"], window=window, db_url=_pg_url())
                except Exception as e:
                    st.error(str(e))
                    trends = []

                if trends:
                    units = trends[0].get("units") or ""
                    label = f"{selected['description']}{' (' + units + ')' if units else ''}"

                    df = pd.DataFrame(trends)
                    df["obs_date"] = pd.to_datetime(df["obs_date"])
                    df = df.set_index("obs_date")

                    latest = trends[-1]
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Latest value", f"{latest['value']:.2f}" + (f" {units}" if units else ""))
                    m2.metric(f"Rolling avg (n={window})", f"{latest['rolling_avg']:.2f}" if latest["rolling_avg"] is not None else "—")
                    m3.metric("Min (window)", f"{latest['rolling_min']:.2f}" if latest["rolling_min"] is not None else "—")
                    m4.metric("Max (window)", f"{latest['rolling_max']:.2f}" if latest["rolling_max"] is not None else "—")

                    st.subheader(label)
                    st.line_chart(df[["value", "rolling_avg"]].rename(columns={
                        "value": "Raw value", "rolling_avg": f"Rolling avg (n={window})"
                    }))
                    st.subheader("Change from previous reading")
                    st.bar_chart(df[["change_from_prev"]].dropna().rename(columns={"change_from_prev": "Δ value"}))

                    with st.expander("Full data table"):
                        st.dataframe([{
                            "Date": str(r["obs_date"])[:10],
                            "Value": round(r["value"], 3),
                            f"Rolling avg (n={window})": round(r["rolling_avg"], 3) if r["rolling_avg"] is not None else None,
                            "Min (window)": round(r["rolling_min"], 3) if r["rolling_min"] is not None else None,
                            "Max (window)": round(r["rolling_max"], 3) if r["rolling_max"] is not None else None,
                            "Δ from prev": round(r["change_from_prev"], 3) if r["change_from_prev"] is not None else None,
                        } for r in trends], width="stretch")

        # ── Procedure costs ───────────────────────────────────────────
        elif section == "Procedure costs":
            st.markdown(
                "Each procedure has a varying cost — from routine checkups (~$130) to "
                "specialist procedures (~$13,000). Window functions compute running totals and "
                "rolling averages over each patient's procedure history."
            )
            win_c = st.number_input("Rolling window (procedures)", min_value=2, max_value=20, value=5, key="cost_window")
            try:
                ct = pg_queries.get_procedure_costs(patient_id, rolling_window=win_c, db_url=_pg_url())
            except Exception as e:
                st.error(str(e))
                ct = []

            if not ct:
                st.info("No procedure records for this patient.")
            else:
                last = ct[-1]
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Total procedure spend", f"${last['cumulative_cost']:,.0f}")
                m2.metric(f"Rolling avg (n={win_c})", f"${last['rolling_avg_cost']:,.0f}" if last["rolling_avg_cost"] is not None else "—")
                m3.metric("Procedures", len(ct))
                costs = [r["cost"] for r in ct]
                m4.metric("Most expensive", f"${max(costs):,.0f}")

                df_ct = pd.DataFrame(ct)
                df_ct["proc_date"] = pd.to_datetime(df_ct["proc_date"])
                df_ct = df_ct.set_index("proc_date")

                st.subheader("Cumulative procedure spend over time")
                st.area_chart(df_ct[["cumulative_cost"]].rename(columns={"cumulative_cost": "Cumulative spend ($)"}))

                st.subheader(f"Per-procedure cost vs rolling average (n={win_c})")
                st.line_chart(df_ct[["cost", "rolling_avg_cost"]].rename(columns={
                    "cost": "Procedure cost ($)", "rolling_avg_cost": f"Rolling avg ($)"
                }))

                st.subheader("Cost jump from previous procedure")
                st.bar_chart(df_ct[["cost_change"]].dropna().rename(columns={"cost_change": "Δ cost ($)"}))

                with st.expander("Full procedure table"):
                    st.dataframe([{
                        "Date": str(r["proc_date"])[:10],
                        "Procedure": r["procedure"],
                        "Cost ($)": round(r["cost"], 2),
                        "Cumulative ($)": round(r["cumulative_cost"], 2),
                        f"Rolling avg (n={win_c}) ($)": round(r["rolling_avg_cost"], 2) if r["rolling_avg_cost"] is not None else None,
                        "Δ from prev ($)": round(r["cost_change"], 2) if r["cost_change"] is not None else None,
                    } for r in ct], width="stretch")

        # ── Medication burden ─────────────────────────────────────────
        elif section == "Medication burden":
            try:
                mb = pg_queries.get_medication_burden(patient_id, db_url=_pg_url())
            except Exception as e:
                st.error(str(e))
                mb = []

            if not mb:
                st.info("No medication data for this patient.")
            else:
                m1, m2, m3 = st.columns(3)
                m1.metric("Total medications", mb[-1]["cumulative_meds"])
                active = sum(1 for r in mb if r["stop_date"] == "Present")
                m2.metric("Currently active", active)
                gaps = [r["days_since_last_med"] for r in mb if r["days_since_last_med"] is not None]
                m3.metric("Avg days between prescriptions", f"{sum(gaps)//len(gaps)}" if gaps else "—")

                df_mb = pd.DataFrame(mb)
                df_mb["start_date"] = pd.to_datetime(df_mb["start_date"])
                df_mb = df_mb.set_index("start_date")

                st.subheader("Cumulative medications prescribed over time")
                st.line_chart(df_mb[["cumulative_meds"]].rename(columns={"cumulative_meds": "Medications (cumulative)"}))

                st.subheader("Days between consecutive prescriptions")
                gap_df = df_mb[["days_since_last_med"]].dropna().rename(columns={"days_since_last_med": "Days gap"})
                st.bar_chart(gap_df)

                with st.expander("Full medication list"):
                    st.dataframe([{
                        "Start": str(r["start_date"])[:10],
                        "Stop": r["stop_date"],
                        "Medication": _extract_drug_name(r["medication"]),
                        "Reason": r["reason"] or "—",
                        "# so far": r["cumulative_meds"],
                        "Days since prev Rx": r["days_since_last_med"],
                    } for r in mb], width="stretch")

        # ── Condition accumulation ────────────────────────────────────
        elif section == "Condition accumulation":
            try:
                ca = pg_queries.get_condition_accumulation(patient_id, db_url=_pg_url())
            except Exception as e:
                st.error(str(e))
                ca = []

            if not ca:
                st.info("No condition data for this patient.")
            else:
                m1, m2, m3 = st.columns(3)
                m1.metric("Total diagnoses", ca[-1]["cumulative_conditions"])
                still_active = sum(1 for r in ca if not r.get("stop_date"))
                m2.metric("Still active", still_active)
                gaps = [r["days_since_last_dx"] for r in ca if r["days_since_last_dx"] is not None]
                m3.metric("Avg days between diagnoses", f"{sum(gaps)//len(gaps)}" if gaps else "—")

                df_ca = pd.DataFrame(ca)
                df_ca["start_date"] = pd.to_datetime(df_ca["start_date"])
                df_ca = df_ca.set_index("start_date")

                st.subheader("Chronic disease burden accumulation over time")
                st.area_chart(df_ca[["cumulative_conditions"]].rename(columns={"cumulative_conditions": "Active diagnoses (cumulative)"}))

                st.subheader("Days between diagnoses")
                dx_gap_df = df_ca[["days_since_last_dx"]].dropna().rename(columns={"days_since_last_dx": "Days gap"})
                st.bar_chart(dx_gap_df)

                with st.expander("Full condition list"):
                    st.dataframe([{
                        "Diagnosed": str(r["start_date"])[:10],
                        "Condition": r["condition"],
                        "Resolved": str(r["stop_date"])[:10] if r["stop_date"] else "Ongoing",
                        "# diagnoses so far": r["cumulative_conditions"],
                        "Days since prev dx": r["days_since_last_dx"],
                    } for r in ca], width="stretch")


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
                        st.dataframe(out, width="stretch")
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
                    st.dataframe(out, width="stretch")
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
                        st.dataframe(out, width="stretch")
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
                        st.dataframe(out, width="stretch")
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
                        st.dataframe(net["nodes"], width="stretch")
                    if net["edges"]:
                        st.dataframe(net["edges"], width="stretch")
                except Exception as e:
                    st.error(str(e))
            else:
                st.warning("Enter a drug name.")


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
                    st.dataframe(table_rows, width="stretch")

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

                        st.dataframe(rows, width="stretch")
                        st.caption(
                            "Alternatives are **ranked by BioLORD similarity** to the original drug "
                            "(higher = more similar clinical class). "
                            "When FAERS annotation is enabled, you can also compare how often each alternative "
                            "appears in FAERS and what % of those reports were serious."
                        )
                    else:
                        st.info("No alternatives found.")

# ── Page: Evidence & audit (MongoDB) ──────────────────────────────────────

# Well-known FAERS IDs pre-seeded for demo (loaded from MongoDB)
_DEMO_FAERS_IDS = [
    "5801206-7",   # DURAGESIC-100 / overdose
    "10003300",    # BONIVA / GI reactions
    "10003301",    # IBUPROFEN / renal impairment
    "10003304",    # DOXYCYCLINE + TRAMADOL / hypersensitivity
    "10003305",    # LIPITOR + LISINOPRIL / hypersensitivity
]

_DEMO_DRUGS = [
    "Warfarin", "Ibuprofen", "Metformin", "Lipitor", "Lisinopril", "Aspirin",
    "Amoxicillin", "Atorvastatin", "Omeprazole", "Hydrochlorothiazide",
    "Metoprolol", "Losartan", "Gabapentin", "Sertraline",
]
_DEMO_NOTE_TEMPLATES = [
    "No major interactions found. Safe to prescribe.",
    "Monitor renal function closely.",
    "Interacts with existing {drug2} regimen — consider alternative.",
    "Contraindicated with patient's history of {cond}. Proceed with caution.",
    "Low risk profile. Follow standard dosing.",
    "Flagged for potential GI side effects.",
    "Drug-drug interaction detected with {drug2}. Review dosage.",
    "Allergy history reviewed — clear to prescribe.",
    "Borderline risk. Recommend 30-day follow-up.",
]
_DEMO_CONDITIONS = ["hypertension", "diabetes", "renal impairment", "liver disease", "heart failure"]


def _fmt_faers_date(val: str | None) -> str:
    """Convert '20080707' → '2008-07-07'."""
    if not val:
        return ""
    s = str(val).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _seed_demo_audit() -> None:
    """Insert 20 randomised audit records if the collection is empty."""
    import random
    from datetime import datetime, timezone, timedelta
    try:
        existing = mongo_queries.list_safety_checks(limit=1)
        if existing:
            return
        # Pull patient names from PG if available
        patient_names: list[str] = []
        if HAS_PG:
            try:
                pts = pg_queries.list_patients(limit=100, db_url=_pg_url())
                patient_names = [
                    f"{_clean_name(p.get('first_name',''))} {_clean_name(p.get('last_name',''))}"
                    for p in pts
                ]
            except Exception:
                pass
        if not patient_names:
            patient_names = ["Demo Patient A", "Demo Patient B", "Demo Patient C"]

        rng = random.Random(42)
        risks = ["LOW", "LOW", "LOW", "MODERATE", "MODERATE", "HIGH", "UNKNOWN"]
        for _ in range(20):
            drug  = rng.choice(_DEMO_DRUGS)
            drug2 = rng.choice([d for d in _DEMO_DRUGS if d != drug])
            risk  = rng.choice(risks)
            n_int = {"HIGH": rng.randint(1, 4), "MODERATE": rng.randint(1, 2),
                     "LOW": 0, "UNKNOWN": rng.randint(0, 2)}[risk]
            note  = (rng.choice(_DEMO_NOTE_TEMPLATES)
                     .replace("{drug2}", drug2)
                     .replace("{cond}", rng.choice(_DEMO_CONDITIONS)))
            ts = (datetime.now(timezone.utc)
                  - timedelta(days=rng.randint(0, 30), hours=rng.randint(0, 23)))
            mongo_queries.log_safety_check({
                "inputs": {"patient_name": rng.choice(patient_names), "proposed_drug": drug},
                "outputs": {"risk_level": risk, "interactions_found": n_int},
                "notes": note,
                "timestamp": ts.isoformat(),
            })
    except Exception:
        pass


def page_evidence_audit():
    st.header("Evidence & audit (MongoDB)")
    st.caption(
        f"201,000 FAERS adverse-event reports (raw + normalised). "
        "FAERS tab: browse real reports. Audit tab: log and review safety-check runs."
    )
    if not HAS_MONGO:
        st.error(f"MongoDB module unavailable: {_MONGO_ERR}")
        return

    tab_faers, tab_audit = st.tabs(["FAERS Evidence", "Audit Log"])

    # ── Tab 1: FAERS Evidence ─────────────────────────────────────────────
    with tab_faers:
        st.markdown(
            "Browse adverse-event reports from the **FDA FAERS** database. "
            "Reports are stored as raw JSON (API shape) and a normalised summary."
        )

        # Pre-populate with known good IDs
        default_ids = "\n".join(_DEMO_FAERS_IDS)
        ids_text = st.text_area(
            "Report IDs (safetyreportid) — one per line or comma-separated",
            value=default_ids,
            height=120,
        )

        col_l, col_r = st.columns([2, 1])
        with col_l:
            view_mode = st.radio(
                "View mode",
                ["Normalised (summary)", "Raw (full API document)"],
                horizontal=True,
                key="faers_mode",
            )
        with col_r:
            st.write("")
            if st.button("Load more sample IDs"):
                try:
                    more = mongo_queries.sample_faers_ids(limit=10)
                    st.session_state["extra_faers_ids"] = "\n".join(more)
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        if "extra_faers_ids" in st.session_state:
            st.code(st.session_state["extra_faers_ids"], language=None)

        use_raw = "Raw" in view_mode

        if st.button("Fetch reports", key="fetch_faers"):
            ids_list = [x.strip() for x in ids_text.replace(",", "\n").split() if x.strip()]
            if not ids_list:
                st.warning("Enter at least one report ID.")
            else:
                try:
                    docs = mongo_queries.get_faers_reports_by_ids(ids_list, raw=use_raw)
                    st.caption(f"Found **{len(docs)}** of {len(ids_list)} requested reports.")
                    if docs:
                        rows = []
                        for d in docs:
                            if use_raw:
                                patient = d.get("patient", {}) or {}
                                drugs_list = patient.get("drug") or []
                                drug_names = [(x.get("medicinalproduct") or "").title() for x in drugs_list]
                                reactions_list = patient.get("reaction") or []
                                rx_names = [(x.get("reactionmeddrapt") or "").title() for x in reactions_list]
                                report_id = d.get("safetyreportid") or str(d.get("_id", ""))
                                receive_date = _fmt_faers_date(d.get("receivedate"))
                            else:
                                drug_names = [(x or "").title() for x in (d.get("drugs") or [])]
                                rx_names = [(x or "").title() for x in (d.get("reactions") or [])]
                                report_id = d.get("faers_id") or str(d.get("_id", ""))
                                receive_date = _fmt_faers_date(d.get("receivedate"))

                            rows.append({
                                "Report ID": report_id,
                                "Date": receive_date,
                                "Serious": "Yes" if str(d.get("serious", "")) == "1" else "No",
                                "Drugs": ", ".join(drug_names[:4]) + (" …" if len(drug_names) > 4 else ""),
                                "Reactions": ", ".join(rx_names[:4]) + (" …" if len(rx_names) > 4 else ""),
                            })

                        st.dataframe(rows, width="stretch")

                        with st.expander("Full document detail"):
                            for d in docs:
                                rid = d.get("safetyreportid") or d.get("faers_id") or str(d.get("_id", ""))
                                st.markdown(f"**Report {rid}**")
                                if use_raw:
                                    patient = d.get("patient", {}) or {}
                                    drugs_list = patient.get("drug") or []
                                    reactions_list = patient.get("reaction") or []
                                    c1, c2 = st.columns(2)
                                    c1.markdown("**Drugs**")
                                    c1.write([x.get("medicinalproduct","") for x in drugs_list])
                                    c2.markdown("**Reactions**")
                                    c2.write([x.get("reactionmeddrapt","") for x in reactions_list])
                                else:
                                    c1, c2 = st.columns(2)
                                    c1.markdown("**Drugs**")
                                    c1.write(d.get("drugs", []))
                                    c2.markdown("**Reactions**")
                                    c2.write(d.get("reactions", []))
                                st.divider()
                except Exception as e:
                    st.error(str(e))
        else:
            st.info("Click **Fetch reports** to load the pre-filled report IDs above.")

    # ── Tab 2: Audit Log ──────────────────────────────────────────────────
    with tab_audit:
        st.markdown("Every safety-check run can be logged here for audit and reproducibility.")

        # Seed demo data on first view
        _seed_demo_audit()

        # Search / browse existing records
        search_name = st.text_input(
            "Search by patient name", placeholder="e.g. Abbott", key="audit_search"
        )

        try:
            if search_name.strip():
                checks = mongo_queries.search_safety_checks_by_patient(search_name, limit=50)
                label = f'Results for "{search_name}" ({len(checks)} found)'
            else:
                checks = mongo_queries.list_safety_checks(limit=20)
                label = f"Recent runs ({len(checks)} shown)"
        except Exception as e:
            st.error(str(e))
            checks = []
            label = "Error loading records"

        if checks:
            st.subheader(label)
            rows = []
            for c in checks:
                inp = c.get("inputs", {}) or {}
                out = c.get("outputs", {}) or {}
                rows.append({
                    "Patient": inp.get("patient_name") or inp.get("patient_id", "")[:12] or "—",
                    "Proposed drug": inp.get("proposed_drug", "—"),
                    "Risk": out.get("risk_level", "—"),
                    "Interactions": out.get("interactions_found", "—"),
                    "Date": str(c.get("timestamp", ""))[:10],
                    "Notes": (c.get("notes") or "")[:70],
                })
            st.dataframe(rows, width="stretch")
        else:
            if search_name.strip():
                st.warning(f'No records found for "{search_name}".')
            else:
                st.info("No audit records yet. Log a run below.")

        st.divider()
        st.subheader("Log a new safety check")

        # Patient selection — use PG list if available, else free text
        if HAS_PG:
            try:
                patients = pg_queries.list_patients(limit=50, db_url=_pg_url())
                p_labels = [f"{_clean_name(p.get('first_name',''))} {_clean_name(p.get('last_name',''))}" for p in patients]
                p_choice = st.selectbox("Patient", range(len(patients)), format_func=lambda i: p_labels[i], key="audit_patient")
                selected_patient_name = p_labels[p_choice]
                selected_patient_id = patients[p_choice]["id"]
            except Exception:
                selected_patient_name = st.text_input("Patient name", key="audit_patient_name")
                selected_patient_id = None
        else:
            selected_patient_name = st.text_input("Patient name", key="audit_patient_name")
            selected_patient_id = None

        proposed_drug = st.text_input("Proposed drug", value="Warfarin", key="audit_drug")
        risk_level = st.selectbox("Risk level", ["LOW", "MODERATE", "HIGH", "UNKNOWN"], key="audit_risk")
        notes = st.text_area("Notes / summary", key="audit_notes", height=80)

        if st.button("Log run", key="log_run_btn"):
            run = {
                "inputs": {
                    "patient_name": selected_patient_name,
                    "patient_id": selected_patient_id,
                    "proposed_drug": proposed_drug or None,
                },
                "outputs": {"risk_level": risk_level},
                "notes": notes or None,
            }
            try:
                run_id = mongo_queries.log_safety_check(run)
                st.success(f"Logged successfully. Run ID: `{run_id}`")
                st.rerun()
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
            current_med_names = [
                _extract_drug_name(m.get("description") or m.get("code", ""))
                for m in profile["active_medications"]
            ]
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
        st.dataframe(run_outputs["interactions"], width="stretch")
    else:
        st.success("No drug–drug interactions found for the proposed drug vs current meds.")

    if run_outputs.get("side_effects_sample"):
        st.subheader("Side effects (sample)")
        st.dataframe(run_outputs["side_effects_sample"], width="stretch")

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
