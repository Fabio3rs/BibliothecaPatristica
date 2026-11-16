"""
PL182 S. Bernardus Claraevallensis (Ymarus Tusculanus) Locator
Sprint 170 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL182:
1. Editorial Context:
   - PL182 contains S. Bernardi Abbatis Clarae-Vallensis Epistolae.
   - In physical p. 602 (88b11b5c-1327-4f85-b255-95bb167e6b6d-602.txt, col. 1195):
     'INDEX EPISTOLARUM SANCTI BERNARDI.'
     'Igmaro seu Ymaro cardinali episcopo Tusculano, 219, 250-232.'
   - The reference numbers cite Epistles of St. Bernard addressed to Ymarus (Igmarus), Cardinal Bishop of Tusculum:
     - Epistola 219 (physical p. 196 / 0fc4e65b-2d51-4cd1-ab45-a9100084883c-196.txt, cols 381-382):
       'EPISTOLA CCXIX. AD TRES EPISCOPOS CURIÆ, ALBERICUM OSTIENSEM, STEPHANUM PRÆNESTINUM,
        IGNATUM TUSCULANUM, ET GERARDUM CANCELLARIUM.
        Nota 569: Ignarus seu Ymarus, ex monacho Cluniacensis Sancti-Martini a Campis Prior Charitatis...'
     - Epistolae 230-232 (misprinted/misread in index as '250-232'):
       - Epistola 230 (physical p. 214 / 1ad1f822-a2e6-4d22-a4d4-997e8c8f7094-214.txt, cols 417-418):
         'EPISTOLA CCXXX. AD TRES EPISCOPOS OSTIENSEM, TUSCULANUM, PRÆNESTINUM.
          Nota 609: Nomina habet titulus epistolæ 219 Albericum, Stephanum, et Ignarum.'
       - Epistola 231 (physical pp. 214-215, cols 418-419):
         'EPISTOLA CCXXXI. AD EOSDEM PRO ABBATE LATINIACENSI.'
       - Epistola 232 (physical p. 215, cols 419-420):
         'EPISTOLA CCXXXII [typographically CCXXII]. AD EOSDEM. Contra abbatem Sancti Theofredi.
          Nota 616: Quippe Albericus Ostiensis, et Ymarus Tusculanus Cluniacenses erant...'
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
    "group:feed334f3f8836c3022e0c5523f8c1b5dc59940147fc50a1bbe3bd56a22e0860": {
        "label": "Igmaro seu Ymaro cardinali episcopo Tusculano",
        "ref_raw": "219, 250-232",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL182/text/0fc4e65b-2d51-4cd1-ab45-a9100084883c-196.txt",
                "physical": 196,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "epistola_219_ad_ignatum_tusculanum",
                    "pl182_cols_381_382",
                    "nota_569_ignarus_seu_ymarus"
                ],
                "snippet": "EPISTOLA CCXIX... AD TRES EPISCOPOS CURIÆ, ALBERICUM OSTIENSEM, STEPHANUM PRÆNESTINUM, IGNATUM TUSCULANUM... Nota (569) Ignarus seu Ymarus, ex monacho Cluniacensis Sancti-Martini a Campis...",
                "witness": "PL182 cols 381-382, Epistola CCXIX (physical p. 196)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL182/text/1ad1f822-a2e6-4d22-a4d4-997e8c8f7094-214.txt",
                "physical": 214,
                "score": 95.0,
                "rank": 2,
                "reasons": [
                    "epistola_230_ad_episcopum_tusculanum",
                    "epistola_231_ad_eosdem",
                    "pl182_cols_417_418"
                ],
                "snippet": "EPISTOLA CCXXX. AD TRES EPISCOPOS OSTIENSEM, TUSCULANUM, PRÆNESTINUM... EPISTOLA CCXXXI. AD EOSDEM PRO ABBATE LATINIACENSI...",
                "witness": "PL182 cols 417-418, Epistolae CCXXX et CCXXXI (physical p. 214)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL182/text/1ad1f822-a2e6-4d22-a4d4-997e8c8f7094-215.txt",
                "physical": 215,
                "score": 90.0,
                "rank": 3,
                "reasons": [
                    "epistola_232_ad_eosdem",
                    "nota_616_ymarus_tusculanus",
                    "pl182_cols_419_420"
                ],
                "snippet": "EPISTOLA CCXXII [CCXXXII]. AD EOSDEM. Contra abbatem Sancti Theofredi... Nota (616) Quippe Albericus Ostiensis, et Ymarus Tusculanus Cluniacenses erant...",
                "witness": "PL182 cols 419-420, Epistola CCXXXII (physical p. 215)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL182/text/88b11b5c-1327-4f85-b255-95bb167e6b6d-602.txt",
                "physical": 602,
                "score": 80.0,
                "rank": 4,
                "reasons": [
                    "index_primary_source",
                    "pl182_col_1195"
                ],
                "snippet": "Igmaro seu Ymaro cardinali episcopo Tusculano, 219, 250-232.",
                "witness": "PL182 col. 1195, Index Epistolarum S. Bernardi (physical p. 602)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_170_pl182_bernardus"):
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
        meta["resolution_campaign"] = "sprint_170_pl182_bernardus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_170_pl182_bernardus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL182.")
