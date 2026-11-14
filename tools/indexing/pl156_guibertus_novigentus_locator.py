"""
PL156 Guibertus de Novigento (Guibert de Nogent) Locator
Sprint 70 of Onomastic Catalog Recovery

Resolves all 8 residual source_only items from PL156:
1. Editorial Context:
   - PL156 contains the complete works of Venerabilis Guibertus Abbas S. Mariae de Novigento (Guibert de Nogent):
     * Cols 19-338: Moralia in Genesim Libri X
     * Cols 339-478: Tropologiae in Osee, Amos et Jeremiam
     * Cols 537-578: De Laude S. Mariae & De Virginitate
     * Cols 679-838: Gesta Dei per Francos Libri VIII
     * Cols 837-962: De Vita Sua (Monodiae) Libri III
     * Cols 961-1078: Appendices ad Libros de Vita Sua (Vita S. Salabergae, De Religione Praemonstratensium)
     * Cols 1237-1278 (physical pp. 623-638): INDEX RERUM ET VERBORUM
   - Notice at the beginning of INDEX RERUM ET VERBORUM (p. 623):
     'Revocatur Lector ad numeros crassiores textui insertos.'
     The index refers to the page numbers of the princeps edition of Dom Luc d'Achery (Paris, 1651),
     which Migne inserted in bold brackets throughout the volume:
     * 'Cœlum et terra moraliter' & 'Terra et cœlum quid moraliter' (9 et seq) -> Moralia in Genesim lib. I, p. 10
     * 'Alexis imperator multa facessit negotia Francis' (387 et seq) -> Gesta Dei per Francos lib. III, p. 363
     * 'Pyrrus urbis Antiochenae turres tres tradidit Christianis' (406 et seqq) -> Gesta Dei per Francos lib. V, pp. 379, 380
     * 'Actus suos percurrit ac deplet' (431 et seq) -> De Vita Sua lib. I, p. 528
     * 'Præmonstratensis ordinis primordia' (545 et seqq) -> Append. ad De Vita Sua, p. 498
     * 'Exasit celeberrimun monasterium' (546;) -> Append. ad De Vita Sua, p. 500
     * 'Fundator Præmonstratensis monasterii' (550 et seq) -> Append. ad De Vita Sua, p. 502
"""

import argparse
import glob
import json
import os
import sqlite3
import zlib
from typing import Dict, List, Optional, Tuple

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'
SUMMARY_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/catalog_summary.json'

PL156_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Alexis imperator multa facessit negotia Francis -> 387 et seq (p. 363)
    'group:0d72925214b16ede430d5e6cebd94824e902ad0355bd913ca24893de61c59855': (
        'group:0d72925214b16ede430d5e6cebd94824e902ad0355bd913ca24893de61c59855',
        [
            ('PL156', 363, "PL156 cols 717-718 (Gesta Dei per Francos lib. III, d'Achery p. 387: Alexis imperator negotia Francis)", 'pl156_guibertus_alexis_p363')
        ]
    ),

    # 2. Pyrrus urbis Antiochenae turres tres tradidit Christianis -> 406 et seqq (pp. 379, 380)
    'group:f7253651515d207548e9bef1c94fc05734652f06d0419cea68b3b6fd8bbbd56c': (
        'group:f7253651515d207548e9bef1c94fc05734652f06d0419cea68b3b6fd8bbbd56c',
        [
            ('PL156', 379, "PL156 cols 749-750 (Gesta Dei per Francos lib. V, d'Achery p. 406: Pyrrus turres Antiochenae initium)", 'pl156_guibertus_pyrrus_p379'),
            ('PL156', 380, "PL156 cols 751-752 (Gesta Dei per Francos lib. V, d'Achery p. 406: Pyrrus turres Antiochenae continuatio)", 'pl156_guibertus_pyrrus_p380')
        ]
    ),

    # 3. Actus suos percurrit ac deplet -> 431 et seq (p. 528)
    'group:5b6ed119682f8427779acdb0ed91a849059eea72e38676616f18a26dbabacbbd': (
        'group:5b6ed119682f8427779acdb0ed91a849059eea72e38676616f18a26dbabacbbd',
        [
            ('PL156', 528, "PL156 cols 1017-1018 (De Vita Sua / Monodiae, d'Achery p. 431: Actus suos percurrit ac deplet)", 'pl156_guibertus_actus_p528')
        ]
    ),

    # 4. Præmonstratensis ordinis primordia -> 545 et seqq (p. 498)
    'group:a23ac4f28528e8d7cb035a6cfc88a57bde21ff812af3ae4eb50ece36b79a36cd': (
        'group:a23ac4f28528e8d7cb035a6cfc88a57bde21ff812af3ae4eb50ece36b79a36cd',
        [
            ('PL156', 498, "PL156 (Append. ad lib. III de Vita Sua, d'Achery p. 545: Praemonstratensis ordinis primordia)", 'pl156_guibertus_praemonstrat_p498')
        ]
    ),

    # 5. Exasit celeberrimun monasterium -> 546; (p. 500)
    'cb0f564639dd28ff8d47945e910ca1759ddf04025151c844f82d4f85ba8af837': (
        'cb0f564639dd28ff8d47945e910ca1759ddf04025151c844f82d4f85ba8af837',
        [
            ('PL156', 500, "PL156 (Append. ad lib. III de Vita Sua, d'Achery p. 546: celeberrimum monasterium Praemonstratum)", 'pl156_guibertus_monasterium_p500')
        ]
    ),

    # 6. Fundator Præmonstratensis monasterii -> 550 et seq (p. 502)
    'group:c1286928552d05740fbc34ee242f79c55bd73f3bafa05ececb1d85ff1d5ead74': (
        'group:c1286928552d05740fbc34ee242f79c55bd73f3bafa05ececb1d85ff1d5ead74',
        [
            ('PL156', 502, "PL156 (Append. ad lib. III de Vita Sua, d'Achery p. 550: S. Norbertus fundator Praemonstratensis)", 'pl156_guibertus_norbertus_p502')
        ]
    ),

    # 7. Cœlum et terra moraliter -> 9 et seq (p. 10)
    'e8a56299f67196656461858e245bdb2338d372968c6be646d044df32f69d6463': (
        'e8a56299f67196656461858e245bdb2338d372968c6be646d044df32f69d6463',
        [
            ('PL156', 10, "PL156 cols 19-20 (Moralia in Genesim lib. I, d'Achery p. 9: Coelum et terra moraliter)", 'pl156_guibertus_coelum_p10')
        ]
    ),

    # 8. Terra et cœlum quid moraliter -> 9 et seq (p. 10)
    'group:d3540d8837438c9242156e6706c8e0fbf7886987793c381ed9f1c39ce5340aab': (
        'group:d3540d8837438c9242156e6706c8e0fbf7886987793c381ed9f1c39ce5340aab',
        [
            ('PL156', 10, "PL156 cols 19-20 (Moralia in Genesim lib. I, d'Achery p. 9: Terra et coelum quid moraliter)", 'pl156_guibertus_terra_p10')
        ]
    ),
}


def get_physical_file(volume: str, page_num: int) -> str:
    """Finds physical txt file matching volume and page number."""
    matches = glob.glob(f'teste/{volume}/text/*-{page_num:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{page_num}.txt')
    if matches:
        return matches[0]
    return f'teste/{volume}/text/{volume}-{page_num:03d}.txt'


def build_candidate_json(
    volume: str,
    page_num: int,
    file_path: str,
    col_desc: str,
    reason: str,
    rank: int = 1,
) -> bytes:
    """Builds candidate compressed JSON payload."""
    payload = {
        'target_volume': volume,
        'page_num': page_num,
        'file': file_path,
        'col_desc': col_desc,
        'locator_reason': reason,
        'rank': rank,
        'score': 10.0,
    }
    return zlib.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def resolve_pl156_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL156 item to physical destination files."""
    if item_id not in PL156_ITEMS_RAW:
        return None
    group_id, dests = PL156_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint70(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 70 recovery for PL156 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL156' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL156',
        'sprint': 70,
        'total_source_only_initial': total_source_only,
        'resolved_count': 0,
        'candidates_created': 0,
        'applied': apply,
        'items': [],
    }

    if not source_only_rows:
        conn.close()
        return report

    items_to_update = []
    for r in source_only_rows:
        item_id, group_id, label, ref_raw, source = r
        res = resolve_pl156_item(item_id)
        if res:
            _, resolved_dests = res
            items_to_update.append((item_id, label, ref_raw, resolved_dests))

    report['resolved_count'] = len(items_to_update)

    if not apply:
        conn.close()
        return report

    candidates_inserted = 0
    for item_id, label, ref_raw, dests in items_to_update:
        for rank, (vol, page, col_desc, reason, fpath) in enumerate(dests, 1):
            cand_blob = build_candidate_json(vol, page, fpath, col_desc, reason, rank)
            cur.execute("""
                INSERT OR REPLACE INTO candidate_storage
                (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (item_id, fpath, rank, 10.0, 1, cand_blob))
            candidates_inserted += 1

        cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (item_id,))

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'with_destinations'")
    total_with_dest = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM items WHERE state = 'source_only'")
    total_source_only_after = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    cand_count = cur.fetchone()[0]

    report['candidates_created'] = candidates_inserted
    report['total_with_destinations_now'] = total_with_dest
    report['total_source_only_now'] = total_source_only_after
    report['candidate_storage_records'] = cand_count

    conn.close()

    if os.path.exists(summary_path):
        with open(summary_path, 'r', encoding='utf-8') as f:
            summary = json.load(f)
        summary['items_with_destinations'] = total_with_dest
        summary['items_source_only'] = total_source_only_after
        summary['candidate_storage_records'] = cand_count
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Sprint 70: PL156 Guibert de Nogent Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint70(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
