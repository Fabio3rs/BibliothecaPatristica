"""
PG034 S. Macarius Aegyptius / Alexandrinus (Index Analyticus in Homilias et Opuscula) Locator
Sprint 143 of Onomastic Catalog Recovery

Resolves the residual source_only item from PG034:
1. Editorial Context:
   - PG034 contains:
     a) Vitae, Acta, Apophthegmata, Epistolae of SS. Macarii (cols 1-444 / physical pp. 1-224),
        indexed by 'INDEX RERUM MEMORABILIUM' (physical pp. 653-655), where numbers cite Migne columns.
     b) Homiliae L and Opuscula Ascetica (cols 449-968 / physical pp. 227-494), edited from Johann Georg
        Pritius (Leipzig, 1698/1714), indexed by 'INDEX ANALYTICUS IN HOMILIAS ET OPUSCULA ASCETICA'
        (physical pp. 655-673 / cols 1283-1306).
   - The opening rubric of Index II explicitly states (col. 1283 / physical p. 655):
     'Revocatur Lector ad numeros crassiores textui Latino intermixtos.'
     The numbers in Index II refer to the bold page numbers of the Pritius edition embedded in the Latin text.
   - For example:
     - 'Lex naturæ non est posita, sed arbitrio et electioni, 166.' -> col. 835 / physical p. 428 (embedded number 166)
     - 'Artifices in mundo fuere diversimodi, 143.' -> col. 786 / physical pp. 403-404 (embedded number 143)
     - 'Archelaus in Judæam properat, 176; quædam ejus res gestæ, ibid., et seqq.' -> col. 857 / physical p. 439
       (embedded number 176 in Liber de Oratione) and physical p. 440 (embedded number 177 / et seqq.).
   - Parallels:
     - Migne column 176 falls on physical p. 98 (Vitae Macariorum).
     - The index entry itself sits on physical p. 656 (cols 1285-1286).
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
    "4c80e4eac051f10702d08098cbf54cf6b564a2e1413ced652c656339f1504b11": {
        "label": "Archelaus in Judæam properat",
        "ref_raw": "176;",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG034/text/726823e0-e7fc-4932-bcfd-78436400ebc7-439.txt",
                "physical": 439,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "pritius_numerus_crassior_176",
                    "liber_de_oratione",
                    "migne_cols_857_858",
                    "textui_latino_intermixtus"
                ],
                "snippet": "sur se ipsum lactare et 176 decipere, ac, dum ipsuza Spiritus fructibus privare... videntur homines ejusmodi a quinque fatuis in nullo differre virginibus...",
                "witness": "PG034 cols 857-858, S. Macarii Liber de Oratione (Pritius ed. p. 176)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG034/text/726823e0-e7fc-4932-bcfd-78436400ebc7-440.txt",
                "physical": 440,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "pritius_numerus_crassior_177_et_seqq",
                    "liber_de_oratione_cont",
                    "migne_cols_859_860"
                ],
                "snippet": "VIII. Tanta humanitate et bonitate (o divinæ in nos misericordiæ viscera!) demisit se ad consulendum hominibus Dominus... corrumpi contingat : sed per pietatem potius...",
                "witness": "PG034 cols 859-860, S. Macarii Liber de Oratione (Pritius ed. p. 177 / et seqq.)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG034/text/1a8153f9-7b46-4695-9ebe-1b58f31e4a0a-098.txt",
                "physical": 98,
                "score": 85.0,
                "rank": 3,
                "reasons": [
                    "parallel_migne_column_176",
                    "vitae_macariorum_historical_context"
                ],
                "snippet": "475 SS. MACARIORUM AEGYPTII ET ALEXANDRINI VITAE. 476 ... post Didymum Rhodon, qui etiam, ut testis est ipse Philippus, scholam transtulit in illam ipsam civitatem Siden...",
                "witness": "PG034 col. 176 (Migne column parallel)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG034/text/b9be3dcc-66d9-4b57-8661-12917ac7dbcd-656.txt",
                "physical": 656,
                "score": 80.0,
                "rank": 4,
                "reasons": [
                    "index_primary_source",
                    "index_analyticus_migne_cols_1285_1286"
                ],
                "snippet": "Archelaus in Judæam properat, 176; quædam ejus res gestæ, ibid., et seqq.",
                "witness": "PG034 cols 1285-1286, Index Analyticus (physical p. 656)"
            }
        ]
    },
    "db118ae11199058195c275692196f2e5fe0b7c25f8ce9307e39693101125000b": {
        "label": "quædam ejus res gestæ",
        "ref_raw": "ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG034/text/726823e0-e7fc-4932-bcfd-78436400ebc7-439.txt",
                "physical": 439,
                "score": 100.0,
                "rank": 1,
                "reasons": [
                    "inherited_ibid_pritius_176",
                    "liber_de_oratione",
                    "migne_cols_857_858"
                ],
                "snippet": "sur se ipsum lactare et 176 decipere, ac, dum ipsuza Spiritus fructibus privare...",
                "witness": "PG034 cols 857-858, S. Macarii Liber de Oratione (Pritius ed. p. 176)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG034/text/726823e0-e7fc-4932-bcfd-78436400ebc7-440.txt",
                "physical": 440,
                "score": 90.0,
                "rank": 2,
                "reasons": [
                    "inherited_et_seqq_pritius_177",
                    "liber_de_oratione_cont",
                    "migne_cols_859_860"
                ],
                "snippet": "VIII. Tanta humanitate et bonitate... corrumpi contingat : sed per pietatem potius...",
                "witness": "PG034 cols 859-860, S. Macarii Liber de Oratione (Pritius ed. p. 177)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG034/text/b9be3dcc-66d9-4b57-8661-12917ac7dbcd-656.txt",
                "physical": 656,
                "score": 80.0,
                "rank": 3,
                "reasons": [
                    "index_primary_source",
                    "index_analyticus_migne_cols_1285_1286"
                ],
                "snippet": "Archelaus in Judæam properat, 176; quædam ejus res gestæ, ibid., et seqq.",
                "witness": "PG034 cols 1285-1286, Index Analyticus (physical p. 656)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_143_pg034_macarius"):
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
        meta["resolution_campaign"] = "sprint_143_pg034_macarius"

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
        sync_catalog_summary(db_path, SUMMARY_PATH, "sprint_143_pg034_macarius")

    return fixed_count, new_candidates_count


if __name__ == "__main__":
    import sys
    dry_run = "--apply" not in sys.argv
    fixed, cand = apply_destinations(dry_run=dry_run)
    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[{mode}] Fixed {fixed} items, {cand} candidate entries in PG034.")
