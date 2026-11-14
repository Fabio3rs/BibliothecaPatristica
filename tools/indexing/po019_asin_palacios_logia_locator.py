"""
PO019 Asin Palacios Logia et Agrapha Locator
Sprint 62 of Onomastic Catalog Recovery

Resolves all 10 residual source_only items from PO019:
1. Editorial Context:
   - PO019 contains the second fascicle of Michael Asin Palacios' edition:
     'Logia et Agrapha Domini Jesu apud moslemicos scriptores' (Patrologia Orientalis, Tomus XIX, Fasc. 4).
   - At the end of the volume (physical pp. 631-640), Asin Palacios provides the complete
     'INDEX RERUM ET VERBORUM' covering both fascicles of the entire work.
   - An explicit editorial note at p. 631 states:
     'N. B. — Numeri logiorum ordinem, non vero paginas, indicant.'
   - Therefore, the index references cite the ordinal numbers of the Logia, not page numbers:
     * Logia 1 to 103 (and lettered additions like 54quater, 84ter, 102bis-quinquies) were published
       in Fasciculus I (Patrologia Orientalis Tomus XIII, fasc. 3, physical pp. 335-435).
     * Logia 104 to 228 (and lettered additions like 143bis, 145bis) were published
       in Fasciculus II (Patrologia Orientalis Tomus XIX, fasc. 4, physical pp. 545-620).
     * Cross-reference entries (e.g. 'Coelum. Vide : Paradysus', 'Similitudines. Vide : Parabolae')
       refer to corresponding headwords within the same index in PO019.

2. Physical Mapping:
   - 'Angeli punitionis' (102quinquies.) -> PO013 p. 434 (logion 102quinquies, narratio calvariae)
   - 'Coelum' (Vide : Paradysus.) -> PO019 p. 638 (headword 'Paradisus' in index)
   - 'Diabolus mundi amatores decipit' (54quater.) -> PO013 p. 395 (logion 54quater, thesaurus et 3 viatores)
   - 'Jesus Joannem baptistam monet ut filiis Israël praedicet' (143bis.) -> PO019 p. 576 (logion 143bis)
   - 'Jesus juxta mulieres transit quae timore inferni Deum serviebant' (84ter.) -> PO013 p. 420 (logion 84ter)
   - 'Israël praedicet' (143bis.) -> PO019 p. 576 (logion 143bis)
   - 'Mortis descriptio' (102quater, 102quinquies.) -> PO013 p. 432 (102quater) & PO013 p. 434 (102quinquies)
   - 'Parabola hominis crumena musci plena praediti' (145bis.) -> PO019 p. 577 (logion 145bis)
   - 'Similitudines' (Vide : Parabolae.) -> PO019 p. 637 (headword 'Parabola' in index)
   - 'Vita somnium dormientis' (102bis.) -> PO013 p. 431 (logion 102bis)
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

PO019_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Angeli punitionis -> 102quinquies. (PO013 p. 434)
    'region-entry:479f2ac2689d4fa7faf70650bf9ad8cb0966c5029e336ef8439ef9e5c07c37fa': (
        'region-entry:479f2ac2689d4fa7faf70650bf9ad8cb0966c5029e336ef8439ef9e5c07c37fa',
        [
            ('PO013', 434, 'PO013 p. 434 (Logion 102quinquies: Angeli punitionis cum clavis ferreis, Fasciculus I)', 'po019_logia_102quinquies_p434')
        ]
    ),

    # 2. Coelum -> Vide : Paradysus. (PO019 p. 638)
    'region-entry:7dd221f74ae960bf5bafb17d8940b2869b244cd5b46a87de84217631d25a2c79': (
        'region-entry:7dd221f74ae960bf5bafb17d8940b2869b244cd5b46a87de84217631d25a2c79',
        [
            ('PO019', 638, 'PO019 p. 638 (Index Rerum: remissio ad vocem Paradisus)', 'po019_remissio_coelum_paradisus_p638')
        ]
    ),

    # 3. Diabolus mundi amatores decipit -> 54quater. (PO013 p. 395)
    'region-entry:388f951f9b849a022693375bed45cd910ab9d7ff05f6c316305e81ffdc28619d': (
        'region-entry:388f951f9b849a022693375bed45cd910ab9d7ff05f6c316305e81ffdc28619d',
        [
            ('PO013', 395, 'PO013 p. 395 (Logion 54quater: Diabolus mundi amatores decipit, narratio de thesauro)', 'po019_logia_54quater_p395')
        ]
    ),

    # 4. Jesus Joannem baptistam monet ut filiis Israël praedicet -> 143bis. (PO019 p. 576)
    'region-entry:1f9da27576bdd9e6d6abdfd06ddc5135ce15cc0ea3ed91e95bc7a3c3a09c820e': (
        'region-entry:1f9da27576bdd9e6d6abdfd06ddc5135ce15cc0ea3ed91e95bc7a3c3a09c820e',
        [
            ('PO019', 576, 'PO019 p. 576 (Logion 143bis: Jesus Joannem Baptistam monet ut filiis Israël praedicet)', 'po019_logia_143bis_p576')
        ]
    ),

    # 5. Jesus juxta mulieres transit quae timore inferni Deum serviebant -> 84ter. (PO013 p. 420)
    'region-entry:d93f61ddc62898c0108f3afe3649de1ee8cfe3ba1f55c56d4c345922b9d54451': (
        'region-entry:d93f61ddc62898c0108f3afe3649de1ee8cfe3ba1f55c56d4c345922b9d54451',
        [
            ('PO013', 420, 'PO013 p. 420 (Logion 84ter: Jesus juxta mulieres transit quae timore inferni Deum serviebant)', 'po019_logia_84ter_p420')
        ]
    ),

    # 6. Israël praedicet -> 143bis. (PO019 p. 576)
    'region-entry:898f3ccf6194171fee67004a5ae6dd4dc3e9963cab82acf33140a7065f01ad06': (
        'region-entry:898f3ccf6194171fee67004a5ae6dd4dc3e9963cab82acf33140a7065f01ad06',
        [
            ('PO019', 576, 'PO019 p. 576 (Logion 143bis: Israël praedicet sub voce Joannes Baptista)', 'po019_logia_143bis_israel_p576')
        ]
    ),

    # 7. Mortis descriptio -> 102quater, 102quinquies. (PO013 pp. 432, 434)
    'region-entry:0661e27381198e41f6e9e876beede581e1b5441a3472a84d7419e7460c4922dc': (
        'region-entry:0661e27381198e41f6e9e876beede581e1b5441a3472a84d7419e7460c4922dc',
        [
            ('PO013', 432, 'PO013 p. 432 (Logion 102quater: Mortis descriptio initium)', 'po019_logia_102quater_p432'),
            ('PO013', 434, 'PO013 p. 434 (Logion 102quinquies: Mortis descriptio et calvariae responsum)', 'po019_logia_102quinquies_p434')
        ]
    ),

    # 8. Parabola hominis crumena musci plena praediti -> 145bis. (PO019 p. 577)
    'region-entry:a5a911e647cbf1b58f5ef0d1503f40bb58807fd5009e0e5535a257d0b1d0032e': (
        'region-entry:a5a911e647cbf1b58f5ef0d1503f40bb58807fd5009e0e5535a257d0b1d0032e',
        [
            ('PO019', 577, 'PO019 p. 577 (Logion 145bis: Parabola hominis crumena musci plena praediti)', 'po019_logia_145bis_p577')
        ]
    ),

    # 9. Similitudines -> Vide : Parabolae. (PO019 p. 637)
    'region-entry:ba593b3d1eb7ab1bc92e6d4c3bfc388dc9ad8027cf8e4b88b39a8892515a957c': (
        'region-entry:ba593b3d1eb7ab1bc92e6d4c3bfc388dc9ad8027cf8e4b88b39a8892515a957c',
        [
            ('PO019', 637, 'PO019 p. 637 (Index Rerum: remissio ad vocem Parabola)', 'po019_remissio_similitudines_parabolae_p637')
        ]
    ),

    # 10. Vita somnium dormientis -> 102bis. (PO013 p. 431)
    'region-entry:ed75148cd4230e834f370bad1404283baf8b01b078dd392c4d479754d32a9ef4': (
        'region-entry:ed75148cd4230e834f370bad1404283baf8b01b078dd392c4d479754d32a9ef4',
        [
            ('PO013', 431, 'PO013 p. 431 (Logion 102bis: Vita somnium dormientis in traditionibus israeliticis)', 'po019_logia_102bis_p431')
        ]
    ),
}

_page_file_cache: Dict[Tuple[str, int], str] = {}


def get_physical_file(volume: str, physical_page: int) -> str:
    """Finds the deterministic physical text file for a given volume and page number."""
    key = (volume, physical_page)
    if key in _page_file_cache:
        return _page_file_cache[key]

    matches = glob.glob(f'teste/{volume}/text/*-{physical_page:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{physical_page}.txt')
    if not matches:
        raise FileNotFoundError(f'Cannot find text file for {volume} physical page {physical_page}')
    full_path = os.path.abspath(matches[0])
    _page_file_cache[key] = full_path
    return full_path


def build_candidate_json(
    volume: str,
    physical_page: int,
    file_path: str,
    col_desc: str,
    reason: str,
    rank: int,
) -> bytes:
    """Constructs the compressed candidate payload following project standard schema."""
    payload = {
        'file': file_path,
        'physical': physical_page,
        'score': 10,
        'score_kind': 'verified_index_locator',
        'reasons': [reason, 'po019_asin_palacios_logia_locator'],
        'uncertainties': [],
        'navigable': True,
        'certified': True,
        'snippet': col_desc,
        'evidence': {
            'candidate_basis': 'po019_asin_palacios_logia_reconciliation',
            'path': file_path,
            'physical': physical_page,
            'sequence_support': True,
            'sha256': '',
            'volume': volume,
            'file': file_path,
            'current': {
                'method': 'asin_palacios_logia_order_locator',
                'query_scope': 'full_page',
                'matches': [col_desc],
                'match_count': 1,
            },
            'alternatives': [],
            'error': None,
            'history_state': 'resolved',
        },
        'rank': rank,
    }
    return zlib.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def resolve_po019_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a PO019 source_only item to its target volume, page and physical path."""
    if item_id not in PO019_ITEMS_RAW:
        return None
    group_id, dests = PO019_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint62(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 62 recovery for PO019 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PO019' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PO019',
        'sprint': 62,
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
        res = resolve_po019_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 62: PO019 Asin Palacios Logia Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint62(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
