"""
PL013 S. Damasus Papa (Index Analyticus in S. Damasum) Locator
Sprint 157 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL013:
1. Editorial Context:
   - PL013 contains S. Damasi Papae Opera Omnia (cols 9-442), edited from the Roman edition of A. M. Merenda.
   - In physical pp. 615-628 (cols 425-450):
     'INDEX ANALYTICUS IN RES GESTAS ET SCRIPTA DAMASI PAPÆ.'
   - In col. 441 (physical p. 623 / 8670e73b-ac7c-4ddd-b24c-bf9365b35fa8-623.txt):
     '—Ariminensis decreta a Liberio et Damaso cassata, 318, 350 et seq.'
   - The residual item:
     id: group:4311de4749a5c29c766b31363f2142a8e62f2d2658a375a251791fc9c695f691
     'Ariminensis decreta a Liberio et Damaso cassata' -> '318, 350 et seq'
   - Textual and editorial analysis reveals:
     - Col. 318 sits on physical pp. 163-164 (64ddf9be-dcb9-4132-85a4-36718f03d077-164.txt, cols 318-320,
       Merenda's Dissertatio de Gestis Liberii et Damasi):
       'Arimini gesta, apostolica auctoritate cassavit et in irritum misit Liberius...'
     - Cols 349-352 sit on physical pp. 179-180 (6fa5c977-cc7b-4b73-abee-262930e2b956-179.txt and 180.txt,
       S. Damasi Epistola I ad Episcopos Illyrici):
       'Confidimus quidem sanctitatem vestram... decretum Ariminense irritum haberi...'
     - Primary index source sits on physical p. 623 (8670e73b-ac7c-4ddd-b24c-bf9365b35fa8-623.txt).
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
    "group:4311de4749a5c29c766b31363f2142a8e62f2d2658a375a251791fc9c695f691": {
        "label": "Ariminensis decreta a Liberio et Damaso cassata",
        "ref_raw": "318, 350 et seq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL013/text/64ddf9be-dcb9-4132-85a4-36718f03d077-164.txt",
                "physical": 164,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "liberius_arimini_gesta_cassavit",
                    "pl013_col_318_319",
                    "merenda_dissertatio"
                ],
                "snippet": "Arimini gesta, apostolica auctoritate cassavit et in irritum misit... Liberium, qui Ariminense concilium in irritum misit...",
                "witness": "PL013 cols 318-320, Merenda Dissert. in Damasum (physical p. 164)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL013/text/6fa5c977-cc7b-4b73-abee-262930e2b956-179.txt",
                "physical": 179,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pl013_cols_349_350",
                    "damasus_epistola_ad_episcopos_illyrici",
                    "synodus_ariminensis_cassata"
                ],
                "snippet": "Confidimus quidem sanctitatem vestram... decreta Ariminensia a Damaso et nonaginta episcopis cassata...",
                "witness": "PL013 cols 349-350, S. Damasi Epistola I ad Episcopos Illyrici (physical p. 179)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL013/text/8670e73b-ac7c-4ddd-b24c-bf9365b35fa8-623.txt",
                "physical": 623,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_analyticus_in_damasum_col_441"
                ],
                "snippet": "—Ariminensis decreta a Liberio et Damaso cassata, 318, 350 et seq.",
                "witness": "PL013 col. 441, Index Analyticus in S. Damasum (physical p. 623)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_157_pl013_damasus"):
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
        meta["resolution_campaign"] = "sprint_157_pl013_damasus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_157_pl013_damasus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL013.")
