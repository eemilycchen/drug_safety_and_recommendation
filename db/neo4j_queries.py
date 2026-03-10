"""
Part 2: Neo4j query functions for drug–drug interactions and side effects.
Used by the application layer (Part 5) to check drug safety.
"""

from neo4j import GraphDatabase


def get_connection(uri: str = "bolt://127.0.0.1:7687", user: str = "neo4j", password: str = "password"):
    return GraphDatabase.driver(uri, auth=(user, password))


def check_interactions(
    current_med_names: list[str],
    proposed_drug: str,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    """
    Returns list of interactions between current meds and the proposed drug.
    Each dict: {current_drug, proposed_drug, severity, description}

    Uses undirected INTERACTS_WITH match so direction of stored edge doesn't matter.
    """
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
                RETURN DISTINCT
                    d1.name AS current_drug,
                    d2.name AS proposed_drug,
                    coalesce(r.severity, 'unknown') AS severity,
                    coalesce(r.description, 'No description available') AS description
                """,
                current_meds=[name.strip().lower() for name in current_med_names],
                proposed=proposed_drug.strip(),
            )
            return [dict(record) for record in result]
    finally:
        driver.close()


def get_side_effects(
    drug_name: str,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    """
    Returns known side effects for a drug.
    Each dict: {side_effect, frequency}
    """
    if not drug_name:
        return []

    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)-[r:HAS_SIDE_EFFECT]->(se:SideEffect)
                WHERE toLower(trim(d.name)) = toLower(trim($drug_name))
                RETURN DISTINCT
                    se.name AS side_effect,
                    coalesce(r.frequency, 'unknown') AS frequency
                ORDER BY se.name
                """,
                drug_name=drug_name.strip(),
            )
            return [dict(record) for record in result]
    finally:
        driver.close()


def find_interaction_path(
    drug_a: str,
    drug_b: str,
    max_hops: int = 3,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    """
    Find the shortest interaction path between two drugs through shared interacting drugs.
    Like the example project's "shortest path" query (Task 3) — shows how drugs are
    connected through chains of interactions even if they don't directly interact.
    Each dict: {path_drugs: [name1, name2, ...], path_length: int}
    """
    if not drug_a or not drug_b:
        return []

    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Drug), (b:Drug)
                WHERE toLower(trim(a.name)) = toLower(trim($drug_a))
                  AND toLower(trim(b.name)) = toLower(trim($drug_b))
                MATCH path = shortestPath((a)-[:INTERACTS_WITH*1..""" + str(max_hops) + """]->(b))
                RETURN [n IN nodes(path) | n.name] AS path_drugs,
                       length(path) AS path_length
                LIMIT 5
                """,
                drug_a=drug_a.strip(),
                drug_b=drug_b.strip(),
            )
            return [dict(record) for record in result]
    finally:
        driver.close()


def find_shared_side_effects(
    drug_a: str,
    drug_b: str,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    """
    Multi-hop traversal: Drug A -> SideEffect <- Drug B.
    Finds side effects common to both drugs (useful for understanding combined risk).
    Each dict: {side_effect, drug_a, drug_b}
    """
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
                RETURN DISTINCT
                    se.name AS side_effect,
                    a.name AS drug_a,
                    b.name AS drug_b
                ORDER BY se.name
                """,
                drug_a=drug_a.strip(),
                drug_b=drug_b.strip(),
            )
            return [dict(record) for record in result]
    finally:
        driver.close()


def find_safer_alternatives(
    drug_name: str,
    current_meds: list[str],
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> list[dict]:
    """
    Graph-based recommendation: find drugs that share side effects with the proposed drug
    (i.e. treat similar conditions) but do NOT interact with the patient's current meds.
    Like the example project's "similar companies" network query (Task 4).
    Each dict: {alternative_drug, shared_side_effects_count, interacts_with_current: bool}
    """
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
            return [dict(record) for record in result]
    finally:
        driver.close()


def get_interaction_network(
    drug_name: str,
    depth: int = 2,
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> dict:
    """
    Network visualization query: get the interaction neighborhood around a drug up to N hops.
    Like the example project's graph visualizations (Tasks 4 & 5).
    Returns {nodes: [{name}], edges: [{source, target, description}]}.
    (Degree omitted: Neo4j 5+ disallows size(pattern); alternatives use COUNT {} subqueries.)
    """
    if not drug_name:
        return {"nodes": [], "edges": []}

    driver = get_connection(uri, user, password)
    try:
        with driver.session() as session:
            # Try APOC subgraph first. Neo4j 5+ forbids size((n)-[:R]-()); use COUNT {} or omit degree.
            try:
                result = session.run(
                    """
                    MATCH (center:Drug)
                    WHERE toLower(trim(center.name)) = toLower(trim($drug_name))
                    CALL apoc.path.subgraphAll(center, {
                        relationshipFilter: "INTERACTS_WITH",
                        maxLevel: $depth
                    })
                    YIELD nodes, relationships
                    RETURN
                        [n IN nodes | {name: n.name}] AS nodes,
                        [r IN relationships | {
                            source: startNode(r).name,
                            target: endNode(r).name,
                            description: coalesce(r.description, '')
                        }] AS edges
                    """,
                    drug_name=drug_name.strip(),
                    depth=depth,
                )
                record = result.single()
                if record and record["nodes"] is not None:
                    return {"nodes": record["nodes"], "edges": record["edges"]}
            except Exception:
                pass  # APOC missing or other error — use fallback below

            # Fallback without APOC; depth interpolated (safe int only from caller)
            rel_range = max(1, min(int(depth), 5))
            result2 = session.run(
                f"""
                MATCH (center:Drug)
                WHERE toLower(trim(center.name)) = toLower(trim($drug_name))
                MATCH path = (center)-[:INTERACTS_WITH*1..{rel_range}]-(neighbor:Drug)
                WITH center, collect(DISTINCT neighbor) AS neighbors
                WITH [center] + neighbors AS all_nodes
                UNWIND all_nodes AS n
                OPTIONAL MATCH (n)-[r:INTERACTS_WITH]-(m:Drug)
                WHERE m IN all_nodes
                RETURN DISTINCT
                    n.name AS node_name,
                    m.name AS neighbor_name,
                    coalesce(r.description, '') AS description
                LIMIT 200
                """,
                drug_name=drug_name.strip(),
            )
            nodes_set = set()
            edges = []
            for rec in result2:
                nodes_set.add(rec["node_name"])
                if rec["neighbor_name"]:
                    nodes_set.add(rec["neighbor_name"])
                    edges.append({
                        "source": rec["node_name"],
                        "target": rec["neighbor_name"],
                        "description": rec["description"],
                    })
            return {
                "nodes": [{"name": n} for n in nodes_set],
                "edges": edges,
            }
    finally:
        driver.close()


def get_drug_stats(
    uri: str = "bolt://127.0.0.1:7687",
    user: str = "neo4j",
    password: str = "password",
) -> dict:
    """
    Summary statistics about the graph: total drugs, interactions, side effects, etc.
    Useful for the report and verifying data loaded correctly.
    """
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test Neo4j drug safety queries")
    parser.add_argument("--uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="password")
    parser.add_argument("--drug", default="Warfarin", help="Drug to check side effects for")
    parser.add_argument(
        "--current-meds",
        default="Aspirin",
        help="Comma-separated current medications to check interactions against --drug",
    )
    parser.add_argument(
        "--alt-drug",
        default="",
        help="Second drug for path/shared-side-effect queries",
    )
    args = parser.parse_args()

    current = [m.strip() for m in args.current_meds.split(",") if m.strip()]
    conn = {"uri": args.uri, "user": args.user, "password": args.password}

    # 1. Graph stats
    print("\n--- Graph Statistics ---")
    stats = get_drug_stats(**conn)
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # 2. Interactions
    print(f"\n--- Interactions: {current} vs {args.drug} ---")
    interactions = check_interactions(current, args.drug, **conn)
    args = parser.parse_args()

    current = [m.strip() for m in args.current_meds.split(",") if m.strip()]

    print(f"\n--- Interactions: {current} vs {args.drug} ---")
    interactions = check_interactions(current, args.drug, args.uri, args.user, args.password)
    if interactions:
        for i in interactions:
            print(f"  {i['current_drug']} <-> {i['proposed_drug']}: "
                  f"[{i['severity']}] {i['description'][:120]}")
    else:
        print("  No interactions found.")

    # 3. Side effects
    print(f"\n--- Side effects: {args.drug} ---")
    effects = get_side_effects(args.drug, **conn)
    print(f"\n--- Side effects: {args.drug} ---")
    effects = get_side_effects(args.drug, args.uri, args.user, args.password)
    if effects:
        for e in effects[:20]:
            print(f"  {e['side_effect']} (freq: {e['frequency']})")
        if len(effects) > 20:
            print(f"  ... and {len(effects) - 20} more")
    else:
        print("  No side effects found.")

    # 4. Shared side effects (if --alt-drug given)
    alt = args.alt_drug.strip()
    if alt:
        print(f"\n--- Shared side effects: {args.drug} & {alt} ---")
        shared = find_shared_side_effects(args.drug, alt, **conn)
        if shared:
            for s in shared[:15]:
                print(f"  {s['side_effect']}")
            if len(shared) > 15:
                print(f"  ... and {len(shared) - 15} more")
        else:
            print("  No shared side effects found.")

        print(f"\n--- Interaction path: {args.drug} -> {alt} ---")
        paths = find_interaction_path(args.drug, alt, **conn)
        if paths:
            for p in paths:
                print(f"  {' -> '.join(p['path_drugs'])} (hops: {p['path_length']})")
        else:
            print("  No interaction path found.")

    # 5. Safer alternatives
    print(f"\n--- Safer alternatives to {args.drug} (given current meds: {current}) ---")
    alts = find_safer_alternatives(args.drug, current, **conn)
    if alts:
        for a in alts[:10]:
            conflict = "CONFLICTS" if a["interacts_with_current"] else "safe"
            print(f"  {a['alternative_drug']} "
                  f"({a['shared_side_effects_count']} shared SEs, {conflict})")
    else:
        print("  No alternatives found.")

    # 6. Interaction network
    print(f"\n--- Interaction network around {args.drug} (2 hops) ---")
    net = get_interaction_network(args.drug, depth=2, **conn)
    print(f"  Nodes: {len(net['nodes'])}, Edges: {len(net['edges'])}")
