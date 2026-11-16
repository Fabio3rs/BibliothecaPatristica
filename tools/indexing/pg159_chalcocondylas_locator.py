"""
PG159 Laonicus Chalcocondylas / Ducas (Index Rerum et Verborum in Chalcocondylam) Locator
Sprint 154 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG159:
1. Editorial Context:
   - PG159 contains Laonici Chalcocondylae Atheniensis Historiarum Demonstrationes (De Rebus Turcicis)
     libri I-X (cols 13-588), reprinted from the edition of Fabrotus / Combefis / CSHB.
   - In physical pp. 699-715 (cols 1373-1406):
     'INDEX RERUM ET VERBORUM PRÆCIPUORUM QUÆ IN HISTORIA LAONICI CHALCOCONDYLÆ CONTINENTUR.'
   - At col. 1391 (physical p. 707 / 278521df-86be-45d6-84d7-6ff039afc69f-707.txt):
     'Lesbo donatus Franciscus Catalusius, 1535. Sororemque ducis Calojoannis imp. Dominicus Lesbi princeps, 277...'
   - The residual item:
     id: 4daf4b874363ea01a8678bf7c101a926d40ef586ddfc233579eee6095f10c523
     'Lesbo donatus Franciscus Catalusius' -> '1535'
   - Textual and editorial analysis reveals:
     - The entry reproduces verbatim the marginal note (1) in physical p. 260
       (1002121a-b69f-47c3-b9ec-53cdb18c1226-260.txt, cols 511-512, De Rebus Turcicis lib. X):
       '(1) Lesbo donatur Franciscus Catalusius 1352, sororemque ducit Calo-Joannis imp.'
       where the date '1352' (or 1355) was typographically inverted/misprinted in the index as '1535'.
     - It sits within the narrative of the Gattilusio rulers of Lesbos ('Catelusii', cols 511-512, page 278 of the Paris/Bonn edition).
     - Physical p. 259 (1002121a-b69f-47c3-b9ec-53cdb18c1226-259.txt, cols 509-510, page 277) begins the account
       cited as 'Dominicus Lesbi princeps, 277'.
     - Primary index source sits on physical p. 707 (278521df-86be-45d6-84d7-6ff039afc69f-707.txt).
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
    "4daf4b874363ea01a8678bf7c101a926d40ef586ddfc233579eee6095f10c523": {
        "label": "Lesbo donatus Franciscus Catalusius",
        "ref_raw": "1535",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG159/text/1002121a-b69f-47c3-b9ec-53cdb18c1226-260.txt",
                "physical": 260,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "marginal_note_1_verbatim_match",
                    "chalcocondylas_de_rebus_turcicis_lib_x",
                    "franciscus_catalusius_lesbo_donatur",
                    "date_1352_misprinted_1535"
                ],
                "snippet": "Notulæ marginales: (1) Lesbo donatur Franciscus Catalusius 1352, sororemque ducit Calo-Joannis imp. ... Genus principum illorum, Lesbum qui regebant, et Catelusii nuncupabantur...",
                "witness": "PG159 cols 511-512, Chalcocondylas lib. X, note 1 (physical p. 260)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG159/text/1002121a-b69f-47c3-b9ec-53cdb18c1226-259.txt",
                "physical": 259,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "chalcocondylas_page_277",
                    "lesbi_princeps_dominicus",
                    "contextual_precedent"
                ],
                "snippet": "277 Expeditionis regiæ contra Lesbum hanc causam assignant... Lesbi princeps beneficio regis tenebat istam regionem...",
                "witness": "PG159 cols 509-510, Chalcocondylas lib. X (Bonn p. 277 / physical p. 259)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG159/text/278521df-86be-45d6-84d7-6ff039afc69f-707.txt",
                "physical": 707,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_rerum_et_verborum_migne_col_1391"
                ],
                "snippet": "Lesbo donatus Franciscus Catalusius, 1535. Sororemque ducis Calojoannis imp. Dominicus Lesbi princeps, 277.",
                "witness": "PG159 col. 1391, Index Rerum et Verborum in Chalcocondylam (physical p. 707)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_154_pg159_chalcocondylas"):
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
        meta["resolution_campaign"] = "sprint_154_pg159_chalcocondylas"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_154_pg159_chalcocondylas")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG159.")
