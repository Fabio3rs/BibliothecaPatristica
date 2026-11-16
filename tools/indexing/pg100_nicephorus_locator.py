"""
PG100 S. Nicephorus Constantinopolitanus (Index in S. Nicephori Antirrheticos) Locator
Sprint 150 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG100:
1. Editorial Context:
   - PG100 contains the works of S. Nicephorus I of Constantinople.
   - In physical pp. 771-776 (cols 1529-1540):
     'INDEX IN S. NICEPHORI ANTIRRHETICOS.'
     The index citations refer to the pagination of Cardinal Angelo Mai's original edition
     of Nicephorus's Antirrhetici and Apologeticus pro sacris imaginibus ('Spicilegium Romanum' /
     'Scriptorum Veterum Nova Collectio', tom. VIII, Romae).
   - At col. 1532 (physical p. 772 / 95333bd7-0d27-4e08-b4c1-aba3fd6dceeb-772.txt):
     'Idololatriam a Christianismo deletam fuisse latissime demonstratur, 181 seqq.'
   - The residual item:
     id: group:0ed17a609268e1c5b5fa04ed45f2ea6867646ecb9b87e3692401009549a3d8c9
     'Idololatriam a Christianismo deletam fuisse latissime demonstratur' -> '181 seqq'
   - Refers directly to Apologeticus pro sacris imaginibus (cols 613-616 / physical pp. 311-312):
     - Angelo Mai page 181 sits on physical p. 311 (98c13e16-1a07-4f90-a06e-799c379053bd-311.txt):
       'ad unum 181 impietatis præcipitium sua illos utrinque urget blasphemia. Nobis quidem super veræ pietatis fundamento...'
     - Angelo Mai page 182 / seqq. sits on physical p. 312 (98c13e16-1a07-4f90-a06e-799c379053bd-312.txt):
       '615 S. NICEPHORI PATRIARCHÆ CP. 616 ... 34. Atque norum absurditates hactenus narratæ ita se habent...'
     - The index source entry sits on physical p. 772 (95333bd7-0d27-4e08-b4c1-aba3fd6dceeb-772.txt).
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
    "group:0ed17a609268e1c5b5fa04ed45f2ea6867646ecb9b87e3692401009549a3d8c9": {
        "label": "Idololatriam a Christianismo deletam fuisse latissime demonstratur",
        "ref_raw": "181 seqq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG100/text/98c13e16-1a07-4f90-a06e-799c379053bd-311.txt",
                "physical": 311,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "mai_numerus_crassior_181",
                    "apologeticus_pro_sacris_imaginibus",
                    "migne_cols_613_614",
                    "idololatria_a_christianismo_deleta"
                ],
                "snippet": "ad unum 181 impietatis præcipitium sua illos utrinque urget blasphemia. Nobis quidem super veræ pietatis fundamento thte constitutis...",
                "witness": "PG100 cols 613-614, Apologeticus pro sacris imaginibus (Mai ed. p. 181)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG100/text/98c13e16-1a07-4f90-a06e-799c379053bd-312.txt",
                "physical": 312,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "mai_numerus_crassior_182_seqq",
                    "apologeticus_pro_sacris_imaginibus_cont",
                    "migne_cols_615_616"
                ],
                "snippet": "615 S. NICEPHORI PATRIARCHÆ CP. 616 ... 34. Atque norum absurditates hactenus narratæ ita se habent. Mihi vero ad falsitatis refutationem...",
                "witness": "PG100 cols 615-616, Apologeticus pro sacris imaginibus cont. (Mai ed. p. 182 / seqq.)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG100/text/95333bd7-0d27-4e08-b4c1-aba3fd6dceeb-772.txt",
                "physical": 772,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_in_antirrheticos_migne_col_1532"
                ],
                "snippet": "Idololatriam a Christianismo deletam fuisse latissime demonstratur, 181 seqq.",
                "witness": "PG100 col. 1532, Index in Antirrheticos (physical p. 772)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_150_pg100_nicephorus"):
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
        meta["resolution_campaign"] = "sprint_150_pg100_nicephorus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_150_pg100_nicephorus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG100.")
