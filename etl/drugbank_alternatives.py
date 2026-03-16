"""
Build drug -> [alternatives] from DrugBank full database XML using ATC level 4.

By default only includes approved drugs (trusted). Uses streaming parse (iterparse).
Aliases are derived from DrugBank <synonyms> in the XML (no hardcoded list).

Usage:
  python -m etl.drugbank_alternatives --xml "data/full database.xml" --out data/drugbank_alternatives.json
  python -m etl.drugbank_alternatives --xml "data/full database.xml" --all   # include experimental etc.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

try:
    from lxml import etree as ET
    _has_lxml = True
except ImportError:
    try:
        import xml.etree.ElementTree as ET
        _has_lxml = False
    except ImportError:
        ET = None
        _has_lxml = False

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

NS = "http://www.drugbank.ca"


def _tag(name: str) -> str:
    return f"{{{NS}}}{name}" if NS else name


def _get_text(el, default: str = "") -> str:
    if el is None or el.text is None:
        return default
    return (el.text or "").strip()


def _get_approved(groups_el) -> bool:
    if groups_el is None:
        return False
    for g in groups_el.findall(_tag("group")):
        if _get_text(g).lower() == "approved":
            return True
    return False


def _atc_level4_codes(atc_codes_el) -> set[str]:
    codes = set()
    if atc_codes_el is None:
        return codes
    for atc in atc_codes_el.findall(_tag("atc-code")):
        code_attr = atc.get("code") if atc.get is not None else None
        if code_attr and len(code_attr) >= 5:
            codes.add(code_attr[:5])
        for level in atc.findall(_tag("level")):
            c = level.get("code") if level.get is not None else None
            if c and len(c) == 5:
                codes.add(c)
    return codes


def build_alternatives_from_drugbank_xml(
    xml_path: str | Path,
    *,
    approved_only: bool = False,
) -> dict[str, list[str]]:
    """
    Stream-parse DrugBank XML and build drug -> [same-class alternatives].
    Uses ATC level 4 (5-char code). approved_only: only drugs with <group>approved</group>.
    Aliases from <synonyms> are added so synonym -> same list as primary name.
    """
    if ET is None:
        raise RuntimeError("xml.etree.ElementTree is required for DrugBank parsing")

    xml_path = Path(xml_path)
    if not xml_path.exists():
        log.warning("XML path does not exist: %s", xml_path)
        return {}

    atc4_to_drugs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    drug_atc4: dict[str, set[str]] = defaultdict(set)
    drug_synonyms: dict[str, list[str]] = {}

    log.info("Stream-parsing DrugBank XML: %s (lxml=%s, approved_only=%s)", xml_path, _has_lxml, approved_only)
    if _has_lxml:
        context = ET.iterparse(str(xml_path), events=("end",), tag=_tag("drug"))
    else:
        context = ET.iterparse(str(xml_path), events=("end",))
    count = 0
    for _event, elem in context:
        if not _has_lxml and elem.tag != _tag("drug"):
            continue
        count += 1
        if count % 5000 == 0:
            log.info("  ... %d drugs", count)

        db_id_el = None
        for child in elem.findall(_tag("drugbank-id")):
            if child.get("primary") == "true":
                db_id_el = child
                break
        if db_id_el is None:
            db_id_el = elem.find(_tag("drugbank-id"))
        drugbank_id = _get_text(db_id_el) if db_id_el is not None else ""
        name_el = elem.find(_tag("name"))
        name = _get_text(name_el) if name_el is not None else ""
        if not name:
            elem.clear()
            if _has_lxml and elem.getparent() is not None:
                elem.getparent().remove(elem)
            continue

        if approved_only:
            groups = elem.find(_tag("groups"))
            if not _get_approved(groups):
                elem.clear()
                if _has_lxml and elem.getparent() is not None:
                    elem.getparent().remove(elem)
                continue

        atc_codes_el = elem.find(_tag("atc-codes"))
        atc4_set = _atc_level4_codes(atc_codes_el)
        if not atc4_set:
            elem.clear()
            if _has_lxml and elem.getparent() is not None:
                elem.getparent().remove(elem)
            continue

        name_lower = name.lower()
        drug_atc4[name_lower].update(atc4_set)
        for c in atc4_set:
            atc4_to_drugs[c].append((drugbank_id, name))

        synonyms_el = elem.find(_tag("synonyms"))
        if synonyms_el is not None:
            syn_list = [
                _get_text(s).lower()
                for s in synonyms_el.findall(_tag("synonym"))
                if _get_text(s).strip()
            ]
            if syn_list:
                drug_synonyms[name_lower] = syn_list

        elem.clear()
        if _has_lxml and elem.getparent() is not None:
            elem.getparent().remove(elem)

    log.info("Parsed %d drugs with ATC codes. Building alternatives...", count)

    out: dict[str, list[str]] = {}
    for drug_lower, codes in drug_atc4.items():
        others = set()
        for c in codes:
            for _did, dname in atc4_to_drugs.get(c, []):
                other_lower = dname.lower()
                if other_lower != drug_lower:
                    others.add(dname)
        out[drug_lower] = sorted(others)

    for drug_lower, syns in drug_synonyms.items():
        if drug_lower not in out:
            continue
        alts = out[drug_lower]
        for s in syns:
            s_clean = s.strip().lower()
            if s_clean and s_clean not in out:
                out[s_clean] = alts.copy()

    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build drug alternatives from DrugBank full database XML (ATC level 4)"
    )
    parser.add_argument(
        "--xml",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "full database.xml",
        help="Path to full database.xml",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "drugbank_alternatives.json",
        help="Output JSON path (drug -> list of alternative names)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include all drugs with ATC codes (default: approved only)",
    )
    args = parser.parse_args()

    result = build_alternatives_from_drugbank_xml(
        args.xml,
        approved_only=not args.all,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    log.info("Wrote %d drugs with ≥1 alternative to %s", sum(1 for v in result.values() if v), args.out)


if __name__ == "__main__":
    import os
    import sys
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    main()
