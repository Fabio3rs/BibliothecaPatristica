"""
PL165 S. Bruno Astensis Locator
Sprint 168 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL165:
1. Editorial Context:
   - S. Bruno Astensis (Episcopus Signiensis, Abbas Montis Casinensis) has his works in PL164 and PL165,
     reprinted from the Rome edition of Bruno Bruni (1789-1791, 2 tomes in-folio).
   - At the end of PL165 (cols 1321-1336 / physical pp. 661-678):
     'INDEX RERUM ET SENTENTIARUM MEMORABILIUM IN OPERA S. BRUNONIS'
     with the editorial rubric: 'Revocatur lector ad numeros crassiori charactere textui insertos.'
   - The Roman numerals 'I' and 'II' indicate the Tomus of the Roman edition:
     Tomus I corresponds to PL164, and Tomus II corresponds to PL165.
   - The bold inline numbers in the text indicate the page numbers of the Roman edition.
   - In physical p. 674 (523f6416-242c-4faf-ab62-fd9c20c2e6c1-674.txt, col. 1328):
     'Quod peccando donis Dei abutimur, ideo magnam injuriam ei irrogamus, I, 244.'
   - In PL164 physical p. 299 (38055fb3-4951-4c38-b193-3dc4a19e42dc-299.txt, cols 593-594),
     at Expositio in Job cap. XI-XII, bold number '244' appears inline:
     'Facies autem levatur absque macula, 244 cum nihil est in conscientia...'
   - And continuing in col. 595 (physical p. 300 / 38055fb3-4951-4c38-b193-3dc4a19e42dc-300.txt),
     commenting on Job 12:6 ('cum ipse dederit omnia in manibus eorum'):
     'abundant enim mundana felicitate tabernacula prædonum, et audacter ad indignationem,
      et iram provocant Deum... hoc enim faciunt contra Deum, quamvis nihil habeant nisi a Deo.'
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
    "6ba6f3a4a395c904ebf331d752e274d4cc4231e7853c20127b1e78842cf03847": {
        "label": "Quod peccando donis Dei abutimur, ideo magnam injuriam ei irrogamus",
        "ref_raw": "I, 244",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL164/text/38055fb3-4951-4c38-b193-3dc4a19e42dc-299.txt",
                "physical": 299,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "roman_edition_tome_I_page_244",
                    "bold_locator_244_inline",
                    "pl164_cols_593_594",
                    "expositio_in_job_cap_11_12"
                ],
                "snippet": "Facies autem levatur absque macula, 244 cum nihil est in conscientia, unde homo erubescat. Stabilis vero est, et non timet, cujus mens in dilectione solidatur...",
                "witness": "PL164 cols 593-594, S. Brunonis Expositio in Job cap. XI-XII (ed. Rom. tom. I p. 244 / physical p. 299)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL164/text/38055fb3-4951-4c38-b193-3dc4a19e42dc-300.txt",
                "physical": 300,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "expositio_in_job_cap_12_donis_dei_abutimur",
                    "pl164_col_595",
                    "contra_deum_nihil_habeant_nisi_a_deo"
                ],
                "snippet": "abundant enim mundana felicitate tabernacula prædonum, et audacter ad indignationem, et iram provocant Deum; in rapinis videlicet... hoc enim faciunt contra Deum, quamvis nihil habeant nisi a Deo.",
                "witness": "PL164 col. 595, S. Brunonis Expositio in Job cap. XII (physical p. 300)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL165/text/523f6416-242c-4faf-ab62-fd9c20c2e6c1-674.txt",
                "physical": 674,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "pl165_col_1328"
                ],
                "snippet": "Quod peccando donis Dei abutimur, ideo magnam injuriam ei irrogamus, I, 244.",
                "witness": "PL165 col. 1328, Index in S. Brunonem (physical p. 674)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_168_pl165_bruno"):
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
        meta["resolution_campaign"] = "sprint_168_pl165_bruno"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_168_pl165_bruno")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL165.")
