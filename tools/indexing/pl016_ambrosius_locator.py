"""
PL016 S. Ambrosius Mediolanensis (Index Rerum et Sententiarum in S. Ambrosium) Locator
Sprint 158 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL016:
1. Editorial Context:
   - PL016 contains S. Ambrosii Mediolanensis Episcopi Opera (cols 9-1406),
     reprinted from the Maurist / Benedictine edition (Paris, 1686-1690, 2 voll. in-folio).
   - In physical pp. 753-774 (cols 1399-1442):
     'INDEX RERUM ET SENTENTIARUM IN OPERA SANCTI AMBROSII.'
   - In col. 1414 (physical p. 760 / c2935390-297e-4191-80c4-987cdc6ea94e-760.txt):
     'Phœnicis resurrectionem cujus scripturæ auctoritate probet Ambrosius? 1149.
      Illius exemplo resurrectionem corporum probant Patres plurimi, ibid.'
   - The residual item:
     id: d112cfa24e298e63dc8a048fa0d4c67b97d5ffae571ea8f4328be98b86ce3960
     'probet Ambrosius?' -> '1149'
   - Textual and editorial analysis reveals:
     - The citation '1149' refers to column 1149 of Tomus II of the Benedictine folio edition of S. Ambrose,
       which contains De Excessu Fratris Satyri liber II (De Fide Resurrectionis).
     - In Migne PL016, this passage is at cols 1331-1332 (physical p. 666 / f58b3d79-1ebc-4859-b00a-b8f4d07a4e41-666.txt):
       '59. Avis in regione Arabiæ, cui nomen est phœnix, redivivo suæ carnis humore reparabilis,
        cum mortua fuerit, reviviscit; solus non credimus homines resuscitari? Atqui hoc relatione crebra,
        et scripturarum auctoritate cognovimus memoratam avem...'
     - Maurist note b explicitly analyzes:
       'b ... Scripturarum igitur vocem adhibet, ut auctoritatem... videri debet, si phœnicis exemplo
        ad probandam resurrectionem... contra Scripturæ auctoritatem sentire pronuntient.'
     - Primary index source sits on physical p. 760 (c2935390-297e-4191-80c4-987cdc6ea94e-760.txt).
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
    "d112cfa24e298e63dc8a048fa0d4c67b97d5ffae571ea8f4328be98b86ce3960": {
        "label": "probet Ambrosius?",
        "ref_raw": "1149",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL016/text/f58b3d79-1ebc-4859-b00a-b8f4d07a4e41-666.txt",
                "physical": 666,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "de_excessu_fratris_satyri_lib_ii",
                    "phoenicis_resurrectio",
                    "scripturarum_auctoritate",
                    "benedictine_edition_tom_ii_col_1149"
                ],
                "snippet": "59. Avis in regione Arabiæ, cui nomen est phœnix, redivivo suæ carnis humore reparabilis, cum mortua fuerit, reviviscit... b et scripturarum auctoritate cognovimus...",
                "witness": "PL016 cols 1331-1332, De Excessu Fratris Satyri lib. II, n. 59 (Benedictine ed. tom. II col. 1149 / physical p. 666)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL016/text/c2935390-297e-4191-80c4-987cdc6ea94e-760.txt",
                "physical": 760,
                "score": 80.0,
                "rank": 2,
                "reasons": [
                    "index_primary_source",
                    "index_rerum_et_sententiarum_col_1414"
                ],
                "snippet": "Phœnicis resurrectionem cujus scripturæ auctoritate probet Ambrosius? 1149. Illius exemplo resurrectionem corporum probant Patres plurimi, ibid.",
                "witness": "PL016 col. 1414, Index Rerum et Sententiarum in S. Ambrosium (physical p. 760)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_158_pl016_ambrosius"):
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
        meta["resolution_campaign"] = "sprint_158_pl016_ambrosius"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_158_pl016_ambrosius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL016.")
