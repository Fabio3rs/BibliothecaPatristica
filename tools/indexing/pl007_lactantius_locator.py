"""
PL007 Lactantius (Index Rerum et Verborum in Lactantium) Locator
Sprint 155 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL007:
1. Editorial Context:
   - PL006-PL007 form the complete edition of Lactantius (ed. Le Nourry / Lenglet Dufresnoy / Gallaeus).
   - In PL007 physical pp. 583-608 (cols 1159-1212):
     'INDEX RERUM ET VERBORUM IN LACTANTIUM.'
   - In physical p. 601 (dbfdf595-8534-4cfe-b0ca-2a33f44448ad-601.txt):
     'SABÆORUM origo. 974'
     'SABBATICUS fluvius, unde dictus. II, 970 et seq.'
   - The residual item:
     id: group:ff4b70e8cde0b9c83b106b277a9211cfed32ae5115246c557739c978c5a8595f
     'SABÆORUM origo. 974 SABBATICUS fluvius, unde dictus. II' -> '970 et seq'
   - Textual and editorial analysis reveals:
     - 'SABBATICUS fluvius, unde dictus. II, 970 et seq.' corresponds uniquely in the entire corpus
       to PL007 physical p. 289 (85c96c26-2414-455a-80fe-34e3f153bf91-289.txt, cols 571-572, Gisberti Cuperi
       ad Lactantium dissertatio): 'Habet autem quoddam peculiare miraculum... Unde etiam Sabbaticus appellatus est,
       a sacro Judæorum septimo die sic denominatus... Sabbaticum fluvium dici contendant...'
     - 'SABÆORUM origo. 974' corresponds to PL006 physical p. 494 (901e7219-ef71-4a73-bade-b4732306424d-494.txt,
       col. 974, In quartum lib. Divin. Instit.): 'de utraque gente Sabæorum, tum eorum qui a Saba filio Chus...
       Alii Sabæi sunt descendentes a Saba...'
     - Primary index source sits on physical p. 601 (dbfdf595-8534-4cfe-b0ca-2a33f44448ad-601.txt).
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
    "group:ff4b70e8cde0b9c83b106b277a9211cfed32ae5115246c557739c978c5a8595f": {
        "label": "SABÆORUM origo. 974 SABBATICUS fluvius, unde dictus. II",
        "ref_raw": "970 et seq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL007/text/85c96c26-2414-455a-80fe-34e3f153bf91-289.txt",
                "physical": 289,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "sabbaticus_fluvius_unde_dictus",
                    "gisberti_cuperi_dissertatio",
                    "pl007_cols_571_572"
                ],
                "snippet": "Habet autem quoddam peculiare miraculum... Unde etiam Sabbaticus appellatus est, a sacro Judæorum septimo die sic denominatus... Sabbaticum fluvium dici contendant...",
                "witness": "PL007 cols 571-572, Gisberti Cuperi ad Lactantium dissertatio (physical p. 289)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL006/text/901e7219-ef71-4a73-bade-b4732306424d-494.txt",
                "physical": 494,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pl006_col_974",
                    "sabaeorum_origo",
                    "in_quartum_lib_divin_instit"
                ],
                "snippet": "de utraque gente Sabæorum, tum eorum qui a Saba filio Chus... Alii Sabæi sunt descendentes a Saba...",
                "witness": "PL006 col. 974, In quartum lib. Divin. Instit. (physical p. 494)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL007/text/dbfdf595-8534-4cfe-b0ca-2a33f44448ad-601.txt",
                "physical": 601,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_rerum_et_verborum_migne_col_1195"
                ],
                "snippet": "SABÆORUM origo. 974. SABBATICUS fluvius, unde dictus. II, 970 et seq.",
                "witness": "PL007 col. 1195, Index Rerum et Verborum in Lactantium (physical p. 601)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_155_pl007_lactantius"):
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
        meta["resolution_campaign"] = "sprint_155_pl007_lactantius"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_155_pl007_lactantius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL007.")
