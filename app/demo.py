"""
Streamlit demo: Drug Safety & Recommendation
=============================================
Run from drug_safety_and_recommendation/:
    streamlit run app/demo.py
"""

from __future__ import annotations

import os
import re
import sys

if __name__ == "__main__" or "streamlit" in sys.modules:
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)

import streamlit as st
from streamlit_agraph import agraph, Node, Edge, Config

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
        find_similar_drugs,
        find_safe_alternatives_candidates,
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

# Fallback DrugBank lookup keys when proposed drug has no alternatives (e.g. coagulation factors)
_ALT_FALLBACK_KEYS: dict[str, str] = {
    "albutrepenonacog alfa": "coagulation factor ix human",
    "antihemophilic factor (recombinant), pegylated": "antihemophilic factor (recombinant)",
}

# Fallback side effects when proposed drug has none in Neo4j (e.g. coagulation factors)
_SE_FALLBACK: dict[str, list[str]] = {
    "albutrepenonacog alfa": ["Headache", "Allergic reaction", "Injection site reaction", "Dizziness", "Nausea"],
    "antihemophilic factor (recombinant), pegylated": ["Headache", "Injection site pain", "Fever"],
}

SEVERITY_COLORS = {
    "major": "#e74c3c",
    "moderate": "#FF8C00",  # distinct orange
    "minor": "#3498db",
    "unknown": "#95a5a6",
}
SEVERITY_ICONS = {"major": "\U0001F534", "moderate": "\U0001F7E1", "minor": "\U0001F535"}

CLUSTER_NODE_COLORS = [
    "#2196F3",   # vivid blue
    "#4CAF50",   # vivid green
    "#FF5722",   # deep orange
    "#9C27B0",   # purple
    "#FF9800",   # amber
    "#00BCD4",   # cyan
    "#E91E63",   # pink
    "#607D8B",   # blue grey
]

SE_NODE_COLOR = "#81C784"
SE_EDGE_COLOR = "#A5D6A7"
PROPOSED_BORDER_COLOR = "#FFD600"
PROPOSED_DRUG_COLOR = "#D32F2F"  # distinct red for main/proposed drug
CURRENT_MED_COLOR = "#5C6BC0"    # muted indigo for current medications

DEMO_PATIENT_ID = "a2b3c4d5-0000-4e00-8000-000000000001"
# 2 clusters bridged by proposed drug + alternatives (fallback for coagulation factors)
DEMO_PROPOSED = "Albutrepenonacog alfa"
# Cluster 1: Amphotericin B, Baclofen, Bumetanide, Buthiazide | Cluster 2: Alpha-1-proteinase inhibitor, Aminocaproic acid, Aminomethylbenzoic acid
DEMO_MANUAL_MEDS = "Amphotericin B, Baclofen, Bumetanide, Buthiazide, Alpha-1-proteinase inhibitor, Aminocaproic acid, Aminomethylbenzoic acid"


def _pg_url():
    return os.getenv("PG_URL", "postgresql://postgres:postgres@localhost:5432/drug_safety")


def _neo4j_kw():
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        "user": os.getenv("NEO4J_USER", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "password"),
    }


# ── Graph helpers ─────────────────────────────────────────────────────────

def _build_cluster_graph(
    cluster_result: dict,
    side_effects_map: dict[str, list[str]] | None = None,
    max_se_per_drug: int = 0,
    alternatives: list[str] | list[tuple[str, float | None]] | None = None,
    alt_interactions: dict[str, list[dict]] | None = None,
    proposed_only_interactions: bool = True,
) -> tuple[list[Node], list[Edge]]:
    """
    Build a graph with:
     - Drug nodes (only those with interactions or side effects)
     - Alternative nodes (3 max), dotted edges
     - Interaction edges (max 4, no unknown)
     - Side-effect nodes, light faded edges
    """
    nodes = []
    edges = []
    proposed_lower = cluster_result["proposed_drug"].lower()
    all_drugs_set = {d.lower() for d in cluster_result["drugs"]}

    # First: compute which drugs have interactions (from final_ix) and side effects
    def _severity_ok(ix: dict) -> bool:
        return (ix.get("severity") or "unknown").lower() != "unknown"

    def _edge_key(ix: dict) -> tuple:
        return tuple(sorted([ix["drug_a"], ix["drug_b"]]))

    bridge_drugs: set[str] = set()
    proposed_edges: list[tuple[dict, int]] = []
    cluster_edges: list[tuple[dict, int]] = []
    # Use pre-bridge clusters when available (2 clusters before proposed bridges them)
    clusters_raw = cluster_result.get("clusters_pre_bridge") or cluster_result["clusters"]
    clusters_set = [set(c.lower() for c in cl) for cl in clusters_raw]
    seen_keys: set[tuple] = set()

    for ix in cluster_result["interactions"]:
        if not _severity_ok(ix):
            continue
        key = _edge_key(ix)
        if key in seen_keys:
            continue
        a_low, b_low = ix["drug_a"].lower(), ix["drug_b"].lower()
        prio = {"major": 0, "moderate": 1, "minor": 2}.get((ix.get("severity") or "minor").lower(), 3)
        if proposed_lower in (a_low, b_low):
            bridge_drugs.add(ix["drug_a"] if b_low == proposed_lower else ix["drug_b"])
            seen_keys.add(key)
            proposed_edges.append((ix, prio))
        elif not proposed_only_interactions:
            for cl in clusters_set:
                if a_low in cl and b_low in cl:
                    bridge_lower = {d.lower() for d in bridge_drugs}
                    # Add ALL edges within a cluster that has a bridge drug (full chain)
                    if bridge_lower & cl:
                        seen_keys.add(key)
                        cluster_edges.append((ix, prio))
                    break

    proposed_edges.sort(key=lambda x: x[1])
    cluster_edges.sort(key=lambda x: x[1])
    bridges_clusters = cluster_result.get("bridges_clusters", False)
    max_edges = 8 if bridges_clusters else 4
    final_ix: list[dict] = [e[0] for e in proposed_edges[:4]]
    final_keys = {_edge_key(ix) for ix in final_ix}
    if len(final_ix) < max_edges:
        for e in cluster_edges:
            if len(final_ix) >= max_edges:
                break
            k = _edge_key(e[0])
            if k not in final_keys:
                final_keys.add(k)
                final_ix.append(e[0])

    # Connected drugs: only those in the interaction graph (proposed + final_ix)
    # Exclude drugs with no path to proposed drug (e.g. isolated cluster)
    connected_lower: set[str] = {proposed_lower}
    for ix in final_ix:
        connected_lower.add(ix["drug_a"].lower())
        connected_lower.add(ix["drug_b"].lower())

    def _truncate(s: str, max_len: int = 28) -> str:
        s = (s or "").strip()
        return s[: max_len - 1] + "…" if len(s) > max_len else s

    # Main drug nodes: only connected drugs (exclude isolated)
    for drug in cluster_result["drugs"]:
        if drug.lower() not in connected_lower:
            continue
        is_proposed = drug.lower() == proposed_lower
        color = PROPOSED_DRUG_COLOR if is_proposed else CURRENT_MED_COLOR
        nodes.append(Node(
            id=drug,
            label=_truncate(drug, 32 if is_proposed else 24),
            title=drug,
            size=40 if is_proposed else 22,
            color={
                "background": color,
                "border": PROPOSED_BORDER_COLOR if is_proposed else color,
                "highlight": {"background": "#FFCDD2", "border": "#D32F2F"},
            },
            shape="diamond" if is_proposed else "dot",
            font={"size": 12 if is_proposed else 10, "color": "#fff" if is_proposed else "#333", "bold": is_proposed},
            borderWidth=4 if is_proposed else 2,
        ))

    # Alternative nodes (3 max, dotted edges)
    ALT_NODE_COLOR = "#E8F5E9"
    ALT_BORDER = "#2E7D32"
    if alternatives:
        count = 0
        for item in alternatives:
            if count >= 3:
                break
            alt = item[0] if isinstance(item, (list, tuple)) else item
            risk_pct = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else None
            related = item[2] if isinstance(item, (list, tuple)) and len(item) > 2 else None
            if alt.lower() not in all_drugs_set and alt.lower() != proposed_lower:
                parts = [alt]
                if risk_pct is not None:
                    parts.append(f"{risk_pct:.0f}%")
                if related is not None:
                    parts.append(f"rel {related:.2f}")
                label = " (" + ", ".join(parts[1:]) + ")" if len(parts) > 1 else ""
                label = f"{alt}{label}"
                label = label[:30] + "…" if len(label) > 32 else label
                nodes.append(Node(
                    id=f"ALT:{alt}",
                    label=label,
                    title=alt,
                    size=16,
                    color={
                        "background": ALT_NODE_COLOR,
                        "border": ALT_BORDER,
                        "highlight": {"background": "#C8E6C9", "border": ALT_BORDER},
                    },
                    shape="square",
                    font={"size": 10, "color": "#1B5E20"},
                    borderWidth=2,
                ))
                count += 1

    # Alternative ↔ current med interactions (dashed, distinct color)
    if alt_interactions:
        _sev_short = {"major": "Major", "moderate": "Mod", "minor": "Minor"}
        drugs_canonical = {d.lower(): d for d in cluster_result["drugs"]}
        for alt, ix_list in alt_interactions.items():
            alt_id = f"ALT:{alt}"
            if not any(n.id == alt_id for n in nodes):
                continue
            for ix in ix_list:
                curr_raw = ix.get("current_drug") or ix.get("drug_a")
                if not curr_raw or curr_raw.lower() not in connected_lower:
                    continue
                curr = drugs_canonical.get(curr_raw.lower(), curr_raw)
                sev = (ix.get("severity") or "minor").lower()
                if sev == "unknown":
                    continue
                short = _sev_short.get(sev, sev.capitalize())
                edges.append(Edge(
                    source=alt_id,
                    target=curr,
                    label=short,
                    color=SEVERITY_COLORS.get(sev, "#3498db"),
                    width=1,
                    dashes=True,
                    font={"size": 8, "color": SEVERITY_COLORS.get(sev, "#3498db")},
                ))

    # Add interaction edges (short labels for cleaner layout)
    _sev_short = {"major": "Major", "moderate": "Mod", "minor": "Minor"}
    for ix in final_ix:
        sev = (ix.get("severity") or "minor").lower()
        short = _sev_short.get(sev, sev.capitalize())
        edges.append(Edge(
            source=ix["drug_a"],
            target=ix["drug_b"],
            label=short,
            color=SEVERITY_COLORS.get(sev, "#3498db"),
            width=1.5 + (0.5 if sev == "major" else 0),
            font={"size": 9, "color": SEVERITY_COLORS.get(sev, "#3498db")},
        ))

    # Side effects: minimal, light styling (only for connected drugs)
    if side_effects_map:
        added_se = set()
        for drug, ses in side_effects_map.items():
            if drug.lower() not in connected_lower:
                continue
            for se in ses[:max_se_per_drug]:
                se_id = f"SE:{se}"
                if se_id not in added_se:
                    added_se.add(se_id)
                    nodes.append(Node(
                        id=se_id,
                        label=se[:20] + "…" if len(se) > 22 else se,
                        title=se,
                        size=12,
                        color={"background": "#E8F5E9", "border": "#81C784"},
                        shape="triangle",
                        font={"size": 8, "color": "#558B2F"},
                        borderWidth=1,
                    ))
                edges.append(Edge(
                    source=drug,
                    target=se_id,
                    color="#C8E6C9",
                    width=0.5,
                    label="",
                    dashes=True,
                ))

    return nodes, edges


def _build_network_graph(net: dict) -> tuple[list[Node], list[Edge]]:
    nodes = []
    edges = []
    for n in net["nodes"]:
        nodes.append(Node(
            id=n["name"], label=n["name"], size=20,
            color="#3498db", font={"size": 12, "color": "#222"},
        ))
    seen = set()
    for e in net["edges"]:
        key = tuple(sorted([e["source"], e["target"]]))
        if key in seen:
            continue
        seen.add(key)
        sev = e.get("severity", "unknown")
        edges.append(Edge(
            source=e["source"], target=e["target"],
            label=sev, color=SEVERITY_COLORS.get(sev, "#95a5a6"),
            width=e.get("weight", 1) * 1.5,
        ))
    return nodes, edges


def _build_side_effect_graph(drug_name: str, effects: list[dict]) -> tuple[list[Node], list[Edge]]:
    nodes = [Node(id=drug_name, label=drug_name, size=30, color="#3498db", shape="dot")]
    edges = []
    for e in effects[:30]:
        se = e["side_effect"]
        nodes.append(Node(id=se, label=se, size=15, color=SE_NODE_COLOR, shape="triangle",
                          font={"size": 10, "color": "#333"}))
        edges.append(Edge(source=drug_name, target=se, color=SE_EDGE_COLOR, width=1))
    return nodes, edges


def _render_graph(nodes: list[Node], edges: list[Edge], height: int = 560, hierarchical: bool = False, direction: str = "UD"):
    if not nodes:
        st.info("No graph data to display.")
        return
    config = Config(
        width="100%",
        height=height,
        directed=False,
        physics=not hierarchical,
        hierarchical=hierarchical,
        nodeHighlightBehavior=True,
        highlightColor="#FFE082",
        levelSeparation=220,
        nodeSpacing=200,
        treeSpacing=300,
        direction=direction,
        blockShifting=True,
        edgeMinimization=True,
        parentCentralization=True,
        solver="forceAtlas2Based",
    )
    agraph(nodes=nodes, edges=edges, config=config)


def _render_legend():
    st.markdown(
        """
        <div style="display:flex;gap:20px;flex-wrap:wrap;font-size:11px;margin-bottom:8px;color:#666;padding:6px 0">
        <span style="color:#D32F2F"><b>◆</b> Proposed</span>
        <span style="color:#5C6BC0"><b>●</b> Current med</span>
        <span style="color:#2E7D32"><b>■</b> Alternative</span>
        <span style="color:#e74c3c">Major</span>
        <span style="color:#FF8C00">Moderate</span>
        <span style="color:#3498db">Minor</span>
        <span style="color:#81C784"><b>▲</b> Side effect</span>
        <span style="color:#888"><i>Dashed = alternative↔current med</i></span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── Page: Patient data (PostgreSQL) ───────────────────────────────────────

def page_patient_data():
    st.header("Patient Data (PostgreSQL)")
    st.caption("Synthea EHR data: demographics, medications, conditions, timeline.")
    if not HAS_PG:
        st.error(f"PostgreSQL module unavailable: {_PG_ERR}")
        return

    limit = st.sidebar.number_input("Patients to list", min_value=5, max_value=100, value=20)
    try:
        patients = pg_queries.list_patients(limit=limit, db_url=_pg_url())
    except Exception as e:
        st.error(f"Cannot connect to PostgreSQL: {e}")
        return

    if not patients:
        st.warning("No patients. Load Synthea data first.")
        return

    ids = [p["id"] for p in patients]
    labels = [f"{p.get('last_name','')}, {p.get('first_name','')} ({p['id'][:8]}...)" for p in patients]
    choice = st.selectbox("Select a patient", range(len(ids)), format_func=lambda i: labels[i])
    patient_id = ids[choice]

    tab_profile, tab_meds, tab_history, tab_timeline = st.tabs([
        "Profile", "Active medications", "Medication history", "Timeline",
    ])

    with tab_profile:
        try:
            profile = pg_queries.get_patient_profile(patient_id, db_url=_pg_url())
            p = profile["patient"]
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Name", f"{p.get('first_name','')} {p.get('last_name','')}")
                st.metric("Gender", p.get("gender", ""))
            with col2:
                st.metric("Birthdate", str(p.get("birthdate", "")))
            st.subheader("Active conditions")
            st.dataframe(profile.get("conditions") or [{"message": "None"}], use_container_width=True)
        except Exception as e:
            st.error(str(e))

    with tab_meds:
        try:
            meds = pg_queries.get_active_medications(patient_id, db_url=_pg_url())
            st.dataframe(meds if meds else [{"message": "No active medications"}], use_container_width=True)
        except Exception as e:
            st.error(str(e))

    with tab_history:
        limit_h = st.number_input("Max records", min_value=10, value=50, key="limit_h")
        try:
            history = pg_queries.get_medication_history(patient_id, db_url=_pg_url(), limit=limit_h)
            st.dataframe(history if history else [{"message": "No history"}], use_container_width=True)
        except Exception as e:
            st.error(str(e))

    with tab_timeline:
        event_types = st.multiselect(
            "Event types",
            ["medication", "condition", "encounter", "procedure"],
            default=["medication", "condition"],
        )
        try:
            timeline = pg_queries.get_patient_timeline(
                patient_id, db_url=_pg_url(), event_types=event_types or None,
            )
            st.dataframe(timeline if timeline else [{"message": "No events"}], use_container_width=True)
        except Exception as e:
            st.error(str(e))


# ── Page: Drug Knowledge (Neo4j) ─────────────────────────────────────────

def page_drug_knowledge():
    st.header("Drug Knowledge Graph (Neo4j)")
    if not HAS_NEO4J:
        st.error(f"Neo4j module unavailable: {_NEO4J_ERR}")
        return

    conn = _neo4j_kw()
    try:
        stats = neo4j_queries.get_drug_stats(**conn)
    except Exception as e:
        st.error(f"Cannot connect to Neo4j: {e}")
        return

    with st.expander("Graph Statistics", expanded=False):
        cols = st.columns(4)
        for i, (k, v) in enumerate(stats.items()):
            cols[i % 4].metric(k.replace("_", " ").title(), f"{v:,}")

    sub = st.radio(
        "Query type",
        [
            "Polypharmacy Cluster Analysis",
            "Interaction Network",
            "Side Effects",
            "Interaction Path",
            "Shared Side Effects",
        ],
        key="neo4j_sub",
        horizontal=True,
    )

    if sub == "Polypharmacy Cluster Analysis":
        st.subheader("Polypharmacy Cluster Analysis")
        st.caption("Enter current medications + proposed drug to see interaction clusters and bridge risk.")

        col1, col2 = st.columns([2, 1])
        with col1:
            current_meds = st.text_area(
                "Current medications (comma-separated)",
                placeholder=f"e.g. {DEMO_MANUAL_MEDS}",
            )
        with col2:
            proposed = st.text_input("Proposed drug", placeholder=f"e.g. {DEMO_PROPOSED}")
            auto_discover = st.checkbox("Auto-discover example drugs", value=False)

        if auto_discover:
            try:
                top = neo4j_queries.find_example_interacting_drugs(**conn, limit=5)
                if top:
                    proposed = top[0]["drug_name"]
                    group = neo4j_queries.find_interacting_group(proposed, group_size=5, **conn)
                    current_list = [g for g in group if g.lower() != proposed.lower()]
                    current_meds = ", ".join(current_list)
                    st.info(f"Auto-discovered: proposed = **{proposed}**, current = {current_list}")
            except Exception as e:
                st.error(str(e))

        if st.button("Analyze Clusters", type="primary"):
            meds = [m.strip() for m in current_meds.split(",") if m.strip()]
            if not proposed:
                st.warning("Enter a proposed drug.")
            elif not meds:
                st.warning("Enter at least one current medication.")
            else:
                try:
                    result = neo4j_queries.detect_polypharmacy_clusters(meds, proposed, **conn)

                    risk_color = {"high": "red", "moderate": "orange", "low": "green"}.get(result["risk_level"], "gray")
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Risk Level", result["risk_level"].upper())
                    col2.metric("Risk Score", result["risk_score"])
                    col3.metric("Bridges Clusters", "YES" if result["bridges_clusters"] else "No")

                    if result["bridges_clusters"]:
                        st.error(
                            f"**{proposed}** bridges {len(result['clusters'])} previously separate "
                            f"interaction clusters — elevated polypharmacy risk!"
                        )

                    st.subheader("Interaction Cluster Graph")
                    nodes, edges = _build_cluster_graph(
                        result,
                        max_se_per_drug=2,
                        proposed_only_interactions=True,
                    )
                    _render_legend()
                    _render_graph(nodes, edges, height=450)

                    with st.expander("Cluster details"):
                        for idx, c in enumerate(result["clusters"], 1):
                            st.write(f"**Cluster {idx}:** {', '.join(c)}")

                    if result["interactions"]:
                        with st.expander(f"Interactions ({len(result['interactions'])})"):
                            st.dataframe(result["interactions"], use_container_width=True)

                except Exception as e:
                    st.error(str(e))

    elif sub == "Interaction Network":
        st.subheader("Interaction Network")
        drug = st.text_input("Drug name", placeholder="e.g. Cyclosporine", key="net_drug")
        depth = st.slider("Depth (hops)", 1, 3, 1, key="net_depth")
        if st.button("Show Network"):
            if drug:
                try:
                    net = neo4j_queries.get_interaction_network(drug, depth=depth, **conn)
                    st.write(f"**{len(net['nodes'])} nodes, {len(net['edges'])} edges**")
                    nodes, edges = _build_network_graph(net)
                    _render_graph(nodes, edges, height=500)
                except Exception as e:
                    st.error(str(e))

    elif sub == "Side Effects":
        st.subheader("Drug Side Effects")
        drug = st.text_input("Drug name", key="se_drug")
        if st.button("Get Side Effects"):
            if drug:
                try:
                    effects = neo4j_queries.get_side_effects(drug, **conn)
                    if effects:
                        nodes, edges = _build_side_effect_graph(drug, effects)
                        _render_graph(nodes, edges, height=400)
                        st.dataframe(effects, use_container_width=True)
                    else:
                        st.info("No side effects found.")
                except Exception as e:
                    st.error(str(e))

    elif sub == "Interaction Path":
        st.subheader("Shortest Interaction Path")
        col1, col2 = st.columns(2)
        drug_a = col1.text_input("Drug A", key="path_a")
        drug_b = col2.text_input("Drug B", key="path_b")
        max_hops = st.slider("Max hops", 1, 5, 3)
        if st.button("Find Path"):
            if drug_a and drug_b:
                try:
                    paths = neo4j_queries.find_interaction_path(drug_a, drug_b, max_hops=max_hops, **conn)
                    if paths:
                        for p in paths:
                            st.write(f"**{' -> '.join(p['path_drugs'])}** (hops: {p['path_length']})")
                    else:
                        st.info("No path found.")
                except Exception as e:
                    st.error(str(e))

    elif sub == "Shared Side Effects":
        st.subheader("Shared Side Effects Between Two Drugs")
        col1, col2 = st.columns(2)
        drug_a = col1.text_input("Drug A", key="shared_a")
        drug_b = col2.text_input("Drug B", key="shared_b")
        if st.button("Find Shared"):
            if drug_a and drug_b:
                try:
                    shared = neo4j_queries.find_shared_side_effects(drug_a, drug_b, **conn)
                    if shared:
                        st.dataframe(shared, use_container_width=True)
                    else:
                        st.info("No shared side effects found.")
                except Exception as e:
                    st.error(str(e))


# ── Page: Evidence & Audit (MongoDB) ──────────────────────────────────────

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
    st.header("Evidence & Audit (MongoDB)")
    if not HAS_MONGO:
        st.error(f"MongoDB module unavailable: {_MONGO_ERR}")
        return

    sub = st.radio(
        "Action",
        ["Log a safety check", "Retrieve by run_id", "Fetch FAERS reports"],
        key="mongo_sub",
        horizontal=True,
    )

    if "Log" in sub:
        patient_id = st.text_input("Patient ID", key="log_patient")
        proposed_drug = st.text_input("Proposed drug", key="log_drug")
        notes = st.text_area("Notes", key="log_notes")
        if st.button("Log"):
            try:
                run_id = mongo_queries.log_safety_check({
                    "inputs": {"patient_id": patient_id, "proposed_drug": proposed_drug},
                    "outputs": {}, "notes": notes,
                })
                st.success(f"Logged. Run ID: `{run_id}`")
            except Exception as e:
                st.error(str(e))

    elif "Retrieve" in sub:
        run_id = st.text_input("Run ID")
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
            try:
                doc = mongo_queries.get_safety_check(run_id)
                st.json(doc) if doc else st.warning("Not found.")
            except Exception as e:
                st.error(str(e))

    elif "FAERS" in sub:
        ids_text = st.text_area("FAERS report IDs (comma-separated)")
        if st.button("Fetch"):
            ids_list = [x.strip() for x in ids_text.split(",") if x.strip()]
            if ids_list:
                try:
                    docs = mongo_queries.get_faers_reports_by_ids(ids_list, raw=False)
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
                    docs = mongo_queries.get_faers_reports_by_ids(ids_list)
                    st.write(f"Found {len(docs)} reports.")
                    for d in docs:
                        st.json(d)
                except Exception as e:
                    st.error(str(e))


# ── Page: Full Safety Check ──────────────────────────────────────────────

def page_full_safety_check():
    st.header("Full Drug Safety Check")
    st.caption("Search by Patient ID to load medications from PostgreSQL. Or enter manual medications if patient not found.")

    with st.form("safety_check_form"):
        col1, col2 = st.columns(2)
        with col1:
            patient_id = st.text_input("Patient ID", value=DEMO_PATIENT_ID, placeholder=f"e.g. {DEMO_PATIENT_ID}", help="Load demo patient from PostgreSQL. Run load_demo_patient_to_pg.py first.")
            manual_meds = st.text_input("Current medications (comma-separated)", value="", placeholder="Fallback if patient ID not found", help="Only used when Patient ID is empty or lookup fails.")
        with col2:
            proposed_drug = st.text_input("Proposed drug", value=DEMO_PROPOSED)
            log_to_mongo = st.checkbox("Log to MongoDB", value=True)
        submitted = st.form_submit_button("Run Safety Check")

    if not submitted:
        return

    conn = _neo4j_kw()
    current_med_names: list[str] = []
    run_outputs: dict = {}
    patient_info: dict = {}

    # ── Fetch data (no display) ──
    if HAS_PG and patient_id:
        try:
            profile = pg_queries.get_patient_profile(patient_id, db_url=_pg_url())
            p = profile["patient"]
            meds_raw = profile["active_medications"]
            current_med_names = [m.get("description") or m.get("code", "") for m in meds_raw]
            meds_with_times = [(m.get("description") or m.get("code", ""), m.get("start_ts")) for m in meds_raw]
            patient_info = {
                "name": f"{p.get('first_name','')} {p.get('last_name','')}",
                "gender": p.get("gender", ""),
                "dob": str(p.get("birthdate", "")),
                "meds": current_med_names,
                "meds_with_times": meds_with_times,
                "conditions": [(c.get("description", ""), c.get("start_date")) for c in profile.get("conditions", [])],
                "allergies": [(a.get("description", ""), a.get("start_date")) for a in profile.get("allergies", [])],
            }
            run_outputs["patient"] = patient_info
        except Exception as e:
            st.warning(f"Patient not found in PostgreSQL: {e}. Using manual medications if provided.")
            if manual_meds:
                current_med_names = [m.strip() for m in manual_meds.split(",") if m.strip()]
                patient_info = {"meds": current_med_names, "meds_with_times": [], "name": "—", "conditions": [], "allergies": []}
            else:
                st.error("Enter manual medications or run load_demo_patient_to_pg.py to load the demo patient.")
                return
    elif manual_meds:
        current_med_names = [m.strip() for m in manual_meds.split(",") if m.strip()]
        patient_info = {"meds": current_med_names, "meds_with_times": [], "name": "—", "conditions": [], "allergies": []}
    else:
        st.warning("Enter a patient ID or current medications.")
        return

    if not proposed_drug:
        st.warning("Enter a proposed drug.")
        return

    interactions: list[dict] = []
    cluster_result: dict | None = None
    side_effects_map: dict[str, list[str]] = {}
    faers_results: list[dict] = []
    top_alternatives: list[tuple[str, float | None, float | None]] = []  # (name, risk_pct_serious or None, relatedness or None)

    if HAS_NEO4J:
        try:
            interactions = neo4j_queries.check_interactions(current_med_names, proposed_drug, **conn)
            cluster_result = neo4j_queries.detect_polypharmacy_clusters(current_med_names, proposed_drug, **conn)
            all_drug_names = list(set(current_med_names + [proposed_drug]))
            for dname in all_drug_names:
                ses = neo4j_queries.get_side_effects(dname, **conn)
                if ses:
                    side_effects_map[dname] = [s["side_effect"] for s in ses[:3]]
            # Fallback side effects for proposed drug when none in Neo4j
            if proposed_drug and not any(
                k and proposed_drug and k.lower().strip() == proposed_drug.lower().strip()
                for k in side_effects_map
            ):
                fallback = _SE_FALLBACK.get(proposed_drug.strip().lower())
                if fallback:
                    side_effects_map[proposed_drug] = fallback
            run_outputs["interactions"] = interactions
            run_outputs["cluster"] = {
                "risk_level": cluster_result["risk_level"],
                "risk_score": cluster_result["risk_score"],
                "bridges": cluster_result["bridges_clusters"],
            }
        except Exception as e:
            st.error(f"Neo4j: {e}")
            cluster_result = None

    if HAS_QDRANT:
        try:
            conds = patient_info.get("conditions", []) or []
            cond_names = [c[0] if isinstance(c, (list, tuple)) else c for c in conds[:3]]
            patient_summary = f"Patient with {', '.join(cond_names) or 'various conditions'}. Meds: {', '.join(current_med_names)}. Proposed: {proposed_drug}."
            faers_results = find_similar_adverse_events_multi_filter(patient_summary=patient_summary, drug_names=[proposed_drug], top_k=10)
            if faers_results:
                aspects = analyze_adverse_event_aspects(faers_results)
                run_outputs["faers"] = {"count": len(faers_results), "top_reactions": list(aspects.get("top_reactions", {}).keys())[:5]}
        except Exception:
            pass

    try:
        candidates: list[dict] = []
        if HAS_QDRANT:
            qdrant_result = find_safe_alternatives_candidates(proposed_drug, top_k=15)
            candidates = qdrant_result.get("candidates", [])
        if not candidates and HAS_ALTS:
            alts_raw, _ = get_alternatives(proposed_drug, min_count=15, return_sources=True, fetch_from_ndc=True)
            if not alts_raw:
                fallback_key = _ALT_FALLBACK_KEYS.get(proposed_drug.strip().lower())
                if fallback_key:
                    alts_raw, _ = get_alternatives(fallback_key, min_count=5, return_sources=True, fetch_from_ndc=True)
            candidates = [{"name": a[0], "similarity_score": 0.5} for a in alts_raw]
        if candidates:
            scored: list[tuple[str, float, float | None, float | None]] = []  # (name, sort_score, risk_pct, relatedness)
            for c in candidates:
                name = c.get("name", "") if isinstance(c, dict) else str(c)
                if not name or name.lower() == proposed_drug.lower():
                    continue
                risk_pct = None
                relatedness = None
                try:
                    if HAS_QDRANT:
                        s = get_drug_faers_summary(name, top_k=50)
                        risk_pct = (s.get("pct_serious", 1.0) * 100) if s else None
                        sort_score = 1.0 - (s.get("pct_serious", 1.0) if s else 1.0)
                    else:
                        sort_score = c.get("similarity_score", 0.5)
                    try:
                        relatedness = compute_drug_similarity(proposed_drug, name) if HAS_QDRANT else None
                    except Exception:
                        relatedness = None
                    scored.append((name, sort_score, risk_pct, relatedness))
                except Exception:
                    try:
                        relatedness = compute_drug_similarity(proposed_drug, name) if HAS_QDRANT else None
                    except Exception:
                        relatedness = None
                    scored.append((name, 0.5, None, relatedness))
            scored.sort(key=lambda x: x[1], reverse=True)
            top_alternatives = [(s[0], s[2], s[3]) for s in scored[:3]]
            run_outputs["alternatives"] = [(a[0], a[1], a[2]) for a in top_alternatives]
    except Exception:
        if HAS_ALTS:
            try:
                alts_raw, _ = get_alternatives(proposed_drug, min_count=5, return_sources=True, fetch_from_ndc=True)
                if not alts_raw:
                    fallback_key = _ALT_FALLBACK_KEYS.get(proposed_drug.strip().lower())
                    if fallback_key:
                        alts_raw, _ = get_alternatives(fallback_key, min_count=5, return_sources=True, fetch_from_ndc=True)
                rels: list[tuple[str, float | None, float | None]] = []
                for a in alts_raw[:3]:
                    name = a[0]
                    relatedness = None
                    try:
                        relatedness = compute_drug_similarity(proposed_drug, name) if HAS_QDRANT else None
                    except Exception:
                        relatedness = None
                    rels.append((name, None, relatedness))
                top_alternatives = rels
            except Exception:
                pass

    # ── 1. WARNING (highlighted first) ──
    st.markdown("---")
    if cluster_result:
        paragraphs = []
        # Line 1: WARNING: Drug has SEVERITY interaction with patient's current Drug (risk of X).
        if interactions:
            worst = max(interactions, key=lambda x: (x.get("severity") == "major", x.get("weight", 0)))
            sev = worst.get("severity", "unknown").upper()
            curr = worst.get("current_drug", "current medication")
            full_desc = worst.get("description", "") or "interaction risk"
            # Extract short risk phrase: "risk of X" or "risk or severity of X"
            short_risk = "interaction risk"
            fd_lower = full_desc.lower()
            if "risk or severity of" in fd_lower:
                start = fd_lower.find("risk or severity of")
                chunk = full_desc[start:].split(".")[0].split(" when ")[0].split(" can ")[0].strip()
                short_risk = chunk[:45].rstrip(",") + ("…" if len(chunk) > 45 else "")
            elif "risk of" in fd_lower:
                start = fd_lower.find("risk of")
                chunk = full_desc[start:].split(".")[0].split(" when ")[0].strip()
                short_risk = chunk[:45].rstrip(",") + ("…" if len(chunk) > 45 else "")
            elif "increased" in fd_lower:
                short_risk = "increased adverse effects"
            paragraphs.append(f"**{proposed_drug}** has a **{sev}** interaction with the patient's current **{curr}** ({short_risk})")
        elif cluster_result.get("bridges_clusters"):
            n_clusters = len(cluster_result.get("clusters_pre_bridge") or cluster_result["clusters"])
            paragraphs.append(f"**{proposed_drug}** bridges {n_clusters} interaction clusters (polypharmacy risk).")

        # Line 2: Risk level and score
        risk_level = cluster_result.get("risk_level", "unknown").upper()
        risk_score = run_outputs.get("cluster", {}).get("risk_score", 0)
        paragraphs.append(f"The proposed drug has **{risk_level} RISK** rate of {risk_score}.")

        # Line 3: FAERS similar cases
        if faers_results:
            n = len(faers_results)
            top_rx = run_outputs.get("faers", {}).get("top_reactions", [])[:3]
            rx_str = ", ".join(top_rx) if top_rx else "adverse events"
            paragraphs.append(f"{n} patients with similar profiles reported {rx_str}.")

        # Line 4: Recommendation
        if top_alternatives:
            alt_names = [a[0] for a in top_alternatives[:3]]
            alt_str = ", ".join(alt_names)
            paragraphs.append(f"**Recommendation:** Consider an alternative (e.g., {alt_str}) or monitor closely.")
        else:
            paragraphs.append("**Recommendation:** Review with prescriber or monitor closely.")

        if paragraphs:
            text_html = "<br><br>".join(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p) for p in paragraphs)
            st.markdown(
                '<div style="background:#FFEBEE;border-left:4px solid #D32F2F;padding:16px;margin:12px 0;border-radius:4px;overflow:visible">'
                '<p style="margin:0;font-size:16px"><strong>⚠ WARNING</strong></p>'
                f'<p style="margin:8px 0 0 0;line-height:1.6">{text_html}</p></div>',
                unsafe_allow_html=True,
            )
        else:
            st.success(f"No interactions found for **{proposed_drug}** with current medications.")
    elif not HAS_NEO4J:
        st.error(f"Neo4j unavailable: {_NEO4J_ERR}")

    # ── 2. Graph ──
    alt_interactions: dict[str, list[dict]] = {}
    if HAS_NEO4J and top_alternatives and current_med_names:
        try:
            conn = _neo4j_kw()
            for item in top_alternatives[:3]:
                alt = item[0] if isinstance(item, (list, tuple)) else item
                ix_list = neo4j_queries.check_interactions(current_med_names, alt, **conn)
                if ix_list:
                    alt_interactions[alt] = ix_list
        except Exception:
            pass

    if cluster_result:
        st.subheader("Interaction Graph")
        if cluster_result.get("bridges_clusters"):
            st.caption("The proposed drug bridges 2 interaction clusters (polypharmacy risk).")
        _render_legend()
        graph_se_map: dict[str, list[str]] = {}
        if proposed_drug and side_effects_map:
            for k, v in side_effects_map.items():
                if k and proposed_drug and k.lower().strip() == proposed_drug.lower().strip():
                    canonical = cluster_result["proposed_drug"]
                    graph_se_map[canonical] = v[:4]
                    break
        nodes, edges = _build_cluster_graph(
            cluster_result,
            side_effects_map=graph_se_map if graph_se_map else None,
            max_se_per_drug=4,
            alternatives=top_alternatives,
            alt_interactions=alt_interactions,
            proposed_only_interactions=False,
        )
        _render_graph(nodes, edges, height=620, hierarchical=False)

    # ── 3. Interactions & Side Effects (points) ──
    st.subheader("Interactions & Side Effects")
    col_ix, col_se = st.columns(2)
    with col_ix:
        st.markdown("**Proposed drug ↔ current medications**")
        if interactions:
            sev_order = {"major": 0, "moderate": 1, "minor": 2, "unknown": 3}
            sorted_ix = sorted(interactions, key=lambda x: sev_order.get((x.get("severity") or "unknown").lower(), 4))
            seen_curr: set[str] = set()
            for ix in sorted_ix:
                curr = ix.get("current_drug", "—")
                key = curr.lower().strip()
                if key in seen_curr:
                    continue
                seen_curr.add(key)
                sev = (ix.get("severity") or "unknown").lower()
                desc = (ix.get("description") or "").strip()
                color = SEVERITY_COLORS.get(sev, "#95a5a6")
                st.markdown(
                    f'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{color};margin-right:8px;vertical-align:middle"></span>'
                    f'<span style="color:{color};font-weight:600">{sev.upper()}</span> — {curr}',
                    unsafe_allow_html=True,
                )
                if desc:
                    st.caption(desc)
        else:
            st.caption("No interactions found.")
    with col_se:
        st.markdown("**Side effects (proposed drug only)**")
        proposed_ses = side_effects_map.get(proposed_drug) if proposed_drug else None
        if not proposed_ses:
            for k, v in side_effects_map.items():
                if k and proposed_drug and k.lower().strip() == proposed_drug.lower().strip():
                    proposed_ses = v
                    break
        if proposed_ses:
            for se in proposed_ses[:10]:
                st.markdown(
                    f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#81C784;margin-right:6px;vertical-align:middle"></span>'
                    f'<span style="color:#2E7D32">{se}</span>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No side effects for proposed drug.")

    # ── 4. Patient History ──
    st.subheader("Patient History")
    if patient_info:
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"**Name:** {patient_info.get('name', '—')}")
            meds_with_times = patient_info.get("meds_with_times", [])
            if meds_with_times:
                med_lines = []
                for m, t in meds_with_times:
                    ts = str(t)[:10] if t else "—"
                    med_lines.append(f"{m} (since {ts})")
                st.write("**Medications:**")
                for line in med_lines:
                    st.write(f"  • {line}")
            else:
                st.write(f"**Medications:** {', '.join(patient_info.get('meds', []) or ['—'])}")
        with c2:
            conds = patient_info.get("conditions", []) or []
            if conds and isinstance(conds[0], (list, tuple)):
                cond_lines = [f"{c[0]} (since {str(c[1])[:10] if c[1] else '—'})" for c in conds]
                st.write("**Conditions:** " + "; ".join(cond_lines))
            else:
                st.write(f"**Conditions:** {', '.join(str(c) for c in conds) if conds else '—'}")
            allergies = patient_info.get("allergies", []) or []
            if allergies and isinstance(allergies[0], (list, tuple)):
                allergy_lines = [f"{a[0]} (since {str(a[1])[:10] if a[1] else '—'})" for a in allergies]
                st.write("**Allergies:** " + ("; ".join(allergy_lines) if allergy_lines else "None"))
            else:
                st.write(f"**Allergies:** {', '.join(str(a) for a in allergies) if allergies else 'None'}")

    # ── 5. Alternatives ──
    st.subheader("Alternatives")
    if top_alternatives:
        for i, item in enumerate(top_alternatives[:3], 1):
            alt = item[0] if isinstance(item, (list, tuple)) else item
            risk_pct = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else None
            related = item[2] if isinstance(item, (list, tuple)) and len(item) > 2 else None
            risk = f"{risk_pct:.0f}% serious" if risk_pct is not None else "N/A"
            rel_txt = f"{related:.3f}" if related is not None else "N/A"
            st.write(f"**{i}. {alt}** — **Risk:** {risk} · **Relatedness:** {rel_txt}")
    else:
        st.write("No alternatives found.")

    # ── 6. MongoDB Audit ──
    st.subheader("MongoDB Evidence")
    if log_to_mongo and HAS_MONGO:
        try:
            run_id = mongo_queries.log_safety_check({
                "inputs": {"patient_id": patient_id, "proposed_drug": proposed_drug, "current_meds": current_med_names},
                "outputs": run_outputs,
            })
            st.success(f"Audit saved. Run ID: `{run_id}`")
        except Exception as e:
            st.error(f"MongoDB: {e}")
    else:
        st.caption("Audit logging disabled.")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Drug Safety & Recommendation",
        page_icon="\U0001F48A",
        layout="wide",
    )
    st.title("Drug Safety & Recommendation")
    st.caption("PostgreSQL · Neo4j · Qdrant · MongoDB")

    page = st.sidebar.radio(
        "Section",
        [
            "Full safety check",
            "Patient data (PostgreSQL)",
            "Drug knowledge (Neo4j)",
            "FAERS + alternatives (Qdrant)",
            "Evidence & audit (MongoDB)",
        ],
    )

    if page == "Full safety check":
        page_full_safety_check()
    elif page == "Patient data (PostgreSQL)":
        page_patient_data()
    elif page == "Drug knowledge (Neo4j)":
        page_drug_knowledge()
    elif page == "FAERS + alternatives (Qdrant)":
        page_qdrant_and_alternatives()
    elif page == "Evidence & audit (MongoDB)":
        page_evidence_audit()


if __name__ == "__main__":
    main()
