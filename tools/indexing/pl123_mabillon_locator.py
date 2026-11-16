"""
PL123 Ado Viennensis / S. Barnardus (Miracula S. Barnardi - Mabillon) Locator
Sprint 163 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL123:
1. Editorial Context:
   - PL123 contains Adonis Viennensis Archiepiscopi Opera (cols 9-452).
   - In physical pp. 225-234 (cols 435-452):
     Vita, Translatio et Miracula S. Barnardi Archiepiscopi Viennensis,
     reprinted from Joannes Mabillon (Acta Sanctorum Ordinis S. Benedicti, saec. IV, pars II).
   - At col. 445 (physical p. 230 / 3ceb0653-4eb2-47ea-aa30-ff361e109d61-230.txt):
     'MIRACULA EJUSDEM
      Auctore anonymo Viennensi episcopo, ex num. 7, qui sæculo x vixit.
      (Apud Mabill., ibid.)'
   - The residual item:
     id: group:f5bfcb793ad0489c5fe6e6e2cf9340c93eb55be33d4659a6b91a88b316698e78
     '(Apud Mabill.' -> 'ibid'
   - Textual and editorial analysis reveals:
     - This entry represents an editorial citation rubic for the Miracula S. Barnardi,
       referencing Mabillon's Acta Sanctorum O.S.B. where the preceding Vita and Translatio also appeared.
     - Physical p. 230 (3ceb0653-4eb2-47ea-aa30-ff361e109d61-230.txt, cols 445-446) contains the title and beginning
       of the Miracula: '1. Quamvis imperitia mea in posterum velut inscia litterarum calumniabitur,
       tamen vis amoris domini antistitis altiorisque mei Barnardi compellit me scribere virtutes nuper gestas...'
     - Physical p. 229 (3ceb0653-4eb2-47ea-aa30-ff361e109d61-229.txt, cols 443-444) contains the preceding Translatio S. Barnardi,
       also taken from Mabillon.
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
    "group:f5bfcb793ad0489c5fe6e6e2cf9340c93eb55be33d4659a6b91a88b316698e78": {
        "label": "(Apud Mabill.",
        "ref_raw": "ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-230.txt",
                "physical": 230,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "miracula_s_barnardi_auctore_anonymo",
                    "mabillon_acta_sanctorum_osb",
                    "pl123_cols_445_446"
                ],
                "snippet": "MIRACULA EJUSDEM. Auctore anonymo Viennensi episcopo, ex num. 7, qui sæculo x vixit. (Apud Mabill., ibid.) ... 1. Quamvis imperitia mea... Barnardi compellit me scribere virtutes nuper gestas...",
                "witness": "PL123 cols 445-446, Miracula S. Barnardi (physical p. 230)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL123/text/3ceb0653-4eb2-47ea-aa30-ff361e109d61-229.txt",
                "physical": 229,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "translatio_s_barnardi",
                    "preceding_mabillon_text",
                    "ibid_antecedent"
                ],
                "snippet": "Translatio S. Barnardi Viennensis (ex Mabillonio): Hæc ancilla Dei quæ congregationi præerat in superiori monasterio...",
                "witness": "PL123 cols 443-444, Translatio S. Barnardi (physical p. 229)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_163_pl123_mabillon"):
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
        meta["resolution_campaign"] = "sprint_163_pl123_mabillon"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_163_pl123_mabillon")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL123.")
