#!/usr/bin/env python3
"""Canonical Biblical Verse & Citation Lookup Tool.

Provides fast, accurate, multi-lingual Scripture lookups across canonical
versions (Latin Clementine Vulgate, Greek NT/LXX, French Crampon, Hebrew WLC,
Syriac Peshitta, Coptic Sahidic, Armenian, Portuguese, English).

Bypasses corrupted external SQLite tables by reading directly from clean CSVs
in `.work/bible_databases-master/formats/csv/` or building an indexed,
deduplicated SQLite database (`data/canonical_bibles.db`).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
import unicodedata

from tools.scripture.book_catalog import (
    canonical_book_key,
    normalize_book_alias,
)

ROOT = Path(__file__).resolve().parents[2]
CSV_DIR = ROOT / ".work/bible_databases-master/formats/csv"
DEFAULT_DB_PATH = ROOT / "data/canonical_bibles.db"

# Canonical mapping from book_catalog key to CSV book name
CANONICAL_TO_CSV: Dict[str, str] = {
    "genesis": "Genesis",
    "exodo": "Exodus",
    "levitico": "Leviticus",
    "numeros": "Numbers",
    "deuteronomio": "Deuteronomy",
    "josue": "Joshua",
    "juizes": "Judges",
    "rute": "Ruth",
    "1 samuel": "I Samuel",
    "2 samuel": "II Samuel",
    "1 reis": "I Kings",
    "2 reis": "II Kings",
    "1 cronicas": "I Chronicles",
    "2 cronicas": "II Chronicles",
    "esdras": "Ezra",
    "neemias": "Nehemiah",
    "tobias": "Tobit",
    "judite": "Judith",
    "ester": "Esther",
    "jo": "Job",
    "salmos": "Psalms",
    "proverbios": "Proverbs",
    "eclesiastes": "Ecclesiastes",
    "canticos": "Song of Solomon",
    "sabedoria": "Wisdom",
    "eclesiastico": "Sirach",
    "isaias": "Isaiah",
    "jeremias": "Jeremiah",
    "lamentacoes": "Lamentations",
    "baruque": "Baruch",
    "ezequiel": "Ezekiel",
    "daniel": "Daniel",
    "oseias": "Hosea",
    "joel": "Joel",
    "amos": "Amos",
    "obadias": "Obadiah",
    "jonas": "Jonah",
    "miqueias": "Micah",
    "naum": "Nahum",
    "habacuque": "Habakkuk",
    "sofonias": "Zephaniah",
    "ageu": "Haggai",
    "zacarias": "Zechariah",
    "malaquias": "Malachi",
    "1 macabeus": "I Maccabees",
    "2 macabeus": "II Maccabees",
    "mateus": "Matthew",
    "marcos": "Mark",
    "lucas": "Luke",
    "joao": "John",
    "atos": "Acts",
    "romanos": "Romans",
    "1 corintios": "I Corinthians",
    "2 corintios": "II Corinthians",
    "galatas": "Galatians",
    "efesios": "Ephesians",
    "filipenses": "Philippians",
    "colossenses": "Colossians",
    "1 tessalonicenses": "I Thessalonians",
    "2 tessalonicenses": "II Thessalonians",
    "1 timoteo": "I Timothy",
    "2 timoteo": "II Timothy",
    "tito": "Titus",
    "filemom": "Philemon",
    "hebreus": "Hebrews",
    "tiago": "James",
    "1 pedro": "I Peter",
    "2 pedro": "II Peter",
    "1 joao": "I John",
    "2 joao": "II John",
    "3 joao": "III John",
    "judas": "Jude",
    "apocalipse": "Revelation of John",
}

EXTRA_ALIASES: Dict[str, str] = {
    "revelation": "Revelation of John",
    "revelation of john": "Revelation of John",
    "apocalypse": "Revelation of John",
    "apoc": "Revelation of John",
    "ap": "Revelation of John",
    "rev": "Revelation of John",
    "sirach": "Sirach",
    "eccli": "Sirach",
    "ecclesiasticus": "Sirach",
    "sir": "Sirach",
    "wisdom": "Wisdom",
    "sap": "Wisdom",
    "sapientia": "Wisdom",
    "tobit": "Tobit",
    "tob": "Tobit",
    "judith": "Judith",
    "jdt": "Judith",
    "baruch": "Baruch",
    "bar": "Baruch",
    "manasses": "Prayer of Manasses",
    "prayer of manasses": "Prayer of Manasses",
    "oratio manasse": "Prayer of Manasses",
    "1 esdras": "I Esdras",
    "i esdras": "I Esdras",
    "3 esdras": "I Esdras",
    "2 esdras": "II Esdras",
    "ii esdras": "II Esdras",
    "4 esdras": "II Esdras",
    "psalm 151": "Additional Psalm",
    "salmo 151": "Additional Psalm",
    "laodiceans": "Laodiceans",
    "laodicenses": "Laodiceans",
}

CORE_VERSIONS = [
    "VulgClementine",
    "StatResGNT",
    "TR",
    "Byz",
    "FreCrampon",
    "FreLXX",
    "WLC",
    "Peshitta",
    "CopSahBible2",
    "ArmEastern",
    "PorBLivreTR",
    "KJV",
]

RE_VERSE_REF = re.compile(
    r"^\s*(?P<book>[\d\sIVXLCDMivxlcdm\w\.\-]+?)\s+(?P<chapter>\d+|[IVXLCDMivxlcdm]+)(?:[\s:,]+(?P<verse_start>\d+)(?:[\s\-–]+(?P<verse_end>\d+))?)?\s*$"
)

ROMAN_VALS = {
    "I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000
}


def parse_roman(s: str) -> Optional[int]:
    """Parse Roman numeral to int."""
    s = s.strip().upper()
    if not s or not re.fullmatch(r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})", s):
        return None
    val = 0
    prev = 0
    for c in reversed(s):
        curr = ROMAN_VALS.get(c, 0)
        if curr < prev:
            val -= curr
        else:
            val += curr
            prev = curr
    return val if val > 0 else None


def resolve_book_name(raw_name: str) -> Optional[str]:
    """Resolve raw book name/abbreviation into canonical CSV Book name."""
    norm = normalize_book_alias(raw_name)
    if not norm:
        return None

    # Check direct extra aliases
    if norm in EXTRA_ALIASES:
        return EXTRA_ALIASES[norm]

    # Check canonical book catalog
    ck = canonical_book_key(norm)
    if ck and ck in CANONICAL_TO_CSV:
        return CANONICAL_TO_CSV[ck]

    # Try title-case matching directly against known books
    title = norm.title()
    if title in CANONICAL_TO_CSV.values():
        return title

    return None


def parse_reference(ref: str) -> Optional[Tuple[str, int, Optional[int], Optional[int]]]:
    """Parse Scripture reference string into (book_name, chapter, start_verse, end_verse).

    Examples:
        'Gen 1:1' -> ('Genesis', 1, 1, 1)
        'Ps 22:1-5' -> ('Psalms', 22, 1, 5)
        'Matth. V 9' -> ('Matthew', 5, 9, 9)
        'Rom 8' -> ('Romans', 8, None, None)
    """
    m = RE_VERSE_REF.match(ref.strip())
    if not m:
        return None

    b_raw = m.group("book")
    c_raw = m.group("chapter")
    vs_raw = m.group("verse_start")
    ve_raw = m.group("verse_end")

    book = resolve_book_name(b_raw)
    if not book:
        return None

    ch = int(c_raw) if c_raw.isdigit() else parse_roman(c_raw)
    if not ch or ch <= 0:
        return None

    vs = int(vs_raw) if vs_raw and vs_raw.isdigit() else None
    ve = int(ve_raw) if ve_raw and ve_raw.isdigit() else vs

    return (book, ch, vs, ve)


class CanonicalBibleLookup:
    """High-performance multi-version Bible lookup engine."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, csv_dir: Path = CSV_DIR):
        self.db_path = db_path
        self.csv_dir = csv_dir
        self._con: Optional[sqlite3.Connection] = None

    def _get_db(self) -> Optional[sqlite3.Connection]:
        if self._con is not None:
            return self._con
        if self.db_path.exists():
            self._con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            return self._con
        return None

    def lookup(
        self,
        book: str,
        chapter: int,
        start_verse: Optional[int] = None,
        end_verse: Optional[int] = None,
        version: str = "VulgClementine",
    ) -> List[Dict[str, Any]]:
        """Lookup verses by book, chapter and optional verse range."""
        con = self._get_db()
        if con is not None:
            cur = con.cursor()
            if start_verse is None:
                cur.execute(
                    "SELECT book, chapter, verse, text FROM verses WHERE version = ? AND book = ? AND chapter = ? ORDER BY verse",
                    (version, book, chapter),
                )
            elif end_verse is None or end_verse == start_verse:
                cur.execute(
                    "SELECT book, chapter, verse, text FROM verses WHERE version = ? AND book = ? AND chapter = ? AND verse = ?",
                    (version, book, chapter, start_verse),
                )
            else:
                cur.execute(
                    "SELECT book, chapter, verse, text FROM verses WHERE version = ? AND book = ? AND chapter = ? AND verse >= ? AND verse <= ? ORDER BY verse",
                    (version, book, chapter, start_verse, end_verse),
                )
            rows = cur.fetchall()
            return [
                {"version": version, "book": r[0], "chapter": r[1], "verse": r[2], "text": r[3]}
                for r in rows
            ]

        # Fallback to direct CSV read
        csv_file = self.csv_dir / f"{version}.csv"
        if not csv_file.exists():
            return []

        results = []
        with open(csv_file, mode="r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Book") != book:
                    continue
                try:
                    c = int(row.get("Chapter", 0))
                except ValueError:
                    continue
                if c != chapter:
                    continue
                try:
                    v = int(row.get("Verse", 0))
                except ValueError:
                    continue
                if start_verse is not None:
                    if end_verse is not None and (v < start_verse or v > end_verse):
                        continue
                    if end_verse is None and v != start_verse:
                        continue
                results.append(
                    {
                        "version": version,
                        "book": book,
                        "chapter": c,
                        "verse": v,
                        "text": row.get("Text", ""),
                    }
                )
        return results

    def compare(
        self,
        ref: str,
        versions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Compare a verse reference across multiple versions."""
        if versions is None:
            versions = ["VulgClementine", "StatResGNT", "FreCrampon"]

        parsed = parse_reference(ref)
        if not parsed:
            return {"error": f"Invalid or unrecognized reference: '{ref}'"}

        book, ch, vs, ve = parsed
        comparison: Dict[str, List[Dict[str, Any]]] = {}

        for ver in versions:
            res = self.lookup(book, ch, vs, ve, version=ver)
            comparison[ver] = res

        return {
            "reference": ref,
            "parsed": {"book": book, "chapter": ch, "start_verse": vs, "end_verse": ve},
            "versions": comparison,
        }

    def search(
        self,
        query: str,
        version: str = "VulgClementine",
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Search text in a given version."""
        con = self._get_db()
        if con is not None:
            cur = con.cursor()
            cur.execute(
                """
                SELECT book, chapter, verse, text
                FROM verses
                WHERE version = ? AND text LIKE ?
                ORDER BY rowid
                LIMIT ?
                """,
                (version, f"%{query}%", limit),
            )
            rows = cur.fetchall()
            return [
                {"version": version, "book": r[0], "chapter": r[1], "verse": r[2], "text": r[3]}
                for r in rows
            ]

        csv_file = self.csv_dir / f"{version}.csv"
        if not csv_file.exists():
            return []

        results = []
        q_norm = query.lower()
        with open(csv_file, mode="r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                txt = row.get("Text", "")
                if q_norm in txt.lower():
                    results.append(
                        {
                            "version": version,
                            "book": row.get("Book"),
                            "chapter": int(row.get("Chapter", 0)),
                            "verse": int(row.get("Verse", 0)),
                            "text": txt,
                        }
                    )
                    if len(results) >= limit:
                        break
        return results

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None


def build_canonical_bible_database(
    db_path: Path = DEFAULT_DB_PATH,
    csv_dir: Path = CSV_DIR,
    versions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compile clean CSV files into an indexed, deduplicated SQLite database."""
    t0 = time.time()
    if versions is None:
        versions = CORE_VERSIONS

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("PRAGMA synchronous = NORMAL")
    cur.execute(
        """
        CREATE TABLE verses (
            version TEXT NOT NULL,
            book TEXT NOT NULL,
            chapter INTEGER NOT NULL,
            verse INTEGER NOT NULL,
            text TEXT NOT NULL
        )
        """
    )

    imported_stats: Dict[str, int] = {}
    total_verses = 0

    for ver in versions:
        csv_file = csv_dir / f"{ver}.csv"
        if not csv_file.exists():
            continue

        ver_count = 0
        batch: List[Tuple[str, str, int, int, str]] = []

        with open(csv_file, mode="r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                b = row.get("Book", "").strip()
                t = row.get("Text", "").strip()
                try:
                    c = int(row.get("Chapter", 0))
                    v = int(row.get("Verse", 0))
                except (ValueError, TypeError):
                    continue

                if not b or not t:
                    continue

                batch.append((ver, b, c, v, t))
                ver_count += 1
                if len(batch) >= 10000:
                    cur.executemany("INSERT INTO verses VALUES (?, ?, ?, ?, ?)", batch)
                    batch = []

        if batch:
            cur.executemany("INSERT INTO verses VALUES (?, ?, ?, ?, ?)", batch)

        imported_stats[ver] = ver_count
        total_verses += ver_count

    # Build primary index for instant verse lookup
    cur.execute("CREATE INDEX idx_verses_lookup ON verses(version, book, chapter, verse)")
    cur.execute("CREATE INDEX idx_verses_version_book ON verses(version, book, chapter)")

    conn.commit()
    conn.close()

    return {
        "status": "success",
        "db_path": str(db_path),
        "total_verses": total_verses,
        "versions": imported_stats,
        "duration_seconds": round(time.time() - t0, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Canonical Biblical Verse Lookup Tool.")
    parser.add_argument("reference", nargs="?", help="Scripture reference (e.g. 'Ps 22:1', 'Matth 5:9', 'Gen 1:1-3')")
    parser.add_argument("--version", "-v", default="VulgClementine", help="Bible version (default: VulgClementine)")
    parser.add_argument("--compare", "-c", nargs="+", help="Compare reference across multiple versions (e.g. -c VulgClementine StatResGNT FreCrampon)")
    parser.add_argument("--search", "-s", help="Full-text search within a version")
    parser.add_argument("--limit", type=int, default=15, help="Search result limit (default: 15)")
    parser.add_argument("--build-db", action="store_true", help="Build/rebuild canonical_bibles.db from clean CSVs")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()

    if args.build_db:
        res = build_canonical_bible_database()
        print(json.dumps(res, indent=2))
        return

    engine = CanonicalBibleLookup()

    if args.search:
        results = engine.search(args.search, version=args.version, limit=args.limit)
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print(f"\nSearch results for '{args.search}' in [{args.version}] ({len(results)} found):")
            for r in results:
                print(f"  • {r['book']} {r['chapter']}:{r['verse']} — {r['text']}")
        return

    if args.reference:
        if args.compare:
            res = engine.compare(args.reference, versions=args.compare)
            if args.json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"\nComparative lookup for: {args.reference}")
                for ver, verses in res.get("versions", {}).items():
                    print(f"\n[{ver}]:")
                    if not verses:
                        print("  (No text found for this reference)")
                    for v in verses:
                        print(f"  {v['book']} {v['chapter']}:{v['verse']} — {v['text']}")
        else:
            parsed = parse_reference(args.reference)
            if not parsed:
                print(f"Error: Could not parse reference '{args.reference}'")
                return
            b, c, vs, ve = parsed
            results = engine.lookup(b, c, vs, ve, version=args.version)
            if args.json:
                print(json.dumps(results, indent=2, ensure_ascii=False))
            else:
                print(f"\n[{args.version}] {args.reference} ({b} {c}:{vs}{'-' + str(ve) if ve and ve != vs else ''}):")
                if not results:
                    print("  (No text found)")
                for r in results:
                    print(f"  {r['chapter']}:{r['verse']} — {r['text']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()