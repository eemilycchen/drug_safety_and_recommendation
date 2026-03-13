"""
Part 2: ETL — Parse SIDER side-effect TSV and load into Neo4j.
Creates SideEffect nodes and HAS_SIDE_EFFECT relationships from Drug nodes.
Expects Drug nodes to exist (from load_rxnav_to_neo4j.py) when matching by name.
Supports: (1) 6-column SIDER meddra_all_se.tsv + optional drug name mapping,
          (2) Simplified 3-column TSV: drug_name, side_effect_name, frequency
"""

import argparse
import csv
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase

# SIDER meddra_all_se.tsv columns (0-indexed):
# 0: STITCH compound id (flat), 1: STITCH compound id (stereo),
# 2: UMLS concept id, 3: MedDRA concept type, 4: UMLS concept id for MedDRA term, 5: Side effect name


def get_connection(uri: str, user: str, password: str):
    return GraphDatabase.driver(uri, auth=(user, password))


def ensure_constraints(session):
    session.run(
        "CREATE CONSTRAINT side_effect_meddra IF NOT EXISTS FOR (s:SideEffect) REQUIRE s.meddra_id IS UNIQUE"
    )


def load_drug_name_mapping(path: str) -> dict[str, str]:
    """Load optional TSV: stitch_id, drug_name."""
    mapping = {}
    p = Path(path)
    if not p.exists():
        return mapping
    with open(p, encoding="utf-8") as f:
        r = csv.reader(f, delimiter="\t")
        for row in r:
            if len(row) >= 2:
                mapping[row[0].strip()] = row[1].strip()
    return mapping


def load_drug_atc_mapping(path: str) -> dict[str, list[str]]:
    """
    Load SIDER drug_atc.tsv: stitch_id, atc_code (tab-separated).
    Returns stitch_id -> [atc1, atc2, ...] (a drug can have multiple ATC codes).
    """
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
    Returns list of {"drug_key", "drug_name", "atc_codes", "meddra_id", "side_effect_name", "frequency"}.
    atc_codes from drug_atc.tsv (STITCH -> ATC) for matching to DrugBank Drug nodes.
    """
    drug_mapping = drug_mapping or {}
    atc_mapping = atc_mapping or {}
    rows = []
    with open(tsv_path, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 6:
                continue
            stitch_flat, stitch_stereo, _, _, umls_meddra, se_name = (
                row[0].strip(),
                row[1].strip(),
                row[2].strip(),
                row[3].strip(),
                row[4].strip(),
                row[5].strip(),
            )
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
    """
    Parse simplified 3-column TSV: drug_name, side_effect_name, frequency.
    """
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


def load_side_effects(
    session,
    rows: list[dict],
    log_every: int = 50_000,
):
    """
    MERGE SideEffect nodes and attach HAS_SIDE_EFFECT to existing DrugBank Drug nodes.
    Strategy: match existing Drug by name (case-insensitive). This links SIDER side effects
    to the same Drug nodes that already have INTERACTS_WITH from DrugBank XML.
    Drugs not found in Neo4j are skipped (no orphan Drug nodes created).
    """
    start = time.time()
    linked = 0
    skipped = 0
    for idx, r in enumerate(rows, start=1):
        drug_name = r["drug_name"]
        result = session.run(
            """
            MATCH (d:Drug)
            WHERE toLower(trim(d.name)) = toLower(trim($drug_name))
            WITH d LIMIT 1
            MERGE (se:SideEffect {meddra_id: $meddra_id})
            SET se.name = $se_name
            MERGE (d)-[rel:HAS_SIDE_EFFECT]->(se)
            SET rel.frequency = $frequency
            RETURN d.name AS matched
            """,
            drug_name=drug_name,
            meddra_id=r["meddra_id"],
            se_name=r["side_effect_name"],
            frequency=r["frequency"],
        )
        if result.peek():
            linked += 1
        else:
            skipped += 1
        if log_every and idx % log_every == 0:
            elapsed = time.time() - start
            rate = idx / elapsed if elapsed > 0 else 0.0
            print(
                f"Processed {idx}/{len(rows)} rows. "
                f"Linked: {linked}, Skipped (no match): {skipped}. "
                f"({rate:,.0f} rows/s)"
            )
    print(f"Final: {linked} linked, {skipped} skipped (no matching Drug in Neo4j).")


def main():
    parser = argparse.ArgumentParser(description="Load SIDER side effects into Neo4j")
    parser.add_argument("--file", "-f", required=True, help="Path to meddra_all_se.tsv or simplified TSV")
    parser.add_argument("--drug-mapping", default="", help="Optional TSV: stitch_id,drug_name")
    parser.add_argument(
        "--drug-atc",
        default="",
        help="Optional TSV: stitch_id,atc_code (required for ATC-based link to DrugBank Drug nodes)",
    )
    parser.add_argument("--uri", default="bolt://127.0.0.1:7687")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="password")
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Use 3-column TSV: drug_name, side_effect_name, frequency",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    drug_mapping = load_drug_name_mapping(args.drug_mapping) if args.drug_mapping else None
    atc_mapping = load_drug_atc_mapping(args.drug_atc) if args.drug_atc else None

    if args.simple:
        rows = parse_simple_tsv(str(path))
    else:
        rows = parse_meddra_tsv(str(path), drug_mapping, atc_mapping)

    if not rows:
        # Detect likely wrong file type and suggest the right script
        with open(path, "rb") as f:
            head = f.read(200)
        if head.lstrip().startswith(b"<?xml") or head.lstrip().startswith(b"<"):
            print("No rows parsed. This file looks like XML.")
            print("  The SIDER loader expects TSV: meddra_all_se.tsv (6 columns) or --simple (3 columns).")
            print("  For DrugBank XML (e.g. full database.xml) load drug–drug interactions with:")
            print('    python etl/load_drugbank_to_neo4j.py --file "full database.xml"')
            print("  For SIDER side-effect data, download: http://sideeffects.embl.de/media/download/meddra_all_se.tsv.gz")
        else:
            print("No rows parsed. Check file format (6-column SIDER or --simple 3-column).")
        sys.exit(1)

    driver = get_connection(args.uri, args.user, args.password)
    try:
        with driver.session() as session:
            ensure_constraints(session)
            load_side_effects(session, rows)
        print(f"Loaded {len(rows)} side-effect links from SIDER TSV.")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
