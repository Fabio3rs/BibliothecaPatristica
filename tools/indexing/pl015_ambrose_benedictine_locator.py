#!/usr/bin/env python3
"""
Ambrose Benedictine Index Locator (PL015 <-> PL014).

Resolves entries in the Ambrose Index (PL015 pages 538-559) which use the Benedictine
Tomus I folio pagination (pages 1 to ~1615) and paragraph/section numbers:
  - Benedictine pages 1 to ~965 reside in volume PL014 (files 1 to 606).
  - Benedictine pages ~966 to ~1615 reside in volume PL015 (files 1 to 350).

Uses calibrated linear interpolation between monotonic physical page anchors combined
with paragraph heading verification (\\b<para>\\.) and keyword match.
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


class AmbroseBenedictineLocator:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._sources_cache: Dict[str, str] = {}
        self._file_text_cache: Dict[str, str] = {}
        self._f14_files, self._f14_map, self._a14 = self._build_volume_anchors("PL014")
        self._f15_files, self._f15_map, self._a15 = self._build_volume_anchors("PL015")

    def _build_volume_anchors(self, vol: str) -> Tuple[List[str], Dict[int, str], List[Tuple[int, int]]]:
        pattern = re.compile(
            r'[Nn]úmeros?\s+([0-9\s,eaut-]+)\s+(?:em\s+negrito|no\s+corpo|no\s+meio|no\s+fluxo|são\s+marcadores|são\s+paginaç|são\s+referências|de\s+página\s+original|originais)',
            re.IGNORECASE,
        )
        files = sorted(
            glob.glob(f"/homessddata/Projects/pdfocr/teste/{vol}/text/*.txt"),
            key=lambda x: int(re.search(r"-(\d+)\.txt$", x).group(1)),
        )
        fmap: Dict[int, str] = {}
        anchors: List[Tuple[int, int]] = []
        for f in files:
            m_fnum = re.search(r"-(\d+)\.txt$", f)
            if not m_fnum:
                continue
            fnum = int(m_fnum.group(1))
            fmap[fnum] = f
            with open(f, encoding="utf-8", errors="replace") as fp:
                t = fp.read()
            m = pattern.findall(t)
            for s in m:
                for n in re.findall(r"\b\d+\b", s):
                    val = int(n)
                    if 1 <= val <= 2500 and val not in [392, 393]:
                        anchors.append((fnum, val))

        anchors.sort(key=lambda x: (x[0], x[1]))
        clean: List[Tuple[int, int]] = []
        last_p = 0
        for fnum, p in anchors:
            if p > last_p:
                if not clean or (p - last_p) <= (fnum - clean[-1][0] + 1) * 3:
                    clean.append((fnum, p))
                    last_p = p
        return files, fmap, clean

    def _get_source_text(self, src: str) -> str:
        if src not in self._sources_cache:
            if os.path.exists(src):
                with open(src, encoding="utf-8", errors="replace") as fp:
                    self._sources_cache[src] = fp.read()
            else:
                self._sources_cache[src] = ""
        return self._sources_cache[src]

    def _get_file_text(self, path: str) -> str:
        if path not in self._file_text_cache:
            if os.path.exists(path):
                with open(path, encoding="utf-8", errors="replace") as fp:
                    self._file_text_cache[path] = fp.read()
            else:
                self._file_text_cache[path] = ""
        return self._file_text_cache[path]

    def parse_entry_reference(self, src_path: str, label: str, db_ref: str) -> Tuple[Optional[int], Optional[int]]:
        """Extracts (bened_page, paragraph) from the index page text or db_ref fallback."""
        txt = self._get_source_text(src_path)
        if txt:
            # 1. Try finding full or prefix label
            probe = label.strip()
            idx = txt.find(probe) if probe else -1
            if idx == -1 and len(probe) > 25:
                probe = label[:25].strip()
                idx = txt.find(probe)

            if idx != -1:
                after = txt[idx + len(probe) : idx + len(probe) + 120]
                m = re.search(r",\s*(\d+)(?:\s*,\s*(\d+))?", after)
                if m:
                    p = int(m.group(1))
                    para = int(m.group(2)) if m.group(2) else None
                    return p, para

        # Fallback to db_ref
        m3 = re.search(r"\b\d+\b", db_ref or "")
        if m3:
            return int(m3.group(0)), None
        return None, None

    def interpolate_file_num(self, page: int, anchors: List[Tuple[int, int]], max_f: int) -> int:
        if not anchors:
            return 1
        if page <= anchors[0][1]:
            return anchors[0][0]
        if page >= anchors[-1][1]:
            return anchors[-1][0]

        for i in range(len(anchors) - 1):
            f1, p1 = anchors[i]
            f2, p2 = anchors[i + 1]
            if p1 <= page <= p2:
                if p2 == p1:
                    f_est = f1
                else:
                    f_est = int(round(f1 + (page - p1) * (f2 - f1) / (p2 - p1)))
                return max(1, min(max_f, f_est))
        return anchors[-1][0]

    def locate_item(
        self, src_path: str, label: str, db_ref: str
    ) -> Optional[Tuple[str, float, str, int, Optional[int]]]:
        """
        Locates target file for an Ambrose index item.
        Returns (target_path, score, reason, page, para) or None.
        """
        page, para = self.parse_entry_reference(src_path, label, db_ref)
        if not page or page < 1 or page > 2500:
            return None

        # Volume determination
        vol = "PL014" if page < 965 else "PL015"
        anchors = self._a14 if vol == "PL014" else self._a15
        fmap = self._f14_map if vol == "PL014" else self._f15_map
        if not fmap:
            return None

        max_f = max(fmap.keys())
        f_est = self.interpolate_file_num(page, anchors, max_f)

        best_file = fmap.get(f_est, list(fmap.values())[0])
        best_score = 7.0
        reason = "bened_interpolated"

        words = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", label)]

        # Search window [f_est - 4, f_est + 4]
        for cand_fnum in range(max(1, f_est - 4), min(max_f + 1, f_est + 5)):
            cand_path = fmap.get(cand_fnum)
            if not cand_path:
                continue
            cand_txt = self._get_file_text(cand_path)
            score = 7.0

            # Check paragraph heading
            if para and re.search(rf"\b{para}\.\b", cand_txt):
                score += 3.0

            # Check exact page marker in text
            if re.search(rf"\b{page}\b", cand_txt):
                score += 2.0

            # Check content words
            if words:
                matched_words = sum(1 for w in words if w in cand_txt.lower())
                if matched_words / len(words) >= 0.5:
                    score += 2.0

            if score > best_score:
                best_score = score
                best_file = cand_path
                reason = "bened_verified"

        return best_file, best_score, reason, page, para

    def run(self, dry_run: bool = True) -> Dict:
        start_time = time.time()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        cur.execute(
            'SELECT id, source, label, ref_raw, metadata_json FROM items WHERE volume="PL015" AND state="source_only"'
        )
        rows = cur.fetchall()

        promotions = []
        for item_id, src, label, ref_raw, meta_json in rows:
            res = self.locate_item(src, label, ref_raw)
            if res is None:
                continue

            target_file, score, reason, page, para = res
            promotions.append((item_id, target_file, score, reason, page, para, meta_json, label, ref_raw))

        if not dry_run and promotions:
            cur.execute("BEGIN TRANSACTION")
            for item_id, target_file, score, reason, page, para, meta_json, label, ref_raw in promotions:
                cand_payload = {
                    "source_type": f"pl015_ambrose_bened_{reason}",
                    "confidence": "high" if score >= 9.0 else "medium",
                    "bened_page": page,
                    "bened_para": para,
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
                meta["bened_page"] = page
                meta["bened_para"] = para
                meta["matched_file"] = target_file
                meta["locator"] = f"pl015_ambrose_{reason}"
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
            "sample_promotions": [
                {
                    "label": p[7][:40],
                    "ref": f"p.{p[4]}, §{p[5]}",
                    "target": os.path.basename(p[1]),
                    "score": p[2],
                    "reason": p[3],
                }
                for p in promotions[:15]
            ],
        }


def main():
    parser = argparse.ArgumentParser(description="Ambrose Benedictine Index Locator")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run without DB modifications")
    parser.add_argument("--apply", action="store_true", default=False, help="Apply promotions to DB")
    args = parser.parse_args()

    is_dry_run = not args.apply

    locator = AmbroseBenedictineLocator()
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
        print(f"  • {s['label']} ({s['ref']}) -> {s['target']} [{s['reason']} score={s['score']}]")


if __name__ == "__main__":
    main()
