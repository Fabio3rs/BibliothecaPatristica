"""
PO003 Ahoudemmeh (De l'union de l'ame et du corps) Locator
Sprint 173 of Onomastic Catalog Recovery

Resolves the residual source_only item from PO003:
1. Editorial Context:
   - PO003 contains the 'Traité d'Ahoudemmeh sur l'homme' (text syriaque et traduction française par F. Nau).
   - In physical p. 130 (47407ec6-0467-40cb-8c70-9d75083e0385-130.txt):
     'TABLE DES MATIÈRES'
     'III. — De l'union de l'âme et du corps. 1o Classification des opérations bonnes et mau-
      vaises . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . 107'
   - In physical p. 117 (47407ec6-0467-40cb-8c70-9d75083e0385-117.txt, fascicule page [107]):
     Running header: '[107] III. — DE L'UNION DE L'AME ET DU CORPS. 107'
   - In physical p. 118 (47407ec6-0467-40cb-8c70-9d75083e0385-118.txt, fascicule page [108]):
     Section heading: '1° Classification des opérations bonnes et mauvaises. — Le bien de la con-'
   - In physical p. 107 (88004c43-1ff3-4a21-a7e5-7af32b92f3d6-107.txt, Introduction by F. Nau):
     'Il traite d'abord de l'âme et de ses puissances ou facultés, puis de l'union de l'âme avec le corps...'
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
    "region-entry:301e5d51dd50fe598c968849eaed17cc428329167ca895a2594ab9f53101d3f8": {
        "label": "III. — De l'union de l'âme et du corps",
        "ref_raw": "1o Classification des opérations bonnes et mau-",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PO003/text/47407ec6-0467-40cb-8c70-9d75083e0385-117.txt",
                "physical": 117,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "primary_chapter_header_page_107",
                    "de_l_union_de_l_ame_et_du_corps",
                    "fascicule_page_107"
                ],
                "snippet": "[107] III. — DE L'UNION DE L'AME ET DU CORPS. 107",
                "witness": "PO003 fasc. p. [107], Traité d'Ahoudemmeh cap. III (physical p. 117)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PO003/text/47407ec6-0467-40cb-8c70-9d75083e0385-118.txt",
                "physical": 118,
                "score": 95.0,
                "rank": 2,
                "reasons": [
                    "section_title_verbatim_match",
                    "classification_des_operations_bonnes_et_mauvaises",
                    "fascicule_page_108"
                ],
                "snippet": "1° Classification des opérations bonnes et mauvaises. — Le bien de la con-",
                "witness": "PO003 fasc. p. [108], Traité d'Ahoudemmeh (physical p. 118)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PO003/text/88004c43-1ff3-4a21-a7e5-7af32b92f3d6-107.txt",
                "physical": 107,
                "score": 85.0,
                "rank": 3,
                "reasons": [
                    "introduction_by_f_nau",
                    "summary_of_chapter_III"
                ],
                "snippet": "Il traite d'abord de l'âme et de ses puissances ou facultés, puis de l'union de l'âme avec le corps et en particulier des sens du corps...",
                "witness": "PO003 Introduction p. 97 [physical p. 107]"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PO003/text/47407ec6-0467-40cb-8c70-9d75083e0385-130.txt",
                "physical": 130,
                "score": 80.0,
                "rank": 4,
                "reasons": [
                    "table_des_matieres_primary_source",
                    "page_107_target"
                ],
                "snippet": "III. — De l'union de l'âme et du corps. 1o Classification des opérations bonnes et mauvaises . . . . . 107",
                "witness": "PO003 Table des matières (physical p. 130)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_173_po003_ahoudemmeh"):
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
        meta["resolution_campaign"] = "sprint_173_po003_ahoudemmeh"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_173_po003_ahoudemmeh")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PO003.")
