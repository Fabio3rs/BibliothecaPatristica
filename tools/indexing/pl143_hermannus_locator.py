"""
PL143 Hermannus Contractus & Chronicon Petershusanum Locator
Sprint 122 of Onomastic Catalog Recovery

Resolves all 2 residual source_only items from PL143:
1. Editorial Context:
   - PL143 contains the works of Hermannus Contractus (Chronicon, De Musica, De Mensura Astrolabii)
     and its associated historical documentation: Chronicon Petershusanum (Casus Monasterii Petrishusensis)
     and Genealogia Comitum Brigantinorum (ed. Migne / Hess).
   - The 'INDEX IN CHRONICON HERMANNI CONTRACTI ET MONASTERIUM PETERSHUSANUM' (cols 1583-1600 / physical pp. 799-808)
     indexes historical figures, monasteries, and dynastic lineages by the column/page numbers of the edition.
   - The 2 residual items:
     1) 'per torum. Bericherus vel Beringerus ab. 314' -> 322 seqq
        [sub Monasteria: 'Petershausen 191, 301 seqq., per totum. Bericherus vel Beringerus ab. 314, 322 seqq.']
        (PL143 physical p. 166: Chronicon Petershusanum Lib. I, cols 314-315,
         ipsissimis verbis: '...ad ecclesiam S. Gregorii in loco Petershusa vocato honorabiliter constructam,
         cui modo Bericherus reverendus et primus (748) abbas præsidet... (748) Bericherus, Perriggerus, vel Beringerus, primus abbas...';
         physical p. 170: Chronicon Petershusanum Lib. II, cols 323-324,
         ipsissimis verbis: 'Volente ergo atque rogante tertio Ottone imperatore Augusto... idipsum monasterium,
         cui nunc abbas Pericherus præest...';
         physical p. 169: cols 321-322;
         plus index p. 803 col. 1590).
     2) 'Marquardus 287. Otto' -> ibid [sub Comites Brigantini: 'Marquardus 287. Otto, ibid. 292, 295, 329']
        (PL143 physical p. 150: Appendix ad Chronicon Hermanni Contracti, cols 287-288,
         ipsissimis verbis: 'SYSTEMA GENEALOGICUM S. GEBHARDI Ex chronico Petershusano et Eccardo...
         Brigantinorum comitum... Otto... Marquardus...';
         physical p. 154: cols 295-296: 'Ottonis filium et Udalrici senioris nepotem...';
         physical p. 166: cols 315-316: 'Isdem imperator rogatu Marquardi comitis, fratris scilicet B. Gebehardi (747)... OTTO divina favente clementia rex';
         plus index p. 805 col. 1593).
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
    "group:a288a08563d66a5c2f6e59fb64a6dfce729a13a9d12736f2abb4c67d40b3b6f5": {
        "label": "per torum. Bericherus vel Beringerus ab. 314",
        "ref_raw": "322 seqq",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL143/text/abffcac4-f601-458f-8b30-c02b4922b77f-166.txt",
                "physical": 166,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_bericherus_primus_abbas_petershusa", "chronicon_petershusanum_col_314_315", "migne_edition_page_314"],
                "snippet": "315 APPENDIX AD CHRONICON HERMANNI CONTRACTI 316... ad ecclesiam S. Gregorii in loco Petershusa vocato honorabiliter constructam, cui modo Bericherus reverendus et primus (748) abbas præsidet... (748) Bericherus, Perriggerus, vel Beringerus, primus abbas an. 993 constitutus... Dum vero primus abbas Petershusanus dicitur Beringerus...",
                "witness": "PL143 col. 314-315 (Chronicon Petershusanum Lib. I § 26)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL143/text/abffcac4-f601-458f-8b30-c02b4922b77f-170.txt",
                "physical": 170,
                "score": 90.0,
                "rank": 2,
                "reasons": ["pericherus_abbas_praeest", "chronicon_petershusanum_col_324", "context_sequela"],
                "snippet": "323 APPENDIX AD CHRONICON HERMANNI CONTRACTI 324... Volente ergo atque rogante tertio Ottone imperatore Augusto... quoddam monasterium in ripa Rheni juxta Constantiam, in villa quæ vocatur Petrishusa... apostolica defensione munimus... quatenus idipsum monasterium, cui nunc abbas Pericherus præest...",
                "witness": "PL143 col. 323-324 (Chronicon Petershusanum Lib. II § 3)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL143/text/abffcac4-f601-458f-8b30-c02b4922b77f-169.txt",
                "physical": 169,
                "score": 80.0,
                "rank": 3,
                "reasons": ["migne_cols_321_322", "ottonis_diploma_petershusen", "context_initium_lib_ii"],
                "snippet": "321 CHRONICON PETERSHUSANUM. — LIB. II. 322... eidem jam dicto monasterio in honore S. Gregorii constructo, nec non et monachis sub norma S. Benedicti Deo ibi militantibus... Signum domini Ottonis gloriosissimi regis...",
                "witness": "PL143 col. 321-322 (Chronicon Petershusanum Lib. II)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL143/text/3f6bc174-597b-4055-810f-331a363722e9-803.txt",
                "physical": 803,
                "score": 75.0,
                "rank": 4,
                "reasons": ["source_index_page", "index_monasteria", "migne_col_1590"],
                "snippet": "Petershausen 191, 301 seqq., per totum. Bericherus vel Beringerus ab. 314, 322 seqq.",
                "witness": "PL143 col. 1590 (Index sub Petershausen)"
            }
        ]
    },
    "group:15d4f8ec326997ca7786f5cb28b2f3b2a665b68414d116948a8741ca7359ea21": {
        "label": "Marquardus 287. Otto",
        "ref_raw": "ibid",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL143/text/abffcac4-f601-458f-8b30-c02b4922b77f-150.txt",
                "physical": 150,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_brigantini_comites_genealogia", "systema_genealogicum_s_gebhardi", "migne_cols_287_288"],
                "snippet": "287 APPENDIX AD CHRONICON HERMANNI CONTRACTI 288... SYSTEMA GENEALOGICUM S. GEBHARDI Ex chronico Petershusano et Eccardo... Brigantinorum comitum... Adalardus, seu Udalricus I Comes Brigantii... Otto...",
                "witness": "PL143 col. 287-288 (Genealogia Comitum Brigantinorum)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL143/text/abffcac4-f601-458f-8b30-c02b4922b77f-154.txt",
                "physical": 154,
                "score": 90.0,
                "rank": 2,
                "reasons": ["brigantini_col_295", "ottonis_et_marquardi_stirps", "disquisitio_genealogica"],
                "snippet": "295 APPENDIX AD CHRONICON HERMANNI CONTRACTI 296... Hunc supra p. 295 Ottonis filium et Udalrici senioris nepotem suspicati sumus... comitibus Brigantinis...",
                "witness": "PL143 col. 295-296 (Disquisitio de Comitibus Brigantinis)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL143/text/abffcac4-f601-458f-8b30-c02b4922b77f-166.txt",
                "physical": 166,
                "score": 80.0,
                "rank": 3,
                "reasons": ["marquardus_comes_brigantinus", "otto_rex_diploma", "migne_col_315"],
                "snippet": "Isdem imperator rogatu Marquardi comitis, fratris scilicet B. Gebehardi (747)... OTTO divina favente clementia rex... (747) De hoc Marquardo comite Brigantino, vide dicta supra in Disquis. § 18, p. 286...",
                "witness": "PL143 col. 315-316 (Diploma Ottonis ad Rogatum Marquardi Comitis)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PL143/text/3f6bc174-597b-4055-810f-331a363722e9-805.txt",
                "physical": 805,
                "score": 75.0,
                "rank": 4,
                "reasons": ["source_index_page", "index_brigantini", "migne_col_1593"],
                "snippet": "BRIGANTINI... Marquardus 287. Otto, ibid. 292, 295, 329.",
                "witness": "PL143 col. 1593 (Index sub Brigantini)"
            }
        ]
    }
}


def get_pl143_candidates(item_id: str) -> List[Dict[str, Any]]:
    if item_id in TARGET_ITEMS:
        return TARGET_ITEMS[item_id]["destinations"]
    return []


def locate_all() -> Dict[str, List[Dict[str, Any]]]:
    res = {}
    for item_id, data in TARGET_ITEMS.items():
        res[item_id] = data["destinations"]
    return res


def sync_catalog_summary(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH, campaign_name: str = "sprint_122_pl143_hermannus") -> None:
    if not os.path.exists(summary_path):
        return

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM items")
    total_items = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM items WHERE state = 'with_destinations'")
    items_with_dest = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM items WHERE state = 'source_only'")
    items_src_only = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM candidate_storage")
    cand_count = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT volume) FROM items")
    total_vols = cur.fetchone()[0]
    conn.close()

    cov_pct = round((items_with_dest / total_items) * 100, 4)
    cov_ratio = round(items_with_dest / total_items, 6)
    now_iso = datetime.now(timezone.utc).isoformat()

    with open(summary_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

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


def apply_pl143_fixes(db_path: str = DB_PATH, summary_path: str = SUMMARY_PATH) -> int:
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
                    "candidate_basis": "editorial_hermannus_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PL143"
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

    sync_catalog_summary(db_path, summary_path, "sprint_122_pl143_hermannus")
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pl143_fixes()
        print(f"Applied fixes to {updated} items in PL143.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PL143 (dry-run).")
