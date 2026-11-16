"""
PL124 Usuardus Sangermanensis (Index ad Usuardi Auctaria) Locator
Sprint 164 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL124:
1. Editorial Context:
   - PL124 contains Usuardi Monachi Sangermanensis Martyrologium (cols 9-858),
     followed by Usuardi Auctaria (cols 859-1502) and Indices (cols 1495-1524).
   - In physical pp. 651-657 (cols 1495-1510):
     'INDEX AD USUARDI AUCTARIA.'
   - In col. 1503 (physical p. 652 / PL124-652.txt):
     'Florentia virgo, 17 jul.; transl., ibid.'
   - The residual item:
     id: group:0a2bd83f637fff9ccfc7747952fba3051888de7a49073e6a913f61833dcb1048
     'Florentia virgo, 17 jul.; transl.' -> 'ibid'
   - Textual and editorial analysis reveals:
     - The citations in this index refer to liturgical calendar dates of the Roman/Usuard Martyrology.
     - '17 jul.' (XVI Kalendas Augusti) sits on physical pp. 134-135 (PL124-134.txt and PL124-135.txt, cols 267-270):
       - On physical p. 135 (cols 269-270), the manuscript Auctaria record verbatim:
         'In Dacia, sanctæ Florentiæ virginis.'
       - And Greven's Auctaria record:
         'In Dacia, translatio sanctæ Florentiæ virginis, discipulæ sancti Hilarii episcopi.'
     - 'transl., ibid.' refers to the same day (17 July / physical p. 135) where the Translatio S. Florentiae is commemorated.
     - Primary index source sits on physical p. 652 (PL124-652.txt).
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
    "group:0a2bd83f637fff9ccfc7747952fba3051888de7a49073e6a913f61833dcb1048": {
        "label": "Florentia virgo, 17 jul.; transl.",
        "ref_raw": "ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL124/text/PL124-135.txt",
                "physical": 135,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "die_17_julii_xvi_kal_aug",
                    "sanctae_florentiae_virginis",
                    "translatio_sanctae_florentiae_virginis",
                    "pl124_cols_269_270"
                ],
                "snippet": "In Dacia, sanctæ Florentiæ virginis... GREVEN: In Dacia, translatio sanctæ Florentiæ virginis, discipulæ sancti Hilarii episcopi...",
                "witness": "PL124 cols 269-270, Usuardi Martyrologium (17 Julii, physical p. 135)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL124/text/PL124-134.txt",
                "physical": 134,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pl124_cols_267_268",
                    "die_17_julii_initium",
                    "martyres_scillitani"
                ],
                "snippet": "USUARDI MONACHI MARTYROLOGIUM - XVI KALENDAS AUGUSTI / 17 JULII ... In Carthagine, natalis sanctorum martyrum Scillitanorum...",
                "witness": "PL124 cols 267-268, Usuardi Martyrologium (17 Julii initium, physical p. 134)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL124/text/PL124-652.txt",
                "physical": 652,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_ad_usuardi_auctaria_col_1503"
                ],
                "snippet": "Florentia virgo, 17 jul.; transl., ibid.",
                "witness": "PL124 col. 1503, Index ad Usuardi Auctaria (physical p. 652)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_164_pl124_usuardus"):
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
        meta["resolution_campaign"] = "sprint_164_pl124_usuardus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_164_pl124_usuardus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL124.")
