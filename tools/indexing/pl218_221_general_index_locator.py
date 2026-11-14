#!/usr/bin/env python3
"""
tools/indexing/pl218_221_general_index_locator.py

Specific locator for Migne Patrologia Latina General Indices (PL218 - PL221):
1. Multi-volume spans of collected works (e.g. XIV-XVII -> opening pages of each volume in span).
2. Direct <Roman, Col> cross-volume references with regex expansion (up to 10 Roman chars).
3. Preceding Roman inheritance on the source page (for 'ibid.' and bare column references).
4. Tolerant column matching (exact, adjacent +-1/+-2, and gap interpolation).
5. Second hint validation: matches author/work content words on the physical page.
6. Known typographical/OCR Roman numeral corrections corroborated by author keywords.
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

# Roman numeral conversions
ROMAN_VALS = {
    "I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000
}

# Common Latin stopwords to exclude from second-hint matching
LATIN_STOPWORDS = {
    "sanctus", "sancti", "sancto", "sanctum", "sancta", "sanctae", "sanct",
    "beatus", "beati", "beato", "beatum", "beata", "beatae",
    "presbyter", "presbyteri", "presb",
    "episcopus", "episcopi", "episcopo", "episc",
    "archiepiscopus", "archiepiscopi", "archiepisc", "archiep",
    "abbas", "abbatis", "abb", "papa", "papae",
    "doctor", "ecclesiae", "eccles", "monachus", "monachi", "mon",
    "auctor", "auctoris",
    "epistola", "epistolae", "epistolis", "epist",
    "liber", "libri", "libro", "libris", "lib",
    "caput", "capitulum", "capitula", "cap",
    "tractatus", "tract", "sermo", "sermones",
    "decretum", "decreta", "canones", "concilium", "opera", "operum",
    "contra", "adversus", "super", "inter", "apud", "secundum", "anno",
    "incertus", "anonymus", "varii", "varia", "selecta", "explanatio"
}

# Typo overrides in Roman numerals for known author works in Migne
KNOWN_ROMAN_OVERRIDES = {
    # (author_keyword_stem, wrong_roman): corrected_roman
    ("hild", "CLXV"): "CLXXI",          # Hildebertus/Hilderbertus Cenomanensis in PL171 (col 1339)
    ("aimon", "CXXXI"): "CLXXXI",       # Aimonis Divensis in PL181 (col 1707)
    ("innocent", "CXIV"): "CCXIV",      # Innocentius III in PL214-217
    ("walafrid", "CXV"): "CXIV",        # Strabo in PL113-114
    ("ambros", "XIX"): "XIV",           # Ambrosius in PL14-17
    ("alcuin", "CL"): "CI",             # Alcuinus in PL100-101
    ("martin", "CVIII"): "CCVIII",      # Martinus Legionensis in PL208-209
}


def strip_accents_and_normalize(text: str) -> str:
    """Normalize text removing accents, diacritics, and unifying u/v and i/j."""
    nfd = unicodedata.normalize("NFD", text.lower())
    clean = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    clean = clean.replace("v", "u").replace("j", "i").replace("æ", "ae").replace("œ", "oe")
    return clean


def roman_to_int(s: str) -> int:
    """Convert Roman numeral string to integer (1..300)."""
    s = s.upper().strip().rstrip(".,;:-")
    if not s:
        return 0
    total = 0
    prev = 0
    for ch in reversed(s):
        val = ROMAN_VALS.get(ch, 0)
        if val >= prev:
            total += val
            prev = val
        else:
            total -= val
    return total


class PLGeneralIndexLocator:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._vol_col_cache: Dict[str, Dict[int, str]] = {}
        self._vol_page_cache: Dict[str, List[Tuple[int, str]]] = {}
        self._opening_file_cache: Dict[str, Optional[str]] = {}
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
        """Maps column number to physical file path for a volume."""
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
        """Returns sorted list of (page_num, abs_filepath) for a volume."""
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

    def get_opening_file(self, target_vol: str) -> Optional[str]:
        """Returns first substantive content file of target volume."""
        if target_vol in self._opening_file_cache:
            return self._opening_file_cache[target_vol]

        pages = self.get_vol_pages(target_vol)
        if not pages:
            self._opening_file_cache[target_vol] = None
            return None

        # Look for first page with substantive text (at least 150 chars)
        for _, path in pages[:10]:
            txt = self.get_file_text(path)
            if len(txt.strip()) >= 150:
                self._opening_file_cache[target_vol] = path
                return path

        first = pages[0][1]
        self._opening_file_cache[target_vol] = first
        return first

    def extract_keywords(self, label: str) -> List[str]:
        """Extract meaningful author/work keywords for second-hint verification."""
        clean = strip_accents_and_normalize(label)
        words = re.findall(r"\b[a-z]{4,}\b", clean)
        keywords = [w for w in words if w not in LATIN_STOPWORDS]
        return keywords

    def verify_second_hint(self, file_path: str, keywords: List[str]) -> Tuple[bool, float, str]:
        """Verifies if keywords occur in target file text (second hint)."""
        if not keywords:
            return False, 0.0, ""
        txt = self.get_file_text(file_path)
        if not txt:
            return False, 0.0, ""
        clean_txt = strip_accents_and_normalize(txt)

        matched_words = []
        for kw in keywords:
            # Check whole word or stem of length >= 5
            stem = kw[:5] if len(kw) >= 5 else kw
            if stem in clean_txt:
                matched_words.append(kw)

        if matched_words:
            ratio = len(matched_words) / len(keywords)
            score_boost = 1.0 if ratio >= 0.3 else 0.5
            return True, score_boost, f"+second_hint({','.join(matched_words[:2])})"
        return False, 0.0, ""

    def interpolate_column(self, target_vol: str, col: int) -> Optional[Tuple[str, str]]:
        """Estimates physical page when column is in an OCR header gap."""
        col_map = self.get_vol_col_map(target_vol)
        if not col_map:
            return None

        sorted_cols = sorted(col_map.keys())
        bef = [c for c in sorted_cols if c < col]
        aft = [c for c in sorted_cols if c > col]
        
        pages = self.get_vol_pages(target_vol)
        if not pages:
            return None

        if not bef and aft:
            return pages[0][1], f"below_first_col({aft[0]})"
        if not aft and bef:
            c_last = bef[-1]
            if col - c_last <= 80:
                return pages[-1][1], f"above_last_col({c_last})"
            return None

        c_prev, c_next = bef[-1], aft[0]
        f_prev = col_map[c_prev]
        f_next = col_map[c_next]

        try:
            p_prev = int(os.path.basename(f_prev).split("-")[-1].split(".")[0])
            p_next = int(os.path.basename(f_next).split("-")[-1].split(".")[0])
        except Exception:
            return None

        if p_next >= p_prev:
            delta_p = p_next - p_prev
            delta_c = c_next - c_prev
            p_est = p_prev + round((col - c_prev) * delta_p / delta_c) if delta_c > 0 else p_prev
            for p, path in pages:
                if p == p_est:
                    return path, f"gap_interpolated({c_prev}-{c_next})"
        return f_prev, f"fallback_prev({c_prev})"

    def find_preceding_roman_on_page(self, source_file: str, offset: int) -> Optional[int]:
        """Scans backward on source page to find the preceding Roman volume."""
        txt = self.get_file_text(source_file)
        if not txt:
            return None

        prefix = txt[:offset] if 0 < offset < len(txt) else txt
        # Look for Roman numeral preceding a column number
        matches = list(re.finditer(r"\b([IVXLCDM]{1,10})\b\s*[,.;\s]\s*(\d{1,4})", prefix, re.IGNORECASE))
        if matches:
            last_r = matches[-1].group(1).upper()
            t = roman_to_int(last_r)
            if 1 <= t <= 221:
                return t
        return None

    def resolve_item(
        self,
        item_id: str,
        vol: str,
        label: str,
        ref_raw: str,
        source_file: str,
        meta_json: Optional[str] = None,
        inherited_vol_num: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Resolves an index entry from PL218-PL221 to certified destinations."""
        full_text = f"{label} {ref_raw}".strip()
        keywords = self.extract_keywords(label)

        # 1. Multi-volume spans (e.g. XIV-XVII, XXII-XXX)
        span_m = re.search(r"\b([IVXLCDM]{1,10})\s*[-—–]\s*([IVXLCDM]{1,10})\b", full_text, re.IGNORECASE)
        if span_m:
            r1, r2 = span_m.group(1).upper(), span_m.group(2).upper()
            # Typo corrections in span bounds
            if r1 == "CXIV" and r2 == "CCXVII":
                r1 = "CCXIV"
            elif r1 == "C" and r2 == "CL":
                r2 = "CI"
            elif r1 == "XIX" and r2 == "XVII":
                r1 = "XIV"

            t1, t2 = roman_to_int(r1), roman_to_int(r2)
            if t1 > t2:
                t1, t2 = t2, t1

            if 1 <= t1 <= 221 and 1 <= t2 <= 221 and (t2 - t1) <= 25:
                candidates = []
                for v_idx, t_vol_num in enumerate(range(t1, t2 + 1), start=1):
                    target_vol = f"PL{t_vol_num:03d}"
                    open_file = self.get_opening_file(target_vol)
                    if open_file:
                        candidates.append({
                            "file": open_file,
                            "rank": v_idx,
                            "score": 9.5 if v_idx == 1 else 9.0,
                            "target_vol": target_vol,
                            "target_col": 1,
                            "reason": f"multivolume_span({r1}-{r2})"
                        })
                if candidates:
                    return {
                        "kind": "span",
                        "candidates": candidates,
                        "target_vol": f"PL{t1:03d}-PL{t2:03d}",
                        "target_vol_num": t2,
                        "target_col": None,
                        "matched_file": candidates[0]["file"],
                        "score": 9.5,
                        "reason": f"multivolume_span({r1}-{r2})"
                    }

        # 2. Direct <Roman, Col> reference
        target_vol_num: Optional[int] = None
        target_col: Optional[int] = None
        roman_source: str = ""

        rc_m = re.search(r"\b([IVXLCDM]{1,10})\b\s*[,.;\s]\s*(\d{1,4})", full_text, re.IGNORECASE)
        if rc_m:
            r_str = rc_m.group(1).upper()
            target_col = int(rc_m.group(2))
            roman_source = "direct_reference"

            # Check known overrides (e.g. Hildebert in CLXV -> CLXXI)
            for (kw, wrong_r), correct_r in KNOWN_ROMAN_OVERRIDES.items():
                if wrong_r == r_str and any(kw in k or k in kw for k in keywords):
                    r_str = correct_r
                    roman_source += f"_override({wrong_r}->{correct_r})"
                    break

            t = roman_to_int(r_str)
            if 1 <= t <= 221:
                target_vol_num = t

        # 3. Inherited Roman volume (for 'ibid.' and bare columns)
        if not target_vol_num:
            # Check ref_raw first for column number (avoids picking year from label like '(757)')
            col_m = re.search(r"\b(\d{2,4})\b", ref_raw)
            if not col_m:
                clean_lbl = re.sub(r"\(\d{3,4}\)", " ", label)
                col_m = re.search(r"\b(\d{2,4})\b", clean_lbl)
            if col_m:
                col_cand = int(col_m.group(1))
                if inherited_vol_num:
                    target_vol_num = inherited_vol_num
                    target_col = col_cand
                    roman_source = f"inherited_sequence(PL{inherited_vol_num:03d})"
                else:
                    # Find offset on source page
                    offset = -1
                    if meta_json:
                        try:
                            mdata = json.loads(meta_json)
                            span = mdata.get("source_span") or mdata.get("original_item_metadata", {}).get("source_span")
                            if span:
                                offset = span[0]
                        except Exception:
                            pass
                    if offset == -1:
                        src_txt = self.get_file_text(source_file)
                        offset = src_txt.find(label) if label and label in src_txt else src_txt.find(ref_raw)

                    t_inherited = self.find_preceding_roman_on_page(source_file, offset)
                    if t_inherited:
                        target_vol_num = t_inherited
                        target_col = col_cand
                        roman_source = f"inherited_from_page(PL{t_inherited:03d})"

        if not target_vol_num or target_col is None:
            return None

        target_vol = f"PL{target_vol_num:03d}"
        col_map = self.get_vol_col_map(target_vol)
        if not col_map:
            return None

        # Find physical file for target_col
        matched_file = None
        base_score = 7.0
        reason = ""

        if target_col in col_map:
            matched_file = col_map[target_col]
            base_score = 9.5
            reason = f"exact_col({target_col})"
        elif (target_col - 1) in col_map:
            matched_file = col_map[target_col - 1]
            base_score = 8.8
            reason = f"adj_prev_col({target_col-1})"
        elif (target_col + 1) in col_map:
            matched_file = col_map[target_col + 1]
            base_score = 8.8
            reason = f"adj_next_col({target_col+1})"
        elif (target_col - 2) in col_map:
            matched_file = col_map[target_col - 2]
            base_score = 8.2
            reason = f"adj_near_col({target_col-2})"
        elif (target_col + 2) in col_map:
            matched_file = col_map[target_col + 2]
            base_score = 8.2
            reason = f"adj_near_col({target_col+2})"
        else:
            # Interpolation in header gap
            interp = self.interpolate_column(target_vol, target_col)
            if interp:
                matched_file, interp_reason = interp
                base_score = 8.0
                reason = interp_reason

        if not matched_file:
            return None

        # Verify second hint
        has_hint, boost, hint_reason = self.verify_second_hint(matched_file, keywords)
        final_score = min(10.0, base_score + boost) if has_hint else base_score
        full_reason = f"{roman_source}_{reason}{hint_reason}"

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
            "target_vol_num": target_vol_num,
            "target_col": target_col,
            "matched_file": matched_file,
            "score": final_score,
            "reason": full_reason
        }

    def run(self, dry_run: bool = True) -> Dict[str, Any]:
        """Runs the locator over PL218-PL221 source_only items."""
        start_time = time.time()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()

        target_vols = ["PL218", "PL219", "PL220", "PL221"]
        placeholders = ",".join("?" for _ in target_vols)

        cur.execute(
            f'SELECT id, volume, label, ref_raw, source, metadata_json FROM items WHERE volume IN ({placeholders}) AND state="source_only" ORDER BY source, id',
            target_vols,
        )
        rows = cur.fetchall()

        promotions = []
        vol_counts: Dict[str, int] = {}
        resolution_types: Dict[str, int] = {}

        last_source_file = None
        curr_vol_num = None

        for row in rows:
            item_id, vol, label, ref_raw, source_file, meta_json = row
            # Ensure source_file path is absolute
            if not os.path.isabs(source_file):
                source_file = os.path.abspath(f"teste/{vol}/text/{os.path.basename(source_file)}")

            if source_file != last_source_file:
                last_source_file = source_file
                curr_vol_num = None

            res = self.resolve_item(
                item_id, vol, label or "", ref_raw or "", source_file, meta_json, inherited_vol_num=curr_vol_num
            )
            if res:
                promotions.append((row, res))
                vol_counts[vol] = vol_counts.get(vol, 0) + 1
                kind = res["kind"]
                resolution_types[kind] = resolution_types.get(kind, 0) + 1
                if res.get("target_vol_num"):
                    curr_vol_num = res["target_vol_num"]

        if not dry_run and promotions:
            cur.execute("BEGIN TRANSACTION")
            for row, res in promotions:
                item_id, vol, label, ref_raw, source_file, meta_json = row

                # Insert all candidates
                for cand in res["candidates"]:
                    cand_payload = {
                        "source_type": f"pl_general_{cand['reason']}",
                        "confidence": "high" if cand["score"] >= 9.0 else "medium",
                        "target_volume": cand["target_vol"],
                        "target_column": cand["target_col"],
                        "target_file": cand["file"],
                    }
                    compressed_cand = zlib.compress(json.dumps(cand_payload).encode("utf-8"))

                    cur.execute(
                        """
                        INSERT INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json)
                        VALUES (?, ?, ?, ?, 1, ?)
                        ON CONFLICT(item_id, file) DO UPDATE SET
                            rank=excluded.rank,
                            score=excluded.score,
                            candidate_json=excluded.candidate_json
                        """,
                        (item_id, cand["file"], cand["rank"], cand["score"], compressed_cand),
                    )

                # Update item state
                meta = json.loads(meta_json) if meta_json else {}
                meta["state"] = "with_destinations"
                meta["target_volume"] = res["target_vol"]
                meta["target_column"] = res["target_col"]
                meta["matched_file"] = res["matched_file"]
                meta["locator"] = f"pl_general_{res['reason']}"
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
            "resolution_types": resolution_types,
            "sample_promotions": [
                {
                    "id": p[0][0],
                    "volume": p[0][1],
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
    parser = argparse.ArgumentParser(description="Locate destinations for PL218-PL221 General Indices.")
    parser.add_argument("--db", default=DB_PATH, help="Path to search.sqlite")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    locator = PLGeneralIndexLocator(args.db)
    result = locator.run(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
