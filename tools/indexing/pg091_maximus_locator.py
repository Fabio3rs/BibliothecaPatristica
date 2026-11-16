"""
PG091 S. Maximus Confessor (Index Verborum in Ambigua) Locator
Sprint 148 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG091:
1. Editorial Context:
   - PG091 contains the works of S. Maximus Confessor.
   - In physical pp. 821-828:
     'INDICES AD AMBIGUORUM LIBRUM. (Numeri respondent foliis libri Gudiani typis grandioribus expressis.)
      II. INDEX VERBORUM.'
   - At physical p. 824 (c61c5555-0694-4bd8-8f43-937ede070ce9-824.txt):
     'ἐκτινάσσω, 296 a. et sæpius.'
   - The residual item:
     id: region-entry:d5b4eeb530568120affe5b5aeacf5bfde16d9eb85cc25fbd85dfdfaf7b87d9c5
     'ἐκτινάσσω' -> '296 a. et sæpius.'
   - Refers to textual occurrences of the Greek verb in S. Maximus's works (literally ἐκτινάσσοντες / ἐκτινάσσων,
     rendered in Latin as 'excutere / abicere'):
     - Physical p. 334 (cols 599-600 / 851dcb5a-054f-4460-b6a7-139a42d672e7-334.txt):
       'τῇ προσδοκίᾳ τῶν αἰωνίων κολάσεων αὐτοὺς τῆς ψυχῆς ἐκτινάσσοντες' /
       'quas æternorum cruciatuum exspectatione longe animo excutiamus'
     - Physical p. 245 (cols 453-454 / fd8cb3b2-7668-49a3-bf09-68c8dc03471d-245.txt):
       'ἀγαθοὺς ἐκτινάσσων, καὶ πάσης πόρρω τιθέμενος βουλῆς καὶ δυναστείας'
     - Primary index source on physical p. 824 (c61c5555-0694-4bd8-8f43-937ede070ce9-824.txt).
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
    "region-entry:d5b4eeb530568120affe5b5aeacf5bfde16d9eb85cc25fbd85dfdfaf7b87d9c5": {
        "label": "ἐκτινάσσω",
        "ref_raw": "296 a. et sæpius.",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG091/text/851dcb5a-054f-4460-b6a7-139a42d672e7-334.txt",
                "physical": 334,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_greek_ektinassontes",
                    "excutiamus_animo",
                    "migne_cols_599_600"
                ],
                "snippet": "τῇ προσδοκίᾳ τῶν αἰωνίων κολάσεων αὐτοὺς τῆς ψυχῆς ἐκτινάσσοντες ... quas æternorum cruciatuum exspectatione longe animo excutiamus...",
                "witness": "PG091 cols 599-600, S. Maximi Confessoris (physical p. 334)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG091/text/fd8cb3b2-7668-49a3-bf09-68c8dc03471d-245.txt",
                "physical": 245,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "literal_greek_ektinasson",
                    "migne_cols_453_454",
                    "epistolae_s_maximi"
                ],
                "snippet": "ἀγαθοὺς ἐκτινάσσων, καὶ πάσης πόρρω τιθέμενος βουλῆς καὶ δυναστείας...",
                "witness": "PG091 cols 453-454, Epistolae (physical p. 245)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG091/text/c61c5555-0694-4bd8-8f43-937ede070ce9-824.txt",
                "physical": 824,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_verborum_ad_ambigua"
                ],
                "snippet": "ἐκτινάσσω, 296 a. et sæpius.",
                "witness": "PG091 physical p. 824, Index Verborum ad Ambigua"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_148_pg091_maximus"):
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
        meta["resolution_campaign"] = "sprint_148_pg091_maximus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_148_pg091_maximus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG091.")
