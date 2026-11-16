"""
PG070 S. Cyrillus Alexandrinus (Index Analyticus in Commentarium in Isaiam) Locator
Sprint 145 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG070:
1. Editorial Context:
   - PG070 contains S. Cyrillus Alexandrinus's Commentary on the Prophet Isaiah (cols 9-1450).
   - In the 'INDEX ANALYTICUS IN COMMENTARIUM IN ISAIAM' (cols 1449-1476 / physical pp. 726-748):
     The entries cite Migne columns directly.
   - At col. 1460 (physical p. 740 / 0c99bc89-da02-4617-891e-f88dca2a26ef-740.txt):
     Under the main entry 'Judæi / Judæorum':
     'Judæorum exitii quæ causa, 41, 447. Judæorum extrema impietas, 810. Cæcitas, 333 et seq. In Christum scelus quotuplex, 714.'
   - The residual item:
     id: group:4be3a43f72657e3778556944ec6021c3c5b9578c1cea18954bb78db900e5ae97
     '[Judæorum] Cæcitas' -> '333 et seq'
   - Refers directly to cols 333-336 (In Isaiam lib. II tomus I / Isa. xi, 14):
     - Migne cols 333-334 sit on physical p. 171 (76482732-ca19-494e-aca5-43bd2ac06142-171.txt):
       'At cum ad summum ac culmen omnis impietatis provecti, deblaterassent in Christum, ac vitæ principem interemissent...'
     - Migne cols 335-336 sit on physical p. 172 (76482732-ca19-494e-aca5-43bd2ac06142-172.txt):
       'Nam post Judæorum regionem, omnes deinceps finitimæ et vicinæ...'
     - The index source entry sits on physical p. 740 (0c99bc89-da02-4617-891e-f88dca2a26ef-740.txt).
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
    "group:4be3a43f72657e3778556944ec6021c3c5b9578c1cea18954bb78db900e5ae97": {
        "label": "Cæcitas",
        "ref_raw": "333 et seq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG070/text/76482732-ca19-494e-aca5-43bd2ac06142-171.txt",
                "physical": 171,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "migne_col_333_334",
                    "in_isaiam_lib_ii_tom_i",
                    "judaeorum_impietas_et_caecitas"
                ],
                "snippet": "At cum ad summum ac culmen omnis impietatis provecti, deblaterassent in Christum, ac vitæ principem interemissent, et sanctum et justum negassent...",
                "witness": "PG070 cols 333-334, In Isaiam lib. II tom. I (physical p. 171)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG070/text/76482732-ca19-494e-aca5-43bd2ac06142-172.txt",
                "physical": 172,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "migne_col_335_336_et_seq",
                    "in_isaiam_lib_ii_tom_i_cont"
                ],
                "snippet": "335 S. CYRILLI ALEXANDRINI ARCHIEP. 336 ... Nam post Judæorum regionem, omnes deinceps finitimæ et vicinæ...",
                "witness": "PG070 cols 335-336, In Isaiam lib. II tom. I (physical p. 172 / et seq.)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG070/text/0c99bc89-da02-4617-891e-f88dca2a26ef-740.txt",
                "physical": 740,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_analyticus_migne_col_1460"
                ],
                "snippet": "Judæorum exitii quæ causa, 41, 447. Judæorum extrema impietas, 810. Cæcitas, 333 et seq. In Christum scelus quotuplex, 714.",
                "witness": "PG070 col. 1460, Index Analyticus (physical p. 740)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_145_pg070_cyrillus"):
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
        meta["resolution_campaign"] = "sprint_145_pg070_cyrillus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_145_pg070_cyrillus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG070.")
