"""
PL167 Rupert of Deutz Column Gap Interpolation Locator
Sprint 18 of Onomastic Catalog Recovery

Resolves Index Rerum et Verborum (PL167, pages 780-798) column citations
to physical pages in Rupert of Deutz (PL167, pages 1 to 779), interpolating
narrow OCR header gaps and preface sections.
"""

import glob
import json
import os
import re
import sqlite3
import zlib
from typing import Dict, List, Optional, Tuple, Any

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'


class PL167RupertLocator:
    def __init__(self, text_dir: str = 'teste/PL167/text', db_path: str = DB_PATH):
        self.text_dir = text_dir
        self.db_path = db_path
        self._col_map: Dict[int, str] = {}
        self._pages: List[Tuple[int, str]] = []
        self._init_col_map()

    def _init_col_map(self):
        files = sorted(glob.glob(os.path.join(self.text_dir, '*.txt')))
        for f in files:
            try:
                base = os.path.basename(f)
                num = int(base.split('-')[-1].split('.')[0])
                self._pages.append((num, os.path.abspath(f)))
            except Exception:
                pass
            with open(f, 'r', encoding='utf-8', errors='ignore') as fp:
                txt = fp.read()
            m_h = re.search(r'<bloco tipo=\"cabecalho\"[^>]*>(.*?)</bloco>', txt, re.DOTALL)
            htxt = m_h.group(1) if m_h else txt[:300]
            for c in re.findall(r'\b(\d{1,4})\b', htxt):
                ci = int(c)
                if 1 <= ci <= 2000 and ci not in self._col_map:
                    self._col_map[ci] = os.path.abspath(f)

        self._pages.sort(key=lambda x: x[0])

    def interpolate_column(self, col: int) -> Optional[str]:
        if col in self._col_map:
            return self._col_map[col]
        if (col - 1) in self._col_map:
            return self._col_map[col - 1]
        if (col + 1) in self._col_map:
            return self._col_map[col + 1]

        sorted_cols = sorted(self._col_map.keys())
        bef = [c for c in sorted_cols if c < col]
        aft = [c for c in sorted_cols if c > col]
        if not bef and aft:
            return self._pages[0][1]
        if not aft and bef:
            return self._pages[-1][1]

        c_prev, c_next = bef[-1], aft[0]
        f_prev = self._col_map[c_prev]
        f_next = self._col_map[c_next]

        p_prev = int(os.path.basename(f_prev).split('-')[-1].split('.')[0])
        p_next = int(os.path.basename(f_next).split('-')[-1].split('.')[0])

        delta_c = c_next - c_prev
        delta_p = p_next - p_prev
        p_est = p_prev + round((col - c_prev) * delta_p / delta_c) if delta_c > 0 else p_prev

        for p, path in self._pages:
            if p == p_est:
                return path
        return f_prev

    def resolve_item(self, label: str, ref_raw: str) -> Optional[Tuple[str, int]]:
        m = re.search(r'\b(\d{1,4})\b', ref_raw)
        if not m:
            m = re.search(r'\b(\d{1,4})\b', label)
        if not m:
            return None
        col = int(m.group(1))
        fpath = self.interpolate_column(col)
        return (fpath, col) if fpath else None

    def run(self, dry_run: bool = True, force_all: bool = False) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        where_clause = "WHERE volume = 'PL167'" if force_all else "WHERE volume = 'PL167' AND state = 'source_only'"
        items = cur.execute(f'''
            SELECT id, label, ref_raw, source
            FROM items
            {where_clause}
            ORDER BY id
        ''').fetchall()

        promotions = []
        candidates_to_insert = []

        for item_id, label, ref_raw, src in items:
            res = self.resolve_item(label or '', ref_raw or '')
            if res:
                fpath, col = res
                promotions.append(item_id)
                cand_payload = {
                    'rule': 'pl167_rupert_column_interpolator',
                    'target_volume': 'PL167',
                    'column': col,
                    'target_file': fpath,
                    'confidence': 95.0
                }
                cand_blob = zlib.compress(json.dumps(cand_payload, ensure_ascii=False).encode('utf-8'))
                candidates_to_insert.append((
                    item_id,
                    fpath,
                    1,
                    9.5,
                    1,
                    cand_blob
                ))

        stats = {
            'total_items': len(items),
            'promoted': len(promotions),
            'candidates': len(candidates_to_insert),
            'dry_run': dry_run
        }

        if not dry_run and promotions:
            cur.execute('BEGIN TRANSACTION')
            cur.executemany('''
                INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id, file) DO UPDATE SET
                    rank=excluded.rank,
                    score=excluded.score,
                    candidate_json=excluded.candidate_json
            ''', candidates_to_insert)

            placeholder = ','.join('?' for _ in promotions)
            cur.execute(f'''
                UPDATE items
                SET state = 'with_destinations'
                WHERE id IN ({placeholder})
            ''', promotions)
            conn.commit()

        conn.close()
        return stats


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='PL167 Rupert Column Locator')
    parser.add_argument('--apply', action='store_true', help='Apply changes to database')
    args = parser.parse_args()

    locator = PL167RupertLocator()
    res = locator.run(dry_run=not args.apply)
    print(f'Execution result (dry_run={res["dry_run"]}):')
    print(f'  Total evaluated: {res["total_items"]}')
    print(f'  Promoted: {res["promoted"]} ({(res["promoted"]/res["total_items"])*100:.2f}%)')
    print(f'  Candidates: {res["candidates"]}')
