#!/usr/bin/env python3
"""Burchard of Worms Decretorum Libri XX chapter locator for PL140.

Resolves Capitula tables at the head of each Liber (files 275, 313, 337, 367, etc.)
to the corresponding chapter body (CAP. I..LVI) in that Liber's text.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
import zlib

from scripts.jev_narrow_window_verifier import roman_value

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_DB = ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
SOURCE_ROOT = ROOT / "teste"

BOOK_BOUNDARIES = [
    (1, 275), (2, 313), (3, 337), (4, 367), (5, 386), (6, 393), (7, 398),
    (8, 410), (9, 419), (10, 431), (11, 442), (12, 446), (13, 449), (14, 451),
    (15, 458), (16, 463), (17, 473), (18, 476), (19, 511), (20, 525)
]

RE_CAP = re.compile(r"\b(?:CAP\.|CAPUT)\s+([IVXLCDM]+|\d+)\b", re.IGNORECASE)


def get_seq(path_or_str: str | Path) -> int:
    m = re.search(r"-(\d+)\.txt$", str(path_or_str))
    return int(m.group(1)) if m else 0


class PL140BurchardLocator:
    """Locates Burchard's Decretum chapters for PL140."""

    def __init__(self, source_root: Path = SOURCE_ROOT):
        self.chapter_map: Dict[Tuple[int, int], str] = {}
        pl140_dir = source_root / "PL140/text"

        if not pl140_dir.exists():
            return

        all_files = sorted(pl140_dir.glob("*.txt"), key=get_seq)
        file_by_seq = {get_seq(f): f for f in all_files}

        for i in range(len(BOOK_BOUNDARIES)):
            b_num, start_seq = BOOK_BOUNDARIES[i]
            end_seq = BOOK_BOUNDARIES[i + 1][1] if i + 1 < len(BOOK_BOUNDARIES) else 550
            for s in range(start_seq, end_seq):
                if s in file_by_seq:
                    f = file_by_seq[s]
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                    for m in RE_CAP.findall(txt):
                        raw = m.upper()
                        v = roman_value(raw) if not raw.isdigit() else int(raw)
                        if v and (b_num, v) not in self.chapter_map:
                            self.chapter_map[(b_num, v)] = str(f)

    def locate(self, source_file: str, label: str, ref_raw: str) -> Optional[Tuple[str, float, str, Dict[str, Any]]]:
        src_seq = get_seq(source_file)
        b_num = 1
        for b, s in BOOK_BOUNDARIES:
            if src_seq >= s:
                b_num = b
            else:
                break

        ref_clean = (ref_raw or "").strip().upper()
        # Clean potential prefixes like 'CAP. 2'
        m_num = re.search(r"([IVXLCDM]+|\d+)", ref_clean)
        if not m_num:
            return None

        raw_val = m_num.group(1)
        ch_val = roman_value(raw_val) if not raw_val.isdigit() else int(raw_val)

        if ch_val and (b_num, ch_val) in self.chapter_map:
            fpath = self.chapter_map[(b_num, ch_val)]
            return (
                fpath,
                9.8,
                "pl140_burchard_chapter",
                {
                    "liber": b_num,
                    "caput": ch_val,
                    "raw_ref": ref_raw,
                },
            )
        return None


def resolve_pl140_chapters(
    catalog_db_path: Path = DEFAULT_CATALOG_DB,
    source_root: Path = SOURCE_ROOT,
    apply: bool = False,
) -> Dict[str, Any]:
    t0 = time.time()
    conn = sqlite3.connect(str(catalog_db_path))
    cur = conn.cursor()

    locator = PL140BurchardLocator(source_root)

    cur.execute(
        """
        SELECT id, label, ref_raw, source
        FROM items
        WHERE volume = 'PL140' AND state = 'source_only'
        """
    )
    items = cur.fetchall()

    total_promoted = 0
    samples: List[Dict[str, Any]] = []

    for iid, lbl, ref, src in items:
        res = locator.locate(src, lbl, ref)
        if not res:
            continue

        target_file, score, method, extra = res
        total_promoted += 1

        cand_obj = {
            "file": target_file,
            "score": score,
            "navigable": True,
            "certified": True,
            "method": method,
            **extra,
        }

        if len(samples) < 25:
            samples.append({"volume": "PL140", "label": lbl, "ref_raw": ref, **cand_obj})

        if apply:
            cur.execute("UPDATE items SET state = 'with_destinations' WHERE id = ?", (iid,))
            cur.execute(
                "UPDATE residual_embedding_queue SET status = 'confirmed', candidate_files_json = ? WHERE item_id = ?",
                (json.dumps([target_file]), iid),
            )
            blob = zlib.compress(json.dumps(cand_obj).encode("utf-8"))
            cur.execute(
                "INSERT OR REPLACE INTO candidate_storage (item_id, file, rank, score, navigable, candidate_json) VALUES (?, ?, 1, ?, 1, ?)",
                (iid, target_file, score, blob),
            )

    if apply:
        conn.commit()
        summary_path = catalog_db_path.parent / "catalog_summary.json"
        if summary_path.exists():
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                cur.execute("SELECT count(*) FROM items WHERE state = 'with_destinations'")
                sdata["items_with_destinations"] = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM residual_embedding_queue WHERE status = 'pending'")
                sdata["residual_queue_count"] = cur.fetchone()[0]
                if sdata.get("total_items", 0) > 0:
                    sdata["destination_coverage"] = round(sdata["items_with_destinations"] / sdata["total_items"], 4)
                with open(summary_path, "w", encoding="utf-8") as f:
                    json.dump(sdata, f, indent=2)
            except Exception:
                pass

    conn.close()
    duration = time.time() - t0

    return {
        "status": "success",
        "dry_run": not apply,
        "total_promoted": total_promoted,
        "duration_seconds": round(duration, 2),
        "sample_promotions": samples,
    }


def main():
    parser = argparse.ArgumentParser(description="PL140 Burchard Decretum Chapter Locator.")
    parser.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    res = resolve_pl140_chapters(
        catalog_db_path=args.catalog_db,
        source_root=args.source_root,
        apply=args.apply,
    )
    print(json.dumps({k: v for k, v in res.items() if k != "sample_promotions"}, indent=2))
    print("\nSample promotions:")
    for s in res.get("sample_promotions", []):
        print(f"  • [{s['extra']['liber'] if 'extra' in s else '?'}] \"{s['label'][:40]}\" (ref: {s['ref_raw']}) -> {s['method']}")
        print(f"    Target file: {Path(s['file']).name} (score: {s['score']})")


if __name__ == "__main__":
    main()
