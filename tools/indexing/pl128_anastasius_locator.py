"""
PL128 Anastasius Bibliothecarius (Index Generalis in Vitas Romanorum Pontificum) Locator
Sprint 165 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL128:
1. Editorial Context:
   - PL127 and PL128 contain Anastasii Bibliothecarii Historia de Vitis Romanorum Pontificum (Liber Pontificalis),
     edited from the Roman edition of Blanchinus (Francesco Bianchini) and Vignolius.
   - In PL128 physical pp. 719-740 (cols 1403-1446):
     'INDEX RERUM ET VERBORUM NOTABILIUM IN OMNES VITAS ROMANORUM PONTIFICUM.'
   - In col. 1407 (physical p. 721 / a2b185dc-269b-496b-a79c-21cdc00f42c6-721.txt):
     'Canthara cerostrata 54, 56 et passim. Intersatilis, in quam thus mittitur 567.
      Argentea 75, 80, 585, et passim. Aurea 70, 646.
      Cantharus sessilis 581.
      Cerostratæ argenteæ 86, 105, 119, 525, 565, 598...'
   - The residual item:
     id: group:7077291c529e8208009749dfc892089f22a3d81fb6c9a659862893a3b3beb043
     'Canthara cerostrata 54' -> '56 et passim'
   - Textual and editorial analysis reveals:
     - Columns 54 and 56 sit on physical pp. 31-32 (1c79decf-b952-41f0-8e75-afdebac24b46-031.txt and 032.txt,
       Historia de Vitis Rom. Pont. — S. Liberius / S. Felix):
       describing church furniture, sacred vessels, and basilica dedications.
     - On physical p. 41 (cols 73-74, S. Damasus), the text records verbatim:
       'canthara cerostrata ærea pensantem libras 16; canthara cerostata ærea...'
     - On physical p. 105 (cols 201-202, S. Leo), the text records:
       'cantharas cerostratas æreas 14... Cantharas argenteas cerostratas in gremio basilicæ...'
     - Primary index source sits on physical p. 721 (a2b185dc-269b-496b-a79c-21cdc00f42c6-721.txt).
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
    "group:7077291c529e8208009749dfc892089f22a3d81fb6c9a659862893a3b3beb043": {
        "label": "Canthara cerostrata 54",
        "ref_raw": "56 et passim",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL128/text/1c79decf-b952-41f0-8e75-afdebac24b46-031.txt",
                "physical": 31,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_col_54_match",
                    "pl128_cols_53_54",
                    "liber_pontificalis_s_liberius",
                    "vasa_sacra_basilicarum"
                ],
                "snippet": "53 HISTORIA DE VITIS ROM. PONT. — S. LIBERIUS. 54 ... basilicam nomini suo juxta macellum Liviæ...",
                "witness": "PL128 cols 53-54, Liber Pontificalis (physical p. 31)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL128/text/1c79decf-b952-41f0-8e75-afdebac24b46-032.txt",
                "physical": 32,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "literal_col_56_match",
                    "pl128_cols_55_56",
                    "anastasii_bibliothecarii"
                ],
                "snippet": "55 ANASTASII BIBLIOTHECARII 56 ... canthara et vasa sacra donata basilicis...",
                "witness": "PL128 cols 55-56, Liber Pontificalis (physical p. 32)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL128/text/1c79decf-b952-41f0-8e75-afdebac24b46-041.txt",
                "physical": 41,
                "score": 85.0,
                "rank": 3,
                "reasons": [
                    "literal_phrase_canthara_cerostrata",
                    "pl128_cols_73_74",
                    "liber_pontificalis_s_damasus"
                ],
                "snippet": "73 HISTORIA DE VITIS ROM. PONT. — S. DAMASUS. 74 ... canthara cerostrata ærea pensantem libras 16; canthara cerostata ærea...",
                "witness": "PL128 cols 73-74, Liber Pontificalis (physical p. 41)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL128/text/a2b185dc-269b-496b-a79c-21cdc00f42c6-721.txt",
                "physical": 721,
                "score": 80.0,
                "rank": 4,
                "reasons": [
                    "index_primary_source",
                    "index_rerum_notabilium_col_1407"
                ],
                "snippet": "Canthara cerostrata 54, 56 et passim. Intersatilis, in quam thus mittitur 567.",
                "witness": "PL128 col. 1407, Index Rerum et Verborum in Vitas Rom. Pont. (physical p. 721)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_165_pl128_anastasius"):
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
        meta["resolution_campaign"] = "sprint_165_pl128_anastasius"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_165_pl128_anastasius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL128.")
