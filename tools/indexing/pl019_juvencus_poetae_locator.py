"""
PL019 Juvencus et Poetae Christiani Locator
Sprint 69 of Onomastic Catalog Recovery

Resolves all 9 residual source_only items from PL019:
1. Editorial Context:
   - PL019 contains early Christian Latin poets, principally:
     * Cols 57-378: C. Vettius Aquilinus Juvencus (Evangelicae Historiae Libri IV)
     * Cols 387-432: Publilius Optatianus Porphyrius (Panegyricus Constantino Augusto)
     * Cols 433-752: Sedulius, Severus Sanctus Endelechius, etc.
     * Cols 987-1048 (physical pp. 500-526): INDEX ONOMASTICUS / RERUM
   - Citations in the INDEX ONOMASTICUS with suffix 'seq' refer to Migne columns:
     * 'Stella Magorum dux, 103 seq' -> col 103, physical pp. 57, 58 (Juvencus lib. I)
     * 'Felicis impugnator, 112 seq' -> col 112, physical p. 62 (Juvencus lib. I)
     * 'Adoperationum fraudulenta haeresis, 112 seq' -> col 112, physical p. 62 (Juvencus lib. I)
     * 'De quinque millibus hominum... 246 seq' -> col 246, physical p. 129 (Juvencus lib. III)
     * 'Alpha, et v Deus, 268 seq' -> col 268, physical p. 140 (Juvencus lib. III)
     * 'Parabola de decem virginibus, 335 seq' -> col 335, physical p. 174 (Juvencus lib. IV)
     * 'Tacitus in uitiis celebrandis adhibita, 335 seq' -> col 335, physical p. 174 (Juvencus lib. IV)
     * 'Compunctiones frequentes in Juvenco, 339 seq' -> col 339, physical p. 176 (Juvencus lib. IV)
     * 'Jacob, et Esau, 421 seq' -> col 421, physical p. 217 (Optatianus Porphyrius)
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

PL019_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Stella Magorum dux -> 103 seq (pp. 57, 58)
    'group:acf7387824fe6c8cfeebce009b35d18054cb4718fb1f3d4024b35174dfe9ef6c': (
        'group:acf7387824fe6c8cfeebce009b35d18054cb4718fb1f3d4024b35174dfe9ef6c',
        [
            ('PL019', 57, 'PL019 cols 103-104 (Juvenci Evangelicae Historiae lib. I: Stella Magorum dux)', 'pl019_juvencus_col103_p57'),
            ('PL019', 58, 'PL019 cols 103-104 (Juvenci Evangelicae Historiae lib. I: Stella Magorum continuatio)', 'pl019_juvencus_col103_p58')
        ]
    ),

    # 2. Felicis impugnator -> 112 seq (p. 62)
    'group:0cd00af69d0e293018038f0d3d18a22155387bfed63b077321f23ecdb2642ac9': (
        'group:0cd00af69d0e293018038f0d3d18a22155387bfed63b077321f23ecdb2642ac9',
        [
            ('PL019', 62, 'PL019 cols 111-112 (Juvenci Evangelicae Historiae lib. I: Felicis impugnator)', 'pl019_juvencus_col112_p62')
        ]
    ),

    # 3. Adoperationum fraudulenta haeresis' -> 112 seq (p. 62)
    'group:74a7ae6c85c0f84ec660a61fca16c5caa35771963226913d52367d9a96ac2265': (
        'group:74a7ae6c85c0f84ec660a61fca16c5caa35771963226913d52367d9a96ac2265',
        [
            ('PL019', 62, 'PL019 cols 111-112 (Juvenci Evangelicae Historiae lib. I: Adoperationum fraudulenta haeresis)', 'pl019_juvencus_col112_adopt_p62')
        ]
    ),

    # 4. De quinque millibus hominum... -> 246 seq (p. 129)
    'group:269cd54f8c6d3eb2118a9cb9512a08c966053e4a0fb0a41e2436c2c603524c75': (
        'group:269cd54f8c6d3eb2118a9cb9512a08c966053e4a0fb0a41e2436c2c603524c75',
        [
            ('PL019', 129, 'PL019 cols 245-246 (Juvenci Evangelicae Historiae lib. III: De quinque millibus hominum saturatis)', 'pl019_juvencus_col246_p129')
        ]
    ),

    # 5. Alpha, et v Deus -> 268 seq (p. 140)
    'group:90f94a6f0e38c80db56617d258fb6ffacc3e556988b9c93668d46e505159513b': (
        'group:90f94a6f0e38c80db56617d258fb6ffacc3e556988b9c93668d46e505159513b',
        [
            ('PL019', 140, 'PL019 cols 267-268 (Juvenci Evangelicae Historiae lib. III: Alpha et Omega Deus)', 'pl019_juvencus_col268_p140')
        ]
    ),

    # 6. Parabola de decem virginibus -> 335 seq (p. 174)
    'group:f736cc874595826888c6f0172bade1d4a16c3b859639f25a0d1669688a9ad814': (
        'group:f736cc874595826888c6f0172bade1d4a16c3b859639f25a0d1669688a9ad814',
        [
            ('PL019', 174, 'PL019 cols 335-336 (Juvenci Evangelicae Historiae lib. IV: Parabola de decem virginibus)', 'pl019_juvencus_col335_p174')
        ]
    ),

    # 7. Tacitus in uitiis celebrandis adhibita -> 335 seq (p. 174)
    'group:1132d3bd896eba8d50560204295574ee6e34cb79418e91a255dff07cb191aa03': (
        'group:1132d3bd896eba8d50560204295574ee6e34cb79418e91a255dff07cb191aa03',
        [
            ('PL019', 174, 'PL019 cols 335-336 (Juvenci Evangelicae Historiae lib. IV: Tacitus in vitiis celebrandis)', 'pl019_juvencus_col335_tacitus_p174')
        ]
    ),

    # 8. Compunctiones frequentes in Juvenco -> 339 seq (p. 176)
    'group:5c03032ac0f58b4b62e4f3c753787327e243d6a62b314a4ced1d85529fbf82c6': (
        'group:5c03032ac0f58b4b62e4f3c753787327e243d6a62b314a4ced1d85529fbf82c6',
        [
            ('PL019', 176, 'PL019 cols 339-340 (Juvenci Evangelicae Historiae lib. IV: Compunctiones in Juvenco)', 'pl019_juvencus_col339_p176')
        ]
    ),

    # 9. Jacob, et Esau -> 421 seq (p. 217)
    'group:020b9938939a1999ead2a09858d036237e4f70f8b0b8898f029595c27309bd9b': (
        'group:020b9938939a1999ead2a09858d036237e4f70f8b0b8898f029595c27309bd9b',
        [
            ('PL019', 217, 'PL019 cols 421-422 (Publilii Optatiani Porphyrii Panegyricus: Jacob et Esau)', 'pl019_optatianus_col421_p217')
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


def resolve_pl019_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL019 item to physical destination files."""
    if item_id not in PL019_ITEMS_RAW:
        return None
    group_id, dests = PL019_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint69(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 69 recovery for PL019 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL019' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL019',
        'sprint': 69,
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
        res = resolve_pl019_item(item_id)
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
    parser = argparse.ArgumentParser(description="Sprint 69: PL019 Juvencus et Poetae Locator")
    parser.add_argument('--apply', action='store_true', help="Apply changes to the database")
    parser.add_argument('--db', default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument('--summary', default=SUMMARY_PATH, help="Path to catalog_summary.json")
    args = parser.parse_args()

    res = execute_sprint69(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(res, indent=2, ensure_ascii=False))
