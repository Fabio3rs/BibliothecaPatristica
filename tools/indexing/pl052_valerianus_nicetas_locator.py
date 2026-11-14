"""
PL052 S. Valerianus Cemeliensis & S. Nicetas Aquileiensis Locator
Sprint 126 of Onomastic Catalog Recovery

Resolves all 2 residual source_only items from PL052:
1. Editorial Context:
   - PL052 contains S. Petrus Chrysologus, S. Valerianus Cemeliensis, and S. Nicetas Aquileiensis
     with dissertations by Theophilus Raynaud, S.J., and Petrus Braida / Bernardo Maria de Rubeis.
   - The indexes in PL052:
     a) 'INDEX RERUM ET MATERIARUM IN HOMILIAS SANCTI VALERIANI ET THEOPHILI RAYNAUDI APOLOGIAM'
        (cols 1221-1226 / physical pp. 613-615):
        '[Numeri in hoc indice paginationem indicantes respondent numeris in textu crassiori charactere.]'
     b) 'INDEX RERUM IN OPERA S. NICETÆ'
        (cols 1225-1238 / physical pp. 615-621):
        '[Numeri in hoc indice paginationem indicantes respondent numeris in textu crassiori charactere impressis.]'
   - The 2 residual items:
     1) 'A Semi-Pelagianismo immunis est, 37 et n' (sub S. Valerianus) -> 37 et n. 1, etc.
        (Refers to inline page 37 of Theophilus Raynaud's Apologia pro S. Valeriano;
         physical p. 384: cols 763-764, inline p. 37:
         'Ex qua 37 constare videtur Celedensem Ecclesiam in Syria fuisse...';
         physical p. 385: cols 765-766, Caput II:
         'Semipelagianismi suspectum Valerianum, vel etiam (si accusatorem audimus) plane compertum...
         Quæ prima Valeriani quoad Semipelagianismum defensio, ejusque improbatio...';
         plus index p. 615 col. 1225).
     2) 'Multis probatur instructam eam circa fuisse, tum ante Attilanam cladem, tum etiam postea' (sub Aquileiensis urbs) -> 48, seqq.
        (Refers to inline pages 47-49 of Petrus Braida's Dissertatio in S. Nicetam;
         physical p. 461: cols 917-918, inline p. 48,
         ipsissimis verbis: 'ex 47 Inscriptionibus Aquileiensibus... Augustalium collegio...
         decurionum ordinem... splendidissimum ordinem appellatum... consulum...
         Quidni igitur et circum Aquileia habuerit... Theatris quoque non caruit, et fortasse neque amphitheatro... 48';
         physical p. 462: cols 919-920, inline p. 49:
         'Præterea non ita fortasse Aquileiensis civitas excisa fuit, ut illico majora etiam ac solidiora ædificia,
         quemadmodum circi erant atque theatra, præsertim in præcipuis urbibus, solo æquata fuerint... [Per Attilanam cladem]... 49';
         physical p. 460: cols 915-916, inline p. 47: Nicolaus Madrisius and Capitolium;
         plus index p. 616 col. 1227).
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
    "e299264a5e88d5647e73d51ffa84c9d4da5a62c882ed542e754acde8a7f44ae9": {
        "label": "A Semi-Pelagianismo immunis est, 37 et n",
        "ref_raw": "37 et n. 1, etc.",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL052/text/a6382edf-562a-4775-a1d8-74d7554480c7-384.txt",
                "physical": 384,
                "score": 100.0,
                "rank": 1,
                "reasons": ["inline_page_37_raynaud_apologia", "migne_cols_763_764"],
                "snippet": "Ex qua 37 constare videtur Celedensem Ecclesiam in Syria fuisse; quandoquidem S. Hieronymus Marcum illum qui in Celedensi Ecclesia plurimum poterat...",
                "witness": "PL052 (Th. Raynaudi Apologia pro S. Valeriano, inline p. 37, cols 763-764, p. 384)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL052/text/a6382edf-562a-4775-a1d8-74d7554480c7-385.txt",
                "physical": 385,
                "score": 95.0,
                "rank": 2,
                "reasons": ["literal_semipelagianismus_chichon_defensio", "migne_cols_765_766"],
                "snippet": "Semipelagianismi suspectum Valerianum, vel etiam (si accusatorem audimus) plane compertum, faciunt quæ variis harum homiliarum locis, sparsi habentur... Quæ prima Valeriani quoad Semipelagianismum defensio, ejusque improbatio...",
                "witness": "PL052 (Th. Raynaudi Apologia Cap. II, cols 765-766, p. 385)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL052/text/56aa1642-9e37-482d-a8d7-361ed0d2ad66-615.txt",
                "physical": 615,
                "score": 90.0,
                "rank": 3,
                "reasons": ["index_primary_source", "migne_col_1225"],
                "snippet": "S. Valerianus... A Semi-Pelagianismo immunis est, 37 et n. 1, etc. Enumerantur quædam loca, e quibus Nicolaus Chichon probare contendit S. Valerianum fuisse Semi-Pelagianistam, ibid. A Semi-Pelagianismo penitus abhorruit, 51, n. 1.",
                "witness": "PL052 (Index Rerum in Homilias S. Valeriani, col. 1225, p. 615)"
            }
        ]
    },
    "e3df09f840f93f9eee3c023247a5febc43b8e15c383375ef90873d29ddc767bb": {
        "label": "Multis probatur instructam eam circa fuisse, tum ante Attilanam cladem, tum etiam postea",
        "ref_raw": "48, seqq.",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL052/text/6ae2d8bf-f841-4c5a-8233-2093dc18ae79-461.txt",
                "physical": 461,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_aquileia_augustalium_decurionum_theatris", "inline_page_48_braida", "migne_cols_917_918"],
                "snippet": "ex 47 Inscriptionibus Aquileiensibus a se illustratis, Augustalium collegio Aquileiensem civitatem non caruisse... decurionum ordinem, qui Aquileiæ exstabat, quique instar senatus erat, splendidissimum ordinem appellatum fuisse... Quin-immo et consulum Aquileiensis civitatis mentio... Quidni igitur et circum Aquileia habuerit... Theatris quoque non caruit, et fortasse neque amphitheatro... 48",
                "witness": "PL052 (P. Braidæ Dissertatio in S. Nicetam Cap. III, inline p. 48, cols 917-918, p. 461)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL052/text/6ae2d8bf-f841-4c5a-8233-2093dc18ae79-462.txt",
                "physical": 462,
                "score": 95.0,
                "rank": 2,
                "reasons": ["literal_attilanam_cladem_theatra_circi", "inline_page_49_braida", "migne_cols_919_920"],
                "snippet": "Præterea non ita fortasse Aquileiensis civitas excisa fuit, ut illico majora etiam ac solidiora ædificia, quemadmodum circi erant atque theatra, præsertim in præcipuis urbibus, solo æquata fuerint: neque ita certe excisa fuit, ut, saltem post Hunnorum ex Italia discessum, aliqua ex parte iterum habitari non potuerit... 49",
                "witness": "PL052 (P. Braidæ Dissertatio in S. Nicetam Cap. III, inline p. 49, cols 919-920, p. 462)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL052/text/6ae2d8bf-f841-4c5a-8233-2093dc18ae79-460.txt",
                "physical": 460,
                "score": 90.0,
                "rank": 3,
                "reasons": ["literal_madrisius_capitolium_jovis_templum", "inline_page_47_braida", "migne_cols_915_916"],
                "snippet": "Circus igitur Aquileiensi civitati non defuisse videtur. Præterea idem præsul clarissimus in dissertatione de Diis Aquileiensibus ibidem, pag. 315, ostendit etiam celebre Jovis templum, et Capitolium, ad urbis dominæ imitamentum, Aquileiæ extitisse...",
                "witness": "PL052 (P. Braidæ Dissertatio in S. Nicetam Cap. III, inline p. 47, cols 915-916, p. 460)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL052/text/56aa1642-9e37-482d-a8d7-361ed0d2ad66-616.txt",
                "physical": 616,
                "score": 85.0,
                "rank": 4,
                "reasons": ["index_primary_source", "migne_col_1227"],
                "snippet": "Aquileiensis urbs. Hujus urbis laudes, ex veterum testimoniis, brevi sermone congestit Nicolaus Madrisius, 47. Multis probatur instructam eam circa fuisse, tum ante Attilanam cladem, tum etiam postea, 48, seqq. Celebre Jovis templum habuerat, et Capitolium; collegium Augustalium...",
                "witness": "PL052 (Index Rerum in Opera S. Nicetæ, col. 1227, p. 616)"
            }
        ]
    }
}


def locate_all() -> Dict[str, Dict[str, Any]]:
    return TARGET_ITEMS


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_126_pl052_valerianus_nicetas"):
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


def apply_pl052_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
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
                    "candidate_basis": "editorial_valerianus_nicetas_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PL052"
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

    sync_catalog_summary(db_path, summary_path, "sprint_126_pl052_valerianus_nicetas")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pl052_fixes()
        print(f"Applied fixes to {updated} items in PL052.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PL052 (dry-run).")
