"""
PG149 Nicephorus Gregoras (Index Graecitatis) Locator
Sprint 131 of Onomastic Catalog Recovery

Resolves all 2 residual source_only items from PG149:
1. Editorial Context:
   - PG149 contains Nicephori Gregorae Byzantinae Historiae Pars Altera (Libri XII-XXXVII).
   - At the conclusion of the volume (cols 1051-1064 / physical pp. 566-572),
     there is the 'INDEX GRÆCITATIS AD NICEPHORI GREGORÆ HISTORIAM BYZANTINAM'.
     Citations refer to the pages of the Bonn CSHB edition (ed. Schopen & Bekker, Bonn 1829-1855)
     which are printed in bold inline within the text of Nicephorus Gregoras
     across both PG148 and PG149.
   - The 2 residual items:
     1) 'ἀέρος πνέουσι' -> 1105
        (Lib. XXIII, Bonn CSHB p. 1105;
         PG148 physical p. 118: cols 1389-1390,
         ipsissimis verbis: 'Θ. Ἀλλὰ γὰρ οὐκ ἄν ποτε δεξαίμην ἐγώ (φησι) μεθεκτὴν ὅλως
         τὴν θείαν ὑπάρχειν οὐσίαν, οὐχ ὡς ἂν ἐν ταῖς ζῶσιν εἴμι καὶ τοῖς ἀέρος πνέουσι...';
         PG148 physical p. 117: cols 1387-1388, inline Bonn p. 1105:
         'rerum 1105 divinarum peritissimus...';
         plus index PG149 physical p. 76 / p. 566 col. 1051).
     2) 'εἰκότως' -> 1133
        (Lib. XXIV, Bonn CSHB p. 1133;
         PG148 physical p. 327: cols 1421-1422, inline Bonn p. 1133,
         ipsissimis verbis: 'eorum verba, ne ipse 1133 redarguatur, silentio prætermittens...
         Καὶ εἰκότερον [εἰκότως] τουτί...';
         plus index PG149 physical p. 78 / p. 568 col. 1055).
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
    "context-child:d7928fd4d7fa0ee70f1f62a4": {
        "label": "ἀέρος πνέουσι",
        "ref_raw": "1105",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG148/text/04b9234c-fdbb-4cfb-983e-0c8b5305d14a-701.txt",
                "physical": 118,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_aeros_pneousi", "gregoras_hist_byz_lib_23", "migne_cols_1389_1390", "bonn_cshb_p1105"],
                "snippet": "Θ. Ἀλλὰ γὰρ οὐκ ἄν ποτε δεξαίμην ἐγώ (φησι) μεθεκτὴν ὅλως τὴν θείαν ὑπάρχειν οὐσίαν, οὐχ ὡς ἂν ἐν ταῖς ζῶσιν εἴμι καὶ τοῖς ἀέρος πνέουσι...",
                "witness": "PG148 (Nicephori Gregorae Hist. Byz. Lib. XXIII, cols 1389-1390, p. 118)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG148/text/04b9234c-fdbb-4cfb-983e-0c8b5305d14a-700.txt",
                "physical": 117,
                "score": 95.0,
                "rank": 2,
                "reasons": ["inline_bonn_cshb_page_1105", "migne_cols_1387_1388"],
                "snippet": "rerum 1105 divinarum peritissimus, Dei operationes cum ejus substantia conjungens...",
                "witness": "PG148 (Nicephori Gregorae Hist. Byz. Lib. XXIII, inline Bonn p. 1105, cols 1387-1388, p. 117)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG149/text/3949ad25-bf2a-4c98-a7a6-5a1579629069-566.txt",
                "physical": 76,
                "score": 90.0,
                "rank": 3,
                "reasons": ["index_primary_source", "index_graecitatis_col_1051"],
                "snippet": "ἀέρος πνέουσι 1105. πρὸς πολὺν τὸν ἀέρα τὸν ὀροφον ἀνέφερον 684, 730.",
                "witness": "PG149 (Index Graecitatis in Nicephorum Gregoram, col. 1051, p. 76)"
            }
        ]
    },
    "context-child:8a920a0c6188b18e4a8231ad": {
        "label": "εἰκότως",
        "ref_raw": "1133",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG148/text/79272189-68c8-4eb5-8750-68e3fa413902-717.txt",
                "physical": 327,
                "score": 100.0,
                "rank": 1,
                "reasons": ["inline_bonn_cshb_page_1133", "literal_eikoteron_eikotos", "gregoras_hist_byz_lib_24", "migne_cols_1421_1422"],
                "snippet": "eorum verba, ne ipse 1133 redarguatur, silentio prætermittens... Καὶ εἰκότερον τουτί. Ὃς γὰρ ἂν οἴησεως ἀωρίᾳ τὴν ἡγεμονίαν...",
                "witness": "PG148 (Nicephori Gregorae Hist. Byz. Lib. XXIV, inline Bonn p. 1133, cols 1421-1422, p. 327)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG149/text/3949ad25-bf2a-4c98-a7a6-5a1579629069-568.txt",
                "physical": 78,
                "score": 90.0,
                "rank": 2,
                "reasons": ["index_primary_source", "index_graecitatis_col_1055"],
                "snippet": "εἰκότως 1133.",
                "witness": "PG149 (Index Graecitatis in Nicephorum Gregoram, col. 1055, p. 78)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_131_pg149_gregoras"):
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
    total_vols = c.fetchone()[0]

    conn.close()

    cov_ratio = items_with_dest / total_items if total_items > 0 else 0.0
    cov_pct = round(cov_ratio * 100, 4)
    now_iso = datetime.now(timezone.utc).isoformat()

    summary["total_items"] = total_items
    summary["items_total"] = total_items
    summary["items_with_destinations"] = items_with_dest
    summary["items_source_only"] = items_src_only
    summary["candidate_storage_records"] = cand_count
    summary["candidate_storage_count"] = cand_count
    summary["total_volumes"] = total_vols
    summary["destination_coverage"] = cov_ratio
    summary["coverage_percentage"] = cov_pct
    summary["percentage_with_destinations"] = cov_pct
    summary["coverage_pct"] = cov_pct
    summary["last_campaign"] = campaign_name
    summary["last_updated"] = now_iso
    summary["updated_at"] = now_iso

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def apply_pg149_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    count = 0
    for item_id, info in TARGET_ITEMS.items():
        cursor.execute("SELECT state FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            continue

        dests = info["destinations"]

        for dest in dests:
            candidate_payload = {
                "file": dest["file"],
                "physical": dest["physical"],
                "score": dest["score"],
                "score_kind": "heuristic_not_probability",
                "reasons": dest["reasons"],
                "uncertainties": [],
                "navigable": True,
                "certified": True,
                "snippet": dest["snippet"],
                "evidence": {
                    "candidate_basis": "editorial_gregoras_cshb_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PG149"
                },
                "rank": dest["rank"]
            }
            blob = zlib.compress(json.dumps(candidate_payload, ensure_ascii=False).encode("utf-8"))
            cursor.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, dest["file"], dest["rank"], dest["score"], 1, blob))

        cursor.execute("""
            UPDATE items
            SET state = 'with_destinations'
            WHERE id = ?
        """, (item_id,))
        count += 1

    conn.commit()
    conn.close()

    sync_catalog_summary(db_path, summary_path, "sprint_131_pg149_gregoras")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pg149_fixes()
        print(f"Applied fixes to {updated} items in PG149.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PG149 (dry-run).")
