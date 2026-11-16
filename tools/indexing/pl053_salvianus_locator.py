"""
PL053 S. Salvianus Massiliensis (Index Rerum et Verborum in Salvianum) Locator
Sprint 159 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL053:
1. Editorial Context:
   - PL053 contains S. Salviani Massiliensis Presbyteri Opera Omnia (cols 25-262),
     edited by Baluzius and Conrad Rittershusius.
   - In physical pp. 513-524 (cols 1013-1036):
     'INDEX RERUM ET VERBORUM IN S. SALVIANUM.'
   - In col. 1013 (physical p. 513 / 1e7778e6-78f9-4fd0-81f9-022d220e6a3e-513.txt):
     'Abundantia rerum, causa vitiorum, 150 seq.'
   - The residual item:
     id: group:fff9ab4ad191a321317e3701555fada51a57ec82e67467d7ca59e397b2ff14f7
     'Abundantia rerum, causa vitiorum' -> '150 seq'
   - Textual and editorial analysis reveals:
     - '150 seq.' refers to columns 149-152 of PL053, in De Gubernatione Dei lib. VII, cap. XX-XXII
       (physical pp. 80-82 / 5e97655d-9b57-4b93-aa47-173882cda030-080.txt, 081.txt, 082.txt):
       'in quibus haec barbaris gentibus non crescat cum prosperitate vitiositas?...
        Excessi paulisper coeptum sermonis ordinem, rerum indignitate compulsus...'
     - Primary index source sits on physical p. 513 (1e7778e6-78f9-4fd0-81f9-022d220e6a3e-513.txt).
"""

import os
import json
import zlib
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Any

SUMMARY_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json"
DB_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"

TARGET_ITEMS: Dict[str, Dict[str, Any]] = {
    "group:fff9ab4ad191a321317e3701555fada51a57ec82e67467d7ca59e397b2ff14f7": {
        "label": "Abundantia rerum, causa vitiorum",
        "ref_raw": "150 seq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL053/text/5e97655d-9b57-4b93-aa47-173882cda030-081.txt",
                "physical": 81,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "de_gubernatione_dei_lib_vii",
                    "pl053_col_149_150",
                    "prosperitas_et_vitiositas"
                ],
                "snippet": "DE GUBERNATIONE DEI LIB. VII. ... Excessi paulisper cœptum sermonis ordinem, rerum indignitate compulsus. Nunc ad superiora...",
                "witness": "PL053 cols 149-150, S. Salviani De Gubernatione Dei lib. VII, cap. 21-22 (physical p. 81)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL053/text/5e97655d-9b57-4b93-aa47-173882cda030-082.txt",
                "physical": 82,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pl053_cols_151_152",
                    "de_gubernatione_dei_lib_vii_cont",
                    "abundantia_rerum_sequela"
                ],
                "snippet": "151 S. SALVIANI MASSILIENSIS PRESBYTERI 152 ... De Gubernatione Dei lib. VII continuatio",
                "witness": "PL053 cols 151-152, De Gubernatione Dei lib. VII (physical p. 82)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL053/text/1e7778e6-78f9-4fd0-81f9-022d220e6a3e-513.txt",
                "physical": 513,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_in_salvianum_col_1013"
                ],
                "snippet": "Abundantia rerum, causa vitiorum, 150 seq. Adversa nobis per iracundiam Dei veniunt, secunda per gratiam, 150.",
                "witness": "PL053 col. 1013, Index in S. Salvianum (physical p. 513)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_159_pl053_salvianus"):
    if not os.path.exists(summary_path):
        return

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM items")
    total_items = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    items_with_dest = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    items_src_only = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT volume) FROM items")
    vol_count = c.fetchone()[0]
    conn.close()

    summary["total_items"] = total_items
    summary["items_with_destinations"] = items_with_dest
    summary["items_source_only"] = items_src_only
    summary["candidate_storage_records"] = cand_count
    summary["total_volumes"] = vol_count
    summary["coverage_percentage"] = round((items_with_dest / total_items) * 100, 4) if total_items else 0.0
    summary["last_campaign"] = campaign_name
    summary["last_sync"] = datetime.now(timezone.utc).isoformat()

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def apply_destinations(db_path: str = DB_PATH, dry_run: bool = True):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    fixed_count = 0
    new_candidates_count = 0

    for item_id, item_data in TARGET_ITEMS.items():
        c.execute("SELECT state, metadata_json FROM items WHERE id = ?", (item_id,))
        row = c.fetchone()
        if not row:
            continue

        current_state, meta_str = row
        meta = json.loads(meta_str) if meta_str else {}

        meta["verified"] = True
        meta["status"] = "resolved_single_target"
        meta["destinations"] = item_data["destinations"]
        meta["resolution_campaign"] = "sprint_159_pl053_salvianus"

        if not dry_run:
            c.execute(
                "UPDATE items SET state = 'with_destinations', metadata_json = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), item_id)
            )

            # Insert candidates into candidate_storage
            for dest in item_data["destinations"]:
                cand_blob = zlib.compress(json.dumps(dest, ensure_ascii=False).encode("utf-8"))
                c.execute(
                    """
                    INSERT OR REPLACE INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (item_id, dest["file"], dest["rank"], dest["score"], 1, cand_blob)
                )
                new_candidates_count += 1

        fixed_count += 1

    if not dry_run:
        conn.commit()
    conn.close()

    if not dry_run:
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_159_pl053_salvianus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL053.")
