"""
PL177 Hugo de S. Victore (Speculum Ecclesiae) Locator
Sprint 169 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL177:
1. Editorial Context:
   - PL177 contains Tomus III of Hugo de Sancto Victore (Opera Dogmatica et Mystica).
   - In physical p. 622 (22c5236a-3aa2-40fc-adc3-613011ae584e-622.txt, col. 1236):
     'INDEX RERUM ANALYTICUS.'
     'Speculum Ecclesiæ, 556 et seqq.'
   - The treatise referred to is the famous 'SPECULUM DE MYSTERIIS ECCLESIÆ' (frequently styled
     'SPECULUM ECCLESIÆ' in running headers across cols 335-380 / physical pp. 172-194).
   - In the Table of Contents (col. 1245 / physical p. 627):
     'SPECULUM DE MYSTERIIS ECCLESIÆ. Prologus. 335... CAP. VII. — De celebratione missæ. 356'
   - The reference '556 et seqq.' is a classic 19th-century typographical numeral inversion/misprint
     for '356 et seqq.' (Cap. VII on the Mass) / '335 et seqq.' (the opening of the treatise),
     where digits 3 and 5 are inverted or misread.
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
    "group:26d880d69d9a304a7077e18df2ea8c03c19cffbcc184c92935d8d5ab0cd96d5f": {
        "label": "Speculum Ecclesiæ",
        "ref_raw": "556 et seqq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL177/text/38f4b2ec-63b6-4e34-a1b9-aed81f3c5a27-172.txt",
                "physical": 172,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "treatise_opening_title_and_prologue",
                    "speculum_de_mysteriis_ecclesiae",
                    "pl177_col_335",
                    "typographical_inversion_356_335_to_556"
                ],
                "snippet": "SPECULUM DE MYSTERIIS ECCLESIÆ. PROLOGUS. De sacramentis ecclesiasticis, ut tractarem... Desideratum itaque libellum interno fluentem nectare, velut favum mellis, vestræ propinavi intelligentiæ...",
                "witness": "PL177 col. 335, Speculum de Mysteriis Ecclesiae (physical p. 172)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL177/text/38f4b2ec-63b6-4e34-a1b9-aed81f3c5a27-183.txt",
                "physical": 183,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "running_header_speculum_ecclesiae",
                    "caput_VII_de_celebratione_missae",
                    "pl177_cols_356_358",
                    "source_of_356_misprinted_as_556"
                ],
                "snippet": "357 SPECULUM ECCLESIÆ. 358... De celebratione missæ...",
                "witness": "PL177 cols 356-358, Speculum Ecclesiae cap. VII (physical p. 183)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL177/text/22c5236a-3aa2-40fc-adc3-613011ae584e-627.txt",
                "physical": 627,
                "score": 85.0,
                "rank": 3,
                "reasons": [
                    "elenchus_operum_col_1245",
                    "quae_in_hoc_tomo_continentur"
                ],
                "snippet": "SPECULUM DE MYSTERIIS ECCLESIÆ. Prologus. 335... CAP. VII. — De celebratione missæ. 356",
                "witness": "PL177 col. 1245, Elenchus Operum (physical p. 627)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL177/text/22c5236a-3aa2-40fc-adc3-613011ae584e-622.txt",
                "physical": 622,
                "score": 80.0,
                "rank": 4,
                "reasons": [
                    "index_primary_source",
                    "pl177_col_1236"
                ],
                "snippet": "Speculum Ecclesiæ, 556 et seqq.",
                "witness": "PL177 col. 1236, Index Rerum Analyticus (physical p. 622)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_169_pl177_hugo"):
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
        meta["resolution_campaign"] = "sprint_169_pl177_hugo"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_169_pl177_hugo")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL177.")
