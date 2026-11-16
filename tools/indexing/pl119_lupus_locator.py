"""
PL119 S. Servatus Lupus Ferrariensis (Index in S. Lupum) Locator
Sprint 162 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL119:
1. Editorial Context:
   - PL119 contains B. Servati Lupi Abbatis Ferrariensis Opera Omnia (cols 423-700),
     edited by Stephanus Baluzius (Paris, 1664).
   - In physical pp. 611-615 (cols 1213-1222):
     'INDEX RERUM ET VERBORUM IN S. LUPUM.'
   - In col. 1215 (physical p. 612 / 31dd9616-20c8-45e9-9063-62144f1f7638-612.txt):
     'Carolomannus rex , filius Ludovici Pii, 524 et seqq.'
   - The residual item:
     id: group:2206c96267b754691c1cdba112387d31ed907336de74518f07647d419f1423aa
     'Carolomannus rex , filius Ludovici Pii' -> '524 et seqq'
   - Textual and editorial analysis reveals:
     - '524 et seqq.' points to Migne column 524 (and continuing letters/notes) in S. Lupi Epistolae:
       - Cols 523-524 sit on physical p. 266 (722bbc5d-9921-4eb2-b39b-ea22614cad57-266.txt,
         Epistolae LIX-LX ad Marcwardum abbatem Prumiensem):
         'Non ignoratis, credo, reges nostros apud Trajectum hebdomade secunda Quadragesimæ celebraturos colloquium...'
       - Cols 525-526 sit on physical p. 267 (722bbc5d-9921-4eb2-b39b-ea22614cad57-267.txt, Epistolae LXI-LXII).
       - In cols 545-546 (physical p. 277 / 722bbc5d-9921-4eb2-b39b-ea22614cad57-277.txt), Baluzius's note b
         expressly annotates: 'cui sub Carlomanno principe Georgius episcopus... Pippini et Carlomanni studio acta est...'
     - Primary index source sits on physical p. 612 (31dd9616-20c8-45e9-9063-62144f1f7638-612.txt).
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
    "group:2206c96267b754691c1cdba112387d31ed907336de74518f07647d419f1423aa": {
        "label": "Carolomannus rex , filius Ludovici Pii",
        "ref_raw": "524 et seqq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL119/text/722bbc5d-9921-4eb2-b39b-ea22614cad57-266.txt",
                "physical": 266,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_col_524_match",
                    "pl119_col_523_524",
                    "epistolae_ad_marcwardum",
                    "colloquium_regum_apud_trajectum"
                ],
                "snippet": "523 B. S. LUPI ABBATIS FERRARIENSIS ... Non ignoratis, credo, reges nostros apud Trajectum hebdomade secunda Quadragesimæ celebraturos colloquium... cum rege quartum ago mensem...",
                "witness": "PL119 cols 523-524, S. Lupi Epistolae LIX-LX (physical p. 266)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL119/text/722bbc5d-9921-4eb2-b39b-ea22614cad57-267.txt",
                "physical": 267,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pl119_cols_525_526",
                    "epistolae_lupenses_cont",
                    "et_seqq_continuity"
                ],
                "snippet": "525 EPISTOLÆ. 526 ... Ingenti clementia Dei nostri discordiæ peste mitigata, quæ totas Gallias Germaniamque hactenus...",
                "witness": "PL119 cols 525-526, S. Lupi Epistolae LXI-LXII (physical p. 267)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL119/text/722bbc5d-9921-4eb2-b39b-ea22614cad57-277.txt",
                "physical": 277,
                "score": 85.0,
                "rank": 3,
                "reasons": [
                    "carlomannus_historical_notes",
                    "baluzius_annotation",
                    "sub_carlomanno_principe"
                ],
                "snippet": "b Consiliarior. consensus. Ex hoc loco collegit Carlomanno, præsente Bonifacio... cui sub Carlomanno principe Georgius episcopus...",
                "witness": "PL119 cols 545-546, Baluzii Notae ad Epistolas Lupi (physical p. 277)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL119/text/31dd9616-20c8-45e9-9063-62144f1f7638-612.txt",
                "physical": 612,
                "score": 80.0,
                "rank": 4,
                "reasons": [
                    "index_primary_source",
                    "index_in_lupum_col_1215"
                ],
                "snippet": "Carolomannus rex , filius Ludovici Pii, 524 et seqq.",
                "witness": "PL119 col. 1215, Index in S. Lupum (physical p. 612)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_162_pl119_lupus"):
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
        meta["resolution_campaign"] = "sprint_162_pl119_lupus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_162_pl119_lupus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL119.")
