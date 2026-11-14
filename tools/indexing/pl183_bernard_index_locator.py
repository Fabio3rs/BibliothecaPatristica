#!/usr/bin/env python3
"""
tools/indexing/pl183_bernard_index_locator.py

Specific locator for the Bernard of Clairvaux Index in PL183
(Index Rerum et Verborum Voluminis Primi):
- Columns <= 1150 map to PL182 (Tomus I: Epistolae & Tractatus).
- Columns > 1150 cite the continuous Mabillon numbering of Tomus I Part 2 (Sermones),
  reprinted in Migne as PL183 with offset (Migne_col ≈ Mabillon_col - 355).
- Matches content words via second hint to confirm page destination.
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import time
import unicodedata
import zlib
from typing import Dict, List, Optional, Tuple, Any

DB_PATH = "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
MABILLON_OFFSET = 355

LATIN_STOPWORDS = {
    "sanctus", "sancti", "sancto", "sanctum", "sancta", "sanctae",
    "beatus", "beati", "beato", "beatum", "beata", "beatae",
    "presbyter", "presbyteri", "episcopus", "episcopi", "episcopo",
    "archiepiscopus", "archiepiscopi", "abbas", "abbatis", "papa", "papae",
    "doctor", "ecclesiae", "monachus", "monachi", "auctor", "auctoris",
    "epistola", "epistolae", "epistolis", "liber", "libri", "libro", "libris",
    "caput", "capitulum", "capitula", "tractatus", "sermo", "sermones",
    "decretum", "decreta", "canones", "concilium", "opera", "operum",
    "contra", "adversus", "super", "inter", "apud", "secundum", "anno",
    "incertus", "anonymus", "varii", "varia", "selecta", "explanatio",
    "quod", "quae", "sunt", "esse", "habet", "non", "nisi"
}


def strip_accents_and_normalize(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text.lower())
    clean = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    clean = clean.replace("v", "u").replace("j", "i").replace("æ", "ae").replace("œ", "oe")
    return clean


class PL183BernardIndexLocator:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._vol_col_cache: Dict[str, Dict[int, str]] = {}
        self._vol_page_cache: Dict[str, List[Tuple[int, str]]] = {}
        self._file_text_cache: Dict[str, str] = {}

    def get_file_text(self, path: str) -> str:
        if path not in self._file_text_cache:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    self._file_text_cache[path] = f.read()
            else:
                self._file_text_cache[path] = ""
        return self._file_text_cache[path]

    def get_vol_col_map(self, target_vol: str) -> Dict[int, str]:
        if target_vol in self._vol_col_cache:
            return self._vol_col_cache[target_vol]

        col_map: Dict[int, str] = {}
        files = glob.glob(f"teste/{target_vol}/text/*.txt")
        if not files:
            files = glob.glob(f"/homessddata/Projects/pdfocr/teste/{target_vol}/text/*.txt")

        for f in files:
            txt = self.get_file_text(f)
            header_match = re.search(r'<bloco[^>]*tipo="cabecalho"[^>]*>(.*?)</bloco>', txt, re.DOTALL)
            header_text = header_match.group(1) if header_match else txt[:300]
            cols = [int(m) for m in re.findall(r"\b(\d{1,4})\b", header_text)]
            for c in cols:
                if 1 <= c <= 3000 and c not in col_map:
                    col_map[c] = os.path.abspath(f)

        self._vol_col_cache[target_vol] = col_map
        return col_map

    def get_vol_pages(self, target_vol: str) -> List[Tuple[int, str]]:
        if target_vol in self._vol_page_cache:
            return self._vol_page_cache[target_vol]

        files = glob.glob(f"teste/{target_vol}/text/*.txt")
        if not files:
            files = glob.glob(f"/homessddata/Projects/pdfocr/teste/{target_vol}/text/*.txt")

        pages = []
        for f in files:
            try:
                base = os.path.basename(f)
                num = int(base.split("-")[-1].split(".")[0])
                pages.append((num, os.path.abspath(f)))
            except Exception:
                pass

        pages.sort(key=lambda x: x[0])
        self._vol_page_cache[target_vol] = pages
        return pages

    def extract_keywords(self, label: str) -> List[str]:
        clean = strip_accents_and_normalize(label)
        words = re.findall(r"\b[a-z]{4,}\b", clean)
        keywords = [w for w in words if w not in LATIN_STOPWORDS]
        return keywords

    def verify_second_hint(self, file_path: str, keywords: List[str]) -> Tuple[bool, float, str]:
        if not keywords or not file_path:
            return False, 0.0, ""
        txt = self.get_file_text(file_path)
        if not txt:
            return False, 0.0, ""
        clean_txt = strip_accents_and_normalize(txt)

        matched_words = []
        for kw in keywords:
            stem = kw[:5] if len(kw) >= 5 else kw
            if stem in clean_txt:
                matched_words.append(kw)

        if matched_words:
            ratio = len(matched_words) / len(keywords)
            score_boost = 1.0 if ratio >= 0.3 else 0.5
            return True, score_boost, f"+second_hint({','.join(matched_words[:2])})"
        return False, 0.0, ""

    def interpolate_column(self, target_vol: str, col: int) -> Optional[Tuple[str, str]]:
        col_map = self.get_vol_col_map(target_vol)
        if not col_map:
            return None

        sorted_cols = sorted(col_map.keys())
        bef = [c for c in sorted_cols if c < col]
        aft = [c for c in sorted_cols if c > col]
        if not bef or not aft:
            return None

        c_prev, c_next = bef[-1], aft[0]
        if (c_next - c_prev) > 30:
            return None

        f_prev = col_map[c_prev]
        f_next = col_map[c_next]

        try:
            p_prev = int(os.path.basename(f_prev).split("-")[-1].split(".")[0])
            p_next = int(os.path.basename(f_next).split("-")[-1].split(".")[0])
        except Exception:
            return None

        if p_next < p_prev or (p_next - p_prev) > 10:
            return None

        delta_p = p_next - p_prev
        delta_c = c_next - c_prev
        p_est = p_prev + round((col - c_prev) * delta_p / delta_c)

        pages = self.get_vol_pages(target_vol)
        for p, path in pages:
            if p == p_est:
                return path, f"gap_interpolated({c_prev}-{c_next})"

        return None

    def resolve_item(
        self,
        item_id: str,
        vol: str,
        label: str,
        ref_raw: str,
        source_file: str,
        meta_json: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if vol != "PL183":
            return None

        m_cols = [int(x) for x in re.findall(r"\b(\d{1,4})\b", ref_raw or "")]
        if not m_cols:
            m_cols = [int(x) for x in re.findall(r"\b(\d{1,4})\b", label or "")]
        if not m_cols:
            return None

        orig_col = m_cols[0]
        keywords = self.extract_keywords(label or "")

        # Route to PL182 or PL183
        if orig_col <= 1150:
            target_vol = "PL182"
            target_col = orig_col
            mapping_kind = "direct_tome_1"
        else:
            target_vol = "PL183"
            target_col = orig_col - MABILLON_OFFSET
            mapping_kind = f"mabillon_offset(-{MABILLON_OFFSET})"

        col_map = self.get_vol_col_map(target_vol)
        matched_file = None
        base_score = 7.0
        reason = ""

        # Search exact or adjacent
        for cand_c in [target_col, target_col - 1, target_col + 1, target_col - 2, target_col + 2]:
            if cand_c in col_map:
                matched_file = col_map[cand_c]
                base_score = 9.5 if cand_c == target_col else 8.8
                reason = f"col_match({cand_c})"
                break

        if not matched_file:
            bounded_col = max(1, min(1198, target_col))
            interp = self.interpolate_column(target_vol, bounded_col)
            if interp:
                matched_file, interp_reason = interp
                base_score = 8.2
                reason = interp_reason

        if not matched_file:
            return None

        # Corroborate with second hint
        has_hint, boost, hint_reason = self.verify_second_hint(matched_file, keywords)
        final_score = min(10.0, base_score + boost) if has_hint else base_score
        full_reason = f"bernard_{mapping_kind}_{reason}{hint_reason}"

        return {
            "kind": "single",
            "candidates": [{
                "file": matched_file,
                "rank": 1,
                "score": final_score,
                "target_vol": target_vol,
                "target_col": target_col,
                "reason": full_reason
            }],
            "target_vol": target_vol,
            "target_col": target_col,
            "matched_file": matched_file,
            "score": final_score,
            "reason": full_reason
        }

    def run(self, dry_run: bool = True) -> Dict[str, Any]:
        start_time = time.time()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        cur.execute(
            'SELECT id, volume, label, ref_raw, source, metadata_json FROM items WHERE volume="PL183" AND state="source_only"'
        )
        rows = cur.fetchall()

        promotions = []
        resolution_types: Dict[str, int] = {}

        for row in rows:
            item_id, vol, label, ref_raw, source_file, meta_json = row
            if not os.path.isabs(source_file):
                source_file = os.path.abspath(f"teste/{vol}/text/{os.path.basename(source_file)}")

            res = self.resolve_item(item_id, vol, label or "", ref_raw or "", source_file, meta_json)
            if res:
                promotions.append((row, res))
                kind = res["target_vol"]
                resolution_types[kind] = resolution_types.get(kind, 0) + 1

        if not dry_run and promotions:
            cur.execute("BEGIN TRANSACTION")
            for row, res in promotions:
                item_id, vol, label, ref_raw, source_file, meta_json = row

                cand = res["candidates"][0]
                cand_payload = {
                    "source_type": f"pl183_{cand['reason']}",
                    "confidence": "high" if cand["score"] >= 9.0 else "medium",
                    "target_volume": cand["target_vol"],
                    "target_column": cand["target_col"],
                    "target_file": cand["file"],
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
                    (item_id, cand["file"], cand["score"], compressed_cand),
                )

                meta = json.loads(meta_json) if meta_json else {}
                meta["state"] = "with_destinations"
                meta["target_volume"] = res["target_vol"]
                meta["target_column"] = res["target_col"]
                meta["matched_file"] = res["matched_file"]
                meta["locator"] = f"pl183_{res['reason']}"
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
            "promotions_by_target_vol": resolution_types,
            "sample_promotions": [
                {
                    "id": p[0][0],
                    "label": p[0][2],
                    "ref": p[0][3],
                    "target_vol": p[1]["target_vol"],
                    "target_col": p[1]["target_col"],
                    "matched_file": p[1]["matched_file"],
                    "score": p[1]["score"],
                    "reason": p[1]["reason"],
                }
                for p in promotions[:10]
            ],
        }


def main():
    parser = argparse.ArgumentParser(description="Locate destinations for PL183 Bernard index.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PL183BernardIndexLocator(args.db)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
