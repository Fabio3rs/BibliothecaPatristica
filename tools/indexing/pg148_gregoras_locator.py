"""
PG148 Nicephorus Gregoras (Index Rerum et Verborum in Byzantinam Historiam) Locator
Sprint 153 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG148:
1. Editorial Context:
   - PG148 contains Nicephori Gregorae Byzantinae Historiae libri XII-XXXVI (cols 9-1448),
     reprinted from the Bonn Corpus Scriptorum Historiae Byzantinae (CSHB, ed. Hieronymus Wolf / Boivin / Schopen).
   - In physical pp. 727-756 (cols 1441-1500):
     'INDEX RERUM ET VERBORUM NOTABILIUM IN NICEPHORI GREGORÆ HISTORIAM BYZANTINAM.'
   - At col. 1469 (physical p. 741 / 79272189-68c8-4eb5-8750-68e3fa413902-741.txt):
     'Ecclesiæ patriarchales et episcopales pæne omnes principibus barbaris subjectæ, 1954'
   - The residual item:
     id: df07fbae9b98823cb0c95a3571e7a48b1d49fb24d80f71a4ebf241ffec349872
     'Ecclesiæ patriarchales et episcopales pæne omnes principibus barbaris subjectæ' -> '1954'
   - Textual analysis reveals this is an editorial/typographical erratum for Bonn page 1034 (or Migne col. 1310):
     In Byzantinae Historiae lib. XXI (cols 1309-1310 / physical p. 661 / 04b9234c-fdbb-4cfb-983e-0c8b5305d14a-661.txt):
     'patriarchæ autem fere omnes, et episcopi plerique, 1034 eruditione justa ac dogmatum usu præstarent...
      cum patriarchales ecclesiæ, et episcopales pene dixerim universæ, principibus barbaris subjiciuntur;
      atque idcirco persecutores simul et ii qui persecutionem patiuntur... Byzantio urbe, includuntur...'
   - Primary index source sits on physical p. 741 (79272189-68c8-4eb5-8750-68e3fa413902-741.txt).
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
    "df07fbae9b98823cb0c95a3571e7a48b1d49fb24d80f71a4ebf241ffec349872": {
        "label": "Ecclesiæ patriarchales et episcopales pæne omnes principibus barbaris subjectæ",
        "ref_raw": "1954",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG148/text/04b9234c-fdbb-4cfb-983e-0c8b5305d14a-661.txt",
                "physical": 661,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "bonn_cshb_page_1034_erratum_1954",
                    "byzantinae_historiae_lib_xxi",
                    "literal_text_match",
                    "patriarchales_ecclesiae_principibus_barbaris_subjectae"
                ],
                "snippet": "cum patriarchales ecclesiæ, et episcopales pene dixerim universæ, principibus barbaris subjiciuntur; atque idcirco persecutores simul et ii qui persecutionem patiuntur...",
                "witness": "PG148 cols 1309-1310, Byzantinae Historiae lib. XXI, cap. VII (Bonn p. 1034, misprinted 1954)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG148/text/79272189-68c8-4eb5-8750-68e3fa413902-741.txt",
                "physical": 741,
                "score": 80.0,
                "rank": 2,
                "reasons": [
                    "index_primary_source",
                    "index_rerum_et_verborum_migne_col_1469"
                ],
                "snippet": "Ecclesiæ patriarchales et episcopales pæne omnes principibus barbaris subjectæ, 1954",
                "witness": "PG148 col. 1469, Index Rerum et Verborum in Nicephori Gregorae Historiam Byzantinam (physical p. 741)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_153_pg148_gregoras"):
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
        meta["resolution_campaign"] = "sprint_153_pg148_gregoras"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_153_pg148_gregoras")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG148.")
