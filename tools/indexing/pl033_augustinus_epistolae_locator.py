"""
PL033 S. Augustini Epistolae Locator
Sprint 68 of Onomastic Catalog Recovery

Resolves all 9 residual source_only items from PL033:
1. Editorial Context:
   - PL033 contains the complete collection of Saint Augustine's Letters (S. Aurelii Augustini Hipponensis Episcopi Epistolae):
     * Cols 61-180: Epistolarum Classis I (Epistolae I-XXX)
     * Cols 179-494: Epistolarum Classis II (Epistolae XXXI-CXXIII)
     * Cols 493-1020: Epistolarum Classis III & IV (Epistolae CXXIV-CCXXXIX)
     * Cols 1019-1094: Epistolarum Classis IV continuatio (Epistolae CCXL-CCLXX)
     * Cols 1095-1174: INDEX RERUM ET VERBORUM IN EPISTOLAS S. AUGUSTINI
   - The index cites letters by their Roman numeral and Migne column:
     * 'Nebridii Augustino, V, 67; VI, ibid' -> Epist. V (col 67) & Epist. VI (col 68), physical p. 37
     * 'Festo LXXXIX, 1306' -> Epist. LXXXIX (col 306, misread '1306' in index), physical p. 158
     * 'Pascentii Augustino, CCXL, 1051' -> Epist. CCXL (col 1051), physical p. 529
     * 'Elpidio, CCXLII, 1052' -> Epist. CCXLII (col 1052), physical p. 529
     * 'Felici, CCLII, 1068' -> Epist. CCLII (col 1068), physical p. 537
     * 'Ediciæ, CCLVIII, 1077' -> Epist. CCLVIII (col 1077), physical p. 539
     * 'Maximæ, CCLXIV, 1084' -> Epist. CCLXIV (col 1084), physical p. 545
     * 'Seleucianæ, CCLXV, 1086' -> Epist. CCLXV (col 1086), physical p. 546
     * 'Anonymi Augustino, CCLXX, 1094' -> Epist. CCLXX (col 1094), physical p. 550
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

PL033_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Nebridii Augustino, V, 67; VI -> ibid (p. 37)
    'group:0877d3732e71caf44a2182884929be13a9345aac18bb19ec44115dae543fcde4': (
        'group:0877d3732e71caf44a2182884929be13a9345aac18bb19ec44115dae543fcde4',
        [
            ('PL033', 37, 'PL033 cols 67-68 (Epistola V & Epistola VI: Nebridii ad Augustinum)', 'pl033_augustinus_epist5_6_p37')
        ]
    ),

    # 2. Festo LXXXIX -> 1306 (col 306, p. 158)
    'c22408acebc97aa8e857f55a3b5ea55f052747bfe0c7d242559022d1fb3e14b7': (
        'c22408acebc97aa8e857f55a3b5ea55f052747bfe0c7d242559022d1fb3e14b7',
        [
            ('PL033', 158, 'PL033 col 306 (Epistola LXXXIX: Augustinus Festo de Donatistis)', 'pl033_augustinus_epist89_p158')
        ]
    ),

    # 3. Pascentii Augustino -> CCXL, 1051 (p. 529)
    '0bde723050d9651fd5ec1e0a9322190c91c312f24539082283ac657cc1126825': (
        '0bde723050d9651fd5ec1e0a9322190c91c312f24539082283ac657cc1126825',
        [
            ('PL033', 529, 'PL033 col 1051 (Epistola CCXL: Pascentius Augustino)', 'pl033_augustinus_epist240_p529')
        ]
    ),

    # 4. Elpidio -> CCXLII, 1052 (p. 529)
    '8f674fd414134878f7d2b7b81a83b4edcc8704b8eea72bf337e7541ddb618ca2': (
        '8f674fd414134878f7d2b7b81a83b4edcc8704b8eea72bf337e7541ddb618ca2',
        [
            ('PL033', 529, 'PL033 col 1052 (Epistola CCXLII: Augustinus Elpidio)', 'pl033_augustinus_epist242_p529')
        ]
    ),

    # 5. Felici -> CCLII, 1068 (p. 537)
    '3915e7b203d56aa8c21c8324c7dfe6ac0322463422e84fd201e36e7b05b0bdc0': (
        '3915e7b203d56aa8c21c8324c7dfe6ac0322463422e84fd201e36e7b05b0bdc0',
        [
            ('PL033', 537, 'PL033 col 1068 (Epistola CCLII: Augustinus Felici)', 'pl033_augustinus_epist252_p537')
        ]
    ),

    # 6. Ediciæ -> CCLVIII, 1077 (p. 539)
    'd4f0321b31db943f242940f60fa08fc0e95d6f3d73113704cde650febfd8ba4c': (
        'd4f0321b31db943f242940f60fa08fc0e95d6f3d73113704cde650febfd8ba4c',
        [
            ('PL033', 539, 'PL033 col 1077 (Epistola CCLVIII: Augustinus Ediciae)', 'pl033_augustinus_epist258_p539')
        ]
    ),

    # 7. Maximæ -> CCLXIV, 1084 (p. 545)
    '747ccdeea52ad8febd66bb4600ee7a9e0abeeafa69e04964dcf754f0fe961b5b': (
        '747ccdeea52ad8febd66bb4600ee7a9e0abeeafa69e04964dcf754f0fe961b5b',
        [
            ('PL033', 545, 'PL033 col 1084 (Epistola CCLXIV: Augustinus Maximae piae)', 'pl033_augustinus_epist264_p545')
        ]
    ),

    # 8. Seleucianæ -> CCLXV, 1086 (p. 546)
    '7e46b9d9ac4d5ffef789c8cba4758c39a17cd53922fe9d4163cd7d0a7c05163e': (
        '7e46b9d9ac4d5ffef789c8cba4758c39a17cd53922fe9d4163cd7d0a7c05163e',
        [
            ('PL033', 546, 'PL033 col 1086 (Epistola CCLXV: Augustinus Seleucianae de baptismo)', 'pl033_augustinus_epist265_p546')
        ]
    ),

    # 9. Anonymi Augustino -> CCLXX, 1094 (p. 550)
    '2c21425564876631d27e0ca3f04e2db7fd9c94ec740a16bc59c2b5dc0fbace78': (
        '2c21425564876631d27e0ca3f04e2db7fd9c94ec740a16bc59c2b5dc0fbace78',
        [
            ('PL033', 550, 'PL033 col 1094 (Epistola CCLXX: Augustino Anonymus)', 'pl033_augustinus_epist270_p550')
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


def resolve_pl033_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL033 item to physical destination files."""
    if item_id not in PL033_ITEMS_RAW:
        return None
    group_id, dests = PL033_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint68(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 68 recovery for PL033 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL033' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL033',
        'sprint': 68,
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
        res = resolve_pl033_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 68: PL033 S. Augustini Epistolae Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint68(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
