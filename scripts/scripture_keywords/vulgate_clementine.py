"""Read-only lookup helpers for the Vulgata Clementina JSON export."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


DEFAULT_VULGATE_JSON = (
    Path(__file__).resolve().parents[2]
    / ".work/bible_databases-master/sources/la/VulgClementine/VulgClementine.json"
)

VULGATE_EMBEDDING_PROFILE = "vulg-clementine-keyword-v1"


BOOK_NAME_MAP = {
    "Gênesis": "Genesis",
    "Êxodo": "Exodus",
    "Levítico": "Leviticus",
    "Números": "Numbers",
    "Deuteronômio": "Deuteronomy",
    "Josué": "Joshua",
    "Juízes": "Judges",
    "Rute": "Ruth",
    "I Samuel": "I Samuel",
    "II Samuel": "II Samuel",
    "I Reis": "I Kings",
    "II Reis": "II Kings",
    "I Crônicas": "I Chronicles",
    "II Crônicas": "II Chronicles",
    "Esdras": "Ezra",
    "Neemias": "Nehemiah",
    "Tobias": "Tobit",
    "Judite": "Judith",
    "Ester": "Esther",
    "Jó": "Job",
    "Salmos": "Psalms",
    "I Macabeus": "I Maccabees",
    "II Macabeus": "II Maccabees",
    "Provérbios": "Proverbs",
    "Eclesiastes": "Ecclesiastes",
    "Cântico dos Cânticos": "Song of Solomon",
    "Sabedoria": "Wisdom",
    "Eclesiástico": "Sirach",
    "Isaías": "Isaiah",
    "Jeremias": "Jeremiah",
    "Lamentações": "Lamentations",
    "Baruc": "Baruch",
    "Ezequiel": "Ezekiel",
    "Daniel": "Daniel",
    "Oséias": "Hosea",
    "Joel": "Joel",
    "Amós": "Amos",
    "Abdias": "Obadiah",
    "Jonas": "Jonah",
    "Miquéias": "Micah",
    "Naum": "Nahum",
    "Habacuc": "Habakkuk",
    "Sofonias": "Zephaniah",
    "Ageu": "Haggai",
    "Zacarias": "Zechariah",
    "Malaquias": "Malachi",
    "São Mateus": "Matthew",
    "São Marcos": "Mark",
    "São Lucas": "Luke",
    "São João": "John",
    "Atos dos Apóstolos": "Acts",
    "Romanos": "Romans",
    "I Coríntios": "I Corinthians",
    "II Coríntios": "II Corinthians",
    "Gálatas": "Galatians",
    "Efésios": "Ephesians",
    "Filipenses": "Philippians",
    "Colossenses": "Colossians",
    "I Tessalonicenses": "I Thessalonians",
    "II Tessalonicenses": "II Thessalonians",
    "I Timóteo": "I Timothy",
    "II Timóteo": "II Timothy",
    "Tito": "Titus",
    "Filêmon": "Philemon",
    "Hebreus": "Hebrews",
    "São Tiago": "James",
    "I São Pedro": "I Peter",
    "II São Pedro": "II Peter",
    "I São João": "I John",
    "II São João": "II John",
    "III São João": "III John",
    "São Judas": "Jude",
    "Apocalipse": "Revelation of John",
}


_VERSE_SPEC_RE = re.compile(r"^(?P<start>\d{1,3})(?:-(?P<end>\d{1,3}))?$")


@dataclass(frozen=True)
class PassageLookup:
    status: str
    text: str | None = None
    book: str | None = None
    chapter: int | None = None
    verse_start: int | None = None
    verse_end: int | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.text)

    @property
    def locator_key(self) -> tuple[str | None, int | None, int | None, int | None]:
        return self.book, self.chapter, self.verse_start, self.verse_end


@dataclass(frozen=True)
class KeywordEnrichment:
    status: str
    embedding_text: str
    lookups: tuple[PassageLookup, ...]

    @property
    def enriched(self) -> bool:
        return self.status == "enriched"


class VulgateClementine:
    """In-memory, immutable index over the clean Clementine JSON export."""

    def __init__(self, chapters: Mapping[tuple[str, int], Mapping[int, str]]) -> None:
        self._chapters = {
            key: dict(sorted(verses.items())) for key, verses in chapters.items()
        }

    @classmethod
    def from_json(cls, path: Path | str = DEFAULT_VULGATE_JSON) -> "VulgateClementine":
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"Vulgata Clementina JSON not found: {source}")
        payload = json.loads(source.read_text(encoding="utf-8"))
        books = payload.get("books") if isinstance(payload, dict) else None
        if not isinstance(books, list):
            raise ValueError("Invalid Clementine JSON: expected a books list")

        chapters: dict[tuple[str, int], dict[int, str]] = {}
        for raw_book in books:
            if not isinstance(raw_book, dict) or not isinstance(raw_book.get("name"), str):
                raise ValueError("Invalid Clementine JSON: malformed book")
            book_name = raw_book["name"]
            raw_chapters = raw_book.get("chapters")
            if not isinstance(raw_chapters, list):
                raise ValueError(
                    f"Invalid Clementine JSON: malformed chapters for {book_name}"
                )
            for raw_chapter in raw_chapters:
                if not isinstance(raw_chapter, dict):
                    raise ValueError(f"Invalid Clementine JSON: malformed chapter for {book_name}")
                chapter = raw_chapter.get("chapter")
                if not isinstance(chapter, int) or chapter < 1:
                    raise ValueError(f"Invalid Clementine chapter for {book_name}: {chapter!r}")
                key = (book_name, chapter)
                if key in chapters:
                    raise ValueError(f"Duplicate Clementine chapter: {book_name} {chapter}")
                verses: dict[int, str] = {}
                for raw_verse in raw_chapter.get("verses") or []:
                    if not isinstance(raw_verse, dict):
                        raise ValueError(f"Invalid Clementine verse in {book_name} {chapter}")
                    verse = raw_verse.get("verse")
                    text = raw_verse.get("text")
                    if not isinstance(verse, int) or verse < 1 or not isinstance(text, str):
                        raise ValueError(f"Invalid Clementine verse in {book_name} {chapter}")
                    if verse in verses:
                        raise ValueError(f"Duplicate Clementine verse: {book_name} {chapter}:{verse}")
                    verses[verse] = text.strip()
                chapters[key] = verses
        return cls(chapters)

    def lookup(
        self,
        book_canonical: str,
        chapter: int,
        verse_spec: str | int | None = None,
    ) -> PassageLookup:
        book = BOOK_NAME_MAP.get(book_canonical)
        if book is None:
            return PassageLookup(
                status="book_unmapped",
                reason=f"No Clementine mapping for {book_canonical!r}",
            )
        if not isinstance(chapter, int) or chapter < 1:
            return PassageLookup(
                status="chapter_invalid",
                book=book,
                reason=f"Invalid chapter: {chapter!r}",
            )
        verses = self._chapters.get((book, chapter))
        if verses is None:
            return PassageLookup(
                status="chapter_missing",
                book=book,
                chapter=chapter,
                reason=f"Chapter not found: {book} {chapter}",
            )

        if verse_spec is None:
            text = " ".join(text for text in verses.values() if text)
            if not text:
                return PassageLookup(
                    status="text_empty",
                    book=book,
                    chapter=chapter,
                    reason=f"Chapter has no literal text: {book} {chapter}",
                )
            return PassageLookup(status="ok", text=text, book=book, chapter=chapter)

        match = _VERSE_SPEC_RE.fullmatch(str(verse_spec).strip())
        if match is None:
            return PassageLookup(
                status="verse_invalid",
                book=book,
                chapter=chapter,
                reason=f"Invalid verse specification: {verse_spec!r}",
            )
        verse_start = int(match.group("start"))
        verse_end = int(match.group("end") or verse_start)
        if verse_end < verse_start:
            return PassageLookup(
                status="range_invalid",
                book=book,
                chapter=chapter,
                verse_start=verse_start,
                verse_end=verse_end,
                reason=f"Descending verse range: {verse_start}-{verse_end}",
            )
        requested = range(verse_start, verse_end + 1)
        missing = [verse for verse in requested if not verses.get(verse)]
        if missing:
            return PassageLookup(
                status="verse_missing",
                book=book,
                chapter=chapter,
                verse_start=verse_start,
                verse_end=verse_end,
                reason=f"Missing or empty verses: {', '.join(map(str, missing))}",
            )
        text = " ".join(verses[verse] for verse in requested)
        return PassageLookup(
            status="ok",
            text=text,
            book=book,
            chapter=chapter,
            verse_start=verse_start,
            verse_end=verse_end,
        )

    def lookup_record(self, record: Mapping[str, Any]) -> PassageLookup:
        chapter = record.get("number")
        if not isinstance(chapter, int) or chapter < 1:
            return PassageLookup(
                status="chapter_missing_from_parse",
                reason="Parser record has no positive chapter",
            )
        book_canonical = record.get("book_canonical")
        if not isinstance(book_canonical, str) or not book_canonical.strip():
            return PassageLookup(
                status="book_missing_from_parse",
                chapter=chapter,
                reason="Parser record has no canonical book",
            )
        if book_canonical == "Salmos":
            alternate = record.get("alt_number")
            if isinstance(alternate, int) and alternate > 0:
                chapter = alternate
        return self.lookup(book_canonical, chapter, record.get("verse"))


def enrich_keyword(
    keyword: str,
    citation_records: Sequence[Mapping[str, Any]],
    bible: VulgateClementine,
) -> KeywordEnrichment:
    """Append literal passages only when every parsed citation resolves."""
    eligible_records = [
        record for record in citation_records if record.get("number") is not None
    ]
    if not eligible_records:
        return KeywordEnrichment("parser_no_citation", keyword, ())

    lookups: list[PassageLookup] = []
    seen: set[tuple[str | None, int | None, int | None, int | None]] = set()
    for record in eligible_records:
        lookup = bible.lookup_record(record)
        if not lookup.ok:
            return KeywordEnrichment("lookup_failed", keyword, tuple(lookups + [lookup]))
        if lookup.locator_key in seen:
            continue
        seen.add(lookup.locator_key)
        lookups.append(lookup)

    if not lookups:
        return KeywordEnrichment("parser_no_citation", keyword, ())
    literal_text = "\n".join(lookup.text or "" for lookup in lookups)
    return KeywordEnrichment("enriched", f"{keyword}\n{literal_text}", tuple(lookups))


__all__ = [
    "BOOK_NAME_MAP",
    "DEFAULT_VULGATE_JSON",
    "KeywordEnrichment",
    "PassageLookup",
    "VULGATE_EMBEDDING_PROFILE",
    "VulgateClementine",
    "enrich_keyword",
]
