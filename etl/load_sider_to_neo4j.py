"""
Part 2: ETL — Parse SIDER side-effect TSV and load into Neo4j.
Creates SideEffect nodes and HAS_SIDE_EFFECT relationships linked to Drug nodes
that were loaded from DrugBank XML (by load_drugbank_to_neo4j.py).

Matching strategy (in order of priority):
  1. Exact case-insensitive name match against Drug.name
  2. CONTAINS match (SIDER name inside DrugBank name or vice versa)
  3. ATC code match (if drug_atc.tsv is provided)
Unmatched SIDER drugs are skipped.
"""

import argparse
import csv
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase

FREQUENCY_WEIGHT = {
    "very common": 4, "common": 3, "uncommon": 2,
    "rare": 1, "very rare": 1, "unknown": 1,
}


def get_connection(uri: str, user: str, password: str):
    return GraphDatabase.driver(uri, auth=(user, password))


def ensure_constraints(session):
    session.run(
        "CREATE CONSTRAINT side_effect_meddra IF NOT EXISTS "
        "FOR (s:SideEffect) REQUIRE s.meddra_id IS UNIQUE"
    )


def _side_effect_weight(frequency: str) -> int:
    if not frequency:
        return 1
    return FREQUENCY_WEIGHT.get(frequency.strip().lower(), 1)


def load_drug_name_mapping(path: str) -> dict[str, str]:
    """Load SIDER drug_names.tsv: stitch_id -> drug_name."""
    mapping = {}
    p = Path(path)
    if not p.exists():
        return mapping
    with open(p, encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                mapping[parts[0].strip()] = parts[1].strip()
    return mapping


def load_drug_atc_mapping(path: str) -> dict[str, list[str]]:
    """Load SIDER drug_atc.tsv: stitch_id -> [atc_code, ...]."""
    mapping: dict[str, list[str]] = {}
    p = Path(path)
    if not p.exists():
        return mapping
    with open(p, encoding="utf-8") as f:
        r = csv.reader(f, delimiter="\t")
        for row in r:
            if len(row) >= 2:
                stitch = row[0].strip()
                atc = row[1].strip()
                if stitch and atc:
                    mapping.setdefault(stitch, []).append(atc)
    return mapping


def parse_meddra_tsv(
    tsv_path: str,
    drug_mapping: dict[str, str] | None = None,
    atc_mapping: dict[str, list[str]] | None = None,
) -> list[dict]:
    """
    Parse 6-column SIDER meddra_all_se.tsv.
    Returns list of {drug_key, drug_name, atc_codes, meddra_id, side_effect_name, frequency}.
    """
    drug_mapping = drug_mapping or {}
    atc_mapping = atc_mapping or {}
    rows = []
    with open(tsv_path, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 6:
                continue
            stitch_flat = row[0].strip()
            stitch_stereo = row[1].strip()
            umls_meddra = row[4].strip()
            se_name = row[5].strip()
            if not se_name or not umls_meddra:
                continue
            drug_key = stitch_stereo or stitch_flat
            drug_name = drug_mapping.get(drug_key) or drug_mapping.get(stitch_flat) or drug_key
            atc_codes = atc_mapping.get(drug_key) or atc_mapping.get(stitch_flat) or []
            rows.append({
                "drug_key": drug_key,
                "drug_name": drug_name,
                "atc_codes": atc_codes,
                "meddra_id": umls_meddra,
                "side_effect_name": se_name,
                "frequency": "unknown",
            })
    return rows


def parse_simple_tsv(tsv_path: str) -> list[dict]:
    """Parse simplified 3-column TSV: drug_name, side_effect_name, frequency."""
    rows = []
    with open(tsv_path, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 2:
                continue
            drug_name = row[0].strip()
            se_name = row[1].strip()
            frequency = row[2].strip() if len(row) > 2 else "unknown"
            if drug_name and se_name:
                rows.append({
                    "drug_key": drug_name,
                    "drug_name": drug_name,
                    "atc_codes": [],
                    "meddra_id": se_name.replace(" ", "_")[:64],
                    "side_effect_name": se_name,
                    "frequency": frequency or "unknown",
                })
    return rows


def _build_neo4j_drug_index(session) -> dict[str, str]:
    """
    Pull all Drug node names from Neo4j, return {lowercase_name: original_name}.
    Used for matching SIDER drug names to existing DrugBank Drug nodes.
    """
    result = session.run("MATCH (d:Drug) WHERE d.name IS NOT NULL RETURN d.name AS name")
    index = {}
    for record in result:
        name = record["name"]
        index[name.lower()] = name
    return index


def _resolve_drug_name(
    sider_name: str,
    neo4j_index: dict[str, str],
) -> str | None:
    """
    Try to match SIDER drug name to a DrugBank Drug node:
      1. Exact case-insensitive
      2. SIDER name is contained in a DrugBank name
      3. DrugBank name is contained in SIDER name
    """
    key = sider_name.lower().strip()
    if key in neo4j_index:
        return neo4j_index[key]
    for db_lower, db_original in neo4j_index.items():
        if key in db_lower or db_lower in key:
            return db_original
    return None


def load_side_effects(
    session,
    rows: list[dict],
    batch_size: int = 5000,
):
    """
    Batch-load SideEffect nodes and HAS_SIDE_EFFECT edges.
    First builds a mapping of SIDER drug names -> Neo4j Drug names, then
    inserts only matched rows in UNWIND batches.
    """
    start = time.time()
    print("  Building Drug name index from Neo4j...")
    neo4j_index = _build_neo4j_drug_index(session)
    print(f"  Found {len(neo4j_index)} Drug nodes in Neo4j.")

    unique_sider_names = {r["drug_name"] for r in rows}
    print(f"  Unique SIDER drug names: {len(unique_sider_names)}")

    name_map: dict[str, str | None] = {}
    for sider_name in unique_sider_names:
        name_map[sider_name] = _resolve_drug_name(sider_name, neo4j_index)

    matched = sum(1 for v in name_map.values() if v is not None)
    print(f"  Matched {matched}/{len(unique_sider_names)} SIDER drugs to Neo4j Drug nodes.")

    batch = []
    total_linked = 0
    total_skipped = 0

    for r in rows:
        resolved_name = name_map.get(r["drug_name"])
        if resolved_name is None:
            total_skipped += 1
            continue
        batch.append({
            "drug_name": resolved_name,
            "meddra_id": r["meddra_id"],
            "se_name": r["side_effect_name"],
            "frequency": r["frequency"],
            "weight": _side_effect_weight(r.get("frequency", "unknown")),
        })
        if len(batch) >= batch_size:
            _flush_se_batch(session, batch)
            total_linked += len(batch)
            elapsed = time.time() - start
            rate = total_linked / elapsed if elapsed > 0 else 0
            print(
                f"  Loaded {total_linked} side-effect links "
                f"(skipped {total_skipped}). {rate:,.0f} rows/s"
            )
            batch.clear()

    if batch:
        _flush_se_batch(session, batch)
        total_linked += len(batch)

    elapsed = time.time() - start
    print(f"  Done: {total_linked} linked, {total_skipped} skipped ({elapsed:.1f}s).")


def _flush_se_batch(session, batch: list[dict]) -> None:
    session.run(
        """
        UNWIND $rows AS r
        MATCH (d:Drug)
        WHERE toLower(trim(d.name)) = toLower(trim(r.drug_name))
        MERGE (se:SideEffect {meddra_id: r.meddra_id})
        SET se.name = r.se_name
        MERGE (d)-[rel:HAS_SIDE_EFFECT]->(se)
        SET rel.frequency = r.frequency,
            rel.weight = r.weight,
            rel.source = 'sider'
        """,
        rows=batch,
    )


def main():
    parser = argparse.ArgumentParser(description="Load SIDER side effects into Neo4j")
    parser.add_argument("--file", "-f", required=True, help="Path to meddra_all_se.tsv or simplified TSV")
    parser.add_argument("--drug-mapping", default="", help="Optional TSV: stitch_id,drug_name (drug_names.tsv)")
    parser.add_argument(
        "--drug-atc", default="",
        help="Optional TSV: stitch_id,atc_code (drug_atc.tsv)",
    )
    parser.add_argument("--uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="password")
    parser.add_argument(
        "--simple", action="store_true",
        help="Use 3-column TSV: drug_name, side_effect_name, frequency",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    with open(path, "rb") as f:
        head = f.read(200)
    if head.lstrip().startswith(b"<?xml") or head.lstrip().startswith(b"<"):
        print("ERROR: This file is XML, not a SIDER TSV.")
        print('  For DrugBank XML: python etl/load_drugbank_to_neo4j.py --file "full database.xml"')
        print("  For SIDER: use meddra_all_se.tsv (download from sideeffects.embl.de)")
        sys.exit(1)

    drug_mapping = load_drug_name_mapping(args.drug_mapping) if args.drug_mapping else None
    atc_mapping = load_drug_atc_mapping(args.drug_atc) if args.drug_atc else None

    if args.simple:
        rows = parse_simple_tsv(str(path))
    else:
        rows = parse_meddra_tsv(str(path), drug_mapping, atc_mapping)

    if not rows:
        print("No rows parsed. Check file format (6-column SIDER or --simple 3-column).")
        sys.exit(1)

    print(f"Parsed {len(rows)} rows from SIDER TSV.")

    driver = get_connection(args.uri, args.user, args.password)
    try:
        with driver.session() as session:
            ensure_constraints(session)
            load_side_effects(session, rows)
        print("SIDER load complete.")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
