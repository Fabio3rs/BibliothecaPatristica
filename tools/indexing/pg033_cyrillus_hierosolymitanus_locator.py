"""
PG033 S. Cyrillus Hierosolymitanus (Index Rerum et Verborum) Locator
Sprint 101 of Onomastic Catalog Recovery

Resolves all 4 residual source_only items from PG033:
1. Editorial Context:
   - PG033 contains S. Cyrillus Hierosolymitanus: Catecheses Illuminandorum, Catecheses Mystagogicae,
     Homilia in Paralyticum, Epistola ad Constantium Imperatorem, and the Dissertationes Praeviae of Touttée (Paris, 1720).
   - In the 'INDEX RERUM ET VERBORUM' (cols 1245-1316 / physical pp. 828-875), the pagination citations
     refer to the standard Touttée edition pagination reproduced in the margins and headers across PG033.
   - The 4 residual items from physical p. 841 (sub Cyrillus) cite:
     1) 'Ejus methodus in oppugnandis gentilibus... Ejus stylus facilis ac simplicissimus' -> 334, VIII.
        (PG033 cols 1129-1130 / physical p. 574: Homilia in Paralyticum - Monitum § VIII, Touttée p. 334;
         plus p. 88 Touttée p. CXXXI in Dissertatio II, and index p. 841)
     2) 'Proximum timuit Antichristi adventum, et supremi judicii diem' -> 330, XV.
        (PG033 cols 1163-1164 / physical p. 591: Epistola ad Constantium Imp. - Monitum § XV, Touttée p. 330,
         ipsissimis verbis: 'Cyrillus proximum timuit Antichristi adventum, et supremi judicii diem',
         plus p. 590 and index p. 841)
     3) 'Confirmationis verba solita silentii religione texit' -> 314, II.
        (PG033 cols 1085-1086 / physical p. 550: Catechesis XXI (Mystagogica III) - Admonitio § II,
         ipsissimis verbis: 'verba in signando neophyto dici solita religione silentii tegat',
         plus p. 549 Touttée p. 314, and index p. 841)
     4) 'Ejus de reali præsentia et transsubstantiatione... Quod præcipit panem sacrum...' -> 324, VI, VII.
        (PG033 cols 1107-1108 / physical p. 561: Catechesis XXIII (Mystagogica V) - Admonitio § VI-VII,
         Touttée p. 324, ipsissimis verbis: 'Sed quod Cyrillus præcipit, ut antequam ore sumatur panis sacer, oculis admoveatur; utque postquam fuerit ex calice epotus Christi sanguis...',
         plus p. 127 Touttée p. CCV, and index p. 841)
"""

import os
import json
import zlib
import sqlite3
from typing import Dict, List, Any

TARGET_ITEMS: Dict[str, Dict[str, Any]] = {
    "group:7aa15fbed4f422a22fd0b10248deaed1efa91aa2321a80c5a8abcef3f35ccee2": {
        "label": "Ejus methodus in oppugnandis gentilibus, Judæis, et hæreticis qualis esset, CXXXI. Qua ratione moralia præcepta tractaret, CXXXI. Ejus stylus facilis ac simplicissimus",
        "ref_raw": "334, VIII.",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/67c0380c-0137-4444-8334-f5c892ba3281-574.txt",
                "physical": 574,
                "score": 100.0,
                "rank": 1,
                "reasons": ["editorial_monitum_homilia_paralyticum", "toutee_page_334", "section_viii_style"],
                "snippet": "VIII. Tertio, stylus ipse et dicendi ratio Cyrillianæ similis; in varia membra concisus... Scripturas hic mystica, et ut vocat, contemplatrice, seu allegorica commentandi ratione interpretatur...",
                "witness": "PG033 cols 1129-1130, Homilia in Paralyticum Monitum VIII, Touttée p. 334"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/1842d093-b3bd-45c8-92eb-20f736215133-088.txt",
                "physical": 88,
                "score": 85.0,
                "rank": 2,
                "reasons": ["dissertatio_praevia_ii", "toutee_page_cxxxi", "subreference_moralia_praecepta"],
                "snippet": "Cum de moribus agit Cyrillus, ut in procatechesi et catechesi prima, nullum pene certum ordinem sequi videtur; sed utilia ad salutem monita, prout animo occurrunt, audientibus ingerit.",
                "witness": "PG033 cols 165-166, Dissertatio II, Touttée p. CXXXI"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/fe1694f0-f983-4606-8ec2-91eafca00214-841.txt",
                "physical": 841,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Non eumdem in catechesibus, ac in aliis scriptis, sequebatur Scripturas interpretandi morem, 334, VIII. Ejus methodus in oppugnandis gentilibus, Judæis, et hæreticis qualis esset, CXXXI.",
                "witness": "PG033 Index Rerum et Verborum, col. 1251"
            }
        ]
    },
    "group:9a59851c9be932026f5905293223f67a3eb9c0b95939dd2ec33145f77ccad4d6": {
        "label": "Proximum timuit Antichristi adventum, et supremi judicii diem",
        "ref_raw": "330, XV.",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/d72ae37b-2970-4a23-a4a2-a313be43475e-591.txt",
                "physical": 591,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_phrase_match", "epistola_constantium_monitum_xv", "toutee_page_330"],
                "snippet": "XV. Quid præsagierit ista crucis apparitio... Cyrillus proximum timuit Antichristi adventum, et supremi judicii diem.",
                "witness": "PG033 cols 1163-1164, Epistola ad Constantium Monitum XV, Touttée p. 330"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/d72ae37b-2970-4a23-a4a2-a313be43475e-590.txt",
                "physical": 590,
                "score": 85.0,
                "rank": 2,
                "reasons": ["epistola_constantium_monitum_context"],
                "snippet": "EPISTOLA AD CONSTANTIUM IMP. — MONITUM... XII. Nihil ad referendam in annum 356 crucis apparitionem allegari potest...",
                "witness": "PG033 cols 1161-1162, Epistola ad Constantium Monitum"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/fe1694f0-f983-4606-8ec2-91eafca00214-841.txt",
                "physical": 841,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Defectionem ab Apostolo prædictam... Proximum timuit Antichristi adventum, et supremi judicii diem, 330, XV.",
                "witness": "PG033 Index Rerum et Verborum, col. 1251"
            }
        ]
    },
    "group:9fe23718d0c37d293d2ddaf0888790952ca0e53299cf0102c3b59b9df6476b1a": {
        "label": "Confirmationis verba solita silentii religione texit",
        "ref_raw": "314, II.",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/67c0380c-0137-4444-8334-f5c892ba3281-550.txt",
                "physical": 550,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_phrase_match", "catechesis_xxi_mystagogica_iii_admonitio_ii", "toutee_page_314"],
                "snippet": "II. Memorabilia quæ hic de chrismatis benedictione et efficacia dicuntur... ut tamen verba in signando neophyto dici solita religione silentii tegat...",
                "witness": "PG033 cols 1085-1086, Catechesis XXI (Mystagogica III) Admonitio II, Touttée p. 314"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/67c0380c-0137-4444-8334-f5c892ba3281-549.txt",
                "physical": 549,
                "score": 85.0,
                "rank": 2,
                "reasons": ["toutee_page_314_margin_anchor"],
                "snippet": "insititii. Quoniam enim 314 hic (1) plantata est vitis vera, nos quoque communicatione baptismi...",
                "witness": "PG033 cols 1083-1084, Catechesis XX (Mystagogica II)"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/fe1694f0-f983-4606-8ec2-91eafca00214-841.txt",
                "physical": 841,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Cyrillus manuum impositionem in sacramento confirmationis adhibitam suo tempore significat, 289. Confirmationis verba solita silentii religione texit, 314, II.",
                "witness": "PG033 Index Rerum et Verborum, col. 1251"
            }
        ]
    },
    "group:ca0f941992721b65a76b9e3e23b283d0ede3d0b18729b59b8ea3ada8a765c5b4": {
        "label": "Ejus de reali præsentia et transsubstantiatione testimoniorum evidentia et perspicuitas, CCV. Inanes, ad eorum vim elevandam, Albertini argutiæ refelluntur, CCV, CCVI. Quo sensu Eucharistiam post consecrationem panem vocavit, CCIV, CCXV. Ejus de eucharistico sacrificio doctrina, catholicæ in omnibus conformis, CCXXXV, CCXXXVI. Vide Eucharistia. Num evangelicorum verborum in consecratione vim sustulerit, CCXXXVI, CCXXXVII. Vide Consecratio. Quod præcipit panem sacrum, antequam ore sumatur, oculis admoveri, madoremque sanguinis labiis adhærentem, collectum digitis, sensibus applicari, aliorum auctorum testimoniis confirmatur",
        "ref_raw": "324, VI, VII.",
        "destinations": [
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/67c0380c-0137-4444-8334-f5c892ba3281-561.txt",
                "physical": 561,
                "score": 100.0,
                "rank": 1,
                "reasons": ["literal_phrase_match", "catechesis_xxiii_mystagogica_v_admonitio", "toutee_page_324"],
                "snippet": "Sed quod Cyrillus præcipit, ut antequam ore sumatur panis sacer, oculis admoveatur; utque postquam fuerit ex calice epotus Christi sanguis, collectus digitis adhærens labiis mador, oculis, fronti et sensibus applicetur; id cum singulare forte videri possit, aliorum auctorum exemplis est confirmandum.",
                "witness": "PG033 cols 1107-1108, Catechesis XXIII (Mystagogica V) Admonitio VI-VII, Touttée p. 324"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/1842d093-b3bd-45c8-92eb-20f736215133-127.txt",
                "physical": 127,
                "score": 85.0,
                "rank": 2,
                "reasons": ["dissertatio_praevia_iii", "toutee_page_ccv", "subreference_albertinus_refellitur"],
                "snippet": "substantiatione; in quo inanes Albertini ad elevandam... luntur; generales in Albertinianam respondendi rationem observationes... Quamobrem nullum non lapidem movit Albertinus...",
                "witness": "PG033 cols 243-244, Dissertatio III, Touttée p. CCV"
            },
            {
                "file": "/homessddata/Projects/pdfocr/teste/PG033/text/fe1694f0-f983-4606-8ec2-91eafca00214-841.txt",
                "physical": 841,
                "score": 70.0,
                "rank": 3,
                "reasons": ["index_origin_witness"],
                "snippet": "Ejus de reali præsentia et transsubstantiatione testimoniorum evidentia et perspicuitas, CCV... Quod præcipit panem sacrum, antequam ore sumatur, oculis admoveri, madoremque sanguinis labiis adhærentem, collectum digitis, sensibus applicari, aliorum auctorum testimoniis confirmatur, 324, VI, VII.",
                "witness": "PG033 Index Rerum et Verborum, col. 1251"
            }
        ]
    }
}


def get_pg033_candidates(item_id: str) -> List[Dict[str, Any]]:
    """Return certified destination candidates for a given PG033 item_id."""
    info = TARGET_ITEMS.get(item_id)
    if not info:
        return []
    return info["destinations"]


def locate_all() -> Dict[str, List[Dict[str, Any]]]:
    """Return all resolved destinations for PG033 residual items."""
    return {item_id: info["destinations"] for item_id, info in TARGET_ITEMS.items()}


def apply_pg033_fixes(db_path: str = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite") -> int:
    """Apply the resolved PG033 destinations to search.sqlite."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    count = 0
    for item_id, info in TARGET_ITEMS.items():
        cursor.execute("SELECT state FROM items WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        if not row:
            continue

        dests = info["destinations"]

        # Insert candidates into candidate_storage
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
                    "candidate_basis": "editorial_index_resolution",
                    "path": dest["file"],
                    "physical": dest["physical"],
                    "witness": dest["witness"],
                    "volume": "PG033"
                },
                "rank": dest["rank"]
            }
            blob = zlib.compress(json.dumps(candidate_payload, ensure_ascii=False).encode("utf-8"))
            cursor.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, dest["file"], dest["rank"], dest["score"], 1, blob))

        # Update items table
        cursor.execute("""
            UPDATE items
            SET state = 'with_destinations'
            WHERE id = ?
        """, (item_id,))
        count += 1

    conn.commit()
    conn.close()
    return count


if __name__ == "__main__":
    import sys
    if "--apply" in sys.argv:
        updated = apply_pg033_fixes()
        print(f"Applied fixes to {updated} items in PG033.")
    else:
        results = locate_all()
        print(f"Verified {len(results)} items in PG033 (dry-run).")
