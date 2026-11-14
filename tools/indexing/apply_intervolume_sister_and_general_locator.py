#!/usr/bin/env python3
"""
Inter-Volume Sister and General Locator.

Resolves entries in items that reference physical files residing in another sister volume:
  1. Multi-part volume splits (Index printed at the end of the final part, referencing prior parts):
     - PL045 -> PL044 (cols < 1000)
     - PG007.02 -> PG007.01 (cols < 800)
     - PG016.03 -> PG016.01 / PG016.02
     - PG087.03 -> PG087.01 / PG087.02
     - PL046 -> PL044 / PL045
     - PL024 -> PL023
     - PL076 -> PL075 / PL077
  2. Migne General Indices (PL218, PL219, PL220, PL221):
     - Remissions citing <Tomo Romano>, <Coluna Migne> (e.g. LXXIV, 1384 -> PL074).
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
from typing import Dict, List, Optional, Tuple


DB_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"

SISTER_MAP = {
    "PL045": lambda col: "PL044" if col < 1000 else None,
    "PG007.02": lambda col: "PG007.01" if col < 800 else None,
    "PG016.03": lambda col: "PG016.01" if col <= 1000 else ("PG016.02" if col <= 2000 else None),
    "PG087.03": lambda col: "PG087.01" if col <= 1200 else ("PG087.02" if col <= 2700 else None),
    "PL046": lambda col: "PL044" if col <= 1000 else ("PL045" if col <= 2000 else None),
    "PL024": lambda col: "PL023" if col <= 1100 else None,
    "PL076": lambda col: "PL075" if col <= 1100 else ("PL077" if 1400 <= col <= 1650 else None),
}


def roman_to_int(s: str) -> int:
    rom_val = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    s = s.upper().strip().rstrip(".,;:")
    total = 0
    prev = 0
    for c in reversed(s):
        curr = rom_val.get(c, 0)
        if curr >= prev:
            total += curr
        else:
            total -= curr
        prev = curr
    return total


class InterVolumeSisterAndGeneralLocator:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._vol_col_cache: Dict[str, Dict[int, str]] = {}
        self._file_text_cache: Dict[str, str] = {}

    def get_vol_col_map(self, target_vol: str) -> Dict[int, str]:
        if target_vol in self._vol_col_cache:
            return self._vol_col_cache[target_vol]

        col_map: Dict[int, str] = {}
        vpath = f"/homessddata/Projects/pdfocr/teste/{target_vol}/text/*.txt"
        files = sorted(
            glob.glob(vpath),
            key=lambda x: int(re.search(r"-(\d+)\.txt$", x).group(1)) if re.search(r"-(\d+)\.txt$", x) else 0,
        )

        for f in files:
            try:
                with open(f, encoding="utf-8", errors="replace") as fp:
                    t = fp.read()
            except Exception:
                continue

            cb = re.findall(r'<bloco tipo="cabecalho"[^>]*>(.*?)</bloco>', t, re.DOTALL)
            for b in cb:
                for num in re.findall(r"\b\d{1,4}\b", b):
                    val = int(num)
                    if 1 <= val <= 3500 and val not in col_map:
                        col_map[val] = f

            marginals = re.findall(r'<bloco tipo="nota_marginal"[^>]*>(\d{1,4})</bloco>', t)
            for num in marginals:
                val = int(num)
                if 1 <= val <= 3500 and val not in col_map:
                    col_map[val] = f

        self._vol_col_cache[target_vol] = col_map
        return col_map

    def _get_file_text(self, path: str) -> str:
        if path not in self._file_text_cache:
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as fp:
                    self._file_text_cache[path] = fp.read()
            else:
                self._file_text_cache[path] = ""
        return self._file_text_cache[path]

    def locate_item(
        self, vol: str, label: str, ref_raw: Optional[str], meta_json: Optional[str]
    ) -> Optional[Tuple[str, str, float, str, int]]:
        """
        Locates target physical file across volumes.
        Returns (target_vol, target_file, score, reason, column) or None.
        """
        target_vol = None
        col = None
        ref_str = (ref_raw or "").replace("\n", " ").strip()

        # Case 1: Sister Volume
        if vol in SISTER_MAP:
            m = re.search(r"\b\d+\b", ref_str)
            if m:
                c = int(m.group(0))
                tv = SISTER_MAP[vol](c)
                if tv:
                    target_vol = tv
                    col = c

        # Case 2: General Index Multi-Volume (PL218 - PL221)
        elif vol in ("PL218", "PL219", "PL220", "PL221"):
            m = re.search(r"\b([IVXLCDM]{1,8})\b\s*[,.\s]\s*(\d{1,4})", ref_str, re.IGNORECASE)
            if m:
                r_str = m.group(1).upper()
                if r_str == "CCLXXX":
                    r_str = "CLXXX"
                t = roman_to_int(r_str)
                if 1 <= t <= 221:
                    target_vol = f"PL{t:03d}"
                    col = int(m.group(2))
            elif meta_json:
                try:
                    mdata = json.loads(meta_json)
                    sec = mdata.get("section") or ""
                    m_sec = re.search(r"\b([IVXLCDM]{1,8})\b", sec)
                    if m_sec:
                        t = roman_to_int(m_sec.group(1))
                        if 1 <= t <= 221:
                            target_vol = f"PL{t:03d}"
                    m_col = re.search(r"\b(\d{1,4})\b", ref_str)
                    if m_col:
                        col = int(m_col.group(1))
                except Exception:
                    pass

        if not target_vol or col is None:
            return None

        col_map = self.get_vol_col_map(target_vol)
        if not col_map:
            return None

        target_file = None
        base_score = 7.0
        reason = ""

        if col in col_map:
            target_file = col_map[col]
            base_score = 9.5
            reason = "intervolume_exact_col"
        elif (col - 1) in col_map:
            target_file = col_map[col - 1]
            base_score = 8.5
            reason = "intervolume_adj_prev"
        elif (col + 1) in col_map:
            target_file = col_map[col + 1]
            base_score = 8.5
            reason = "intervolume_adj_next"

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
                    reason += "+text_match"

        return target_vol, target_file, final_score, reason, col

    def run(self, dry_run: bool = True) -> Dict:
        start_time = time.time()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        target_vols = list(SISTER_MAP.keys()) + ["PL218", "PL219", "PL220", "PL221"]
        placeholders = ",".join("?" for _ in target_vols)

        cur.execute(
            f'SELECT id, volume, label, ref_raw, metadata_json FROM items WHERE volume IN ({placeholders}) AND state="source_only"',
            target_vols,
        )
        rows = cur.fetchall()

        promotions = []
        vol_counts: Dict[str, int] = {}

        for item_id, vol, label, ref_raw, meta_json in rows:
            res = self.locate_item(vol, label, ref_raw, meta_json)
            if res is None:
                continue

            target_vol, target_file, score, reason, col = res
            promotions.append(
                (item_id, vol, target_vol, target_file, score, reason, col, meta_json, label, ref_raw)
            )
            vol_counts[vol] = vol_counts.get(vol, 0) + 1

        if not dry_run and promotions:
            cur.execute("BEGIN TRANSACTION")
            for item_id, vol, target_vol, target_file, score, reason, col, meta_json, label, ref_raw in promotions:
                cand_payload = {
                    "source_type": f"intervolume_{reason}",
                    "confidence": "high" if score >= 9.0 else "medium",
                    "target_volume": target_vol,
                    "target_column": col,
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
                meta["target_volume"] = target_vol
                meta["target_column"] = col
                meta["matched_file"] = target_file
                meta["locator"] = f"intervolume_{reason}"
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
            "promotions_by_volume": dict(sorted(vol_counts.items(), key=lambda x: x[1], reverse=True)),
            "sample_promotions": [
                {
                    "src_vol": p[1],
                    "target_vol": p[2],
                    "label": p[8][:35],
                    "col": p[6],
                    "target_file": os.path.basename(p[3]),
                    "score": p[4],
                    "reason": p[5],
                }
                for p in promotions[:15]
            ],
        }


def main():
    parser = argparse.ArgumentParser(description="Inter-Volume Sister and General Locator")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run without DB modifications")
    parser.add_argument("--apply", action="store_true", default=False, help="Apply promotions to DB")
    args = parser.parse_args()

    is_dry_run = not args.apply

    locator = InterVolumeSisterAndGeneralLocator()
    results = locator.run(dry_run=is_dry_run)
    print(
        json.dumps(
            {k: v for k, v in results.items() if k != "sample_promotions"},
            indent=2,
            ensure_ascii=False,
        )
    )
    print("\nSample promotions:")
    for s in results["sample_promotions"]:
        print(
            f"  • [{s['src_vol']} -> {s['target_vol']}] \"{s['label']}\" (col {s['col']}) -> {s['target_file']} [{s['reason']} score={s['score']}]"
        )


if __name__ == "__main__":
    main()
