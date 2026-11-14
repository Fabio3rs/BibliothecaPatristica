"""
PL219 Indices Generales Auctorum Locator
Sprint 80 of Onomastic Catalog Recovery

Resolves all 6 residual source_only items from PL219:
1. Editorial Context:
   - PL219 contains the general onomastic and author index of the Patrologia Latina (INDEX AUCTORUM, INDEX RERUM, etc.)
   - Cross-references cite Roman tomo numerals and columns in the target volumes (PL001-PL217):
     1) 'Capitulare Vernense Pippini Franc. regis (755)' -> ibid., 1809 (typo in PL219 for PL096 cols 1505-1508 on physical pp. 745-746)
     2) 'Capitula Vernense Pippini regis Franc. regis (753), XCVII' -> 1515 (typo for PL096 col 1515 on physical p. 750 and p. 749)
     3) 'Epist. et privilegia Urbani II papæ (1187), CII' -> 1551 (typo in PL219 for Urbanus III papa, 1187, Tomus CCII col 1551 on physical p. 781: Epistola ad Episcopum Castellanum/Venetum)
     4) 'Anonym., incerti anni (sæc. VI)' -> XCVII, 1575 (typo for PL096 cols 1575-1576 on physical p. 780: Saeculi VIII Monumenta selecta)
     5) 'Pippinus, Francorum rex (742-778), XCII' -> 1501 (typo for PL096 cols 1501-1502 on physical p. 743: Pippini et Carolomanni Francorum regum Diplomata)
     6) 'Pontius, Sancti Rufi abbas (1124)' -> ibid., 1477 (referring to Tomus CLXIII col 1477 on physical p. 741: Pontii Abbatis S. Rufi Epistola ad Abbatem Calmosiacensem)
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

PL219_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Capitulare Vernense Pippini Franc. regis (755) -> ibid., 1809 (PL096 pp. 745, 746)
    'group:7c4f8741fcd51e978db2921d6bfd5b94a84baa4bfd7095e0f7706c38f5982daf': (
        'group:7c4f8741fcd51e978db2921d6bfd5b94a84baa4bfd7095e0f7706c38f5982daf',
        [
            ('PL096', 745, 'PL096 cols 1505-1506 (Pippini Francorum regis Capitulare Vernense / Vermeriense an. 755)', 'pl219_pippinus_capitulare_vernense_p745'),
            ('PL096', 746, 'PL096 cols 1507-1508 (Pippini Capitulare Vernense continuatio)', 'pl219_pippinus_capitulare_vernense_p746'),
        ]
    ),

    # 2. Capitula Vernense Pippini regis Franc. regis (753), XCVII -> 1515 (PL096 pp. 750, 749)
    '245dd4d5096bfad1b296bec0a8fb5c269adc4e03b4f001388d7f0fb58c1923a4': (
        '245dd4d5096bfad1b296bec0a8fb5c269adc4e03b4f001388d7f0fb58c1923a4',
        [
            ('PL096', 750, 'PL096 col 1515 (Capitularia Pippini regis Francorum: Conventus Attiniacensis & Capitula Vernensia/Compendiensia)', 'pl219_pippinus_capitula_vernense_p750'),
            ('PL096', 749, 'PL096 cols 1513-1514 (Capitularia Pippini continuatio)', 'pl219_pippinus_capitula_vernense_p749'),
        ]
    ),

    # 3. Epist. et privilegia Urbani II papæ (1187), CII -> 1551 (PL202 p. 781)
    '6ed0d1e5275606abdc5465cad74de56aeb62f5f94846459143b6d9db80ac917a': (
        '6ed0d1e5275606abdc5465cad74de56aeb62f5f94846459143b6d9db80ac917a',
        [
            ('PL202', 781, 'PL202 col 1551 (Urbanus III papa [1187]: Epistola ad Episcopum Castellanum/Venetum pro canonicis S. Salvatoris)', 'pl219_urbanus_iii_epistola_venetum_p781'),
        ]
    ),

    # 4. Anonym., incerti anni (sæc. VI) -> XCVII, 1575 (PL096 p. 780)
    '5bdf2e20d081f2a7cc1fde5fdee2609986055f3061b703eb391abe9da1e1072b': (
        '5bdf2e20d081f2a7cc1fde5fdee2609986055f3061b703eb391abe9da1e1072b',
        [
            ('PL096', 780, 'PL096 cols 1575-1576 (Saeculi VIII Monumenta selecta: Praeceptum Carlomanni regis)', 'pl219_anonymus_saec_viii_monumenta_p780'),
        ]
    ),

    # 5. Pippinus, Francorum rex (742-778), XCII -> 1501 (PL096 p. 743)
    '525a9c0465b0f9036cd834266bd281416b00f84c721f63c43a593344dbc7953e': (
        '525a9c0465b0f9036cd834266bd281416b00f84c721f63c43a593344dbc7953e',
        [
            ('PL096', 743, 'PL096 cols 1501-1502 (Pippini et Carolomanni Francorum regum Diplomata: Saeculi VIII Monumenta selecta)', 'pl219_pippinus_rex_diplomata_p743'),
        ]
    ),

    # 6. Pontius, Sancti Rufi abbas (1124) -> ibid., 1477 (PL163 p. 741)
    'group:38ba9e516a4bfb2bd08b5f48b724242934f8bd72dd8365efcddab05c8c6f7f3a': (
        'group:38ba9e516a4bfb2bd08b5f48b724242934f8bd72dd8365efcddab05c8c6f7f3a',
        [
            ('PL163', 741, 'PL163 cols 1477-1478 (Pontii Abbatis S. Rufi Epistola ad Abbatem Calmosiacensem)', 'pl219_pontius_sancti_rufi_epistola_p741'),
        ]
    ),
}


def get_physical_file(volume: str, page_num: int) -> str:
    """Finds physical txt file matching volume and page number."""
    matches = glob.glob(f'teste/{volume}/text/*-{page_num:03d}.txt') + glob.glob(f'teste/{volume}/text/*-{page_num}.txt')
    matches = list(dict.fromkeys(matches))
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


def resolve_pl219_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL219 item to physical destination files."""
    if item_id not in PL219_ITEMS_RAW:
        return None
    group_id, dests = PL219_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint80(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 80 recovery for PL219 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL219' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL219',
        'sprint': 80,
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
        res = resolve_pl219_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 80: PL219 Indices Generales Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    rep = execute_sprint80(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
