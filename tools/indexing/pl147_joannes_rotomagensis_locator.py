"""
PL147 Joannes Rotomagensis (Index in Joannem Rotomagensem) Locator
Sprint 167 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL147:
1. Editorial Context:
   - PL147 contains Joannis Abrincensis (Rotomagensis Archiepiscopi) Liber de Officiis Ecclesiasticis (cols 9-262),
     edited by Franciscus Prevotius (Rouen, 1679).
   - In physical pp. 665-670 (cols 1515-1526):
     'INDEX HISTORICUS ET DOGMATICUS IN JOANNEM ROTOMAGENSEM.'
   - In col. 1515 (physical p. 665 / 6b7a4a48-1548-46d6-96e9-dc5571962f78-665.txt):
     'Elevatio hostiæ et calicis solemnis alias inusitata 108.
      Olim unica ante orationem Dominicam, ibid.
      Hostia in oblatione cooperiebatur corporali, 20, 108.
      Hostiæ et calicis seorsim elevatio non adeo antiqua 108.'
   - The residual item:
     id: 579a76e012f4e2a4afd60a578d5da227fa445cf32ef7adcdf59a72ee2e8b6d0f
     'Elevatio hostiæ et calicis solemnis alias inusitata' -> '108'
   - Textual and editorial analysis reveals:
     - The reference '108' cites page 108 of Prevotius's 1679 Rouen edition of the Liber de Officiis Ecclesiasticis,
       which corresponds to Prevotius's famous liturgical annotation (73) on the Elevation of the Host and Chalice
       in cols 77-78 of PL147 (physical p. 44 / 0c699d1c-31b0-456a-9ba7-28454cafb728-044.txt):
       '(73) Oratione finita uterque calicem levent, et simul ponant et cooperiant. Hodie diaconus non levat calicem,
        sed tantum discooperit, et post elevationem cooperit. Olim calix levabatur a sacerdote et diacono, simul
        et deponiebatur; verum non solus calix, sed etiam hostia in patena calici superposita...
        Adverte autem apud auctorem nostrum nihil legi de elevatione corporis et sanguinis Christi seorsim,
        ut hodie sit simul atque panis et vinum consecrata sunt... quem ritum non puto adeo esse antiquum...'
     - In the primary liturgical text of Joannes Rotomagensis (col. 35 / physical p. 23 / 0c699d1c-31b0-456a-9ba7-28454cafb728-023.txt),
       note (73) is attached to: 'Oratione finita uterque calicem levent (73)... per elevationem de altari hostiæ,
       depositionem de cruce...'
     - Primary index source sits on physical p. 665 (6b7a4a48-1548-46d6-96e9-dc5571962f78-665.txt).
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
    "579a76e012f4e2a4afd60a578d5da227fa445cf32ef7adcdf59a72ee2e8b6d0f": {
        "label": "Elevatio hostiæ et calicis solemnis alias inusitata",
        "ref_raw": "108",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL147/text/0c699d1c-31b0-456a-9ba7-28454cafb728-044.txt",
                "physical": 44,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "prevotius_edition_page_108",
                    "nota_73_de_elevatione_hostiae_et_calicis",
                    "elevatio_seorsim_non_adeo_antiqua",
                    "pl147_cols_77_78"
                ],
                "snippet": "(73) Oratione finita uterque calicem levent, et simul ponant et cooperiant. Hodie diaconus non levat calicem... Olim calix levabatur a sacerdote et diacono... non solus calix, sed etiam hostia in patena... Adverte autem apud auctorem nostrum nihil legi de elevatione corporis et sanguinis Christi seorsim... quem ritum non puto adeo esse antiquum...",
                "witness": "PL147 cols 77-78, Prevotii Notae in Joannem Rotomagensem n. 73 (Prevotius ed. p. 108 / physical p. 44)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL147/text/0c699d1c-31b0-456a-9ba7-28454cafb728-023.txt",
                "physical": 23,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "joannes_rotomagensis_textus_primarius",
                    "pl147_col_35",
                    "orione_finita_uterque_calicem_levent"
                ],
                "snippet": "Oratione finita uterque calicem levent (73)... per elevationem de altari hostiæ, depositionem de cruce...",
                "witness": "PL147 col. 35, Joannis Rotomagensis Liber de Officiis Ecclesiasticis (physical p. 23)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL147/text/6b7a4a48-1548-46d6-96e9-dc5571962f78-665.txt",
                "physical": 665,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_in_joannem_rotomagensem_col_1515"
                ],
                "snippet": "Elevatio hostiæ et calicis solemnis alias inusitata 108. Olim unica ante orationem Dominicam, ibid.",
                "witness": "PL147 col. 1515, Index in Joannem Rotomagensem (physical p. 665)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_167_pl147_joannes"):
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
        meta["resolution_campaign"] = "sprint_167_pl147_joannes"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_167_pl147_joannes")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL147.")
