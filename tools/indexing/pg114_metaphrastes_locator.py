"""
PG114 Symeon Metaphrastes / Leo Allatius (Index Auctorum in Diatribam) Locator
Sprint 151 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG114:
1. Editorial Context:
   - PG114 contains the works of Symeon Metaphrastes, preceded by Leo Allatius's famous treatise
     'De Symeonum scriptis diatriba' (Paris, 1661, 4°).
   - In the 'INDEX AUCTORUM' to the Diatriba (cols 147-152 / physical pp. 79-81):
     The editorial rubric on physical p. 79 explicitly states:
     'Numeri ad eos te revocant quos in textu inter uncos posuimus.'
     The numbers in the index refer to the bracketed section numbers of the Allatius treatise.
   - At col. 150 (physical p. 80 / a299e027-72ba-48f4-a6be-9b346db10378-080.txt):
     'Joannes Damascenus, de Symeone Stylita Montis Mirabilis, 17, 18.'
   - The residual item:
     id: region-entry:a2963ccee2129fc0ffca3906911424825ff80098bd145f1ef2eee0ade7d741c3
     'Joannes Damascenus, de Symeone Stylita Montis Mirabilis' -> '17, 18'
   - Refers to sections [17] and [18] of the complete Paris 1661 edition of Allatius's Diatriba,
     which treated Symeon Stylita Junior (Montis Mirabilis) before transitioning at section [24]
     (on physical p. 15 / d6f23682-85a2-43e0-8559-77c536080868-015.txt) directly to Symeon Metaphrastes:
     '[24] Sed jam me vocat ad se, cujus vel solius gratia hæc nostra Diatriba instituta est Symeon Metaphrastes...'
   - Witnesses:
     - Physical p. 15: Start of Diatriba text in PG114 with bracketed section markers [24], [25] etc.
     - Physical p. 79: Index opening with the editorial warning 'Numeri ad eos te revocant quos in textu inter uncos posuimus.'
     - Physical p. 80: The index entry itself.
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
    "region-entry:a2963ccee2129fc0ffca3906911424825ff80098bd145f1ef2eee0ade7d741c3": {
        "label": "Joannes Damascenus, de Symeone Stylita Montis Mirabilis",
        "ref_raw": "17, 18",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG114/text/d6f23682-85a2-43e0-8559-77c536080868-015.txt",
                "physical": 15,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "allatius_diatriba_bracketed_sections",
                    "notitia_scriptorum_symeonis_metaphrastae",
                    "paris_1661_edition_context"
                ],
                "snippet": "NOTITIA SCRIPTORUM SYMEONIS METAPHRASTÆ (Ex Allatio, De Symeonum scriptis. Paris 1661, 4°.) [24] Sed jam me vocat ad se, cujus vel solius gratia hæc nostra Diatriba instituta est Symeon Metaphrastes...",
                "witness": "PG114 cols 1-2 (physical p. 15), Allatii Diatriba in Metaphrastem"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG114/text/a299e027-72ba-48f4-a6be-9b346db10378-079.txt",
                "physical": 79,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "editorial_rubric_bracketed_numbers",
                    "indices_in_diatribam_migne_cols_147_148"
                ],
                "snippet": "147 INDICES IN DIATRIBAM. 148 ... Numeri ad eos te revocant quos in textu inter uncos posuimus.",
                "witness": "PG114 cols 147-148, Indices in Diatribam (physical p. 79)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG114/text/a299e027-72ba-48f4-a6be-9b346db10378-080.txt",
                "physical": 80,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_auctorum_migne_col_150"
                ],
                "snippet": "Joannes Damascenus, de Symeone Stylita Montis Mirabilis, 17, 18. In sanctam martyrem Barbaram, 115.",
                "witness": "PG114 col. 150, Index Auctorum in Diatribam (physical p. 80)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_151_pg114_metaphrastes"):
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
        meta["resolution_campaign"] = "sprint_151_pg114_metaphrastes"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_151_pg114_metaphrastes")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG114.")
