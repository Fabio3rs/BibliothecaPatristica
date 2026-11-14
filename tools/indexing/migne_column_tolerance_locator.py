#!/usr/bin/env python3
"""
Migne Column Tolerance Locator.

Resolves entries in items that have explicit column numbers (either in metadata_json['columns']
or in ref_raw) where the physical OCR file was not previously linked due to:
  - Deconcatenator inserting entries without a linking pass
  - Single-column header OCR omission in 2-column Migne spreads (±1 column tolerance)
  - Slight header OCR noise or spacing differences

Assigns target physical files with exact or adjacent header proof and optional text corroboration.
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time
import zlib
from typing import Dict, List, Optional, Tuple, Set


DB_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"


class MigneColumnToleranceLocator:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._vol_cache: Dict[str, Tuple[List[str], Dict[int, str]]] = {}
        self._file_text_cache: Dict[str, str] = {}

    def get_volume_col_map(self, vol: str) -> Tuple[List[str], Dict[int, str]]:
        if vol in self._vol_cache:
            return self._vol_cache[vol]

        vpath = f"/homessddata/Projects/pdfocr/teste/{vol}/text/*.txt"
        files = sorted(
            glob.glob(vpath),
            key=lambda x: int(re.search(r"-(\d+)\.txt$", x).group(1)) if re.search(r"-(\d+)\.txt$", x) else 0,
        )
        col_map: Dict[int, str] = {}

        for f in files:
            try:
                with open(f, encoding="utf-8", errors="replace") as fp:
                    t = fp.read()
            except Exception:
                continue

            # Header blocks
            cb = re.findall(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', t, re.DOTALL)
            for block in cb:
                for num in re.findall(r"\b\d{1,4}\b", block):
                    val = int(num)
                    # Filter out non-column numbers (e.g. single digit chapter numbers, or years)
                    if 1 <= val <= 3500 and val not in col_map:
                        col_map[val] = f

            # Marginal notes often hold column numbers in some editions
            marginals = re.findall(r'<bloco tipo="nota_marginal"[^>]*>(\d{1,4})</bloco>', t)
            for num in marginals:
                val = int(num)
                if 1 <= val <= 3500 and val not in col_map:
                    col_map[val] = f

        self._vol_cache[vol] = (files, col_map)
        return files, col_map

    def _get_file_text(self, path: str) -> str:
        if path not in self._file_text_cache:
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as fp:
                    self._file_text_cache[path] = fp.read()
            else:
                self._file_text_cache[path] = ""
        return self._file_text_cache[path]

    def extract_target_column(self, ref_raw: Optional[str], meta_json: Optional[str]) -> Optional[int]:
        """Extracts primary column target from metadata_json['columns'] or ref_raw."""
        if meta_json:
            try:
                mdata = json.loads(meta_json)
                cols = mdata.get("columns")
                if cols and isinstance(cols, list):
                    for c in cols:
                        if isinstance(c, int) and 1 <= c <= 3500:
                            return c
                        elif isinstance(c, str):
                            m = re.match(r"^\s*(\d{1,4})", c.strip())
                            if m and 1 <= int(m.group(1)) <= 3500:
                                return int(m.group(1))
            except Exception:
                pass

        if ref_raw:
            m = re.match(r"^\s*(\d{1,4})(?:\s*[a-d]\.?)?\s*$", ref_raw.strip(), re.IGNORECASE)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 3500:
                    return val

        return None

    def locate_item(
        self, vol: str, label: str, ref_raw: Optional[str], meta_json: Optional[str]
    ) -> Optional[Tuple[str, float, str, int]]:
        """
        Locates target file for a Migne column entry.
        Returns (target_file, score, match_type, column) or None.
        """
        col = self.extract_target_column(ref_raw, meta_json)
        if col is None:
            return None

        files, col_map = self.get_volume_col_map(vol)
        if not col_map:
            return None

        target_file = None
        base_score = 7.0
        match_type = ""

        if col in col_map:
            target_file = col_map[col]
            base_score = 9.5
            match_type = "exact_header_col"
        elif (col - 1) in col_map:
            target_file = col_map[col - 1]
            base_score = 8.5
            match_type = "adjacent_header_col_prev"
        elif (col + 1) in col_map:
            target_file = col_map[col + 1]
            base_score = 8.5
            match_type = "adjacent_header_col_next"

        if not target_file:
            return None

        # Corroborate with label words in file text
        final_score = base_score
        txt = self._get_file_text(target_file)
        if txt and label:
            words = [w.lower() for w in re.findall(r"[a-zA-Z\u0370-\u03ff]{4,}", label)]
            if words:
                matches = sum(1 for w in words if w in txt.lower())
                if matches / len(words) >= 0.5:
                    final_score += 1.5
                    match_type += "+text_match"

        return target_file, final_score, match_type, col

    def run(self, dry_run: bool = True, volumes: Optional[List[str]] = None) -> Dict:
        start_time = time.time()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        query = 'SELECT id, volume, label, ref_raw, metadata_json FROM items WHERE state="source_only"'
        params = []
        if volumes:
            placeholders = ",".join("?" for _ in volumes)
            query += f" AND volume IN ({placeholders})"
            params.extend(volumes)

        cur.execute(query, params)
        rows = cur.fetchall()

        promotions = []
        vol_counts: Dict[str, int] = {}

        for item_id, vol, label, ref_raw, meta_json in rows:
            res = self.locate_item(vol, label, ref_raw, meta_json)
            if res is None:
                continue

            target_file, score, match_type, col = res
            promotions.append((item_id, vol, target_file, score, match_type, col, meta_json, label, ref_raw))
            vol_counts[vol] = vol_counts.get(vol, 0) + 1

        if not dry_run and promotions:
            cur.execute("BEGIN TRANSACTION")
            for item_id, vol, target_file, score, match_type, col, meta_json, label, ref_raw in promotions:
                cand_payload = {
                    "source_type": f"migne_col_{match_type}",
                    "confidence": "high" if score >= 9.0 else "medium",
                    "column": col,
                    "target_file": target_file,
                }
                compressed_cand = zlib.compress(json.dumps(cand_payload).encode("utf-8"))

                cur.execute(
                    """
                    INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                    VALUES (?, ?, 1, ?, 1, ?)
                    ON CONFLICT(item_id, file) DO UPDATE SET
                        score=excluded.score,
                        candidate_json=excluded.candidate_json
                    """,
                    (item_id, target_file, score, compressed_cand),
                )

                meta = json.loads(meta_json) if meta_json else {}
                meta["state"] = "with_destinations"
                meta["column"] = col
                meta["matched_file"] = target_file
                meta["locator"] = f"migne_col_{match_type}"
                updated_meta_json = json.dumps(meta, ensure_ascii=False)

                cur.execute(
                    'UPDATE items SET state="with_destinations", metadata_json=? WHERE id=?',
                    (updated_meta_json, item_id),
                )

            con.commit()

        con.close()
        duration = round(time.time() - start_time, 2)

        return {
            "status": "success",
            "dry_run": dry_run,
            "total_evaluated": len(rows),
            "total_promoted": len(promotions),
            "duration_seconds": duration,
            "promotions_by_volume": dict(sorted(vol_counts.items(), key=lambda x: x[1], reverse=True)[:25]),
            "sample_promotions": [
                {
                    "vol": p[1],
                    "label": p[7][:35],
                    "col": p[5],
                    "target": os.path.basename(p[2]),
                    "score": p[3],
                    "type": p[4],
                }
                for p in promotions[:15]
            ],
        }


def main():
    parser = argparse.ArgumentParser(description="Migne Column Tolerance Locator")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run without DB modifications")
    parser.add_argument("--apply", action="store_true", default=False, help="Apply promotions to DB")
    parser.add_argument("--volumes", nargs="+", help="Optional list of volumes to restrict execution to")
    args = parser.parse_args()

    is_dry_run = not args.apply

    locator = MigneColumnToleranceLocator()
    results = locator.run(dry_run=is_dry_run, volumes=args.volumes)
    print(
        json.dumps(
            {k: v for k, v in results.items() if k != "sample_promotions"},
            indent=2,
            ensure_ascii=False,
        )
    )
    print("\nSample promotions:")
    for s in results["sample_promotions"]:
        print(f"  • [{s['vol']}] \"{s['label']}\" (col {s['col']}) -> {s['target']} [{s['type']} score={s['score']}]")


if __name__ == "__main__":
    main()
