#!/usr/bin/env python3
"""Scripture Citation Index Connector.

Bridges the onomastic catalog (search.sqlite) with the deterministic
scripture citation database (scripture_citations.db) and volume fascicles to resolve
Scripture citation index entries (e.g. PL142, PO012, PO015, PO019).
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
import unicodedata
import zlib

from scripts.jev_narrow_window_verifier import roman_value
from tools.scripture.book_catalog import canonical_book_key

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_DB = ROOT / "data/index_catalog/unified_onomastic_catalog_20261110/search.sqlite"
DEFAULT_SCRIPTURE_DB = ROOT / "data/scripture_citations.db"

RE_PSALM_LABEL = re.compile(
    r"\b(?:psal[m\.]*|psa\.|ps\.)\s*([ivxlcdm]+|\d+)\b",
    re.IGNORECASE,
)
RE_GENERIC_SCRIPTURE = re.compile(
    r"^(.*?)\s+([IVXLCDM]+|\d+)(?:\s+([\d\-,\s\.]+))?$",
    re.IGNORECASE,
)


def norm_book_text(s: str) -> str:
    """Normalize book string removing accents and parenthetical qualifiers."""
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\(.*?\)", "", t)
    return t.strip()


class PL142PsalmCommentaryLocator:
    """Locates Psalm citations in PL142 (Bruno Herbipolensis, Expositio Psalmorum)."""

    def __init__(self, scripture_db_path: Path):
        self.psalm_file_map: Dict[int, str] = {}
        if scripture_db_path.exists():
            con = sqlite3.connect(f"file:{scripture_db_path}?mode=ro", uri=True)
            cur = con.cursor()
            cur.execute(
                """
                SELECT o.chapter_start, f.file_path
                FROM citation_occurrences o
                JOIN citation_groups g ON o.group_id = g.group_id
                JOIN citation_files f ON g.file_id = f.file_id
                WHERE f.volume_id = 'PL142' AND o.book_key = 'salmos' AND f.is_index_source = 0
                GROUP BY o.chapter_start
                ORDER BY o.chapter_start
                """
            )
            self.psalm_file_map = dict(cur.fetchall())
            con.close()
        self.last_psalm: Optional[int] = None

    def locate(self, label: str, ref_raw: str) -> Optional[Tuple[str, float, str, Dict[str, Any]]]:
        comb = f"{label} {ref_raw}"
        m = RE_PSALM_LABEL.search(comb)
        target_psalm: Optional[int] = None

        if m:
            raw_val = m.group(1).upper()
            val = roman_value(raw_val) if not raw_val.isdigit() else int(raw_val)
            if val and 1 <= val <= 150:
                target_psalm = val
                self.last_psalm = val
        elif "ibid" in (ref_raw or "").lower() or "ibid" in (label or "").lower():
            target_psalm = self.last_psalm
        elif self.last_psalm is not None and re.match(r"^(?:vers\b|\d+$)", (label or "").strip(), re.I):
            target_psalm = self.last_psalm

        if target_psalm and target_psalm in self.psalm_file_map:
            fpath = self.psalm_file_map[target_psalm]
            return (
                fpath,
                9.8,
                "scripture_psalm_commentary",
                {"psalm": target_psalm, "book_key": "salmos"},
            )
        return None


class PO015FascicleScriptureLocator:
    """Locates Scripture entries in PO015 (Les Trophées de Damas, Table des citations bibliques)."""

    def __init__(self, volume_dir: Path):
        self.volume_dir = volume_dir
        self.files_by_seq = {}
        if volume_dir.exists():
            for f in volume_dir.glob("*.txt"):
                m = re.search(r"-(\d+)\.txt$", f.name)
                if m:
                    self.files_by_seq[int(m.group(1))] = f

    def locate(self, label: str, ref_raw: str) -> Optional[Tuple[str, float, str, Dict[str, Any]]]:
        # Ref raw contains e.g. '79, 5 s.', '23, 3 s.', '32, 4'
        m = re.match(r"^\s*(\d+)", ref_raw or "")
        if not m:
            return None
        page = int(m.group(1))
        # Fascicle mapping for Les Trophées de Damas: File_Seq = 178 + page
        target_seq = 178 + page
        if target_seq in self.files_by_seq:
            fpath = str(self.files_by_seq[target_seq])
            return (
                fpath,
                9.8,
                "po_fascicle_scripture_page",
                {"fascicle_page": page, "raw_citation": label},
            )
        return None


class PO019LogiaScriptureLocator:
    """Locates citations to Logia et Agrapha Domini Jesu in PO019 and PO013.

    In PO019 (Index locorum, file 626), the note specifies:
    'Numeri non paginas, sed logiorum ordinem, significant.'
    Logia 1-103 are published in PO013 (Fasc. III, pp. 335-440).
    Logia 104-235 are published in PO019 (Fasc. IV, pp. 531-625).
    """

    def __init__(self, source_root: Path):
        self.logia_map: Dict[int, str] = {}
        po13_dir = source_root / "PO013/text"
        po19_dir = source_root / "PO019/text"

        re_log_13 = re.compile(r"(\b\d{1,3})\s*[°º\.]\s*—")
        if po13_dir.exists():
            for f in sorted(po13_dir.glob("*.txt")):
                txt = f.read_text(encoding="utf-8", errors="ignore")
                for m in re_log_13.findall(txt):
                    n = int(m)
                    if 1 <= n <= 103 and n not in self.logia_map:
                        self.logia_map[n] = str(f)

        if po19_dir.exists():
            for f in sorted(po19_dir.glob("*.txt")):
                seq_m = re.search(r"-(\d+)\.txt$", f.name)
                if seq_m and 550 <= int(seq_m.group(1)) <= 625:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                    for m in re.findall(r"\b([12]\d\d)\s*[\.=\-—]", txt):
                        n = int(m)
                        if 104 <= n <= 235 and n not in self.logia_map:
                            self.logia_map[n] = str(f)

            for n, fn in [
                (163, "ced07bbc-e86b-4202-b8d2-87016ccf3630-585.txt"),
                (164, "ced07bbc-e86b-4202-b8d2-87016ccf3630-586.txt"),
                (219, "ced07bbc-e86b-4202-b8d2-87016ccf3630-613.txt"),
                (226, "ced07bbc-e86b-4202-b8d2-87016ccf3630-618.txt"),
                (227, "ced07bbc-e86b-4202-b8d2-87016ccf3630-619.txt"),
                (228, "ced07bbc-e86b-4202-b8d2-87016ccf3630-620.txt"),
                (229, "ced07bbc-e86b-4202-b8d2-87016ccf3630-621.txt"),
                (230, "ced07bbc-e86b-4202-b8d2-87016ccf3630-621.txt"),
                (231, "ced07bbc-e86b-4202-b8d2-87016ccf3630-621.txt"),
                (232, "ced07bbc-e86b-4202-b8d2-87016ccf3630-622.txt"),
                (233, "ced07bbc-e86b-4202-b8d2-87016ccf3630-622.txt"),
                (234, "ced07bbc-e86b-4202-b8d2-87016ccf3630-623.txt"),
                (235, "ced07bbc-e86b-4202-b8d2-87016ccf3630-623.txt"),
            ]:
                fp = po19_dir / fn
                if fp.exists():
                    self.logia_map[n] = str(fp)

    def locate(self, label: str, ref_raw: str) -> Optional[Tuple[str, float, str, Dict[str, Any]]]:
        nums = [int(x) for x in re.findall(r"\b\d+\b", ref_raw or "")]
        if not nums:
            return None
        target_logion = nums[0]
        if target_logion in self.logia_map:
            fpath = self.logia_map[target_logion]
            vol_target = "PO013" if target_logion <= 103 else "PO019"
            return (
                fpath,
                9.8,
                f"po_logia_agrapha_citation_{vol_target.lower()}",
                {
                    "logion_number": target_logion,
                    "target_volume": vol_target,
                    "raw_ref": ref_raw,
                },
            )
        return None


class CorpusScriptureOccurrenceLocator:
    """Matches scripture citations (book, chapter, verse) to physical files via scripture_citations.db."""

    def __init__(self, scripture_db_path: Path):
        self.scripture_db_path = scripture_db_path
        self._con: Optional[sqlite3.Connection] = None
        if scripture_db_path.exists():
            self._con = sqlite3.connect(f"file:{scripture_db_path}?mode=ro", uri=True)

    def locate(self, volume: str, label: str, ref_raw: str) -> Optional[Tuple[str, float, str, Dict[str, Any]]]:
        if not self._con:
            return None

        m = RE_GENERIC_SCRIPTURE.match((label or "").strip())
        if not m:
            return None

        b_raw, ch_raw, vs_raw = m.groups()
        b_key = canonical_book_key(norm_book_text(b_raw))
        ch_num = roman_value(ch_raw.upper()) if not ch_raw.isdigit() else int(ch_raw)

        if not b_key or not ch_num:
            return None

        cur = self._con.cursor()
        cur.execute(
            """
            SELECT f.file_path, count(*) as cnt
            FROM citation_occurrences o
            JOIN citation_groups g ON o.group_id = g.group_id
            JOIN citation_files f ON g.file_id = f.file_id
            WHERE f.volume_id = ? AND o.book_key = ? AND o.chapter_start = ? AND f.is_index_source = 0
            GROUP BY f.file_path
            ORDER BY cnt DESC
            LIMIT 1
            """,
            (volume, b_key, ch_num),
        )
        row = cur.fetchone()
        if row:
            fpath = row[0]
            return (
                fpath,
                9.5,
                "scripture_corpus_occurrence",
                {
                    "book_key": b_key,
                    "chapter_start": ch_num,
                    "verse_raw": vs_raw,
                    "occurrence_count": row[1],
                },
            )
        return None

    def close(self) -> None:
        if self._con:
            self._con.close()
            self._con = None


def resolve_scripture_citations(
    catalog_db_path: Path = DEFAULT_CATALOG_DB,
    scripture_db_path: Path = DEFAULT_SCRIPTURE_DB,
    source_root: Path = ROOT / "teste",
    target_volumes: Optional[List[str]] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    t0 = time.time()
    conn = sqlite3.connect(str(catalog_db_path))
    cur = conn.cursor()

    if target_volumes is None:
        target_volumes = ["PL142", "PO015", "PO012", "PO019"]

    pl142_loc = PL142PsalmCommentaryLocator(scripture_db_path)
    po015_loc = PO015FascicleScriptureLocator(source_root / "PO015/text")
    po019_loc = PO019LogiaScriptureLocator(source_root)
    corpus_loc = CorpusScriptureOccurrenceLocator(scripture_db_path)

    total_promoted = 0
    promoted_by_vol = defaultdict(int)
    samples: List[Dict[str, Any]] = []

    for vol in target_volumes:
        cur.execute(
            """
            SELECT id, label, ref_raw, source
            FROM items
            WHERE volume = ? AND state = 'source_only'
            """,
            (vol,),
        )
        items = cur.fetchall()
        if not items:
            continue

        for iid, lbl, ref, src in items:
            res: Optional[Tuple[str, float, str, Dict[str, Any]]] = None

            if vol == "PL142":
                res = pl142_loc.locate(lbl, ref)
            elif vol == "PO015":
                res = po015_loc.locate(lbl, ref)
            elif vol == "PO019":
                res = po019_loc.locate(lbl, ref)
                if not res:
                    res = corpus_loc.locate(vol, lbl, ref)
            else:
                res = corpus_loc.locate(vol, lbl, ref)

            if not res:
                continue

            target_file, score, method, extra = res
            total_promoted += 1
            promoted_by_vol[vol] += 1

            cand_obj = {
                "file": target_file,
                "score": score,
                "navigable": True,
                "certified": True,
                "method": method,
                **extra,
            }

            if len(samples) < 25:
                samples.append({"volume": vol, "label": lbl, "ref_raw": ref, **cand_obj})

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

    corpus_loc.close()
    conn.close()
    duration = time.time() - t0

    return {
        "status": "success",
        "dry_run": not apply,
        "total_promoted": total_promoted,
        "promoted_by_vol": dict(promoted_by_vol),
        "duration_seconds": round(duration, 2),
        "sample_promotions": samples,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Scripture Citation Index Connector for Onomastic Catalog."
    )
    parser.add_argument(
        "--catalog-db",
        type=Path,
        default=DEFAULT_CATALOG_DB,
        help="Path to search.sqlite catalog database.",
    )
    parser.add_argument(
        "--scripture-db",
        type=Path,
        default=DEFAULT_SCRIPTURE_DB,
        help="Path to scripture_citations.db database.",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT / "teste",
        help="Path to OCR text files root directory.",
    )
    parser.add_argument(
        "--volumes",
        nargs="+",
        default=["PL142", "PO015", "PO012", "PO019"],
        help="Target volumes to process (default: PL142 PO015 PO012 PO019).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply confirmed destinations atomically to database.",
    )
    args = parser.parse_args()

    res = resolve_scripture_citations(
        catalog_db_path=args.catalog_db,
        scripture_db_path=args.scripture_db,
        source_root=args.source_root,
        target_volumes=args.volumes,
        apply=args.apply,
    )
    print(json.dumps({k: v for k, v in res.items() if k != "sample_promotions"}, indent=2))
    print("\nSample promotions:")
    for s in res.get("sample_promotions", []):
        print(f"  • [{s['volume']}] \"{s['label'][:40]}\" (ref: {s['ref_raw']}) -> {s['method']}")
        print(f"    Target file: {Path(s['file']).name} (score: {s['score']})")


if __name__ == "__main__":
    main()
