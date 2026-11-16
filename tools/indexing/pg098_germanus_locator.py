"""
PG098 S. Germanus Constantinopolitanus (Index Analyticus) Locator
Sprint 149 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG098:
1. Editorial Context:
   - PG098 contains the works of S. Germanus I of Constantinople and Cosmas Indicopleustes.
   - In the 'INDEX ANALYTICUS IN S. GERMANUM' (cols 1487-1510 / physical pp. 753-764):
     Entries cite Migne columns with column subdivisions a, b, c, d.
   - At col. 1490-1491 (physical p. 754 / 55ad3854-97e3-4a11-a5bb-9ff33a720caa-754.txt):
     Under 'Anna, Virginis Mariæ mater':
     'Anna, Virginis Mariæ mater, ex tribu Aaronitica, prophetica et regia stirpe orta est, 314, a.
      Deum precatur ut sterilitatis opprobrium auferat ab ea, ibid. b; exauditur, ibid. c, d.
      Anna diuturnam sterilitatem felici partu compensans... 315, a, b, c.'
   - The residual item:
     id: group:9e30f8cab57fac531db912401c09b2f1d5ff1cc01f859e8de9142b661c5cc452
     '[Anna...] b; exauditur' -> 'ibid'
   - Refers directly to In Praesentationem SS. Deiparae II (cols 313-316):
     - Migne cols 313-314 sit on physical p. 161 (a2ea20a7-0f7d-48f0-b8a6-9b81f73ad00b-161.txt):
       Col. 314 b, c, d contains Anna's prayer and her being heard by God (exauditio / conception of Mary).
     - Migne cols 315-316 sit on physical p. 162 (a2ea20a7-0f7d-48f0-b8a6-9b81f73ad00b-162.txt):
       Col. 315 a, b, c continues the Presentation homily.
     - Primary index source sits on physical p. 754 (55ad3854-97e3-4a11-a5bb-9ff33a720caa-754.txt).
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
    "group:9e30f8cab57fac531db912401c09b2f1d5ff1cc01f859e8de9142b661c5cc452": {
        "label": "b; exauditur",
        "ref_raw": "ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG098/text/a2ea20a7-0f7d-48f0-b8a6-9b81f73ad00b-161.txt",
                "physical": 161,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "migne_col_314_bcd_exauditio",
                    "anna_mater_mariae_oratio",
                    "in_praesentationem_ss_deiparae_ii"
                ],
                "snippet": "313 IN PRÆSENTATIONEM SS. DEIPARÆ. II. 314 ... O Domine, Domine, qui mœstas ac dolore oppressas animas celerrime exaudis ... angelum suum misit, qui nobis hujusce filiæ meæ conceptionem annuntiaret...",
                "witness": "PG098 cols 313-314, In Praesentationem SS. Deiparae II (physical p. 161)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG098/text/a2ea20a7-0f7d-48f0-b8a6-9b81f73ad00b-162.txt",
                "physical": 162,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "migne_col_315_abc_context",
                    "in_praesentationem_ss_deiparae_ii_cont"
                ],
                "snippet": "315 S. GERMANI PATRIARCHÆ CP. 316 ... Idcirco igitur, ablactata jam puella, « Vota mea reddo Deo, quæ distinxerunt labia mea... »",
                "witness": "PG098 cols 315-316, In Praesentationem SS. Deiparae II cont. (physical p. 162)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG098/text/55ad3854-97e3-4a11-a5bb-9ff33a720caa-754.txt",
                "physical": 754,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_analyticus_migne_col_1491"
                ],
                "snippet": "Anna, Virginis Mariæ mater ... Deum precatur ut sterilitatis opprobrium auferat ab ea, ibid. b; exauditur, ibid. c, d.",
                "witness": "PG098 col. 1491, Index Analyticus (physical p. 754)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_149_pg098_germanus"):
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
        meta["resolution_campaign"] = "sprint_149_pg098_germanus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_149_pg098_germanus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG098.")
