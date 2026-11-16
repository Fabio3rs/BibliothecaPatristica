"""
PL012 S. Philastrius Brixiensis (Index in Philastrium) Locator
Sprint 156 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL012:
1. Editorial Context:
   - PL012 contains S. Philastrii Brixiensis Episcopi Liber de Haeresibus (cols 1111-1302),
     edited by J. A. Fabricius and Galeardus.
   - In physical pp. 691-698 (cols 1301-1316):
     'INDEX RERUM ET VERBORUM IN PHILASTRIUM.'
   - In col. 1308 (physical p. 692 / ed855fd5-35ce-4b79-98d3-9e5c1a325eba-692.txt):
     'Lex media inter Mandatum, et Gratiam, 1500. Admonitio Mandati, 1300...'
   - The residual item:
     id: 54e27fab49483fe4d6958a9adfc10aa32837da09b46d1fcc27b61ff492794cb7
     'Lex media inter Mandatum, et Gratiam' -> '1500'
   - Textual and editorial analysis reveals:
     - The maximum column of Philastrius is ~1302. The citation '1500' is a typographical erratum
       in Migne for cols 1231 / 1289-1300.
     - In Liber de Haeresibus cap. 115 (cols 1231-1232 / physical p. 655 / 48b7163e-f7e1-447b-b6e8-b1ca717b18f8-655.txt),
       Fabricius's note m comments:
       'm Inter mandatum et gratiam. Id est, inter statum antelegalem et gratiam Evangelii. Mandatum hoc sensu habes infra...'
     - In Liber de Haeresibus cap. 144 (cols 1289-1290 / physical p. 684 / ed855fd5-35ce-4b79-98d3-9e5c1a325eba-684.txt),
       note h comments:
       'h In Mandato, Lege, et Christi gratia. Familiaris hæc Philastrii divisio, per Mandatum intelligentis statum ante legem Mosis,
        per Legem statum sub lege Mosis, per Christi gratiam statum Evangelii.'
     - Primary index source sits on physical p. 692 (ed855fd5-35ce-4b79-98d3-9e5c1a325eba-692.txt).
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
    "54e27fab49483fe4d6958a9adfc10aa32837da09b46d1fcc27b61ff492794cb7": {
        "label": "Lex media inter Mandatum, et Gratiam",
        "ref_raw": "1500",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL012/text/48b7163e-f7e1-447b-b6e8-b1ca717b18f8-655.txt",
                "physical": 655,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "inter_mandatum_et_gratiam",
                    "fabricius_annotation_note_m",
                    "philastrius_de_haeresibus_cap_115"
                ],
                "snippet": "m Inter mandatum et gratiam. Id est, inter statum antelegalem et gratiam Evangelii. Mandatum hoc sensu habes infra præsi 144. FABRIC.",
                "witness": "PL012 cols 1231-1232, S. Philastrii Liber de Haeresibus cap. 115, nota m (physical p. 655)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL012/text/ed855fd5-35ce-4b79-98d3-9e5c1a325eba-684.txt",
                "physical": 684,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "parallel_passage_cap_144",
                    "in_mandato_lege_et_christi_gratia",
                    "triplex_status_antelegalis_legalis_evangelicus"
                ],
                "snippet": "h In Mandato, Lege, et Christi gratia. Familiaris hæc Philastrii divisio, per Mandatum intelligentis statum ante legem Mosis, per Legem statum sub lege Mosis, per Christi gratiam statum Evangelii.",
                "witness": "PL012 cols 1289-1290, S. Philastrii Liber de Haeresibus cap. 144, nota h (physical p. 684)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL012/text/ed855fd5-35ce-4b79-98d3-9e5c1a325eba-692.txt",
                "physical": 692,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_in_philastrium_col_1308"
                ],
                "snippet": "Lex media inter Mandatum, et Gratiam, 1500. Admonitio Mandati, 1300.",
                "witness": "PL012 col. 1308, Index in Philastrium (physical p. 692)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_156_pl012_philastrius"):
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
        meta["resolution_campaign"] = "sprint_156_pl012_philastrius"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_156_pl012_philastrius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL012.")
