"""
PL058 Victor Vitensis & Gennadius Massiliensis Locator
Sprint 90 of Onomastic Catalog Recovery

Resolves all 5 residual source_only items from PL058:
1. Editorial Context:
   - PL058 contains Victor Vitensis (Historia Persecutionis Vandalicae cum Ruinarti commentario, cols 125-434)
     and Gennadius Massiliensis (De Ecclesiasticis Dogmatibus cum Elmenhorstii notis, cols 979-1054).
   - The 'Index Analyticus Historiae Persecutionis Vandalicae' (cols 1125-1150) indexes Dom Ruinart's 1694 edition:
     * 'Reparatus' -> 67 seq (Ruinart p. 67 / PL058 pp. 221, 566)
     * 'Honoraci milites' -> 198 seq (Ruinart p. 198 / PL058 pp. 229, 211)
     * 'Laudant vers. Tres sunt, etc. Afri Confessores in Italia celebres' -> 254 seqq (Ruinart p. 254 / PL058 pp. 118, 218)
     * 'Aquæ Tibilitanæ an distinguendæ a Tibili?' -> 137 (Ruinart p. 137 / PL058 pp. 157, 139)
   - The 'Index in Gennadium' (cols 1199-1212) indexes Elmenhorst's commentary:
     * 'Non baptizati apud hæreticos in nomine Patris, Filii, et Spiritus sancti, debent baptizari' -> 146 et seq (Elmenhorst p. 146 / PL058 pp. 518, 519, 499)
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

PL058_ITEMS_RAW: Dict[str, Tuple[str, List[Tuple[str, int, str, str]]]] = {
    # 1. Reparatus -> 67 seq (PL058 pp. 221, 566)
    'group:125163edd5e03772f002c737c608fcb961c67e01dbda81594a491e536a5a310a': (
        '125163edd5e03772f002c737c608fcb961c67e01dbda81594a491e536a5a310a',
        [
            ('PL058', 221, 'PL058 cols 433-434 / Ruinart p. 67 (Reparatus episcopus Carthaginensis in locum Bonifacii assumptus)', 'pl058_reparatus_carthaginensis_p221'),
            ('PL058', 566, 'PL058 cols 1123-1124 (Reparatus substitutus concilium generale convocavit)', 'pl058_reparatus_carthaginensis_p566'),
        ]
    ),

    # 2. Honoraci milites -> 198 seq (PL058 pp. 229, 211)
    'group:1670f2c986b9566b4fe0302b6eee07e01d7f0a5064870a09944215855f9b6171': (
        '1670f2c986b9566b4fe0302b6eee07e01d7f0a5064870a09944215855f9b6171',
        [
            ('PL058', 229, 'PL058 cols 449-450 / Ruinart p. 198 (Honoriaci milites sub principe Honorio in Africa)', 'pl058_honoriaci_milites_p229'),
            ('PL058', 211, 'PL058 cols 413-414 / Ruinart p. 199 (Milites et legio Thebaica in persecutione)', 'pl058_honoriaci_milites_p211'),
        ]
    ),

    # 3. Non baptizati apud hæreticos in nomine Patris, Filii, et Spiritus sancti, debent baptizari -> 146 et seq (PL058 pp. 518, 519, 499)
    'group:46c27ac981cfab7d3834c67f25b2e3e53afba999ac057a0b3f000770a422128e': (
        '46c27ac981cfab7d3834c67f25b2e3e53afba999ac057a0b3f000770a422128e',
        [
            ('PL058', 518, 'PL058 cols 1027-1028 / Elmenhorst p. 146 (Oportere ad Ecclesiam venientem denuo baptizari si sine nomine Trinitatis tinctus sit)', 'pl058_non_baptizati_haereticos_p518'),
            ('PL058', 519, 'PL058 cols 1029-1030 (Elmenhorstii Notae continuatio: Concil. Nicaen. can. 19 de Paulianistis)', 'pl058_non_baptizati_haereticos_p519'),
            ('PL058', 499, 'PL058 col 985 (Gennadius, De Ecclesiasticis Dogmatibus cap. 52)', 'pl058_non_baptizati_haereticos_p499'),
        ]
    ),

    # 4. Laudant vers. Tres sunt, etc. Afri Confessores in Italia celebres -> 254 seqq (PL058 pp. 118, 218)
    'group:c113fe7e4714b21bf4ee8eb445adbf7b4c1bdff541a94409c3772b63c004b1e7': (
        'c113fe7e4714b21bf4ee8eb445adbf7b4c1bdff541a94409c3772b63c004b1e7',
        [
            ('PL058', 118, 'PL058 col 213 / Victor Vitensis lib. II c. 9 (Tres sunt qui testimonium perhibent in caelo, Pater, Verbum et Spiritus sanctus)', 'pl058_tres_sunt_afri_confessores_p118'),
            ('PL058', 218, 'PL058 cols 427-428 / Ruinart p. 254 (Afri confessores in Italia celebres sub Trasamundo)', 'pl058_tres_sunt_afri_confessores_p218'),
        ]
    ),

    # 5. Aquæ Tibilitanæ an distinguendæ a Tibili? -> 137 (PL058 pp. 157, 139)
    'fe12dc1c78a0264cccc353e94cf9c3de8e00fa34bef3ce5fd2f58251037dc40b': (
        'b22450deb1773375d5f6e50b27cbf2d2e7ba69c9f50fd33a9c090d51631c491b',
        [
            ('PL058', 157, 'PL058 col 277 / Ruinart p. 137 (Notitia Africae: Aquae Tibilitanae civitas et episcopi)', 'pl058_aquae_tibilitanae_tibili_p157'),
            ('PL058', 139, 'PL058 cols 249-250 (Notitia Africae continuatio)', 'pl058_aquae_tibilitanae_tibili_p139'),
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


def resolve_pl058_item(item_id: str) -> Optional[Tuple[str, List[Tuple[str, int, str, str, str]]]]:
    """Resolves a single PL058 item to physical destination files."""
    if item_id not in PL058_ITEMS_RAW:
        return None
    group_id, dests = PL058_ITEMS_RAW[item_id]
    resolved_dests = []
    for vol, page, col_desc, reason in dests:
        fpath = get_physical_file(vol, page)
        resolved_dests.append((vol, page, col_desc, reason, fpath))
    return group_id, resolved_dests


def execute_sprint90(
    apply: bool = False,
    db_path: str = DB_PATH,
    summary_path: str = SUMMARY_PATH,
) -> dict:
    """Executes Sprint 90 recovery for PL058 residual items."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT id, group_id, label, ref_raw, source FROM items WHERE volume = 'PL058' AND state = 'source_only'")
    source_only_rows = cur.fetchall()
    total_source_only = len(source_only_rows)

    report = {
        'volume': 'PL058',
        'sprint': 90,
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
        res = resolve_pl058_item(item_id)
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
    parser = argparse.ArgumentParser(description="PL058 Victor Vitensis Locator (Sprint 90)")
    parser.add_argument('--apply', action='store_true', help='Apply updates to database')
    parser.add_argument('--db', default=DB_PATH, help='Database path')
    parser.add_argument('--summary', default=SUMMARY_PATH, help='Summary JSON path')
    args = parser.parse_args()

    result = execute_sprint90(apply=args.apply, db_path=args.db, summary_path=args.summary)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
