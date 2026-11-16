"""
PL205 Petrus Cantor (Pallidi Sancti) Locator
Sprint 171 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL205:
1. Editorial Context:
   - PL205 contains Petri Cantoris Parisiensis Verbum Abbreviatum cum commentariis et notis Georgii Galopini.
   - In physical p. 517 (564b42d9-40d2-490d-95e8-40a5dd347491-517.txt, col. 1007):
     'INDEX RERUM ET VERBORUM IN PETRUM CANTOREM.'
     'Pallidi Sancti quinam sunt, 3519.
      Eorum simplicitas in vestitu, 233.
      Jejunia, 296.'
   - The reference number '3519' is a typographical and OCR coalescence of the two explanatory notes
     defining 'qui sunt sancti pallidi' by Georgius Galopinus:
     1. Note ad cap. 116 in cols 499-500 (physical p. 263 / aca4d8f7-a915-480f-8b1e-329a1e19341d-263.txt):
        'In concilio etiam sanctorum Pallidorum.] De quibus in libro Vitæ Patrum.
         Pallidi dicti a mortificatione corporis in facie pallorem præ se ferentis.
         De hoc concilio, seu collatione, vide Cassianum in collationibus Patrum, Collat. 2, c. 2.'
     2. Note ad cap. 136 in col. 516 (physical p. 271 / 6cdba9cc-3be2-4a3b-b0f2-d03a6eb7bb2c-271.txt):
        'Sanctorum pallidorum.] Sic vocati a pallore vultus, et maceratione corporis, ut dixi ad c. 116.'
     3. Primary text of Petrus Cantor in col. 328 (physical p. 171 / f97378dc-ab83-475b-8507-ff8becb56a49-171.txt):
        'Hæc fuit via sanctorum pallidorum et exsanguium studentium ad patriam.
         Ut enim testatur Hieronymus... Paulus primus eremita semper jejunavit...'
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
    "39e8cbca231165b94d6b0ae2ab6c080a451e06bf43ff8e4c165559bc11b318d9": {
        "label": "Pallidi Sancti quinam sunt",
        "ref_raw": "3519",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL205/text/aca4d8f7-a915-480f-8b1e-329a1e19341d-263.txt",
                "physical": 263,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "galopinus_notae_cap_116",
                    "definitio_sanctorum_pallidorum",
                    "pallidi_dicti_a_mortificatione_corporis",
                    "pl205_cols_499_500"
                ],
                "snippet": "In concilio etiam sanctorum Pallidorum.] De quibus in libro Vitæ Patrum. Pallidi dicti a mortificatione corporis in facie pallorem præ se ferentis. De hoc concilio, seu collatione, vide Cassianum in collationibus Patrum, Collat. 2, c. 2.",
                "witness": "PL205 cols 499-500, Galopini Notae in Verbum Abbreviatum cap. 116 (physical p. 263)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL205/text/6cdba9cc-3be2-4a3b-b0f2-d03a6eb7bb2c-271.txt",
                "physical": 271,
                "score": 95.0,
                "rank": 2,
                "reasons": [
                    "galopinus_notae_cap_136",
                    "remissio_ad_cap_116",
                    "pl205_cols_515_516",
                    "source_of_516_519_reference"
                ],
                "snippet": "Sanctorum pallidorum.] Sic vocati a pallore vultus, et maceratione corporis, ut dixi ad c. 116.",
                "witness": "PL205 cols 515-516, Galopini Notae in Verbum Abbreviatum cap. 136 (physical p. 271)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL205/text/f97378dc-ab83-475b-8507-ff8becb56a49-171.txt",
                "physical": 171,
                "score": 90.0,
                "rank": 3,
                "reasons": [
                    "petrus_cantor_verbum_abbreviatum_textus_primarius",
                    "pl205_col_328",
                    "via_sanctorum_pallidorum"
                ],
                "snippet": "Hæc fuit via sanctorum pallidorum et exsanguium studentium ad patriam. Ut enim testatur Hieronymus (in Vita sancti Pauli), Paulus primus eremita semper jejunavit...",
                "witness": "PL205 col. 328, Petri Cantoris Verbum Abbreviatum cap. 136 (physical p. 171)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL205/text/564b42d9-40d2-490d-95e8-40a5dd347491-517.txt",
                "physical": 517,
                "score": 80.0,
                "rank": 4,
                "reasons": [
                    "index_primary_source",
                    "pl205_col_1007"
                ],
                "snippet": "Pallidi Sancti quinam sunt, 3519. Eorum simplicitas in vestitu, 233. Jejunia, 296.",
                "witness": "PL205 col. 1007, Index in Petrum Cantorem (physical p. 517)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_171_pl205_cantor"):
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
        meta["resolution_campaign"] = "sprint_171_pl205_cantor"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_171_pl205_cantor")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL205.")
