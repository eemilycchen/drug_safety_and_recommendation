"""
Part 2: ETL — Parse DrugBank full database XML and load drug–drug interactions into Neo4j.
Creates Drug nodes and INTERACTS_WITH relationships.

Drug nodes use drugbank-id as the unique key (stored in rxcui for schema compatibility).
Side effects are loaded separately by load_sider_to_neo4j.py (SIDER has proper MedDRA terms).
"""

import argparse
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from neo4j import GraphDatabase


def get_connection(uri: str, user: str, password: str):
    return GraphDatabase.driver(uri, auth=(user, password))


def _interaction_severity_and_weight(description: str) -> tuple[str, int]:
    """
    Derive severity and numeric weight from interaction description text.
    Weight: 3 = major, 2 = moderate, 1 = minor.
    """
    if not description:
        return "unknown", 1
    d = description.lower()
    if any(kw in d for kw in (
        "contraindicated", "fatal", "serotonin syndrome",
        "qt prolongation", "cardiac arrest", "torsade",
        "life-threatening", "do not use", "avoid",
    )):
        return "major", 3
    if any(kw in d for kw in ("hemorrhage", "bleeding", "seizure", "hypotension")):
        return "major", 3
    if any(kw in d for kw in (
        "risk or severity", "risk of", "increase the risk",
        "nephrotoxic", "hepatotoxic", "neurotoxic",
        "adverse effects can be increased",
    )):
        return "moderate", 2
    if any(kw in d for kw in (
        "may increase", "may decrease", "serum concentration",
        "activities of", "metabolism of", "absorption of",
    )):
        return "minor", 1
    return "unknown", 1


def ensure_constraints(session):
    session.run("CREATE CONSTRAINT drug_rxcui IF NOT EXISTS FOR (d:Drug) REQUIRE d.rxcui IS UNIQUE")


def build_atc_map(xml_path: str) -> dict[str, list[str]]:
    """First pass: scan every <drug> block and record drugbank_id -> atc_codes."""
    atc_map: dict[str, list[str]] = {}
    current = {"id": None, "atc_codes": []}
    take_next_name = False

    for event, elem in ET.iterparse(xml_path, events=("start", "end")):
        tag = elem.tag
        if not isinstance(tag, str):
            continue
        local = tag.split("}", 1)[1] if "}" in tag else tag

        if event == "start":
            if local == "drug":
                current = {"id": None, "atc_codes": []}
                take_next_name = False
            continue

        if local == "drugbank-id" and elem.get("primary") == "true" and elem.text:
            current["id"] = elem.text.strip()
            take_next_name = True
        elif local == "name" and take_next_name:
            take_next_name = False
        elif local == "atc-code" and elem.get("code"):
            current["atc_codes"].append(elem.get("code").strip())
        elif local == "drug-interaction":
            elem.clear()
        elif local == "drug":
            if current["id"]:
                atc_map[current["id"]] = list(current["atc_codes"])
            elem.clear()

    return atc_map


def iter_drugbank_drugs(xml_path: str):
    """
    Yield (drugbank_id, drug_name, atc_codes, interactions) from DrugBank XML.
    interactions: list[(other_id, other_name, description)]
    """
    current = {"id": None, "name": None, "atc_codes": [], "interactions": []}
    take_next_name = False

    for event, elem in ET.iterparse(xml_path, events=("start", "end")):
        tag = elem.tag
        if not isinstance(tag, str):
            continue
        local = tag.split("}", 1)[1] if "}" in tag else tag

        if event == "start":
            if local == "drug":
                current = {"id": None, "name": None, "atc_codes": [], "interactions": []}
                take_next_name = False
            continue

        if local == "drugbank-id" and elem.get("primary") == "true" and elem.text:
            current["id"] = elem.text.strip()
            take_next_name = True
        elif local == "name":
            text = (elem.text or "").strip()
            if take_next_name and text:
                current["name"] = text
                take_next_name = False
        elif local == "atc-code" and elem.get("code"):
            current["atc_codes"].append(elem.get("code").strip())
        elif local == "drug-interaction":
            di_id = di_name = di_desc = None
            for c in elem:
                clocal = c.tag.split("}", 1)[1] if "}" in c.tag else c.tag
                if clocal == "drugbank-id" and c.text:
                    di_id = c.text.strip()
                elif clocal == "name" and c.text:
                    di_name = c.text.strip()
                elif clocal == "description" and c.text:
                    di_desc = (c.text or "").strip()
            if di_id and di_name:
                current["interactions"].append((di_id, di_name, di_desc or ""))
            elem.clear()
        elif local == "drug":
            if current["id"] and current["name"]:
                yield (current["id"], current["name"], list(current["atc_codes"]), current["interactions"])
            elem.clear()


def flush_batch(session, drugs: list[dict], edges: list[dict]) -> None:
    if drugs:
        session.run(
            """
            UNWIND $drugs AS d
            MERGE (n:Drug {rxcui: d.id})
            SET n.name = d.name,
                n.atc_codes = CASE
                    WHEN d.atc_codes IS NOT NULL AND size(d.atc_codes) > 0
                    THEN d.atc_codes
                    ELSE coalesce(n.atc_codes, [])
                END
            """,
            drugs=drugs,
        )
    if edges:
        session.run(
            """
            UNWIND $edges AS e
            MERGE (d1:Drug {rxcui: e.src_id})
            SET d1.name = coalesce(d1.name, e.src_name)
            MERGE (d2:Drug {rxcui: e.tgt_id})
            SET d2.name = coalesce(d2.name, e.tgt_name)
            MERGE (d1)-[r:INTERACTS_WITH]->(d2)
            SET r.severity = e.severity,
                r.description = e.description,
                r.weight = e.weight
            """,
            edges=edges,
        )


def load_drugbank_interactions_batched(
    session,
    xml_path: str,
    batch_edges: int = 100_000,
    atc_map: dict[str, list[str]] | None = None,
) -> tuple[int, int]:
    if atc_map is None:
        atc_map = {}

    def atc_for(drug_id: str, fallback: list[str] | None = None) -> list[str]:
        if drug_id in atc_map:
            return atc_map[drug_id]
        return list(fallback) if fallback else []

    drugs_seen: dict[str, dict] = {}
    pending_edges: list[dict] = []
    processed_drugs = 0
    processed_edges = 0

    t0 = time.time()
    last_flush = t0

    for drugbank_id, drug_name, atc_codes, interactions in iter_drugbank_drugs(xml_path):
        processed_drugs += 1
        drugs_seen[drugbank_id] = {
            "name": drug_name,
            "atc_codes": atc_for(drugbank_id, atc_codes),
        }
        for other_id, other_name, description in interactions:
            if other_id not in drugs_seen:
                drugs_seen[other_id] = {
                    "name": other_name,
                    "atc_codes": atc_for(other_id, []),
                }
            desc = (description or "DrugBank interaction")[:2000]
            severity, weight = _interaction_severity_and_weight(desc)
            pending_edges.append({
                "src_id": drugbank_id,
                "src_name": drug_name,
                "tgt_id": other_id,
                "tgt_name": other_name,
                "description": desc,
                "severity": severity,
                "weight": weight,
            })
        if len(pending_edges) >= batch_edges or len(drugs_seen) >= 20_000:
            flush_drugs = [
                {"id": k, "name": v["name"], "atc_codes": atc_for(k, v.get("atc_codes"))}
                for k, v in drugs_seen.items()
            ]
            n_edges = len(pending_edges)
            flush_batch(session, flush_drugs, pending_edges)
            processed_edges += n_edges
            drugs_seen.clear()
            pending_edges.clear()
            now = time.time()
            dt = now - last_flush
            total_dt = now - t0
            rate = (processed_edges / total_dt) if total_dt > 0 else 0.0
            print(
                f"Flushed {len(flush_drugs)} drugs, {n_edges} edges. "
                f"Total edges: {processed_edges}. Rate: {rate:,.0f} edges/s. Last batch: {dt:,.1f}s."
            )
            last_flush = now

    if drugs_seen or pending_edges:
        flush_drugs = [
            {"id": k, "name": v["name"], "atc_codes": atc_for(k, v.get("atc_codes"))}
            for k, v in drugs_seen.items()
        ]
        flush_batch(session, flush_drugs, pending_edges)
        processed_edges += len(pending_edges)

    return processed_drugs, processed_edges


def main():
    parser = argparse.ArgumentParser(
        description="Load DrugBank XML drug–drug interactions into Neo4j"
    )
    parser.add_argument("--file", "-f", required=True, help="Path to DrugBank full database.xml")
    parser.add_argument("--uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="password")
    parser.add_argument(
        "--batch-edges",
        type=int,
        default=100_000,
        help="Approx edges per Neo4j batch write",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    driver = get_connection(args.uri, args.user, args.password)
    try:
        with driver.session() as session:
            ensure_constraints(session)
            print("Pass 1: building ATC map from XML...")
            t_atc = time.time()
            atc_map = build_atc_map(str(path))
            print(f"  ATC map: {len(atc_map)} drugs ({time.time() - t_atc:.1f}s)")
            with_atc = sum(1 for v in atc_map.values() if v)
            print(f"  Drugs with ATC: {with_atc}")
            print("Pass 2: loading interactions...")
            num_drugs, num_edges = load_drugbank_interactions_batched(
                session, str(path), batch_edges=args.batch_edges, atc_map=atc_map,
            )
        print(f"Done. {num_drugs} drugs, {num_edges} INTERACTS_WITH edges.")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
