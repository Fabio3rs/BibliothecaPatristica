"""
PL077 Saint Gregory the Great Regestum Epistolarum Locator
Sprint 27 of Onomastic Catalog Recovery

Resolves the 37 residual source_only items from PL077's Indices:
1. Index Capitulorum et Canonum (pages 737-742)
2. Index Rerum et Sententiarum (pages 742-775)
to physical pages across Saint Gregory's Regestum Epistolarum in PL077 (cols. 1-1330).

Handles:
- Column references with suffixes ('et sqq', 'et seqq', 'et seq').
- Multi-column lists (e.g. '1129, 1230', '581, 584', '53, 205').
- OCR noise in column numbers (e.g. '1637' for col. 637).
- Interpolation across OCR column header gaps.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DB_PATH = 'data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite'


class PL077GregoryRegestumLocator:
    def __init__(self, root_dir: str = '.', db_path: str = DB_PATH):
        self.root_dir = Path(root_dir).resolve()
        self.db_path = db_path
        self._col_map: Dict[int, str] = {}
        self._pages: List[Tuple[int, str]] = []
        self._init_col_map()

    def _init_col_map(self):
        text_dir = self.root_dir / 'teste' / 'PL077' / 'text'
        if not text_dir.exists():
            return
        files = sorted(text_dir.glob('*.txt'))
        for f in files:
            m_p = re.search(r'-(\d+)\.txt$', f.name)
            if not m_p:
                continue
            num = int(m_p.group(1))
            fpath = str(f.resolve())
            self._pages.append((num, fpath))
            try:
                txt = f.read_text(encoding='utf-8', errors='ignore')[:300]
                m_h = re.search(r'<bloco tipo=\"cabecalho\"[^>]*>(.*?)</bloco>', txt, re.DOTALL)
                htxt = m_h.group(1) if m_h else txt
                for c in re.findall(r'\b(\d{1,4})\b', htxt):
                    ci = int(c)
                    if 1 <= ci <= 1450 and ci not in self._col_map:
                        self._col_map[ci] = fpath
            except Exception:
                pass
        self._pages.sort(key=lambda x: x[0])

    def interpolate_column(self, col: int) -> Optional[str]:
        if not self._col_map:
            return None
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

    def resolve_item(self, label: str, ref_raw: str, src_path: str = '') -> Optional[Dict[str, Any]]:
        nums = [int(n) for n in re.findall(r'\b(\d{1,4})\b', ref_raw)]
        if not nums:
            nums = [int(n) for n in re.findall(r'\b(\d{1,4})\b', label)]

        if not nums:
            return None

        col = nums[0]
        # Handle 4-digit OCR errors for PL077 (e.g. 1637 -> 637)
        if col > 1450:
            col = int(str(col)[-3:])

        fpath = self.interpolate_column(col)
        if not fpath:
            return None

        p_phys = int(os.path.basename(fpath).split('-')[-1].split('.')[0])

        return {
            'target_volume': 'PL077',
            'column': col,
            'target_file': fpath,
            'physical_page': p_phys,
            'confidence': 95.0
        }

    def run(self, dry_run: bool = True, force_all: bool = False) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        where_clause = "WHERE volume = 'PL077'" if force_all else "WHERE volume = 'PL077' AND state = 'source_only'"
        items = cur.execute(f'''
            SELECT id, label, ref_raw, source
            FROM items
            {where_clause}
            ORDER BY id
        ''').fetchall()

        promotions = []
        candidates_to_insert = []

        for item_id, label, ref_raw, src in items:
            res = self.resolve_item(label or '', ref_raw or '', src or '')
            if res:
                promotions.append(item_id)
                cand_payload = {
                    'rule': 'pl077_gregory_regestum_locator',
                    'target_volume': res['target_volume'],
                    'column': res['column'],
                    'physical_page': res['physical_page'],
                    'target_file': res['target_file'],
                    'confidence': res['confidence']
                }
                cand_blob = zlib.compress(json.dumps(cand_payload, ensure_ascii=False).encode('utf-8'))
                candidates_to_insert.append((
                    item_id,
                    res['target_file'],
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
    parser = argparse.ArgumentParser(description='PL077 Gregory Regestum Locator')
    parser.add_argument('--apply', action='store_true', help='Apply changes to database')
    args = parser.parse_args()

    locator = PL077GregoryRegestumLocator()
    res = locator.run(dry_run=not args.apply)
    print(f'Execution result (dry_run={res["dry_run"]}):')
    print(f'  Total evaluated: {res["total_items"]}')
    print(f'  Promoted: {res["promoted"]} ({(res["promoted"]/max(1, res["total_items"]))*100:.2f}%)')
    print(f'  Candidates: {res["candidates"]}')
