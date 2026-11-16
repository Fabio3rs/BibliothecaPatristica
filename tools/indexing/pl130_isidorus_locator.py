"""
PL130 Isidorus Mercator (Index in Isidorum Mercatorem) Locator
Sprint 166 of Onomastic Catalog Recovery

Resolves the residual source_only item from PL130:
1. Editorial Context:
   - PL130 contains Isidori Mercatoris Collectio Decretalium (cols 9-1178).
   - In physical pp. 645-660 (cols 1167-1198):
     'INDEX HISTORICUS ET DOGMATICUS IN ISIDORUM MERCATOREM.'
   - In col. 1175 (physical p. 649 / c6e96b78-37a2-463f-8bc0-463978f8b0bf-649.txt):
     'Ignorantia est origo totius mali... 9.
      Ignorantia est tolerabilior quam verbalis cogitata concupiscentia, 91.
      Ignorantia mitius punitur quam dolus, 167.
      Ignorantia intolerabilis quae se putat aliquid scire, 0.
      Ignorantia in his qui praesunt, est prorsus intolerabilis, 192.
      Ignorantia omnium errorum mater in sacerdotibus est intolerabilis, 114.'
   - The residual item:
     id: group:7145bdb71e03914d4d4a338c7982f561db90e4580365e48ea8ca4aacdb8f4e99
     'Ignorantia intolerabilis quae se putat aliquid scire' -> '0'
   - Textual and editorial analysis reveals:
     - The numeral '0' in the index is a broken type / OCR truncation for column '48' (or '50').
     - In S. Clementis Epistola IV (cols 47-48 / physical p. 53 / bbd51eed-91f2-422d-a730-8b2faabf1278-053.txt),
       the text states verbatim:
       'Et ideo paululum laborandum nobis est, ut indignantes ignorantiæ præsumptiones scientiæ ratione resecemus
        in his præcipue qui in aliquibus minus rectis opinionibus præventi sunt, per quas quasi sub specie alicujus
        scientiæ ignorantia in his vehementius radicata est. Nihil enim est gravius quam si id quod ignorat quis,
        scire se credat et defendat verum esse quod falsum est...'
     - Physical p. 54 (bbd51eed-91f2-422d-a730-8b2faabf1278-054.txt, cols 49-50) concludes Clement's epistle.
     - Primary index source sits on physical p. 649 (c6e96b78-37a2-463f-8bc0-463978f8b0bf-649.txt).
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
    "group:7145bdb71e03914d4d4a338c7982f561db90e4580365e48ea8ca4aacdb8f4e99": {
        "label": "Ignorantia intolerabilis quae se putat aliquid scire",
        "ref_raw": "0",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL130/text/bbd51eed-91f2-422d-a730-8b2faabf1278-053.txt",
                "physical": 53,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "literal_text_match",
                    "clementis_epistola_iv",
                    "ignorantia_sub_specie_scientiae",
                    "nihil_gravius_quam_si_id_quod_ignorat_scire_se_credat",
                    "pl130_cols_47_48"
                ],
                "snippet": "47 APPEND. AD SÆC. IX. — ISIDORI MERCATORIS 48 ... quasi sub specie alicujus scientiæ ignorantia in his vehementius radicata est. Nihil enim est gravius quam si id quod ignorat quis, scire se credat...",
                "witness": "PL130 cols 47-48, Isidori Mercatoris Decretales, S. Clementis Epistola IV (physical p. 53)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL130/text/bbd51eed-91f2-422d-a730-8b2faabf1278-054.txt",
                "physical": 54,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pl130_cols_49_50",
                    "clementis_epistola_iv_conclusio",
                    "contextual_continuation"
                ],
                "snippet": "49 S. CLEMENTIS PAPÆ EPISTOLA IV. 50 ... de veritatis cognitione et ignorantiae fuga",
                "witness": "PL130 cols 49-50, S. Clementis Epistola IV (physical p. 54)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL130/text/c6e96b78-37a2-463f-8bc0-463978f8b0bf-649.txt",
                "physical": 649,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_in_isidorum_mercatorem_col_1175"
                ],
                "snippet": "Ignorantia intolerabilis quae se putat aliquid scire, 0.",
                "witness": "PL130 col. 1175, Index in Isidorum Mercatorem (physical p. 649)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_166_pl130_isidorus"):
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
        meta["resolution_campaign"] = "sprint_166_pl130_isidorus"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_166_pl130_isidorus")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PL130.")
