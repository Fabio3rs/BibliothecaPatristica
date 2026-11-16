"""
PG030 S. Basilius Magnus (Index Rerum et Verborum quae ante Appendicem inveniuntur) Locator
Sprint 142 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG030:
1. Editorial Context:
   - PG030 contains the 'APPENDIX TOMI PRIMI OPERUM S. BASILII MAGNI', followed by the comprehensive
     indices to the whole Tomus I (both to the works in PG029 and to the Appendix in PG030).
   - In the 'INDEX RERUM ET VERBORUM QUÆ ANTE APPENDICEM INVENIUNTUR' (cols 1153-1196 / physical pp. 574-595):
     The numbers cite the pages of the edition of Julien Garnier (Paris, 1721, Tomus I), which is reprinted
     in PG029.
   - At col. 1189 (physical p. 592 / 880803cb-8607-454c-8867-62a6e9ba9389-592.txt):
     'Resurrectionis mysterium jam compleri exoptat David cum ait, Exsurge, Domine, in ira tua, 100.
      Resurrectionis mysterium indicare videtur cum ait psal. VII, 8 : Exsurge, Domine Deus, 100.'
   - And at col. 1188 (physical p. 591):
     'Verba ista v 8, Exsurge, Domine Deus, etc., vel mysterium resurrectionis indicant... 100.'
   - The residual item:
     'Resurrectionis mysterium indicare videtur cum ait psal' -> 'VII, 8'
     id: group:fdc36602d6164ddc12d6996b3f84a9ab75422fd3cbb49b81b397999bb71554a3
     Points directly to Garnier p. 100, which in PG029 corresponds to physical p. 528 (cols 235-236 /
     91e34095-db3d-4ee6-bbf7-f26c3fe6bbfa-528.txt):
     '4. Et exsurge, Domine Deus meus, in præcepto quod mandasti : et synagoga populorum circumdabit te.
      Resurrectionis mysterium videtur his verbis indicari... /
      Τὸ τῆς ἀναστάσεως μυστήριον ἔοικεν αἰνίττεσθαι διὰ τούτων...'
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
    "group:fdc36602d6164ddc12d6996b3f84a9ab75422fd3cbb49b81b397999bb71554a3": {
        "label": "Resurrectionis mysterium indicare videtur cum ait psal",
        "ref_raw": "VII, 8",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG029/text/91e34095-db3d-4ee6-bbf7-f26c3fe6bbfa-528.txt",
                "physical": 528,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_resurrectionis_mysterium_indicari",
                    "homilia_in_psalmum_vii_v8",
                    "garnier_page_100",
                    "migne_cols_235_236"
                ],
                "snippet": "4. Et exsurge, Domine Deus meus, in præcepto quod mandasti : et synagoga populorum circumdabit te. Resurrectionis mysterium videtur his verbis indicari... / Τὸ τῆς ἀναστάσεως μυστήριον ἔοικεν αἰνίττεσθαι...",
                "witness": "PG029 cols 235-236, Homilia in Psalmum VII, n. 4 (Garnier tom. I p. 100)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG030/text/880803cb-8607-454c-8867-62a6e9ba9389-591.txt",
                "physical": 591,
                "score": 90.0,
                "rank": 2,
                "reasons": ["parallel_index_entry_psal_vii", "migne_col_1188"],
                "snippet": "Verba ista v 8, Exsurge, Domine Deus, etc., vel mysterium resurrectionis indicant, vel per ea hortatur Deum Propheta, ut filium suum corripiat, 100.",
                "witness": "PG030 col. 1188, Index Rerum et Verborum (physical p. 591)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG030/text/880803cb-8607-454c-8867-62a6e9ba9389-592.txt",
                "physical": 592,
                "score": 85.0,
                "rank": 3,
                "reasons": ["index_primary_source", "migne_col_1189"],
                "snippet": "Resurrectionis mysterium indicare videtur cum ait psal. VII, 8 : Exsurge, Domine Deus, 100.",
                "witness": "PG030 col. 1189, Index Rerum et Verborum (physical p. 592)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_142_pg030_basilius"):
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
        meta["resolution_campaign"] = "sprint_142_pg030_basilius"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_142_pg030_basilius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG030.")
