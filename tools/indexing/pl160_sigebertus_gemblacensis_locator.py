"""
PL160 Sigebertus Gemblacensis Locator
Sprint 85 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PL160:
1. Editorial Context:
   - PL160 contains Sigebertus Gemblacensis: Opera Omnia.
   - The 'Index Scriptorum Ecclesiasticorum qui a Sigeberto Gemblacensi memorantur' (physical p. 295)
     indexes the chapters of Sigebert's 'Liber de Scriptoribus Ecclesiasticis' (cols 547-588, physical pp. 274-294).
   - The 5 residual items:
     1) 'Boetius consul Romanus' -> 37 (Cap. XXXVII: Boetius vir consularis, cols 555-556, physical p. 278)
     2) 'Aldhelmus Malmesburiensis' -> 66 132 (Cap. LXVI: Adelmus abbas, col 561, p. 281; Cap. CXXXII: Adelmus episcopus, col 576, p. 288)
     3) 'Laurentius Mellifluus' -> 120 (Cap. CXX: Laurentius Melfius cognomine Mellifluus, col 573, physical pp. 286, 287)
     4) 'Bonifacius episcopus et poeta' -> 121 (Cap. CXXI: Bonifacius episcopus de virtutibus et vitiis metrico stylo, col 573, physical p. 287)
     5) 'Abbo Floriacensis abbas' -> cap. 139 (Cap. CXXXIX: Abbo abbas Floriacensis, col 578, physical p. 289)
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

PL160_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Boetius consul Romanus -> 37 (PL160 p. 278)
    'region-entry:839a3021d97a30a7eb73ec3567f74c61360b1dcb055e6b1ae0939f7ea6e29f6a': (
        'region-group:839a3021d97a30a7eb73ec3567f74c61360b1dcb055e6b1ae0939f7ea6e29f6a',
        [
            ('PL160', 278, 'PL160 col 555 / Cap. XXXVII (Boetius, vir consularis, conferendus philosophis)', 'pl160_boetius_consul_p278'),
        ]
    ),

    # 2. Aldhelmus Malmesburiensis -> 66 132 (PL160 pp. 281, 288)
    'region-entry:f0178d59d7336aadcadfd285b534f2d225642d53a453c486767735c82a4ffb94': (
        'region-group:f0178d59d7336aadcadfd285b534f2d225642d53a453c486767735c82a4ffb94',
        [
            ('PL160', 281, 'PL160 col 561 / Cap. LXVI (Adelmus seu Aldhelmus abbas Scotorum: Liber de Pascha)', 'pl160_aldhelmus_malmesburiensis_p281'),
            ('PL160', 288, 'PL160 col 576 / Cap. CXXXII (Adelmus episcopus aenigmata versibus hexametris)', 'pl160_aldhelmus_malmesburiensis_p288'),
        ]
    ),

    # 3. Laurentius Mellifluus -> 120 (PL160 pp. 286, 287)
    'region-entry:7d69f5c114febe9386269e0dc1864be10dd74b923e105eee312fcc93b7d958a8': (
        'region-group:7d69f5c114febe9386269e0dc1864be10dd74b923e105eee312fcc93b7d958a8',
        [
            ('PL160', 286, 'PL160 col 573 / Cap. CXX (Laurentius Melfius: Liber De duobus temporibus)', 'pl160_laurentius_mellifluus_p286'),
            ('PL160', 287, 'PL160 col 574 (Laurentius stylo mellito, unde agnominatur Mellifluus)', 'pl160_laurentius_mellifluus_p287'),
        ]
    ),

    # 4. Bonifacius episcopus et poeta -> 121 (PL160 p. 287)
    'region-entry:05657e8bbb490bbeeaaeb4b915085840a886b92dd71adcb6a2e4a92649d761a9': (
        'region-group:05657e8bbb490bbeeaaeb4b915085840a886b92dd71adcb6a2e4a92649d761a9',
        [
            ('PL160', 287, 'PL160 col 573 / Cap. CXXI (Bonifacius episcopus scripsit metrico stylo librum de virtutibus et vitiis)', 'pl160_bonifacius_episcopus_poeta_p287'),
        ]
    ),

    # 5. Abbo Floriacensis abbas -> cap. 139 (PL160 p. 289)
    'region-entry:4d30499e9e4ca98cb42364304c6784247bb5909e26d30d5cf26354f6b1002e62': (
        'region-group:4d30499e9e4ca98cb42364304c6784247bb5909e26d30d5cf26354f6b1002e62',
        [
            ('PL160', 289, 'PL160 col 578 / Cap. CXXXIX (Abbo abbas Floriacensis: Epistolae, computus et passio S. Edmundi)', 'pl160_abbo_floriacensis_p289'),
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


def resolve_pl160_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL160 item to physical destination files."""
    if item_id not in PL160_ITEMS_RAW:
        return None
    group_id, dests = PL160_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint85(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 85 recovery for PL160 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL160' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL160',
        'sprint': 85,
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
        res = resolve_pl160_item(item_id)
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
    total_src_only = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidate_storage")
    total_candidates = cur.fetchone()[0]

    report['candidates_created'] = candidates_inserted
    report['total_with_destinations_now'] = total_with_dest
    report['total_source_only_now'] = total_src_only
    report['candidate_storage_records_now'] = total_candidates

    # Update summary file if exists
    if os.path.exists(summary_path):
        try:
            with open(summary_path, 'r', encoding='utf-8') as sf:
                summary = json.load(sf)
            summary['items_with_destinations'] = total_with_dest
            summary['items_source_only'] = total_src_only
            summary['candidate_storage_records'] = total_candidates
            with open(summary_path, 'w', encoding='utf-8') as sf:
                json.dump(summary, sf, indent=2, ensure_ascii=False)
        except Exception as e:
            report['summary_update_error'] = str(e)

    conn.close()
    return report


def main():
    parser = argparse.ArgumentParser(description="PL160 Sigebertus Gemblacensis Locator (Sprint 85)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint85(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
