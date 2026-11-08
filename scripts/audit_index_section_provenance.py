#!/usr/bin/env python3
"""Audit source-section provenance in the experimental catalog, strictly readonly."""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import readonly


def audit(catalog):
    with closing(readonly(catalog)) as db:
        kinds = Counter()
        rows, legacy_section_ids, explicit_specific = 0, 0, set()
        for row in db.execute("SELECT id,metadata_json FROM items"):
            rows += 1
            m = json.loads(row["metadata_json"])
            kind = m.get("section_kind") or m.get("region_kind")
            kinds[kind or "missing"] += 1
            legacy_section_ids += bool(m.get("section_id") or m.get("section"))
            if kind and kind not in {"unknown", "index_unspecified"}:
                explicit_specific.add(row["id"])
        declared, titled, sections = set(), set(), []
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='editorial_sections'"
        ).fetchone():
            members = defaultdict(set)
            assignment_counts = Counter()
            for relation in db.execute(
                """SELECT r.* FROM item_editorial_sections r JOIN items i ON i.id=r.item_id"""
            ):
                members[(relation["section_id"], relation["origin"])].add(
                    relation["item_id"]
                )
                assignment_counts[relation["assignment_kind"]] += 1
            for s in db.execute(
                "SELECT * FROM editorial_sections ORDER BY section_id,origin"
            ):
                ids = members[(s["section_id"], s["origin"])]
                if s["index_type"] not in {"unknown", "index_unspecified"}:
                    declared.update(ids)
                if s["title_raw"]:
                    titled.update(ids)
                sections.append(
                    {
                        "section_id": s["section_id"],
                        "index_type": s["index_type"],
                        "indexed_material": s["indexed_material"],
                        "title_raw": s["title_raw"],
                        "source": s["source"],
                        "items": len(ids),
                    }
                )
        specific = explicit_specific | declared
        return {
            "catalog": str(catalog.resolve()),
            "total_items": rows,
            "legacy_type_counts": dict(kinds),
            "legacy_section_ids": legacy_section_ids,
            "specific_type_in_legacy_metadata": len(explicit_specific),
            "items_with_opening_section_relation": len(declared),
            "items_with_printed_section_title_relation": len(titled),
            "items_with_specific_type_any_source": len(specific),
            "items_without_specific_type": rows - len(specific),
            "sections": sections,
            "assignment_counts": dict(assignment_counts) if sections else {},
            "scope_note": "Audit of fields available in the active catalog; older artifacts may contain additional unconnected section evidence. title_raw on an entry alone is not counted as an index title.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.catalog)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
