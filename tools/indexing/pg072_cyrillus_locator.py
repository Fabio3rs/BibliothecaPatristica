"""
PG072 S. Cyrillus Alexandrinus (Index Rerum et Verborum in Supplemento) Locator
Sprint 146 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG072:
1. Editorial Context:
   - PG072 contains the second volume of S. Cyrillus Alexandrinus's Scriptural Commentaries (cols 9-1024).
   - In physical pp. 490-496 (cols 489-496 of the supplement / cols 1017-1024 overall):
     'INDEX RERUM ET VERBORUM QUÆ in supplemento, Cyrilli commentaria in Matthæum et in Lucam complectente, reperiuntur.'
   - The numbers in this index refer to the embedded pagination numbers of Cardinal Angelo Mai's
     edition ('Scriptorum Veterum Nova Collectio', tom. VIII, Romae).
   - At physical p. 490 (e3547f24-7b4d-4a12-9b71-e6670436098d-490.txt):
     'Circumcisio et baptismus, 130 seq. Circumcisionis tres typicæ significationes, 131.'
   - The residual item:
     id: group:78b7523248d6a196bca95149db9ac2fcbf39be5ae7917b0d31a4282dafff8a47
     'Circumcisio et baptismus' -> '130 seq'
   - Refers directly to Commentarius in Lucam (cols 497-500 / physical pp. 253-254):
     - Angelo Mai page 130 sits on physical p. 253 (2bca7c42-220f-48c4-b576-06d6fde6f5b1-253.txt):
       'Nam circumcisio, si rei 130 ipsius natura spectetur, nihil omnino est... Fieri vero dicimus spiritualem circumcisionem tempore potissimum sancti baptismatis...'
     - Angelo Mai page 131 sits on physical p. 254 (2bca7c42-220f-48c4-b576-06d6fde6f5b1-254.txt):
       'Videtur enim mihi circumcisio tria effecisse. Primo videlicet signo quodam impresso, Abrahæ filios...'
     - The index primary witness sits on physical p. 490 (e3547f24-7b4d-4a12-9b71-e6670436098d-490.txt).
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
    "group:78b7523248d6a196bca95149db9ac2fcbf39be5ae7917b0d31a4282dafff8a47": {
        "label": "Circumcisio et baptismus",
        "ref_raw": "130 seq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG072/text/2bca7c42-220f-48c4-b576-06d6fde6f5b1-253.txt",
                "physical": 253,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "mai_numerus_crassior_130",
                    "commentarius_in_lucam",
                    "migne_cols_497_498",
                    "circumcisio_et_baptismus_literal"
                ],
                "snippet": "Nam circumcisio, si rei 130 ipsius natura spectetur, nihil omnino est... Fieri vero dicimus spiritualem circumcisionem tempore potissimum sancti baptismatis, quo nimirum participes efficiemur sancti Spiritus...",
                "witness": "PG072 cols 497-498, Comment. in Lucam (Mai ed. p. 130)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG072/text/2bca7c42-220f-48c4-b576-06d6fde6f5b1-254.txt",
                "physical": 254,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "mai_numerus_crassior_131_seq",
                    "commentarius_in_lucam_cont",
                    "circumcisionis_tres_significationes"
                ],
                "snippet": "Videtur enim mihi circumcisio tria effecisse. Primo videlicet signo quodam impresso, Abrahæ filios secernere ab aliis nationibus...",
                "witness": "PG072 cols 499-500, Comment. in Lucam (Mai ed. p. 131 / seq.)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG072/text/e3547f24-7b4d-4a12-9b71-e6670436098d-490.txt",
                "physical": 490,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_rerum_supplementum"
                ],
                "snippet": "Circumcisio et baptismus, 130 seq. Circumcisionis tres typicæ significationes, 131.",
                "witness": "PG072 physical p. 490, Index Rerum et Verborum in Supplemento"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_146_pg072_cyrillus"):
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
        meta["resolution_campaign"] = "sprint_146_pg072_cyrillus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_146_pg072_cyrillus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG072.")
