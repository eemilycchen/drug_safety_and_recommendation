"""
Load a small sample of openFDA data for Qdrant (FAERS) and NDC (alternatives cache).

Use for quick local testing without a full ETL run.

Usage (from project root):
    python -m etl.load_sample_openfda
    python -m etl.load_sample_openfda --faers-limit 50 --ndc-classes 5
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load small openFDA sample: FAERS → Qdrant, NDC → alternatives cache"
    )
    parser.add_argument(
        "--faers-limit",
        type=int,
        default=100,
        help="Max FAERS reports to fetch and load into Qdrant (default: 100)",
    )
    parser.add_argument(
        "--ndc-classes",
        type=int,
        default=2,
        help="Max NDC pharm_class groups to fetch for alternatives cache (default: 2)",
    )
    parser.add_argument(
        "--qdrant-path",
        type=str,
        default="",
        help="Qdrant local path (default: use Docker host)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data",
        help="Directory for faers_raw.json and openfda_alternatives.json",
    )
    args = parser.parse_args()

    cache_dir = args.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    faers_cache = cache_dir / "faers_raw.json"
    ndc_cache = cache_dir / "openfda_alternatives.json"

    # 1) FAERS → Qdrant (small sample)
    print("--- 1) Loading FAERS sample into Qdrant ---")
    cmd_faers = [
        sys.executable,
        "-m",
        "etl.load_faers_to_qdrant",
        "--limit",
        str(args.faers_limit),
    ]
    if args.qdrant_path:
        cmd_faers.extend(["--qdrant-path", args.qdrant_path])
    r1 = subprocess.run(cmd_faers, cwd=str(Path(__file__).resolve().parent.parent))
    if r1.returncode != 0:
        print("FAERS load failed.")
        sys.exit(r1.returncode)

    # 2) NDC → alternatives cache (small sample)
    print("\n--- 2) Loading NDC sample into alternatives cache ---")
    cmd_ndc = [
        sys.executable,
        "-m",
        "etl.openfda_alternatives",
        "--source",
        "ndc",
        "--max-classes",
        str(args.ndc_classes),
        "--cache",
        str(ndc_cache),
    ]
    r2 = subprocess.run(cmd_ndc, cwd=str(Path(__file__).resolve().parent.parent))
    if r2.returncode != 0:
        print("NDC load failed.")
        sys.exit(r2.returncode)

    print("\nDone. Sample loaded:")
    print(f"  Qdrant: up to {args.faers_limit} adverse event reports (adverse_events collection)")
    print(f"  NDC cache: {ndc_cache} (up to {args.ndc_classes} pharm classes)")


if __name__ == "__main__":
    main()
