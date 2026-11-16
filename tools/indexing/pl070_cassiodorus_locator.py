"""
PL070 M. Aurelius Cassiodorus (Index Analyticus in Cassiodori Tomum II) Locator
Sprint 161 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL070:
1. Editorial Context:
   - PL070 contains M. Aurelii Cassiodori Senatoris Opera Omnia tomus II (cols 9-1416),
     specifically the Expositio in Psalterium (cols 9-1056).
   - In physical pp. 717-736 (cols 1125-1164):
     'INDEX ANALYTICUS IN SECUNDUM CASSIODORI TOMUM.'
   - In col. 1130 (physical p. 719 / 99afcb42-78ed-4465-9696-0b9ccccc64b9-719.txt):
     'Misericordia Patris Christus est, 298. Virtus Patris, 224. Manus Patris, 458.
      Anima ejus, framea est Patris, 57...'
   - The residual item:
     id: group:8d23d9c1e45a579d86aefb4d0d3bf27d4fb8a56ec997ea9821ec55628338626e
     'Manus Patris' -> '45-8'
   - Textual and editorial analysis reveals:
     - The raw reference '45-8' is an OCR hyphenation/speck defect for column '458'.
     - Column 458 sits on physical pp. 233-234 (706f2d55-8e41-4f6b-939d-b9a27e79ea21-233.txt and 234.txt,
       Expositio in Psalmum LXVI):
       '457 EXPOSITIO IN PSALTERIUM. PSAL. LXVI. ... sed idem hymnus quem nunc propheta canit,
        ideo praemisit in finem, quia tota oratio ad Dominum Christum sine dubitatione dirigitur...'
     - Primary index source sits on physical p. 719 (99afcb42-78ed-4465-9696-0b9ccccc64b9-719.txt).
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
    "group:8d23d9c1e45a579d86aefb4d0d3bf27d4fb8a56ec997ea9821ec55628338626e": {
        "label": "Manus Patris",
        "ref_raw": "45-8",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL070/text/706f2d55-8e41-4f6b-939d-b9a27e79ea21-233.txt",
                "physical": 233,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "cassiodorus_in_psalm_lxvi",
                    "pl070_cols_457_458",
                    "christus_manus_et_dextra_patris"
                ],
                "snippet": "457 EXPOSTITIO IN PSALTERIUM. PSAL. LXVI. ... sed idem hymnus quem nunc propheta canit, ideo praemisit in finem, quia tota oratio ad Dominum Christum sine dubitatione dirigitur...",
                "witness": "PL070 cols 457-458, Cassiodori Expositio in Psalmum LXVI (physical p. 233)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL070/text/706f2d55-8e41-4f6b-939d-b9a27e79ea21-234.txt",
                "physical": 234,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pl070_cols_459_460",
                    "psalm_lxvi_continuatio",
                    "benedictio_domini"
                ],
                "snippet": "Expositio psalmi: Deus misereatur nostri, et benedicat nos; illuminet vultum suum super nos...",
                "witness": "PL070 cols 459-460, Expositio in Psalmum LXVI (physical p. 234)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL070/text/99afcb42-78ed-4465-9696-0b9ccccc64b9-719.txt",
                "physical": 719,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_analyticus_in_cassiodorum_col_1130"
                ],
                "snippet": "Misericordia Patris Christus est, 298. Virtus Patris, 224. Manus Patris, 45-8. Anima ejus, framea est Patris, 57.",
                "witness": "PL070 col. 1130, Index Analyticus in Secundum Cassiodori Tomum (physical p. 719)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_161_pl070_cassiodorus"):
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
        meta["resolution_campaign"] = "sprint_161_pl070_cassiodorus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_161_pl070_cassiodorus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL070.")
