"""
PG066 Synesius Cyrenensis (Index Analyticus in Synesium) Locator
Sprint 144 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG066:
1. Editorial Context:
   - PG066 contains the works of Synesius Cyrenensis (cols 1021-1756), reprinted from the standard
     Paris edition of Denis Pétau (Dionysius Petavius, 1612/1631/1633).
   - In the 'INDEX ANALYTICUS IN SYNESIUM' (cols 1735-1756 / physical pp. 896-906), the opening rubric states:
     'Revocatur Lector ad numeros crassiori charactere textui insertos.'
   - The bold numbers embedded in the Latin text correspond to the page numbers of the Petavius edition.
   - At col. 1737 (physical p. 897 / da314370-9aaa-43c1-a5df-dfa21fc745b4-897.txt):
     'Astrolabii laus, 307, 308 et seqq.'
   - The residual item:
     id: group:b1f3b616cbf6962aea1d6cc3a26c78de561e18e2ad1eb98fc856e0ead73c703d
     'Astrolabii laus' -> '307, 308 et seqq'
   - Refers directly to Synesius's famous treatise 'Ad Paeonium de Dono Astrolabii' (Sermo de Dono Astrolabii /
     Πρὸς Παιόνιον ὑπὲρ τοῦ δώρου ἀστρολαβίου λόγος, cols 1577-1586 / physical pp. 817-822):
     - Petavius page 307 sits on col. 1577 / physical p. 817:
       '307 Cum non ita pridem te philosophiæ vicem graviter ferre...'
     - Petavius page 308 sits on col. 1579 / physical p. 818:
       '308 propter me potissimum de sorte philosophiæ...'
     - Petavius page 309 et seqq. sit on col. 1581 / physical p. 819 and p. 820:
       developing the praise of astronomy, mathematics and the construction of the astrolabe.
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
    "group:b1f3b616cbf6962aea1d6cc3a26c78de561e18e2ad1eb98fc856e0ead73c703d": {
        "label": "Astrolabii laus",
        "ref_raw": "307, 308 et seqq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG066/text/3b350a8e-69b2-45b7-87ad-48c17001d442-817.txt",
                "physical": 817,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "petavius_numerus_crassior_307",
                    "de_dono_astrolabii_sermo",
                    "migne_cols_1577_1578",
                    "textui_latino_insertus"
                ],
                "snippet": "SYNESII CYRENÆI AD PÆONIUM SERMO DE DONO ASTROLABII ... 307 Cum non ita pridem te philosophiæ vicem graviter ferre, et conqueri audirem...",
                "witness": "PG066 cols 1577-1578, Sermo de Dono Astrolabii (Petavius ed. p. 307)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG066/text/3b350a8e-69b2-45b7-87ad-48c17001d442-818.txt",
                "physical": 818,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "petavius_numerus_crassior_308",
                    "de_dono_astrolabii_cont",
                    "migne_cols_1579_1580"
                ],
                "snippet": "308 propter me potissimum de sorte philosophiæ succensens conquestus es... nobis igitur, qui ab hominibus contemnimur, amplectandus locus est...",
                "witness": "PG066 cols 1579-1580, Sermo de Dono Astrolabii (Petavius ed. p. 308)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG066/text/3b350a8e-69b2-45b7-87ad-48c17001d442-819.txt",
                "physical": 819,
                "score": 85.0,
                "rank": 3,
                "reasons": [
                    "petavius_numerus_crassior_309_et_seqq",
                    "astronomiae_et_astrolabii_laus",
                    "migne_cols_1581_1582"
                ],
                "snippet": "1381 DE DONO ASTROLABII SERMO. 1382 ... Ita societas olim philosophia et civilis administratio fuere, et ubi sic convenere, tantas res præstitērunt...",
                "witness": "PG066 cols 1581-1582, Sermo de Dono Astrolabii (Petavius ed. p. 309 / et seqq.)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG066/text/da314370-9aaa-43c1-a5df-dfa21fc745b4-897.txt",
                "physical": 897,
                "score": 80.0,
                "rank": 4,
                "reasons": [
                    "index_primary_source",
                    "index_analyticus_migne_cols_1737_1738"
                ],
                "snippet": "Astrolabii laus, 307, 308 et seqq.",
                "witness": "PG066 cols 1737-1738, Index Analyticus (physical p. 897)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_144_pg066_synesius"):
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
        meta["resolution_campaign"] = "sprint_144_pg066_synesius"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_144_pg066_synesius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG066.")
