from __future__ import annotations

from dataclasses import dataclass, replace
import re
import unicodedata

from patristica_pipeline.scripture_book_catalog import (
    BOOKS,
    MAX_CHAPTER_BY_BOOK,
    canonical_book_key,
    canonical_book_label,
    contextual_book_tradition,
    aliases_for_book,
)


PARSER_VERSION = "2.1.4-poc"


@dataclass(frozen=True, order=True)
class ScriptureSegment:
    start_chapter: int
    start_verse: int | None
    end_chapter: int
    end_verse: int | None

    def canonical_token(self) -> str:
        start = str(self.start_chapter)
        end = str(self.end_chapter)
        if self.start_verse is not None:
            start += f":{self.start_verse}"
        if self.end_verse is not None:
            end += f":{self.end_verse}"
        return start if start == end else f"{start}-{end}"


@dataclass(frozen=True)
class ScriptureReference:
    book_key: str
    book_label: str
    granularity: str
    segments: tuple[ScriptureSegment, ...]
    raw: str
    normalized: str
    start: int
    end: int
    source_kind: str = "embedded"
    flags: tuple[str, ...] = ()
    alternate_chapter: int | None = None
    chapter_was_roman: bool = False

    @property
    def canonical_key(self) -> str:
        segments = ",".join(segment.canonical_token() for segment in self.segments)
        return f"unknown|{self.book_key}|{segments}"


@dataclass(frozen=True)
class ParseIssue:
    code: str
    raw: str
    start: int
    end: int
    detail: str = ""


@dataclass(frozen=True)
class ScriptureParseResult:
    references: tuple[ScriptureReference, ...]
    issues: tuple[ParseIssue, ...]


@dataclass(frozen=True)
class _TailResult:
    segments: tuple[ScriptureSegment, ...]
    end: int
    flags: tuple[str, ...]
    alternate_chapter: int | None
    chapter_was_roman: bool


_ROMAN_VALUES = {
    "I": 1,
    "V": 5,
    "X": 10,
    "L": 50,
    "C": 100,
    "D": 500,
    "M": 1000,
}
_NUMBER_RE = re.compile(
    r"(?:\d+(?=(?:ss|sqq|ff)\b|[^\w]|$)|[ivxlcdm]+(?!\w))",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s*")
_VERSE_SEPARATOR_RE = re.compile(r"\s*[,.:]\s*")
_RANGE_RE = re.compile(r"\s*[-‐‑‒–—]\s*")
_OPEN_END_RE = re.compile(r"\s*(?:ss|sqq|ff)\.?", re.IGNORECASE)
_CONNECTOR_RE = re.compile(r"\s*(?:,|;|\.|\be\b|\bet\b|\band\b)\s*", re.IGNORECASE)
_STANDALONE_NOISE_RE = re.compile(
    r"\b(?:cf|cfr|conferir|vide|ver|livro|evangelho|epistola|primeira|segunda|"
    r"terceira|segundo|segundo a|de|do|da|dos|das|ad|capitulo)\b",
    re.IGNORECASE,
)
_SINGLE_CHAPTER_BOOKS = {"abdias", "filemon", "2 joao", "3 joao", "judas"}


def _fold(value: str) -> str:
    value = value.translate(str.maketrans({"œ": "oe", "æ": "ae", "ſ": "s"}))
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _alias_pattern() -> re.Pattern[str]:
    aliases: set[str] = set()
    for book in BOOKS:
        aliases.add(_fold(book.key))
        label = canonical_book_label(book.key)
        if label:
            aliases.add(_fold(label))
        aliases.update(_fold(alias) for alias in aliases_for_book(book.key))
    expressions: list[str] = []
    for alias in sorted(aliases, key=lambda item: (-len(item.split()), -len(item), item)):
        tokens = [re.escape(token) for token in alias.split() if token]
        if tokens:
            expressions.append(r"(?:[\s.]+)".join(tokens))
    return re.compile(rf"(?<!\w)(?P<book>{'|'.join(expressions)})(?!\w)", re.IGNORECASE)


_BOOK_RE = _alias_pattern()


def _roman_to_int(token: str) -> int | None:
    upper = token.upper()
    if not upper or any(char not in _ROMAN_VALUES for char in upper):
        return None
    total = 0
    previous = 0
    for char in reversed(upper):
        value = _ROMAN_VALUES[char]
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    return total if _int_to_roman(total) == upper else None


def _int_to_roman(value: int) -> str:
    if not 0 < value < 4000:
        return ""
    pairs = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    output: list[str] = []
    for number, token in pairs:
        while value >= number:
            output.append(token)
            value -= number
    return "".join(output)


def _read_number(text: str, position: int) -> tuple[int, int, bool] | None:
    position = _SPACE_RE.match(text, position).end()
    match = _NUMBER_RE.match(text, position)
    if not match:
        return None
    token = match.group(0)
    if token.isdigit():
        value = int(token)
        was_roman = False
    else:
        value = _roman_to_int(token)
        was_roman = True
        if value is None:
            return None
    return value, match.end(), was_roman


def _read_verse_endpoint(
    text: str,
    position: int,
    chapter: int,
    start_verse: int,
) -> tuple[int, int, int, int] | None:
    first = _read_number(text, position)
    if first is None:
        return None
    value, end, _ = first
    separator = _VERSE_SEPARATOR_RE.match(text, end)
    if separator:
        second = _read_number(text, separator.end())
        separator_token = separator.group(0).strip()
        if second is not None and (
            separator_token == ":"
            or (
                chapter <= value <= chapter + 3
                and (value < start_verse or chapter >= start_verse)
            )
        ):
            verse, second_end, _ = second
            return value, verse, second_end, separator.end()
    return chapter, value, end, end


def _parse_tail(text: str, position: int) -> _TailResult | None:
    position = _SPACE_RE.match(text, position).end()
    if position < len(text) and text[position] == ".":
        position = _SPACE_RE.match(text, position + 1).end()
    chapter_read = _read_number(text, position)
    if chapter_read is None:
        return None
    chapter, cursor, chapter_was_roman = chapter_read
    if chapter <= 0:
        return None

    separator = _VERSE_SEPARATOR_RE.match(text, cursor)
    if separator and separator.group(0).strip() == ".":
        # A full stop after a chapter is frequently sentence punctuation or a
        # heading terminator (``PSALMUS XLII.``), not a chapter/verse
        # separator.  Keep accepting ``Psal. XLII. 3`` below when a numeric
        # token really follows the dot.
        possible_verse = _read_number(text, separator.end())
        if possible_verse is None:
            separator = None
    segments: list[ScriptureSegment] = []
    flags: set[str] = set()
    alternate_chapter: int | None = None

    if separator:
        verse_read = _read_number(text, separator.end())
        if verse_read is None:
            return None
        verse, cursor, _ = verse_read
        if verse <= 0:
            return None
        end_chapter = chapter
        end_verse = verse
        range_match = _RANGE_RE.match(text, cursor)
        if range_match:
            endpoint = _read_verse_endpoint(text, range_match.end(), chapter, verse)
            if endpoint is not None:
                end_chapter, end_verse, cursor, _ = endpoint
        segments.append(ScriptureSegment(chapter, verse, end_chapter, end_verse))

        while True:
            open_end = _OPEN_END_RE.match(text, cursor)
            if open_end:
                flags.add("open_end")
                cursor = open_end.end()
            connector = _CONNECTOR_RE.match(text, cursor)
            if not connector:
                break
            connector_text = connector.group(0)
            number_read = _read_number(text, connector.end())
            if number_read is None:
                break
            first, next_cursor, _ = number_read
            chapter_separator = _VERSE_SEPARATOR_RE.match(text, next_cursor)
            previous = segments[-1]
            separator_token = (
                chapter_separator.group(0).strip() if chapter_separator else ""
            )
            connector_token = connector_text.strip().casefold()
            explicit_chapter = bool(chapter_separator) and (
                separator_token == ":"
                or ";" in connector_text
                or first == previous.start_chapter
                or (
                    previous.start_verse is not None
                    and first < previous.start_verse
                )
                or (
                    connector_token in {"e", "et", "and"}
                    and first == previous.start_chapter + 1
                )
            )
            if explicit_chapter:
                verse_read = _read_number(text, chapter_separator.end())
                if verse_read is None:
                    break
                current_chapter = first
                current_verse, next_cursor, _ = verse_read
            else:
                after_number = _SPACE_RE.match(text, next_cursor).end()
                if ";" in connector_text and (
                    after_number < len(text) and text[after_number].isalpha()
                ):
                    break
                current_chapter = previous.start_chapter
                current_verse = first
            if current_chapter <= 0 or current_verse <= 0:
                break
            end_chapter = current_chapter
            end_verse = current_verse
            range_match = _RANGE_RE.match(text, next_cursor)
            if range_match:
                endpoint = _read_verse_endpoint(
                    text,
                    range_match.end(),
                    current_chapter,
                    current_verse,
                )
                if endpoint is None:
                    break
                end_chapter, end_verse, next_cursor, _ = endpoint
            segments.append(
                ScriptureSegment(current_chapter, current_verse, end_chapter, end_verse)
            )
            cursor = next_cursor
    else:
        range_match = _RANGE_RE.match(text, cursor)
        if range_match:
            end_read = _read_number(text, range_match.end())
            if end_read is not None:
                end_chapter, cursor, _ = end_read
                segments.append(ScriptureSegment(chapter, None, end_chapter, None))
            else:
                segments.append(ScriptureSegment(chapter, None, chapter, None))
        else:
            segments.append(ScriptureSegment(chapter, None, chapter, None))
            paren = re.match(r"\s*\(\s*(\d+)\s*\)", text[cursor:])
            if paren:
                alternate_chapter = int(paren.group(1))
                flags.add("alternate_numbering")
                cursor += paren.end()

    for segment in segments:
        if segment.end_chapter < segment.start_chapter:
            return None
        if (
            segment.start_chapter == segment.end_chapter
            and segment.start_verse is not None
            and segment.end_verse is not None
            and segment.end_verse < segment.start_verse
        ):
            return None

    return _TailResult(
        segments=tuple(segments),
        end=cursor,
        flags=tuple(sorted(flags)),
        alternate_chapter=alternate_chapter,
        chapter_was_roman=chapter_was_roman,
    )


def _granularity(segments: tuple[ScriptureSegment, ...]) -> str:
    if len(segments) > 1:
        return "list"
    segment = segments[0]
    if segment.start_verse is None:
        return "chapter" if segment.start_chapter == segment.end_chapter else "range"
    if (
        segment.start_chapter == segment.end_chapter
        and segment.start_verse == segment.end_verse
    ):
        return "verse"
    return "range"


def _format_reference(book_label: str, segments: tuple[ScriptureSegment, ...]) -> str:
    output = book_label
    previous_chapter: int | None = None
    for segment in segments:
        if segment.start_verse is None:
            token = str(segment.start_chapter)
            if segment.end_chapter != segment.start_chapter:
                token += f"-{segment.end_chapter}"
        else:
            prefix = f"{segment.start_chapter}:" if segment.start_chapter != previous_chapter else ""
            token = f"{prefix}{segment.start_verse}"
            if (
                segment.end_chapter != segment.start_chapter
                or segment.end_verse != segment.start_verse
            ):
                if segment.end_chapter == segment.start_chapter:
                    token += f"-{segment.end_verse}"
                else:
                    token += f"-{segment.end_chapter}:{segment.end_verse}"
        separator = " " if previous_chapter is None else (
            "; " if segment.start_chapter != previous_chapter else ", "
        )
        output += separator + token
        previous_chapter = segment.start_chapter
    return output


def _source_kind(text: str, references: list[ScriptureReference]) -> str:
    remainder = list(_fold(text))
    for reference in references:
        for index in range(reference.start, min(reference.end, len(remainder))):
            remainder[index] = " "
    cleaned = _STANDALONE_NOISE_RE.sub(" ", "".join(remainder))
    cleaned = re.sub(r"[\s\W_]+", "", cleaned, flags=re.UNICODE)
    return "standalone" if not cleaned else "embedded"


def _looks_like_person_ordinal(reference: ScriptureReference, text: str) -> bool:
    if reference.book_key != "joao" or not reference.chapter_was_roman:
        return False
    context = _fold(text[max(0, reference.start - 32) : reference.end + 32])
    if re.search(r"\bpapas?\b", context):
        return True
    if reference.granularity not in {"chapter", "range"}:
        return False
    if any(
        segment.start_verse is not None or segment.end_verse is not None
        for segment in reference.segments
    ):
        return False
    raw_book = _fold(text[reference.start : reference.end]).strip()
    if raw_book.startswith(("ioan", "joh", "jn")):
        return False
    scripture_cues = ("evangelho", "epistola", "capitulo", "versiculo")
    return not any(cue in context for cue in scripture_cues)


def _looks_like_ambiguous_embedded_chapter(
    reference: ScriptureReference,
    text: str,
) -> bool:
    if reference.source_kind != "embedded" or reference.granularity != "chapter":
        return False
    before = text[: reference.start].rstrip()
    if before.endswith(("(", "[", "{")):
        return False
    context = _fold(text[max(0, reference.start - 48) : reference.start])
    cues = (
        "capitulo",
        "comentario",
        "escritura",
        "evangelho",
        "exposicao",
        "livro",
        "paixao",
        "profecia",
        "salmo",
    )
    return not any(cue in context for cue in cues)


def parse_scripture_keyword(
    text: str,
    *,
    collection: str | None = None,
) -> ScriptureParseResult:
    """Parse scripture citations embedded in one summary keyword label.

    The parser deliberately returns only structurally valid, canonical references.
    Ambiguous person ordinals and malformed citation-shaped fragments are reported
    through ``issues`` instead of being silently promoted to references.
    """

    if not isinstance(text, str) or not text.strip():
        return ScriptureParseResult((), ())

    folded = _fold(text)
    references: list[ScriptureReference] = []
    issues: list[ParseIssue] = []
    occupied_until = -1
    for match in _BOOK_RE.finditer(folded):
        if match.start() < occupied_until:
            continue
        captured = match.group("book")
        tradition = contextual_book_tradition(collection, captured)
        raw_book = text[match.start() : match.end()]
        raw_letters = "".join(char for char in raw_book if char.isalpha())
        if (
            len(raw_letters) <= 2
            and raw_book[:1].islower()
            and not raw_book.rstrip().endswith(".")
        ):
            continue
        book_key = canonical_book_key(raw_book, tradition=tradition)
        if _fold(raw_book) == "jo" and "ó" in raw_book.casefold():
            book_key = "jo"
        prefix = folded[max(0, match.start() - 24) : match.start()]
        if book_key == "2 joao" and re.search(
            r"\b(?:evangelho|paixao|relato)\s*$", prefix
        ):
            book_key = "joao"
        if book_key is None:
            continue
        tail = _parse_tail(folded, match.end())
        if tail is None:
            number = _read_number(folded, match.end())
            if number is not None:
                issue_end = number[1]
                issues.append(
                    ParseIssue(
                        "malformed_reference",
                        text[match.start() : issue_end],
                        match.start(),
                        issue_end,
                    )
                )
            continue
        if (
            book_key in _SINGLE_CHAPTER_BOOKS
            and len(tail.segments) == 1
            and tail.segments[0].start_verse is None
            and tail.segments[0].start_chapter == tail.segments[0].end_chapter
        ):
            cited_verse = tail.segments[0].start_chapter
            tail = replace(
                tail,
                segments=(ScriptureSegment(1, cited_verse, 1, cited_verse),),
                flags=tuple(sorted(set(tail.flags) | {"single_chapter_verse"})),
            )
        if (
            book_key == "1 reis"
            and collection in {"PG", "PL"}
            and any(segment.start_chapter > 22 for segment in tail.segments)
        ):
            book_key = "1 samuel"
            tail = replace(
                tail,
                flags=tuple(sorted(set(tail.flags) | {"vulgate_regum_remap"})),
            )
        book_label = canonical_book_label(book_key) or book_key
        reference = ScriptureReference(
            book_key=book_key,
            book_label=book_label,
            granularity=_granularity(tail.segments),
            segments=tail.segments,
            raw=text[match.start() : tail.end],
            normalized=_format_reference(book_label, tail.segments),
            start=match.start(),
            end=tail.end,
            flags=tail.flags,
            alternate_chapter=tail.alternate_chapter,
            chapter_was_roman=tail.chapter_was_roman,
        )
        occupied_until = tail.end
        max_chapter = MAX_CHAPTER_BY_BOOK[book_key]
        if any(
            segment.start_chapter > max_chapter
            or segment.end_chapter > max_chapter
            for segment in reference.segments
        ):
            issues.append(
                ParseIssue(
                    "implausible_chapter",
                    reference.raw,
                    reference.start,
                    reference.end,
                    f"Chapter exceeds the supported maximum ({max_chapter}) for {book_label}",
                )
            )
            continue
        if _looks_like_person_ordinal(reference, text):
            issues.append(
                ParseIssue(
                    "ambiguous_person_ordinal",
                    reference.raw,
                    reference.start,
                    reference.end,
                    "Roman ordinal after João without an explicit scripture cue",
                )
            )
            continue
        references.append(reference)

    source_kind = _source_kind(text, references) if references else "embedded"
    references = [replace(reference, source_kind=source_kind) for reference in references]
    accepted: list[ScriptureReference] = []
    for reference in references:
        if _looks_like_ambiguous_embedded_chapter(reference, text):
            issues.append(
                ParseIssue(
                    "ambiguous_embedded_chapter",
                    reference.raw,
                    reference.start,
                    reference.end,
                    "Embedded book plus chapter-like number lacks a scripture cue",
                )
            )
        else:
            accepted.append(reference)
    return ScriptureParseResult(tuple(accepted), tuple(issues))


__all__ = [
    "PARSER_VERSION",
    "ParseIssue",
    "ScriptureParseResult",
    "ScriptureReference",
    "ScriptureSegment",
    "parse_scripture_keyword",
]
