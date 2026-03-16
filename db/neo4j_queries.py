"""
Part 2: Neo4j query functions for drug–drug interactions and side effects.
Used by the application layer (Part 5) to check drug safety.

Core graph use case: polypharmacy interaction cluster detection.
"""

from neo4j import GraphDatabase


def get_connection(uri: str = "bolt://127.0.0.1:7687", user: str = "neo4j", password: str = "password"):
    return GraphDatabase.driver(uri, auth=(user, password))


# ---------------------------------------------------------------------------
# 1. Core: check pairwise interactions
# ---------------------------------------------------------------------------

def check_interactions(
    current_med_names: list[str],
    proposed_drug: str,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    if not current_med_names or not proposed_drug:
        return []
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d1:Drug)-[r:INTERACTS_WITH]-(d2:Drug)
                WHERE toLower(trim(d1.name)) IN $current_meds
                  AND toLower(trim(d2.name)) = toLower(trim($proposed))
                RETURN d1.name AS current_drug,
                       d2.name AS proposed_drug,
                       coalesce(r.severity, 'unknown') AS severity,
                       coalesce(r.description, '') AS description,
                       coalesce(r.weight, 1) AS weight
                """,
                current_meds=[n.strip().lower() for n in current_med_names],
                proposed=proposed_drug.strip(),
            )
            return [dict(rec) for rec in result]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# 2. Core: polypharmacy interaction cluster detection
# ---------------------------------------------------------------------------

def detect_polypharmacy_clusters(
    current_med_names: list[str],
    proposed_drug: str,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> dict:
    """
    1. Takes patient's current meds + proposed drug
    2. Gets INTERACTS_WITH subgraph among ONLY those drugs
    3. Uses Union-Find to identify connected components (clusters)
    4. Checks if proposed drug bridges separate clusters
    """
    all_drugs = [m.strip() for m in current_med_names if m.strip()]
    proposed = proposed_drug.strip()
    if proposed and proposed.lower() not in [d.lower() for d in all_drugs]:
        all_drugs.append(proposed)

    if len(all_drugs) < 2:
        return {
            "drugs": all_drugs, "interactions": [],
            "clusters": [all_drugs] if all_drugs else [],
            "proposed_drug": proposed,
            "bridges_clusters": False, "risk_score": 0.0, "risk_level": "low",
        }

    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d1:Drug)-[r:INTERACTS_WITH]-(d2:Drug)
                WHERE toLower(trim(d1.name)) IN $names
                  AND toLower(trim(d2.name)) IN $names
                RETURN d1.name AS drug_a,
                       d2.name AS drug_b,
                       coalesce(r.severity, 'unknown') AS severity,
                       coalesce(r.weight, 1) AS weight,
                       coalesce(r.description, '') AS description
                """,
                names=[n.lower() for n in all_drugs],
            )
            interactions = [dict(rec) for rec in result]
    finally:
        driver.close()

    parent: dict[str, str] = {}
    all_drug_names_lower = {d.lower() for d in all_drugs}
    name_canonical: dict[str, str] = {}

    for d in all_drugs:
        parent[d.lower()] = d.lower()
        name_canonical[d.lower()] = d

    for ix in interactions:
        a_low = ix["drug_a"].lower()
        b_low = ix["drug_b"].lower()
        name_canonical[a_low] = ix["drug_a"]
        name_canonical[b_low] = ix["drug_b"]
        if a_low not in parent:
            parent[a_low] = a_low
        if b_low not in parent:
            parent[b_low] = b_low

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    proposed_lower = proposed.lower()
    for ix in interactions:
        a_low = ix["drug_a"].lower()
        b_low = ix["drug_b"].lower()
        if a_low == proposed_lower or b_low == proposed_lower:
            continue
        if a_low in parent and b_low in parent:
            union(a_low, b_low)

    proposed_connects_to: set[str] = set()
    for ix in interactions:
        a_low = ix["drug_a"].lower()
        b_low = ix["drug_b"].lower()
        if a_low == proposed_lower and b_low in parent:
            proposed_connects_to.add(find(b_low))
        elif b_low == proposed_lower and a_low in parent:
            proposed_connects_to.add(find(a_low))

    bridges = len(proposed_connects_to) > 1

    # Clusters BEFORE proposed drug bridges them (for 2-cluster visualization)
    clusters_pre_bridge: list[list[str]] = []
    if bridges:
        pre_clusters: dict[str, list[str]] = {}
        for d_low in all_drug_names_lower:
            if d_low == proposed_lower:
                continue
            if d_low in parent:
                root = find(d_low)
                pre_clusters.setdefault(root, []).append(name_canonical.get(d_low, d_low))
        clusters_pre_bridge = sorted(pre_clusters.values(), key=len, reverse=True)

    for ix in interactions:
        a_low = ix["drug_a"].lower()
        b_low = ix["drug_b"].lower()
        if a_low in parent and b_low in parent:
            union(a_low, b_low)

    final_clusters: dict[str, list[str]] = {}
    for d_low in all_drug_names_lower:
        if d_low in parent:
            root = find(d_low)
            final_clusters.setdefault(root, []).append(name_canonical.get(d_low, d_low))

    cluster_list = sorted(final_clusters.values(), key=len, reverse=True)
    if not bridges:
        clusters_pre_bridge = cluster_list

    total_weight = sum(ix["weight"] for ix in interactions)
    has_major = any(ix["severity"] == "major" for ix in interactions)

    if bridges or has_major:
        risk_level = "high"
    elif len(interactions) >= 3 or total_weight >= 6:
        risk_level = "moderate"
    else:
        risk_level = "low"

    risk_score = round(total_weight + (5.0 if bridges else 0.0) + (3.0 if has_major else 0.0), 2)

    return {
        "drugs": all_drugs,
        "interactions": interactions,
        "clusters": cluster_list,
        "clusters_pre_bridge": clusters_pre_bridge,
        "proposed_drug": proposed,
        "bridges_clusters": bridges,
        "risk_score": risk_score,
        "risk_level": risk_level,
    }


# ---------------------------------------------------------------------------
# 3. Side effects
# ---------------------------------------------------------------------------

def get_side_effects(
    drug_name: str,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    if not drug_name:
        return []
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)-[r:HAS_SIDE_EFFECT]->(se:SideEffect)
                WHERE toLower(trim(d.name)) = toLower(trim($drug_name))
                RETURN se.name AS side_effect,
                       coalesce(r.frequency, 'unknown') AS frequency,
                       coalesce(r.weight, 1) AS weight,
                       coalesce(r.source, 'unknown') AS source
                ORDER BY weight DESC, side_effect
                """,
                drug_name=drug_name.strip(),
            )
            return [dict(rec) for rec in result]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# 4. Shared side effects between two drugs
# ---------------------------------------------------------------------------

def find_shared_side_effects(
    drug_a: str,
    drug_b: str,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    if not drug_a or not drug_b:
        return []
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Drug)-[:HAS_SIDE_EFFECT]->(se:SideEffect)<-[:HAS_SIDE_EFFECT]-(b:Drug)
                WHERE toLower(trim(a.name)) = toLower(trim($drug_a))
                  AND toLower(trim(b.name)) = toLower(trim($drug_b))
                RETURN se.name AS side_effect, a.name AS drug_a, b.name AS drug_b
                ORDER BY side_effect
                """,
                drug_a=drug_a.strip(),
                drug_b=drug_b.strip(),
            )
            return [dict(rec) for rec in result]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# 5. Shortest interaction path
# ---------------------------------------------------------------------------

def find_interaction_path(
    drug_a: str,
    drug_b: str,
    max_hops: int = 3,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    if not drug_a or not drug_b:
        return []
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            hops = max(1, min(int(max_hops), 5))
            result = session.run(
                f"""
                MATCH (a:Drug), (b:Drug)
                WHERE toLower(trim(a.name)) = toLower(trim($drug_a))
                  AND toLower(trim(b.name)) = toLower(trim($drug_b))
                MATCH path = shortestPath((a)-[:INTERACTS_WITH*1..{hops}]-(b))
                RETURN [n IN nodes(path) | n.name] AS path_drugs,
                       length(path) AS path_length
                LIMIT 5
                """,
                drug_a=drug_a.strip(),
                drug_b=drug_b.strip(),
            )
            return [dict(rec) for rec in result]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# 6. Safer alternatives
# ---------------------------------------------------------------------------

def find_safer_alternatives(
    drug_name: str,
    current_meds: list[str],
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    if not drug_name:
        return []
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (proposed:Drug)-[:HAS_SIDE_EFFECT]->(se:SideEffect)<-[:HAS_SIDE_EFFECT]-(alt:Drug)
                WHERE toLower(trim(proposed.name)) = toLower(trim($drug_name))
                  AND proposed <> alt
                WITH alt, count(DISTINCT se) AS shared_se_count
                OPTIONAL MATCH (alt)-[:INTERACTS_WITH]-(current:Drug)
                WHERE toLower(trim(current.name)) IN $current_meds
                WITH alt, shared_se_count, count(current) AS conflict_count
                RETURN alt.name AS alternative_drug,
                       shared_se_count AS shared_side_effects_count,
                       conflict_count > 0 AS interacts_with_current
                ORDER BY interacts_with_current ASC, shared_se_count DESC
                LIMIT 10
                """,
                drug_name=drug_name.strip(),
                current_meds=[m.strip().lower() for m in current_meds],
            )
            return [dict(rec) for rec in result]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# 7. Interaction network (for visualization)
# ---------------------------------------------------------------------------

def get_interaction_network(
    drug_name: str,
    depth: int = 2,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> dict:
    if not drug_name:
        return {"nodes": [], "edges": []}
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            rel_range = max(1, min(int(depth), 5))
            result = session.run(
                f"""
                MATCH (center:Drug)
                WHERE toLower(trim(center.name)) = toLower(trim($drug_name))
                MATCH path = (center)-[:INTERACTS_WITH*1..{rel_range}]-(neighbor:Drug)
                WITH center, collect(DISTINCT neighbor) AS neighbors
                WITH [center] + neighbors AS all_nodes
                UNWIND all_nodes AS n
                OPTIONAL MATCH (n)-[r:INTERACTS_WITH]-(m:Drug)
                WHERE m IN all_nodes
                RETURN n.name AS node_name,
                       m.name AS neighbor_name,
                       coalesce(r.severity, 'unknown') AS severity,
                       coalesce(r.weight, 1) AS weight
                LIMIT 500
                """,
                drug_name=drug_name.strip(),
            )
            nodes_set = set()
            edges = []
            seen_edges = set()
            for rec in result:
                nodes_set.add(rec["node_name"])
                if rec["neighbor_name"]:
                    nodes_set.add(rec["neighbor_name"])
                    edge_key = tuple(sorted([rec["node_name"], rec["neighbor_name"]]))
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append({
                            "source": rec["node_name"],
                            "target": rec["neighbor_name"],
                            "severity": rec["severity"],
                            "weight": rec["weight"],
                        })
            return {"nodes": [{"name": n} for n in nodes_set], "edges": edges}
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# 8. Graph statistics
# ---------------------------------------------------------------------------

def get_drug_stats(
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> dict:
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)
                OPTIONAL MATCH (d)-[i:INTERACTS_WITH]-()
                OPTIONAL MATCH (d)-[h:HAS_SIDE_EFFECT]->()
                WITH count(DISTINCT d) AS total_drugs,
                     count(DISTINCT i) AS total_interactions,
                     count(DISTINCT h) AS total_side_effect_links
                MATCH (se:SideEffect)
                RETURN total_drugs,
                       total_interactions,
                       total_side_effect_links,
                       count(DISTINCT se) AS unique_side_effects
                """
            )
            record = result.single()
            return dict(record) if record else {}
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# 9. Discover drugs that have interactions (diagnostic)
# ---------------------------------------------------------------------------

def find_example_interacting_drugs(
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
    limit: int = 5,
) -> list[dict]:
    """Find drugs that have the most interactions, for use as valid test examples."""
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)-[r:INTERACTS_WITH]-()
                WITH d.name AS drug_name, count(r) AS interaction_count
                ORDER BY interaction_count DESC
                LIMIT $limit
                RETURN drug_name, interaction_count
                """,
                limit=limit,
            )
            return [dict(rec) for rec in result]
    finally:
        driver.close()


def find_interacting_group(
    drug_name: str,
    group_size: int = 5,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[str]:
    """
    Given a drug, find a group of drugs that ALL interact with each other.
    Returns a list of drug names forming a connected clique.
    """
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (center:Drug)-[r:INTERACTS_WITH]-(neighbor:Drug)
                WHERE toLower(trim(center.name)) = toLower(trim($drug_name))
                RETURN neighbor.name AS name, coalesce(r.weight, 1) AS w
                ORDER BY w DESC
                LIMIT 50
                """,
                drug_name=drug_name.strip(),
            )
            neighbors = [rec["name"] for rec in result]

            if not neighbors:
                return [drug_name]

            group = [drug_name]
            for candidate in neighbors:
                if len(group) >= group_size:
                    break
                check = session.run(
                    """
                    MATCH (c:Drug)-[:INTERACTS_WITH]-(g:Drug)
                    WHERE toLower(trim(c.name)) = toLower(trim($candidate))
                      AND toLower(trim(g.name)) IN $group_lower
                    RETURN count(*) AS connections
                    """,
                    candidate=candidate,
                    group_lower=[g.lower() for g in group],
                )
                rec = check.single()
                if rec and rec["connections"] >= len(group) * 0.5:
                    group.append(candidate)

            return group
    finally:
        driver.close()


def find_drugs_known_severity_polypharmacy(
    min_interactions: int = 2,
    limit: int = 100,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    """
    Return drugs that (1) have no INTERACTS_WITH edges with severity 'unknown',
    and (2) satisfy polypharmacy (at least min_interactions other drugs they interact with).
    """
    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)-[r:INTERACTS_WITH]-(o:Drug)
                WITH d.name AS drug_name, collect(r) AS rels
                WHERE size(rels) >= $min_interactions
                  AND all(r IN rels WHERE r.severity IS NOT NULL
                      AND toLower(trim(toString(r.severity))) <> 'unknown')
                RETURN drug_name, size(rels) AS interaction_count
                ORDER BY interaction_count DESC
                LIMIT $limit
                """,
                min_interactions=min_interactions,
                limit=limit,
            )
            return [dict(rec) for rec in result]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# CLI for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test Neo4j drug safety queries")
    parser.add_argument("--uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="password")
    parser.add_argument("--drug", default="", help="Drug to query (leave blank to auto-discover)")
    parser.add_argument(
        "--current-meds", default="",
        help="Comma-separated current medications (leave blank to auto-discover)",
    )
    parser.add_argument("--alt-drug", default="", help="Second drug for path/shared queries")
    parser.add_argument(
        "--known-severity-polypharmacy",
        action="store_true",
        help="List drugs with no unknown edges that satisfy polypharmacy (min 2 interactions)",
    )
    parser.add_argument("--known-severity-limit", type=int, default=50)
    args = parser.parse_args()

    conn = {"uri": args.uri, "user": args.user, "password": args.password}

    if args.known_severity_polypharmacy:
        print("\n=== Drugs with known severity only (no unknown edges), polypharmacy ===\n")
        rows = find_drugs_known_severity_polypharmacy(
            min_interactions=2, limit=args.known_severity_limit, **conn
        )
        for r in rows:
            print(f"  {r['drug_name']}: {r['interaction_count']} interactions")
        print(f"\nTotal: {len(rows)} drugs")
        exit(0)

    current = [m.strip() for m in args.current_meds.split(",") if m.strip()]

    # --- 0. Graph stats ---
    print("\n=== Graph Statistics ===")
    stats = get_drug_stats(**conn)
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # --- 0b. Discover if user didn't provide drugs ---
    drug = args.drug.strip()
    current_input = args.current_meds.strip()

    if not drug or not current_input:
        print("\n=== Discovering drugs with interactions ===")
        top_drugs = find_example_interacting_drugs(**conn, limit=10)
        if top_drugs:
            print("  Top drugs by interaction count:")
            for td in top_drugs:
                print(f"    {td['drug_name']}: {td['interaction_count']} interactions")

            if not drug:
                drug = top_drugs[0]["drug_name"]
                print(f"\n  Auto-selected proposed drug: {drug}")

            if not current_input:
                group = find_interacting_group(drug, group_size=5, **conn)
                proposed_idx = next((i for i, g in enumerate(group) if g.lower() == drug.lower()), 0)
                current = [g for i, g in enumerate(group) if i != proposed_idx]
                print(f"  Auto-discovered current meds: {current}")
            else:
                current = [m.strip() for m in current_input.split(",") if m.strip()]
        else:
            print("  No interactions found in graph! Run load_drugbank_to_neo4j.py first.")
            current = []
    else:
        current = [m.strip() for m in current_input.split(",") if m.strip()]

    if not drug:
        print("\nNo drug to test. Exiting.")
        exit(0)

    # --- 1. Pairwise interactions ---
    print(f"\n=== Interactions: {current} vs {drug} ===")
    interactions = check_interactions(current, drug, **conn)
    if interactions:
        for i in interactions:
            print(f"  {i['current_drug']} <-> {i['proposed_drug']}: "
                  f"[{i['severity']}, w={i['weight']}] {i['description'][:100]}")
    else:
        print("  No interactions found.")

    # 3. Side effects
    print(f"\n--- Side effects: {args.drug} ---")
    effects = get_side_effects(args.drug, **conn)
    if effects:
        for e in effects[:20]:
            print(f"  {e['side_effect']} (freq: {e['frequency']}, source: {e['source']})")
        if len(effects) > 20:
            print(f"  ... and {len(effects) - 20} more")
    else:
        print("  No side effects found.")

    # --- 4. Shared side effects ---
    alt = args.alt_drug.strip()
    if alt:
        print(f"\n=== Shared side effects: {drug} & {alt} ===")
        shared = find_shared_side_effects(drug, alt, **conn)
        if shared:
            for s in shared[:15]:
                print(f"  {s['side_effect']}")
            if len(shared) > 15:
                print(f"  ... and {len(shared) - 15} more")
        else:
            print("  None found.")

        print(f"\n=== Interaction path: {drug} -> {alt} ===")
        paths = find_interaction_path(drug, alt, **conn)
        if paths:
            for p in paths:
                print(f"  {' -> '.join(p['path_drugs'])} (hops: {p['path_length']})")
        else:
            print("  No path found.")

    # --- 5. Safer alternatives ---
    print(f"\n=== Safer alternatives to {drug} (given meds: {current}) ===")
    alts = find_safer_alternatives(drug, current, **conn)
    if alts:
        for a in alts[:10]:
            conflict = "CONFLICTS" if a["interacts_with_current"] else "safe"
            print(f"  {a['alternative_drug']} "
                  f"({a['shared_side_effects_count']} shared SEs, {conflict})")
    else:
        print("  None found.")

    # --- 6. Interaction network ---
    print(f"\n=== Interaction network around {drug} (1 hop) ===")
    net = get_interaction_network(drug, depth=1, **conn)
    print(f"  Nodes: {len(net['nodes'])}, Edges: {len(net['edges'])}")

    # --- 7. Neo4j Browser query ---
    all_names = current + [drug]
    names_lower = [n.lower() for n in all_names]
    names_str = ", ".join(f"'{n}'" for n in names_lower)
    print(f"\n=== Neo4j Browser Query (paste this to see graph) ===")
    print(f"""
WITH [{names_str}] AS drugNames
MATCH (d1:Drug)-[r:INTERACTS_WITH]-(d2:Drug)
WHERE toLower(trim(d1.name)) IN drugNames
  AND toLower(trim(d2.name)) IN drugNames
RETURN d1, r, d2
""")
