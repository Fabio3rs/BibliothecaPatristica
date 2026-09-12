from __future__ import annotations

import hashlib
import json
import re
import difflib
import unicodedata
import copy
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from tools.corpus_utils import PROJECT_ROOT, page_sort_key
from .index_pipeline_ownership import general_section_ownership
from .index_target_locator import normalize_for_search
from tools.ocr_xml_utils import read_ocr_page
from scripts.probe_chapter_targets import (
    greek_numeral_to_int,
    roman_to_int,
)


LOCATOR_NAME = "deterministic_structural_target_locator"
LOCATOR_VERSION = 7
MINIMUM_EDITORIAL_PAGE_CONFIDENCE = 0.82
MINIMUM_EDITORIAL_NEIGHBOR_TITLE_SIMILARITY = 0.53
INDEX_LIST_HEADING_RE = re.compile(
    r"\b(?:INDEX|INDICES|ANALYSIS|SYLLABUS|ELENCHUS|ORDO\s+RERUM|"
    r"TABLE\s+DES\s+MATI[EÈ]RES|CAPITULA|INDICULUS)\b",
    re.IGNORECASE,
)

EXTERNAL_RE = re.compile(r"\b(?:vide|voir|cf\.|confer)\b.{0,80}\b(?:tom|tome|volume|vol\.)", re.I)
INELIGIBLE_SCOPE_KINDS = {"retrospective_table", "volume_table", "fascicle_inventory"}
INELIGIBLE_SECTION_RE = re.compile(r"retrospective_table|volume_table|fascicle_inventory", re.I)
NON_STRUCTURAL_REFERENCE_INDEX_RE = re.compile(
    r"INDEX\s+(?:METHODICUS|GENERALIS\s+ALPHABETICUS|RERUM\s+ET\s+VERBORUM)|"
    r"ORDINE\s+ALPHABETICO|METHODICAL_ALPHABETICAL_INDEX|"
    r"PATR(?:OLOG|IST)ICO[- ]THEOLOGICUS|INDICES\s+PATROLOGI[ÆAE]|"
    r"INDICULUS\s+(?:LIBRORUM|TRACTATUUM|EPISTOLARUM)|"
    r"SPECIAL_EPISTOLARY_INDEX|THEMATIC_EPIST(?:LE|OLARY)_INDEX",
    re.IGNORECASE,
)

LABEL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("chapter", r"CAPP?|CAPUT|CAPITA|CAPITUL(?:UM|A|\.)?|TIT(?:ULUS|ULUM|\.)?|CHAPITRE|CH\."),
    ("chapter", r"ΚΕΦ(?:ΑΛΑΙΟΝ|ΑΛ\.)?"),
    ("chapter", r"الباب"),
    ("book", r"LIBER|LIBRA|LIVRE|BOOK"),
    ("part", r"PARS|PARTIE|PART"),
    ("tract", r"TRACTATUS|TRACT\.|TRAIT[ÉE]"),
    ("sermon", r"SERMO|SERMON"),
    ("homily", r"HOMILIA|HOM\.|HOM[ÉE]LIE"),
    ("epistle", r"EPISTOLA|EPISTULA|EPIST\."),
    ("question", r"QU(?:A|AE|Æ)STIO|QU(?:A|AE|Æ)ST\.|INTERROGATIO|INTERR?\.|DUBITATIO"),
    ("article", r"ARTICULUS|ARTICLE"),
    ("oration", r"ORATIO"),
    ("lesson", r"LECTIO"),
    ("canon", r"CANON"),
    ("distinction", r"DISTINCTIO"),
    ("proposition", r"PROPOSITIO"),
    ("formula", r"FORMULA"),
    ("fascicle", r"FASC\."),
)
LABEL_TO_FAMILY = {
    label.casefold().rstrip("."): family
    for family, pattern in LABEL_PATTERNS
    for label in re.sub(r"[()?\[\]|\\]", " ", pattern).split()
}
LABEL_ALTERNATION = "|".join(f"(?:{pattern})" for _, pattern in LABEL_PATTERNS)
LABELED_PREFIX_RE = re.compile(
    rf"^\s*(?P<label>{LABEL_ALTERNATION})\s*(?:[.():;,\-–—]+\s*)?"
    rf"(?P<ordinal>[0-9٠-٩]{{1,3}}|[IVXLCDM]{{1,10}}|[Α-ΩϚϜϞϠ]{{1,8}}[΄'’]?|"
    rf"PRIM(?:US|A|UM)|SECUND(?:US|A|UM)|TERTI(?:US|A|UM)|QUART(?:US|A|UM)|"
    rf"QUINT(?:US|A|UM)|SEXT(?:US|A|UM)|SEPTIM(?:US|A|UM)|OCTAV(?:US|A|UM)|"
    rf"NON(?:US|A|UM)|DECIM(?:US|A|UM)|PREMIER|PREMI[ÈE]RE|SECOND|SECONDE|"
    rf"TROISI[ÈE]ME|QUATRI[ÈE]ME|CINQUI[ÈE]ME|SIXI[ÈE]ME|SEPTI[ÈE]ME|"
    rf"HUITI[ÈE]ME|NEUVI[ÈE]ME|DIXI[ÈE]ME|1ER|[0-9]+E|"
    rf"الأول|الاول|الأولى|الاولى|الثاني|الثانية|الثالث|الثالثة|الرابع|الرابعة|"
    rf"الخامس|الخامسة|السادس|السادسة|السابع|السابعة|الثامن|الثامنة|التاسع|التاسعة|العاشر|العاشرة)"
    rf"(?:(?-i:[a-h])(?=$|[.():;,\-–—])|(?=$|\s|[.():;,\-–—]))"
    rf"(?P<tail>(?:\s*(?:(?:ET|E|À|A)\b|,|[-–—])\s*"
    rf"(?:[0-9]{{1,4}}|[IVXLCDM]{{1,10}}|[Α-ΩϚϜϞϠ]{{1,8}}[΄'’]?)"
    rf"(?=$|\s|[.():;,\-–—]))*)",
    re.IGNORECASE,
)
LABEL_ONLY_PREFIX_RE = re.compile(
    rf"^\s*(?P<label>{LABEL_ALTERNATION})\s*[.():;,\-–—]*\s+(?P<title>\S.*)$",
    re.IGNORECASE,
)
ORDINAL_BEFORE_LABEL_RE = re.compile(
    rf"^\s*(?P<ordinal>[IVXLCDM]{{1,10}})\s*[.)\-–—:]*\s*"
    rf"(?P<label>{LABEL_ALTERNATION})\b",
    re.IGNORECASE,
)
PAGE_PREFIXED_LABEL_RE = re.compile(
    rf"^\s*[0-9]{{1,4}}\s+(?={LABEL_ALTERNATION}\b)",
    re.IGNORECASE,
)
CANDIDATE_CITATION_RE = re.compile(r"^\s*[,;]\s*(?:n(?:um)?|§)\.?\s*\d", re.I)
BARE_ORDINAL_RE = re.compile(
    r"^\s*(?P<ordinal>[0-9٠-٩]{1,3}|[IVXLCDM]{1,10}|[Α-ΩϚϜϞϠ]{1,8}[΄'’]?)"
    r"\s*[.)\-–—:]\s+(?P<title>\S.*)$",
    re.IGNORECASE,
)
TRAILING_REFERENCE_RE = re.compile(
    r"(?:\s+|\.{2,})(?:COL\.?\s*)?(?:\d{1,4}|IBID\.?)\s*$", re.I
)
TRAILING_REFERENCE_WITH_PUNCT_RE = re.compile(
    r"(?:\s+|\.{2,})(?:COL\.?\s*)?(?:\d{1,4}|IBID\.?)\s*[.)]?\s*$", re.I
)
STRUCTURAL_KIND_RE = re.compile(
    r"chapter|capitul|book|liber|part|tract|sermon|homil|epist|question|quaest|"
    r"interrog|dubitat|article|articul|oration|lectio|canon|distinct|proposit|formula|fasc",
    re.IGNORECASE,
)
CONTAINER_KIND_RE = re.compile(
    r"heading|author|container|division|section|group", re.IGNORECASE
)
KIND_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("chapter", re.compile(r"chapter|capitul", re.I)),
    ("book", re.compile(r"book|liber", re.I)),
    ("part", re.compile(r"part", re.I)),
    ("tract", re.compile(r"tract", re.I)),
    ("sermon", re.compile(r"sermon", re.I)),
    ("homily", re.compile(r"homil", re.I)),
    ("epistle", re.compile(r"epist", re.I)),
    ("question", re.compile(r"question|quaest|interrog|dubitat", re.I)),
    ("article", re.compile(r"article|articul", re.I)),
    ("oration", re.compile(r"oration", re.I)),
    ("lesson", re.compile(r"lesson|lectio", re.I)),
    ("canon", re.compile(r"canon", re.I)),
    ("distinction", re.compile(r"distinct", re.I)),
    ("proposition", re.compile(r"proposit", re.I)),
    ("formula", re.compile(r"formula", re.I)),
    ("fascicle", re.compile(r"fasc", re.I)),
)
WORD_ORDINALS = {
    "primus": 1,
    "prima": 1,
    "primum": 1,
    "secundus": 2,
    "secunda": 2,
    "secundum": 2,
    "tertius": 3,
    "tertia": 3,
    "tertium": 3,
    "quartus": 4,
    "quarta": 4,
    "quartum": 4,
    "quintus": 5,
    "quinta": 5,
    "quintum": 5,
    "sextus": 6,
    "sexta": 6,
    "sextum": 6,
    "septimus": 7,
    "septima": 7,
    "septimum": 7,
    "octavus": 8,
    "octava": 8,
    "octavum": 8,
    "nonus": 9,
    "nona": 9,
    "nonum": 9,
    "decimus": 10,
    "decima": 10,
    "decimum": 10,
    "premier": 1,
    "premiere": 1,
    "première": 1,
    "second": 2,
    "seconde": 2,
    "troisieme": 3,
    "troisième": 3,
    "quatrieme": 4,
    "quatrième": 4,
    "cinquieme": 5,
    "cinquième": 5,
    "sixieme": 6,
    "sixième": 6,
    "septieme": 7,
    "septième": 7,
    "huitieme": 8,
    "huitième": 8,
    "neuvieme": 9,
    "neuvième": 9,
    "dixieme": 10,
    "dixième": 10,
    "1er": 1,
    "الأول": 1,
    "الاول": 1,
    "الأولى": 1,
    "الاولى": 1,
    "الثاني": 2,
    "الثانية": 2,
    "الثالث": 3,
    "الثالثة": 3,
    "الرابع": 4,
    "الرابعة": 4,
    "الخامس": 5,
    "الخامسة": 5,
    "السادس": 6,
    "السادسة": 6,
    "السابع": 7,
    "السابعة": 7,
    "الثامن": 8,
    "الثامنة": 8,
    "التاسع": 9,
    "التاسعة": 9,
    "العاشر": 10,
    "العاشرة": 10,
}
MATCH_STOPWORDS = {
    "the",
    "and",
    "dans",
    "des",
    "les",
    "une",
    "est",
    "quid",
    "quod",
    "quae",
    "qui",
    "quo",
    "cum",
    "non",
    "sunt",
    "sive",
    "item",
    "caput",
    "capitulum",
    "chapter",
    "chapitre",
    "liber",
    "livre",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def payload_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def decision_algorithm_sha256() -> str:
    paths = [
        Path(__file__),
        Path(__file__).with_name("editorial_page_estimator.py"),
        Path(__file__).with_name("index_pipeline_ownership.py"),
        Path(__file__).with_name("index_target_locator.py"),
        PROJECT_ROOT / "tools" / "ocr_xml_utils.py",
        PROJECT_ROOT / "tools" / "corpus_utils.py",
        PROJECT_ROOT / "scripts" / "probe_chapter_targets.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(PROJECT_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _kind(entry: dict[str, Any]) -> str:
    raw_json = entry.get("raw_json")
    values = [entry.get("entry_kind")]
    if isinstance(raw_json, dict):
        values.extend(
            raw_json.get(key)
            for key in ("entry_kind", "kind", "type", "classification")
        )
    return " ".join(str(value or "") for value in values)


def _section_labels(section: dict[str, Any]) -> str:
    return " ".join(
        str(section.get(key) or "")
        for key in ("section_key", "scope_kind", "index_kind", "heading_raw", "heading_norm")
    )


def _label_family(label: str) -> str | None:
    for family, pattern in LABEL_PATTERNS:
        if re.fullmatch(pattern, label.strip(), re.I):
            return family
    return None


def _kind_family(kind: str) -> str | None:
    matches = {family for family, pattern in KIND_FAMILIES if pattern.search(kind)}
    return next(iter(matches)) if len(matches) == 1 else None


def ordinal_to_int(token: str) -> int | None:
    raw_cleaned = token.strip(" .,:;()[]{}'’΄")
    cleaned = raw_cleaned.casefold()
    if not cleaned:
        return None
    if all(char.isdigit() for char in cleaned):
        value = int("".join(str(unicodedata.digit(char)) for char in cleaned))
        return value if 0 < value <= 999 else None
    if cleaned.endswith("e") and cleaned[:-1].isdigit():
        return int(cleaned[:-1])
    if cleaned in WORD_ORDINALS:
        return WORD_ORDINALS[cleaned]
    roman = roman_to_int(cleaned.upper())
    if roman is not None:
        return roman
    return greek_numeral_to_int(raw_cleaned)


def _expand_ordinal_tail(first: int, tail: str) -> list[int]:
    if not tail.strip():
        return [first]
    tokens = re.findall(r"[0-9]{1,3}|[IVXLCDM]{1,10}|[Α-ΩϚϜϞϠ]{1,8}[΄'’]?", tail, re.I)
    values = [value for token in tokens if (value := ordinal_to_int(token)) is not None]
    if not values:
        return [first]
    separator_is_range = bool(re.search(r"[-–—]|\b(?:A|À)\b", tail, re.I)) and not re.search(
        r",|\bET\b|\bE\b", tail, re.I
    )
    if separator_is_range and len(values) == 1 and first < values[0] and values[0] - first <= 100:
        return list(range(first, values[0] + 1))
    return [first, *values]


def _chapter_tail_is_editorial_range(label: str, tail: str) -> bool:
    """Distinguish ``CAP. II, 119-244`` from a range of chapter ordinals."""
    compact_label = re.sub(r"[^A-Z]", "", label.upper())
    if compact_label in {"CAPP", "CAPITA", "CAPITULA"}:
        return False
    return bool(
        re.fullmatch(
            r"\s*,\s*(?:VERS?\.?\s*)?\d{1,4}\s*[-–—]\s*\d{1,4}\s*",
            tail,
            re.I,
        )
    )


@dataclass(slots=True)
class Marker:
    family: str
    ordinals: list[int]
    marker_raw: str
    title_raw: str
    explicit_label: bool


def parse_marker(
    text: str,
    *,
    kind: str = "",
    inherited_family: str | None = None,
    page_hint: int | None = None,
) -> Marker | None:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if not compact:
        return None
    match = LABELED_PREFIX_RE.match(compact)
    if match:
        ordinal = ordinal_to_int(match.group("ordinal"))
        family = _label_family(match.group("label"))
        if ordinal is None or family is None:
            return None
        tail = match.group("tail") or ""
        ordinals = (
            [ordinal]
            if family == "chapter" and _chapter_tail_is_editorial_range(match.group("label"), tail)
            else _expand_ordinal_tail(ordinal, tail)
        )
        if (
            page_hint is not None
            and len(ordinals) > 1
            and ordinals[-1] == page_hint
            and re.fullmatch(r"\s*,\s*\d{1,4}\s*", tail)
        ):
            ordinals = ordinals[:-1]
        remainder = compact[match.end() :]
        if len(ordinals) > 1 and re.match(
            r"\s*(?:EPISTOLAS?|CAPITULA|SERMONES?|HOMILIAS?|TRACTATUS)\b",
            remainder,
            re.I,
        ):
            ordinals = ordinals[:1]
        title = remainder.strip(" .,:;()[]-–—")
        return Marker(family, ordinals, match.group(0).strip(), title, True)
    match = ORDINAL_BEFORE_LABEL_RE.match(compact)
    if match:
        ordinal = ordinal_to_int(match.group("ordinal"))
        family = _label_family(match.group("label"))
        if ordinal is not None and family is not None:
            title = compact[match.end() :].strip(" .,:;()[]-–—")
            return Marker(family, [ordinal], match.group(0).strip(), title, True)
    match = LABEL_ONLY_PREFIX_RE.match(compact)
    if match:
        family = _label_family(match.group("label"))
        if family is not None:
            return Marker(
                family,
                [],
                match.group("label").strip(),
                match.group("title").strip(" .,:;()[]-–—"),
                True,
            )
    match = BARE_ORDINAL_RE.match(compact)
    if not match:
        return None
    token = match.group("ordinal")
    if re.fullmatch(r"[IVXLCDM]+", token, re.I) and token != token.upper():
        return None
    ordinal = ordinal_to_int(token)
    if ordinal is None or token.upper() in {"D", "M"}:
        return None
    if not inherited_family and not STRUCTURAL_KIND_RE.search(kind):
        return None
    return Marker(
        _kind_family(kind) or inherited_family or "ordinal",
        [ordinal],
        compact[: match.start("title")].strip(),
        match.group("title").strip(),
        False,
    )


def entry_query(entry: dict[str, Any], marker: Marker | None) -> str:
    raw = str(entry.get("target_raw") or entry.get("entry_raw") or "").strip()
    raw = TRAILING_REFERENCE_RE.sub("", raw).strip()
    parsed = parse_marker(raw, kind=_kind(entry), inherited_family=marker.family if marker else None)
    if parsed and parsed.title_raw:
        return parsed.title_raw
    if marker and marker.title_raw:
        return marker.title_raw
    return raw


@dataclass(slots=True)
class EntryRef:
    section_index: int
    entry_index: int
    section_key: str
    entry_key: str | None
    entry_raw: str
    family: str
    ordinals: list[int]
    query_raw: str
    source_files: list[str]
    context: list[str]
    existing_target_file: str | None
    existing_evidence: dict[str, Any] | None
    existing_target_is_generic_anchor: bool
    classification: str
    page_hint_int: int | None = None
    page_hint_source: str | None = None
    allow_direct_source: bool = False
    entry_source_files: list[str] = field(default_factory=list)

    @property
    def identity(self) -> str:
        return self.entry_key or f"section[{self.section_index}].entries[{self.entry_index}]"


@dataclass(slots=True)
class Segment:
    key: str
    family: str
    entries: list[EntryRef] = field(default_factory=list)
    context: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HeadingCandidate:
    family: str
    ordinal: int | None
    file: str
    file_position: int
    line: int
    heading_raw: str
    title_raw: str
    context: str
    explicit_label: bool
    block_role: str
    script: str
    bbox: str
    in_dense_structural_list: bool = False
    page_has_dense_structural_list: bool = False

    def scoring_dict(self, family: str) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "target_file": self.file,
            "heading_raw": self.heading_raw,
            "title_raw": self.title_raw,
            "context": self.context,
            "line": self.line,
            "style": "cap" if family == "chapter" else family,
            "qualified": False,
            "position": [self.file_position, self.line],
            "block_role": self.block_role,
            "script": self.script,
            "bbox": self.bbox,
        }


def _source_files(entry: dict[str, Any], section: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for obj in (entry, section):
        raw_json = obj.get("raw_json")
        if isinstance(raw_json, dict):
            for value in raw_json.get("source_files") or []:
                if value:
                    values.append(str(value))
    for key in ("file_start", "file_end"):
        if section.get(key):
            values.append(str(section[key]))
    return list(dict.fromkeys(values))


def _trailing_page_hint(text: str) -> int | None:
    match = re.search(r"(?<!\w)(\d{1,4})\s*[.)]?\s*$", text)
    if not match:
        return None
    prefix = text[: match.start()].rstrip(" .,;:()[]")
    if re.search(r"\b(?:PONTIFICATUS\s+)?ANNO\s+[IVXLCDM\d]+,?\s+CHRISTI$", prefix, re.I):
        return None
    if re.search(
        r"(?:\bANNO|\bA\.?\s*D\.?|\bCAP\.?|\bLIB\.?|\bEPIST\.?|"
        r"\bHOM\.?|\bN\.?|\b§|\bVERS?\.?|\bTOM\.?|\bVOL\.?)\s*$",
        prefix,
        re.I,
    ):
        return None
    value = int(match.group(1))
    return value if 0 < value < 10_000 else None


def _explicit_page_hint(
    entry: dict[str, Any], logical_entry_raw: str | None = None
) -> tuple[int | None, str | None]:
    entry_raw = str(logical_entry_raw or entry.get("entry_raw") or "").strip()
    trailing_hint = _trailing_page_hint(entry_raw)
    terminal_number_match = re.search(r"(?<!\w)(\d{1,4})\s*[.)]?\s*$", entry_raw)
    rejected_terminal_number = (
        int(terminal_number_match.group(1))
        if terminal_number_match and trailing_hint is None
        else None
    )
    raw_json = entry.get("raw_json")
    target_raw = str(entry.get("target_raw") or "").strip()
    suffix_hint: int | None = None
    if target_raw and entry_raw.startswith(target_raw):
        suffix = entry_raw[len(target_raw) :]
        match = re.fullmatch(r"\s*[.,;:]?\s*(?:COL\.?\s*)?(\d{1,4})\s*[.)]?\s*", suffix, re.I)
        if match and trailing_hint == int(match.group(1)):
            suffix_hint = int(match.group(1))
    proven_terminal_hint = trailing_hint if trailing_hint is not None else suffix_hint
    candidates = [entry.get("page_ref_int")]
    if isinstance(raw_json, dict):
        candidates.extend(
            raw_json.get(key)
            for key in ("page_ref_int", "inferred_printed_page", "editorial_page")
        )
        candidates.extend(raw_json.get("page_hint_ints") or [])
    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if (
            0 < value < 10_000
            and value != rejected_terminal_number
            and proven_terminal_hint == value
        ):
            return value, "payload_page_ref"
    if suffix_hint is not None:
        return suffix_hint, "target_raw_suffix"
    if trailing_hint is not None:
        return trailing_hint, "logical_entry_terminal"
    return None, None


def _is_ibid_page_reference(entry: dict[str, Any]) -> bool:
    values = [entry.get("page_ref_raw"), entry.get("entry_raw")]
    raw_json = entry.get("raw_json")
    if isinstance(raw_json, dict):
        values.extend(raw_json.get(key) for key in ("page_ref_raw", "ref_raw"))
    return any(re.search(r"\bIBID(?:EM)?\.?\s*$", str(value or ""), re.I) for value in values)


def _locator_version(value: Any) -> int:
    try:
        version = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, version)


def _entry_source_files(entry: dict[str, Any]) -> list[str]:
    raw_json = entry.get("raw_json")
    if not isinstance(raw_json, dict):
        return []
    return [str(value) for value in raw_json.get("source_files") or [] if value]


def _context_heading(entry: dict[str, Any]) -> bool:
    kind = _kind(entry)
    if CONTAINER_KIND_RE.search(kind):
        return True
    text = str(entry.get("entry_raw") or "")
    return bool(
        text
        and len(text) <= 180
        and text.upper() == text
        and not re.search(r"\d{1,4}\s*$", text)
    )


def _logical_structural_entry_texts(
    section: dict[str, Any],
) -> tuple[dict[int, str], set[int]]:
    labels = _section_labels(section)
    if not re.search(r"\b(?:ORDO|CONTENTS?|CAPITULA|INDEX\s+CAPITUM)\b", labels, re.I):
        return {}, set()
    entries = section.get("entries") or []
    combined: dict[int, str] = {}
    consumed: set[int] = set()
    for index, entry in enumerate(entries):
        if index in consumed or not isinstance(entry, dict):
            continue
        raw = str(entry.get("entry_raw") or "").strip()
        marker = parse_marker(raw, kind=_kind(entry))
        bare_marker = False
        if marker is None:
            marker = parse_marker(raw, kind=_kind(entry), inherited_family="ordinal")
            bare_marker = marker is not None
        if marker is None or not marker.ordinals:
            continue
        parts = [raw]
        cursor = index + 1
        previous_entry = entry
        while cursor < len(entries):
            following = entries[cursor]
            if not isinstance(following, dict):
                break
            continuation = str(following.get("entry_raw") or "").strip()
            if not continuation or parse_marker(continuation, kind=_kind(following)) is not None:
                break
            previous = parts[-1]
            previous_without_ref = TRAILING_REFERENCE_WITH_PUNCT_RE.sub("", previous).rstrip()
            looks_continued = bool(
                re.match(r"^[a-zà-öø-ÿæœ]", continuation)
                or re.search(r"[-,;:]\s*$", previous_without_ref)
                or (
                    not TRAILING_REFERENCE_WITH_PUNCT_RE.search(previous)
                    and TRAILING_REFERENCE_WITH_PUNCT_RE.search(continuation)
                )
            )
            if bare_marker:
                previous_raw_json = previous_entry.get("raw_json")
                following_raw_json = following.get("raw_json")
                if not (
                    isinstance(previous_raw_json, dict)
                    and isinstance(following_raw_json, dict)
                    and previous_raw_json.get("source_file")
                    == following_raw_json.get("source_file")
                    and previous_raw_json.get("source_block_kind")
                    == following_raw_json.get("source_block_kind")
                    and isinstance(previous_raw_json.get("source_line_index"), int)
                    and following_raw_json.get("source_line_index")
                    == previous_raw_json.get("source_line_index") + 1
                ):
                    looks_continued = False
            if not looks_continued:
                break
            if previous_without_ref.endswith("-"):
                parts[-1] = previous_without_ref[:-1]
                parts.append(continuation)
            else:
                parts[-1] = previous_without_ref
                parts.append(" " + continuation)
            consumed.add(cursor)
            previous_entry = following
            cursor += 1
        if len(parts) > 1:
            combined[index] = "".join(parts)
    return combined, consumed


def classify_segments(payload: dict[str, Any]) -> tuple[list[Segment], list[dict[str, Any]]]:
    segments: list[Segment] = []
    dispositions: list[dict[str, Any]] = []
    volume_is_index_compendium = any(
        re.search(
            r"PATROLOGI[ÆAE].{0,80}TOMUS.{0,40}INDICES\b",
            _section_labels(section),
            re.IGNORECASE | re.DOTALL,
        )
        for section in payload.get("sections") or []
        if isinstance(section, dict)
    )
    volume_has_thematic_reference_index = bool(
        re.search(
            r"(?:INDEX\s+)?PATR(?:OLOG|IST)ICO[- ]THEOLOGICUS|INDEX\s+GENERALIS\s+ANALYTICUS",
            json.dumps((payload.get("volume") or {}).get("scan_summary") or {}, ensure_ascii=False),
            re.IGNORECASE,
        )
    )
    for section_index, section in enumerate(payload.get("sections") or []):
        if not isinstance(section, dict):
            continue
        section_key = str(section.get("section_key") or f"section[{section_index}]")
        section_raw_json = section.get("raw_json")
        allow_direct_source = bool(
            isinstance(section_raw_json, dict)
            and (
                section_raw_json.get("entries_are_body_headings") is True
                or re.search(
                    r"structural line item beginning|displayed work/book and epistle entrypoints",
                    str(section_raw_json.get("entries_status_reason") or ""),
                    re.I,
                )
            )
        )
        if volume_is_index_compendium:
            dispositions.append(
                {
                    "section_index": section_index,
                    "section_key": section_key,
                    "classification": "ineligible_index_compendium",
                    "entry_count": len(section.get("entries") or []),
                }
            )
            continue
        if (
            volume_has_thematic_reference_index
            and not section.get("work_key")
            and re.search(r"INDEX[_ ]CAPITUM", _section_labels(section), re.IGNORECASE)
        ):
            dispositions.append(
                {
                    "section_index": section_index,
                    "section_key": section_key,
                    "classification": "ineligible_thematic_reference_index",
                    "entry_count": len(section.get("entries") or []),
                }
            )
            continue
        if (
            str(section.get("scope_kind") or "").casefold() in INELIGIBLE_SCOPE_KINDS
            or INELIGIBLE_SECTION_RE.search(_section_labels(section))
            or NON_STRUCTURAL_REFERENCE_INDEX_RE.search(_section_labels(section))
        ):
            dispositions.append(
                {
                    "section_index": section_index,
                    "section_key": section_key,
                    "classification": "ineligible_retrospective_scope",
                    "entry_count": len(section.get("entries") or []),
                }
            )
            continue
        owned, ownership_reason = general_section_ownership(section)
        if not owned:
            dispositions.append(
                {
                    "section_index": section_index,
                    "section_key": section_key,
                    "classification": "ineligible_non_general_section",
                    "entry_count": len(section.get("entries") or []),
                    "reason": ownership_reason,
                }
            )
            continue
        context: list[str] = []
        inherited_family: str | None = None
        current: Segment | None = None
        high_water = 0
        break_before_next = True
        logical_texts, continuation_indices = _logical_structural_entry_texts(section)
        for entry_index, entry in enumerate(section.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            if entry_index in continuation_indices:
                continue
            entry_raw = str(entry.get("entry_raw") or "")
            search_entry_raw = logical_texts.get(entry_index, entry_raw)
            kind = _kind(entry)
            raw_json = entry.get("raw_json")
            explicit_page_hint, page_hint_source = _explicit_page_hint(entry, search_entry_raw)
            raw_entry_kind = (
                str(raw_json.get("entry_kind") or "")
                if isinstance(raw_json, dict)
                else ""
            )
            if re.search(r"(?:^|_)paragraph(?:$|_)", raw_entry_kind, re.I):
                dispositions.append(
                    {
                        "section_index": section_index,
                        "entry_index": entry_index,
                        "section_key": section_key,
                        "entry_key": entry.get("entry_key"),
                        "classification": "ineligible_numbered_body_paragraph",
                    }
                )
                continue
            if isinstance(raw_json, dict) and re.search(
                r"kept as OCR line|staging|consumed fragment",
                str(raw_json.get("mapping_note") or ""),
                re.I,
            ):
                dispositions.append(
                    {
                        "section_index": section_index,
                        "entry_index": entry_index,
                        "section_key": section_key,
                        "entry_key": entry.get("entry_key"),
                        "classification": "ineligible_staging_ocr_line",
                    }
                )
                break_before_next = True
                continue
            if EXTERNAL_RE.search(entry_raw) or (
                isinstance(raw_json, dict)
                and str(raw_json.get("reference_semantics") or "").casefold()
                in {"external", "cross_volume", "remissive"}
            ):
                dispositions.append(
                    {
                        "section_index": section_index,
                        "entry_index": entry_index,
                        "section_key": section_key,
                        "entry_key": entry.get("entry_key"),
                        "classification": "ineligible_external",
                    }
                )
                continue
            marker = parse_marker(
                search_entry_raw,
                kind=kind,
                inherited_family=inherited_family,
                page_hint=explicit_page_hint,
            )
            if marker is None and STRUCTURAL_KIND_RE.search(kind):
                query = entry_query(
                    {**entry, "entry_raw": search_entry_raw, "target_raw": search_entry_raw},
                    None,
                )
                if len(normalize_for_search(query).split()) >= 3:
                    marker = Marker(
                        inherited_family or "title_only",
                        [],
                        "",
                        query,
                        False,
                    )
            if marker is None:
                if _context_heading(entry):
                    context = [*context[-2:], entry_raw]
                    break_before_next = True
                    label_only = next(
                        (family for family, pattern in LABEL_PATTERNS if re.search(rf"\b(?:{pattern})\b", entry_raw, re.I)),
                        None,
                    )
                    inherited_family = label_only
                    high_water = 0
                continue
            if len(marker.ordinals) > 1 and explicit_page_hint is None:
                dispositions.append(
                    {
                        "section_index": section_index,
                        "entry_index": entry_index,
                        "section_key": section_key,
                        "entry_key": entry.get("entry_key"),
                        "classification": "ineligible_multi_ordinal",
                        "ordinals": marker.ordinals,
                        "reason": "A singular target_file cannot safely represent a range or enumeration.",
                    }
                )
                break_before_next = True
                continue
            if not marker.ordinals and explicit_page_hint is None:
                dispositions.append(
                    {
                        "section_index": section_index,
                        "entry_index": entry_index,
                        "section_key": section_key,
                        "entry_key": entry.get("entry_key"),
                        "classification": "ineligible_unordinaled_structural",
                        "reason": "Unnumbered title-only matching is not strong enough for automatic repair.",
                    }
                )
                break_before_next = True
                continue
            if marker.explicit_label:
                inherited_family = marker.family
            first_ordinal = marker.ordinals[0] if marker.ordinals else 0
            restart = bool(marker.ordinals and first_ordinal == 1 and high_water > 1)
            family_change = current is not None and current.family != marker.family
            if current is None or break_before_next or restart or family_change:
                current = Segment(
                    key=f"{section_key}:structural-segment:{len(segments) + 1:04d}",
                    family=marker.family,
                    context=list(context),
                )
                segments.append(current)
                high_water = 0
            page_hint = explicit_page_hint
            if page_hint is None and _is_ibid_page_reference(entry) and current.entries:
                page_hint = current.entries[-1].page_hint_int
                if page_hint is not None:
                    page_hint_source = "immediate_ibid"
            ref = EntryRef(
                section_index=section_index,
                entry_index=entry_index,
                section_key=section_key,
                entry_key=str(entry.get("entry_key")) if entry.get("entry_key") else None,
                entry_raw=entry_raw,
                family=marker.family,
                ordinals=marker.ordinals,
                query_raw=entry_query(
                    {**entry, "entry_raw": search_entry_raw, "target_raw": search_entry_raw},
                    marker,
                ),
                source_files=_source_files(entry, section),
                context=list(context),
                existing_target_file=str(entry.get("target_file")) if entry.get("target_file") else None,
                existing_evidence=(
                    raw_json.get("physical_target_evidence")
                    if isinstance(raw_json, dict)
                    and isinstance(raw_json.get("physical_target_evidence"), dict)
                    else None
                ),
                existing_target_is_generic_anchor=(
                    isinstance(raw_json, dict)
                    and isinstance(raw_json.get("prior_payload_locator"), dict)
                    and bool(
                        re.search(
                            r"work opening|attached index|not separately resolved|systematic.*anchor.*collapse",
                            str(raw_json["prior_payload_locator"].get("target_anchor") or ""),
                            re.I,
                        )
                    )
                    and not isinstance(raw_json.get("physical_target_evidence"), dict)
                    and not isinstance(raw_json.get("ex_post_chapter_target_locator"), dict)
                ),
                classification=(
                    "eligible_editorial_page_only"
                    if len(marker.ordinals) != 1
                    else "eligible_structural_exact"
                    if marker.explicit_label
                    else "eligible_structural_contextual"
                ),
                page_hint_int=page_hint,
                page_hint_source=page_hint_source,
                allow_direct_source=allow_direct_source,
                entry_source_files=_entry_source_files(entry),
            )
            current.entries.append(ref)
            if marker.ordinals:
                high_water = max(high_water, max(marker.ordinals))
            break_before_next = False
    return segments, dispositions


def _candidate_lines(path: Path) -> Iterable[tuple[int, str, str, str, str, str]]:
    page = read_ocr_page(path)
    lines: list[tuple[str, str, str, str]] = []
    for block in page.blocks:
        tipo = block.tipo.casefold()
        if block.tag_name == "rodape":
            continue
        if any(marker in tipo for marker in ("nota", "aparato", "rodape", "footer")):
            continue
        role = "header" if block.tag_name == "cabecalho" or tipo in {"cabecalho", "header"} else "body"
        lines.extend(
            (line.strip(), role, block.script, block.bbox)
            for line in block.content_clean.splitlines()
            if line.strip()
        )
    for index, (line, role, script, bbox) in enumerate(lines):
        following = [item[0] for item in lines[index + 1 : index + 3]]
        yield index + 1, line, " ".join([line, *following]), role, script, bbox


@lru_cache(maxsize=200_000)
def _meaningful_tokens(text: str) -> tuple[str, ...]:
    normalized = normalize_for_search(text)
    return tuple(
        token
        for token in re.findall(r"[^\W\d_]{3,}", normalized, re.UNICODE)
        if token not in MATCH_STOPWORDS
    )


def _weak_heading_title(text: str) -> bool:
    tokens = _meaningful_tokens(text)
    return not tokens or (len(tokens) == 1 and len(tokens[0]) <= 2)


def _explicit_label_is_lowercase(text: str) -> bool:
    match = LABELED_PREFIX_RE.match(text)
    return bool(match and match.group("label").islower())


def _scan_heading_file(item: tuple[int, str]) -> list[HeadingCandidate]:
    file_position, path_raw = item
    path = Path(path_raw)
    candidates: list[HeadingCandidate] = []
    inherited_family: str | None = None
    parsed_rows: list[tuple[int, str, str, str, str, str, Marker]] = []
    rows = list(_candidate_lines(path))
    for line_number, line, context, block_role, script, bbox in rows:
        candidate_line = re.sub(r"\[/?(?:br|i|b)[^\]]*\]", " ", line, flags=re.I)
        candidate_line = PAGE_PREFIXED_LABEL_RE.sub("", candidate_line, count=1)
        marker = parse_marker(candidate_line, kind="", inherited_family=inherited_family)
        if marker is None:
            continue
        if block_role == "header" and not marker.explicit_label:
            continue
        if marker.explicit_label and _explicit_label_is_lowercase(candidate_line):
            continue
        if marker.explicit_label:
            label_match = LABELED_PREFIX_RE.match(candidate_line)
            if label_match and CANDIDATE_CITATION_RE.match(candidate_line[label_match.end() :]):
                continue
        if marker.explicit_label:
            inherited_family = marker.family
        parsed_rows.append((line_number, line, context, block_role, script, bbox, marker))

    dense_rows: set[int] = set()
    by_block: dict[tuple[str, str], list[tuple[int, Marker]]] = {}
    plural_chapter_blocks: dict[tuple[str, str], list[int]] = {}
    row_text = {line_number: line for line_number, line, *_ in rows}
    for line_number, _line, _context, block_role, _script, bbox, marker in parsed_rows:
        if marker.ordinals:
            by_block.setdefault((block_role, bbox), []).append((line_number, marker))
        compact_label = re.sub(r"[^A-Z]", "", marker.marker_raw.upper())
        if not marker.ordinals and compact_label in {"CAPP", "CAPITA", "CAPITULA"}:
            plural_chapter_blocks.setdefault((block_role, bbox), []).append(line_number)
    for block, parsed_markers in by_block.items():
        run: list[tuple[int, Marker]] = []
        for line_number, marker in parsed_markers:
            if run:
                previous_line, previous_marker = run[-1]
                gap = range(previous_line + 1, line_number)
                maximum_gap = 8 if not previous_marker.explicit_label else 3
                if (
                    marker.explicit_label != previous_marker.explicit_label
                    or line_number - previous_line > maximum_gap
                    or any(
                    len(row_text.get(position, "")) > 180 for position in gap
                    )
                ):
                    if len(run) >= 4:
                        dense_rows.update(item[0] for item in run)
                    run = []
            run.append((line_number, marker))
        if len(run) >= 4:
            dense_rows.update(item[0] for item in run)
        plural_starts = plural_chapter_blocks.get(block) or []
        if plural_starts:
            first_plural = min(plural_starts)
            dense_rows.update(
                line_number
                for line_number, marker in parsed_markers
                if line_number > first_plural
                and marker.family == "chapter"
                and not marker.explicit_label
            )
    pagewide_ordinal_rows = [
        (line_number, marker)
        for line_number, _line, _context, _block_role, _script, _bbox, marker in parsed_rows
        if marker.ordinals
    ]
    has_index_list_heading = any(
        len(line) <= 180
        and INDEX_LIST_HEADING_RE.search(line)
        and (
            block_role == "header"
            or sum(character.isupper() for character in line)
            >= sum(character.islower() for character in line)
        )
        for _line_number, line, _context, block_role, _script, _bbox in rows
    )
    if has_index_list_heading and len(parsed_rows) >= 2:
        dense_rows.update(line_number for line_number, *_rest in parsed_rows)
    # A run of bare numbered paragraphs can be dense inside an ordinary body
    # page.  Keep those individual rows out of heading-sequence matching, but
    # only quarantine the whole page when it advertises itself as an index/list
    # or the dense run itself consists of explicit structural headings.
    explicit_dense_rows = {
        line_number
        for line_number, _line, _context, _block_role, _script, _bbox, marker in parsed_rows
        if line_number in dense_rows and marker.explicit_label
    }
    page_has_dense_structural_list = has_index_list_heading or bool(explicit_dense_rows)

    for line_number, line, context, block_role, script, bbox, marker in parsed_rows:
        title = marker.title_raw
        if _weak_heading_title(title):
            remainder = context[len(line) :].strip()
            remainder = re.sub(r"\[/?(?:br|i|b)[^\]]*\]", " ", remainder, flags=re.I)
            next_marker = parse_marker(remainder, kind="chapter_entry", inherited_family=marker.family)
            if remainder and next_marker is None:
                title = remainder
        for ordinal in marker.ordinals or [None]:
            candidates.append(
                HeadingCandidate(
                    family=marker.family,
                    ordinal=ordinal,
                    file=str(path),
                    file_position=file_position,
                    line=line_number,
                    heading_raw=line,
                    title_raw=title,
                    context=context,
                    explicit_label=marker.explicit_label,
                    block_role=block_role,
                    script=script,
                    bbox=bbox,
                    in_dense_structural_list=line_number in dense_rows,
                    page_has_dense_structural_list=page_has_dense_structural_list,
                )
            )
    return candidates


def scan_dense_structural_headings(path: Path) -> list[HeadingCandidate]:
    """Return block-aware heading candidates that form a dense structural list."""
    return [
        candidate
        for candidate in _scan_heading_file((0, str(path)))
        if candidate.in_dense_structural_list
    ]


def build_heading_catalog(files: list[Path], *, workers: int = 1) -> list[HeadingCandidate]:
    tasks = [(index, str(path)) for index, path in enumerate(files)]
    if workers <= 1 or len(tasks) <= 1:
        batches = map(_scan_heading_file, tasks)
    else:
        executor = ProcessPoolExecutor(max_workers=min(workers, len(tasks)))
        batches = executor.map(_scan_heading_file, tasks, chunksize=max(1, len(tasks) // (workers * 4)))
    try:
        candidates = [candidate for batch in batches for candidate in batch]
    finally:
        if workers > 1 and len(tasks) > 1:
            executor.shutdown()
    candidates.sort(key=lambda candidate: (candidate.file_position, candidate.line, candidate.family, candidate.ordinal or -1))
    return candidates


def build_editorial_page_catalog(
    *, volume_id: str, source_root: Path, files: list[Path]
) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    collection = volume_id[:2].upper()
    if collection not in {"PG", "PL"}:
        return {}, {"status": "unsupported_collection", "collection": collection}
    from .editorial_page_estimator import best_guess_pages, estimate_editorial_pages

    corpus_root = (PROJECT_ROOT / "teste").resolve()
    production_source = source_root.resolve().is_relative_to(corpus_root)
    estimate = estimate_editorial_pages(
        volume_id=volume_id,
        source_root=source_root,
        files=files,
        collection=collection,
        window=4,
        db_path=None if production_source else Path(":memory:"),
        use_cache=production_source,
    )
    catalog: dict[int, list[dict[str, Any]]] = {}
    accepted_files = 0
    positions = {str(path.resolve()): index for index, path in enumerate(files)}
    for item in estimate.get("files") or []:
        confidence = float(item.get("confidence") or 0.0)
        warnings = [str(value) for value in item.get("warnings") or []]
        if confidence < MINIMUM_EDITORIAL_PAGE_CONFIDENCE or any(
            warning in {"competing_hypotheses_close", "nonconsecutive_header_pair_suspected_ocr"}
            for warning in warnings
        ):
            continue
        pages = best_guess_pages(item.get("best_guess"))
        if not pages:
            continue
        accepted_files += 1
        file_path = str(Path(str(item.get("file") or "")).resolve())
        record = {
            "file": file_path,
            "file_position": positions.get(file_path, -1),
            "pages": pages,
            "confidence": confidence,
            "confidence_label": item.get("confidence_label"),
            "warnings": warnings,
            "evidence_kinds": [
                str(evidence.get("kind") or "")
                for evidence in item.get("evidence") or []
                if evidence.get("kind")
            ],
        }
        if record["file_position"] < 0:
            continue
        for page in pages:
            catalog.setdefault(page, []).append(record)
    return catalog, {
        "status": "ok",
        "estimator_db": estimate.get("db"),
        "estimator_window": estimate.get("window"),
        "source_file_count": len(files),
        "accepted_high_confidence_files": accepted_files,
        "mapped_editorial_pages": len(catalog),
        "summary": estimate.get("summary") or {},
    }


def _resolve_path(value: str, source_root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    candidate = (source_root / path).resolve()
    return candidate if candidate.exists() else path.resolve()


def _section_exclusions(payload: dict[str, Any], files: list[Path]) -> dict[int, set[str]]:
    positions = {str(path.resolve()): index for index, path in enumerate(files)}
    result: dict[int, set[str]] = {}
    all_index_files: set[str] = set()
    source_root = files[0].parent if files else Path(".")
    for section_index, section in enumerate(payload.get("sections") or []):
        if not isinstance(section, dict):
            continue
        raw_json = section.get("raw_json")
        raw_source_values = (
            raw_json.get("source_files") if isinstance(raw_json, dict) else []
        )
        explicit_sources = [
            str(value)
            for value in raw_source_values
            if value
        ] if isinstance(raw_source_values, list) else []
        excluded = {
            str(_resolve_path(value, source_root)) for value in explicit_sources
        }
        start = str(_resolve_path(str(section.get("file_start")), source_root)) if section.get("file_start") else None
        end = str(_resolve_path(str(section.get("file_end")), source_root)) if section.get("file_end") else None
        if not explicit_sources and start:
            excluded.add(start)
        if not explicit_sources and end:
            excluded.add(end)
        if not explicit_sources and start in positions and end in positions:
            lo, hi = sorted((positions[start], positions[end]))
            excluded.update(str(path.resolve()) for path in files[lo : hi + 1])
        result[section_index] = excluded
        all_index_files.update(excluded)
    for excluded in result.values():
        excluded.update(all_index_files)
    return result


def _window_for_entry(
    ref: EntryRef,
    payload: dict[str, Any],
    files: list[Path],
    *,
    positions: dict[str, int] | None = None,
    works: list[dict[str, Any]] | None = None,
    work_index_by_key: dict[str, int] | None = None,
    work_key: Any = None,
) -> tuple[int, int, str]:
    if positions is None:
        positions = {str(path.resolve()): index for index, path in enumerate(files)}
    source_root = files[0].parent
    if works is None:
        works = [work for work in payload.get("works") or [] if isinstance(work, dict)]
    if work_key is None:
        raw_json = (payload["sections"][ref.section_index].get("entries") or [])[ref.entry_index].get("raw_json")
        work_key = payload["sections"][ref.section_index].get("work_key")
        if isinstance(raw_json, dict):
            work_key = raw_json.get("parent_work_key") or raw_json.get("work_key") or work_key
    if work_index_by_key is None:
        work_index_by_key = {}
        for index, work in enumerate(works):
            key = work.get("work_key")
            if key is not None:
                work_index_by_key.setdefault(str(key), index)
    work_index = work_index_by_key.get(str(work_key)) if work_key is not None else None
    if work_index is not None:
        work = works[work_index]
        start_path = _resolve_path(str(work.get("start_file")), source_root) if work.get("start_file") else None
        end_path = _resolve_path(str(work.get("end_file")), source_root) if work.get("end_file") else None
        start = positions.get(str(start_path), 0)
        if end_path is not None and str(end_path) in positions:
            return start, positions[str(end_path)] + 1, "bounded_by_work_start_and_end"
        later = []
        for later_work in works[work_index + 1 :]:
            if later_work.get("start_file"):
                later_path = _resolve_path(str(later_work["start_file"]), source_root)
                if str(later_path) in positions and positions[str(later_path)] > start:
                    later.append(positions[str(later_path)])
        return start, min(later) if later else len(files), "bounded_by_work_start_and_next_start"
    section = payload["sections"][ref.section_index]
    start_path = _resolve_path(str(section.get("file_start")), source_root) if section.get("file_start") else None
    end_path = _resolve_path(str(section.get("file_end")), source_root) if section.get("file_end") else None
    anchors = [positions[str(path)] for path in (start_path, end_path) if path is not None and str(path) in positions]
    if anchors and str(section.get("scope_kind") or "").casefold() in {"volume_end", "volume_back", "volume_index"}:
        return 0, min(anchors), "volume_body_before_closing_index"
    if anchors:
        return max(anchors) + 1, len(files), "volume_body_after_opening_index"
    return 0, len(files), "whole_volume_fallback"


def _compact_candidates(
    scored: list[tuple[float, HeadingCandidate]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for score, candidate in scored:
        key = candidate.file
        if key in seen_files:
            continue
        seen_files.add(key)
        result.append(
            {
                "target_file": candidate.file,
                "heading_raw": candidate.heading_raw,
                "title_raw": candidate.title_raw,
                "line": candidate.line,
                "ordinal": candidate.ordinal,
                "similarity": round(score, 6),
                "block_role": candidate.block_role,
                "script": candidate.script,
                "bbox": candidate.bbox,
            }
        )
        if len(result) >= limit:
            break
    return result


def _chapter_title_alias(ref: EntryRef, candidate: HeadingCandidate) -> bool:
    if ref.family != "chapter" or candidate.family != "chapter":
        return False
    ref_is_title = bool(re.match(r"^\s*TIT(?:ULUS|ULUM|\.)", ref.entry_raw, re.I))
    candidate_is_title = bool(re.match(r"^\s*TIT(?:ULUS|ULUM|\.)", candidate.heading_raw, re.I))
    return ref_is_title != candidate_is_title


def _exact_heading_title_prefix(ref: EntryRef, candidate: HeadingCandidate) -> bool:
    """Recognize short titles whose similarity is intentionally capped.

    A title such as ``De grammatica`` has only one meaningful token after
    stop-word removal.  Fuzzy similarity alone must remain insufficient, but
    an exact prefix on an explicit heading, with the same structural marker,
    is strong deterministic evidence.
    """
    if not candidate.explicit_label or candidate.family != ref.family:
        return False
    reference_tokens = _meaningful_tokens(ref.query_raw)
    candidate_tokens = _meaningful_tokens(candidate.title_raw)
    if not reference_tokens or min(map(len, reference_tokens)) < 5:
        return False
    return candidate_tokens[: len(reference_tokens)] == reference_tokens


def _position(candidate: HeadingCandidate) -> tuple[int, int]:
    return candidate.file_position, candidate.line


def _adjacent_heading_over_editorial_map(
    ref: EntryRef,
    mapped: list[HeadingCandidate],
    ranked: list[tuple[float, HeadingCandidate]],
    *,
    minimum_margin: float,
) -> list[HeadingCandidate]:
    if len(ref.ordinals) != 1 or len(mapped) != 1:
        return []
    mapped_candidate = mapped[0]
    per_file: list[tuple[float, HeadingCandidate]] = []
    seen_files: set[str] = set()
    for score, candidate in ranked:
        if candidate.file in seen_files:
            continue
        seen_files.add(candidate.file)
        per_file.append((score, candidate))
    adjacent = [
        item
        for item in per_file
        if item[1].file != mapped_candidate.file
        and abs(item[1].file_position - mapped_candidate.file_position) == 1
        and item[1].explicit_label
        and item[1].family == ref.family
        and item[1].ordinal == ref.ordinals[0]
        and item[0] >= MINIMUM_EDITORIAL_NEIGHBOR_TITLE_SIMILARITY
    ]
    if not adjacent:
        return []
    best_score, best_candidate = max(
        adjacent,
        key=lambda item: (item[0], -item[1].file_position, -item[1].line),
    )
    second_score = max(
        (score for score, candidate in per_file if candidate.file != best_candidate.file),
        default=0.0,
    )
    if best_score - second_score < minimum_margin:
        return []
    return [best_candidate]


def _inspected_files(files: list[Path], start: int, end: int, chosen: list[HeadingCandidate]) -> list[str]:
    selected: list[str] = []
    for candidate in chosen:
        index = candidate.file_position
        for position in range(max(start, index - 1), min(end, index + 2)):
            selected.append(str(files[position]))
    if not selected and start < end:
        selected.extend([str(files[start]), str(files[end - 1])])
    return list(dict.fromkeys(selected))


def _evidence(
    ref: EntryRef,
    segment: Segment,
    *,
    status: str,
    method: str,
    reason: str,
    files: list[Path],
    start: int,
    end: int,
    window_reason: str,
    chosen: list[HeadingCandidate] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    disposition: str | None = None,
    editorial_page: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chosen = chosen or []
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "locator": LOCATOR_NAME,
        "locator_version": LOCATOR_VERSION,
        "status": status,
        "entry_key": ref.entry_key,
        "structural_family": ref.family,
        "method": method,
        "query_raw": ref.entry_raw,
        "body_window": {
            "start_file": str(files[start]) if start < end else None,
            "end_file": str(files[end - 1]) if start < end else None,
            "reason": window_reason,
        },
        "inspected_files": _inspected_files(files, start, end, chosen),
        "reason": reason,
        "sequence": {
            "segment_key": segment.key,
            "index_ordinals": ref.ordinals,
            "matched_body_ordinals": (
                []
                if method == "editorial_page_map"
                else [candidate.ordinal for candidate in chosen]
            ),
        },
    }
    if editorial_page is not None:
        evidence["editorial_page_locator"] = editorial_page
    if disposition:
        evidence["disposition"] = disposition
    if status == "resolved" and chosen:
        evidence.update(
            {
                "target_file": chosen[0].file,
                "matched_heading_raw": chosen[0].heading_raw,
                "resolved_targets": [
                    {
                        "ordinal": candidate.ordinal,
                        "target_file": candidate.file,
                        "matched_heading_raw": candidate.heading_raw,
                        "line": candidate.line,
                        "block_role": candidate.block_role,
                        "script": candidate.script,
                        "bbox": candidate.bbox,
                    }
                    for candidate in chosen
                ],
            }
        )
    if candidates:
        evidence["candidates"] = candidates
    return evidence


def _score_for_ref(ref: EntryRef, candidate: HeadingCandidate) -> float:
    query = ref.query_raw or ref.entry_raw
    query_tokens = _meaningful_tokens(query)
    candidate_tokens = _meaningful_tokens(candidate.title_raw)
    if not query_tokens or not candidate_tokens:
        return 0.0

    query_unique = tuple(dict.fromkeys(query_tokens))
    candidate_unique = tuple(dict.fromkeys(candidate_tokens))

    def token_matches(candidate_token: str, query_token: str) -> bool:
        if candidate_token == query_token:
            return True
        total_length = len(candidate_token) + len(query_token)
        if 2.0 * min(len(candidate_token), len(query_token)) / total_length < 0.76:
            return False
        matcher = difflib.SequenceMatcher(
            None,
            candidate_token,
            query_token,
            autojunk=False,
        )
        # Both methods are documented upper bounds for ratio().  Rejecting a
        # pair here therefore preserves the old >= 0.76 decision while avoiding
        # the expensive matching-block calculation for obviously unrelated
        # words.  any() also stops after the first qualifying OCR-near match.
        return (
            matcher.real_quick_ratio() >= 0.76
            and matcher.quick_ratio() >= 0.76
            and matcher.ratio() >= 0.76
        )

    fuzzy_matches = sum(
        any(token_matches(candidate_token, query_token) for query_token in query_unique)
        for candidate_token in candidate_unique
    )
    candidate_coverage = fuzzy_matches / len(candidate_unique)
    query_coverage = min(1.0, fuzzy_matches / max(1, min(4, len(query_unique))))
    token_score = 0.75 * candidate_coverage + 0.25 * query_coverage
    query_norm = " ".join(query_tokens)
    candidate_norm = " ".join(candidate_tokens)
    similarity = difflib.SequenceMatcher(
        None,
        query_norm[:300],
        candidate_norm[:300],
        autojunk=False,
    ).ratio()

    def contains_token_sequence(longer: tuple[str, ...], shorter: tuple[str, ...]) -> bool:
        return any(
            longer[index : index + len(shorter)] == shorter
            for index in range(len(longer) - len(shorter) + 1)
        )

    if query_tokens and candidate_tokens and (
        contains_token_sequence(query_tokens, candidate_tokens)
        or contains_token_sequence(candidate_tokens, query_tokens)
    ):
        similarity = 1.0
    score = max(token_score, similarity)
    # A one-word side can help a sequence, but is too weak to anchor one by itself.
    if min(len(set(query_tokens)), len(set(candidate_tokens))) == 1:
        score = min(score, 0.78)
    return round(score, 6)


def locate_segment(
    segment: Segment,
    *,
    payload: dict[str, Any],
    files: list[Path],
    catalog: list[HeadingCandidate],
    catalog_index: dict[tuple[str, int | None], list[HeadingCandidate]] | None,
    exclusions: dict[int, set[str]],
    positions: dict[str, int] | None = None,
    works: list[dict[str, Any]] | None = None,
    work_index_by_key: dict[str, int] | None = None,
    catalog_by_file: dict[str, list[HeadingCandidate]] | None = None,
    dense_structural_files: set[str] | None = None,
    editorial_pages_by_file: dict[str, set[int]] | None = None,
    editorial_page_catalog: dict[int, list[dict[str, Any]]] | None = None,
    minimum_sequence_coverage: float = 0.8,
    minimum_title_similarity: float = 0.58,
    minimum_unique_similarity: float = 0.82,
    minimum_unique_margin: float = 0.15,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    direct_by_identity: dict[str, list[HeadingCandidate]] = {}
    direct_alias_identities: set[str] = set()
    exact_prefix_identities: set[str] = set()
    provenance_by_identity: dict[str, list[HeadingCandidate]] = {}
    ranks_by_identity: dict[str, list[tuple[float, HeadingCandidate]]] = {}
    editorial_by_identity: dict[str, list[HeadingCandidate]] = {}
    editorial_neighbor_by_identity: dict[str, list[HeadingCandidate]] = {}
    editorial_evidence_by_identity: dict[str, dict[str, Any]] = {}
    windows: dict[str, tuple[int, int, str]] = {}
    if catalog_index is None:
        catalog_index = {}
        for candidate in catalog:
            catalog_index.setdefault((candidate.family, candidate.ordinal), []).append(candidate)
    if positions is None:
        positions = {str(path.resolve()): index for index, path in enumerate(files)}
    if works is None:
        works = [work for work in payload.get("works") or [] if isinstance(work, dict)]
    if work_index_by_key is None:
        work_index_by_key = {}
        for index, work in enumerate(works):
            key = work.get("work_key")
            if key is not None:
                work_index_by_key.setdefault(str(key), index)
    if catalog_by_file is None:
        catalog_by_file = {}
        for candidate in catalog:
            catalog_by_file.setdefault(candidate.file, []).append(candidate)
    if dense_structural_files is None:
        dense_structural_files = {
            candidate.file for candidate in catalog if candidate.page_has_dense_structural_list
        }
    if editorial_pages_by_file is None:
        editorial_pages_by_file = {}
        for editorial_page, records in (editorial_page_catalog or {}).items():
            for record in records:
                if record.get("file"):
                    editorial_pages_by_file.setdefault(str(record["file"]), set()).add(
                        int(editorial_page)
                    )

    window_cache: dict[tuple[str, Any], tuple[int, int, str]] = {}

    for ref in segment.entries:
        section = payload["sections"][ref.section_index]
        raw_json = (section.get("entries") or [])[ref.entry_index].get("raw_json")
        work_key = section.get("work_key")
        if isinstance(raw_json, dict):
            work_key = raw_json.get("parent_work_key") or raw_json.get("work_key") or work_key
        cache_key = (
            ("work", str(work_key))
            if work_key is not None and str(work_key) in work_index_by_key
            else ("section", ref.section_index)
        )
        if cache_key not in window_cache:
            window_cache[cache_key] = _window_for_entry(
                ref,
                payload,
                files,
                positions=positions,
                works=works,
                work_index_by_key=work_index_by_key,
                work_key=work_key,
            )
        start, end, window_reason = window_cache[cache_key]
        windows[ref.identity] = (start, end, window_reason)
        excluded = exclusions.get(ref.section_index, set())
        source_files = {
            str(_resolve_path(value, files[0].parent)) for value in ref.entry_source_files
        }
        candidate_pool: list[HeadingCandidate] = []
        if ref.classification == "eligible_editorial_page_only":
            candidate_pool = []
        elif ref.ordinals:
            for ordinal in ref.ordinals:
                candidate_pool.extend(catalog_index.get((ref.family, ordinal), []))
                if ref.classification == "eligible_structural_contextual":
                    candidate_pool.extend(catalog_index.get(("ordinal", ordinal), []))
        else:
            candidate_pool = catalog
        compatible = [
            candidate
            for candidate in candidate_pool
            if start <= candidate.file_position < end
            and not candidate.in_dense_structural_list
            and (
                candidate.file not in excluded
                or (
                    ref.allow_direct_source
                    and str(Path(candidate.file).resolve()) in source_files
                )
                or (
                    candidate.page_has_dense_structural_list
                    and candidate.explicit_label
                )
            )
            and (
                ref.family == "title_only"
                or candidate.family == ref.family
                or (
                    ref.classification == "eligible_structural_contextual"
                    and not candidate.explicit_label
                    and candidate.family in {ref.family, "ordinal"}
                )
            )
        ]
        ranked = sorted(
            (
                (_score_for_ref(ref, candidate), candidate)
                for candidate in compatible
                if not ref.ordinals or candidate.ordinal in ref.ordinals
            ),
            key=lambda item: (-item[0], item[1].file_position, item[1].line),
        )
        ranks_by_identity[ref.identity] = ranked
        if ref.page_hint_int is not None:
            global_page_records = [
                record
                for record in (editorial_page_catalog or {}).get(ref.page_hint_int, [])
                if str(record.get("file") or "") not in excluded
            ]
            page_records = [
                record
                for record in global_page_records
                if start <= int(record.get("file_position", -1)) < end
            ]
            used_global_unique_fallback = False
            if not page_records:
                global_records_by_file = {
                    str(record.get("file") or ""): record
                    for record in global_page_records
                    if record.get("file")
                }
                if len(global_records_by_file) == 1:
                    page_records = list(global_records_by_file.values())
                    used_global_unique_fallback = True
            records_by_file = {
                str(record.get("file") or ""): record
                for record in page_records
                if record.get("file")
            }
            if len(records_by_file) == 1:
                mapped_file, page_record = next(iter(records_by_file.items()))
                mapped_candidates = [
                    candidate
                    for candidate in catalog_by_file.get(mapped_file, [])
                    if candidate.file == mapped_file
                    and not candidate.in_dense_structural_list
                    and (
                        not ref.ordinals
                        or candidate.ordinal in ref.ordinals
                    )
                    and candidate.family in {ref.family, "ordinal"}
                ]
                if mapped_candidates:
                    mapped = max(
                        mapped_candidates,
                        key=lambda candidate: (_score_for_ref(ref, candidate), -candidate.line),
                    )
                else:
                    mapped = HeadingCandidate(
                        family=ref.family,
                        ordinal=ref.ordinals[0] if len(ref.ordinals) == 1 else None,
                        file=mapped_file,
                        file_position=int(page_record["file_position"]),
                        line=0,
                        heading_raw="",
                        title_raw="",
                        context="",
                        explicit_label=False,
                        block_role="editorial_page_map",
                        script="",
                        bbox="",
                    )
                editorial_by_identity[ref.identity] = [mapped]
                editorial_evidence_by_identity[ref.identity] = {
                    "page_ref_int": ref.page_hint_int,
                    "page_hint_source": ref.page_hint_source,
                    "target_file": mapped_file,
                    "confidence": page_record.get("confidence"),
                    "confidence_label": page_record.get("confidence_label"),
                    "estimator_evidence_kinds": page_record.get("evidence_kinds") or [],
                    "warnings": page_record.get("warnings") or [],
                    "unique_candidate_in_body_window": not used_global_unique_fallback,
                    "unique_candidate_in_volume": True,
                    "declared_body_window_match": not used_global_unique_fallback,
                    "scope_fallback": (
                        "global_unique_editorial_page_outside_declared_window"
                        if used_global_unique_fallback
                        else None
                    ),
                }
                editorial_neighbor = _adjacent_heading_over_editorial_map(
                    ref,
                    [mapped],
                    ranked,
                    minimum_margin=minimum_unique_margin,
                )
                if editorial_neighbor:
                    editorial_neighbor_by_identity[ref.identity] = editorial_neighbor
        if ref.allow_direct_source and source_files:
            source_ranked = [
                item
                for item in ranked
                if str(Path(item[1].file).resolve()) in source_files
            ]
            if source_ranked and source_ranked[0][0] >= minimum_title_similarity:
                provenance_by_identity[ref.identity] = [source_ranked[0][1]]
        chosen: list[HeadingCandidate] = []
        for ordinal in ref.ordinals or [None]:
            ordinal_ranked = [item for item in ranked if ordinal is None or item[1].ordinal == ordinal]
            per_file: list[tuple[float, HeadingCandidate]] = []
            seen: set[str] = set()
            for item in ordinal_ranked:
                if item[1].file in seen:
                    continue
                seen.add(item[1].file)
                per_file.append(item)
            best = per_file[0] if per_file else None
            second_score = per_file[1][0] if len(per_file) > 1 else 0.0
            ordinary_unique = bool(best and best[0] >= minimum_unique_similarity)
            exact_short_title = bool(
                best
                and best[0] >= 0.78
                and _exact_heading_title_prefix(ref, best[1])
            )
            if not (
                best
                and (ordinary_unique or exact_short_title)
                and best[0] - second_score >= minimum_unique_margin
            ):
                chosen = []
                break
            chosen.append(best[1])
        if chosen:
            direct_by_identity[ref.identity] = chosen
            if all(_exact_heading_title_prefix(ref, candidate) for candidate in chosen):
                exact_prefix_identities.add(ref.identity)
            if any(_chapter_title_alias(ref, candidate) for candidate in chosen):
                direct_alias_identities.add(ref.identity)

    # A direct title match anchors one local physical run. Fill only within a run supported by at
    # least two anchors, preserving exact ordinals and monotonic file/line order.
    sequence_by_identity: dict[str, list[HeadingCandidate]] = {}
    score_by_identity_candidate = {
        ref.identity: {
            (candidate.file, candidate.line, candidate.ordinal, candidate.family): score
            for score, candidate in ranks_by_identity.get(ref.identity, [])
        }
        for ref in segment.entries
    }
    ordinal_refs = [ref for ref in segment.entries if ref.ordinals]
    anchor_items = [
        (ref, direct_by_identity[ref.identity])
        for ref in ordinal_refs
        if ref.identity in direct_by_identity
    ]
    compatible_for_run = sorted(
        {
            (candidate.file, candidate.line, candidate.ordinal, candidate.family): candidate
            for ref in ordinal_refs
            for _, candidate in ranks_by_identity.get(ref.identity, [])
            if candidate.family in {segment.family, "ordinal"}
        }.values(),
        key=lambda candidate: (candidate.file_position, candidate.line),
    )
    runs: list[list[HeadingCandidate]] = []
    current_run: list[HeadingCandidate] = []
    high_water = 0
    for candidate in compatible_for_run:
        if candidate.ordinal is None:
            continue
        if current_run and candidate.ordinal == 1 and high_water > 1:
            runs.append(current_run)
            current_run = []
            high_water = 0
        current_run.append(candidate)
        high_water = max(high_water, candidate.ordinal)
    if current_run:
        runs.append(current_run)
    best_run: list[HeadingCandidate] | None = None
    best_anchor_count = 0
    for run in runs:
        identities = {(candidate.file, candidate.line, candidate.ordinal) for candidate in run}
        anchor_count = sum(
            all((candidate.file, candidate.line, candidate.ordinal) in identities for candidate in chosen)
            for _, chosen in anchor_items
        )
        if anchor_count > best_anchor_count:
            best_anchor_count = anchor_count
            best_run = run
    if best_run is not None and best_anchor_count >= 2:
        last_position = (-1, -1)
        resolved_count = 0
        provisional: dict[str, list[HeadingCandidate]] = {}
        for ref in ordinal_refs:
            selected: list[HeadingCandidate] = []
            for ordinal in ref.ordinals:
                candidates = [candidate for candidate in best_run if candidate.ordinal == ordinal]
                candidate_scores = score_by_identity_candidate.get(ref.identity, {})
                candidates.sort(
                    key=lambda candidate: (
                        -candidate_scores.get(
                            (candidate.file, candidate.line, candidate.ordinal, candidate.family),
                            0.0,
                        ),
                        candidate.file_position,
                        candidate.line,
                    )
                )
                candidate = next(
                    (
                        item
                        for item in candidates
                        if (item.file_position, item.line) >= last_position
                        and candidate_scores.get(
                            (item.file, item.line, item.ordinal, item.family), 0.0
                        )
                        >= minimum_title_similarity
                    ),
                    None,
                )
                if candidate is None:
                    selected = []
                    break
                selected.append(candidate)
                last_position = (candidate.file_position, candidate.line)
            if selected:
                provisional[ref.identity] = selected
                resolved_count += 1
        sequence_coverage = resolved_count / len(ordinal_refs) if ordinal_refs else 0.0
        if sequence_coverage >= minimum_sequence_coverage:
            sequence_by_identity = provisional
        else:
            anchor_positions = [
                (candidate.file_position, candidate.line)
                for _, chosen in anchor_items
                for candidate in chosen
                if candidate in best_run
            ]
            if len(anchor_positions) >= 2:
                lower_anchor = min(anchor_positions)
                upper_anchor = max(anchor_positions)
                sequence_by_identity = {
                    identity: chosen
                    for identity, chosen in provisional.items()
                    if all(
                        lower_anchor <= (candidate.file_position, candidate.line) <= upper_anchor
                        for candidate in chosen
                    )
                }

    def direct_inside_sequence_envelope(ref: EntryRef, chosen: list[HeadingCandidate]) -> bool:
        if not sequence_by_identity:
            return True
        ref_index = segment.entries.index(ref)
        previous = next(
            (
                sequence_by_identity[item.identity][-1]
                for item in reversed(segment.entries[:ref_index])
                if item.identity in sequence_by_identity
            ),
            None,
        )
        following = next(
            (
                sequence_by_identity[item.identity][0]
                for item in segment.entries[ref_index + 1 :]
                if item.identity in sequence_by_identity
            ),
            None,
        )
        return all(
            condition
            for condition in (
                previous is None or _position(chosen[0]) >= _position(previous),
                following is None or _position(chosen[-1]) <= _position(following),
            )
        )

    for ref in segment.entries:
        start, end, window_reason = windows[ref.identity]
        method = ""
        editorial_page_evidence = editorial_evidence_by_identity.get(ref.identity)
        chosen = provenance_by_identity.get(ref.identity, [])
        if chosen:
            method = "direct_source_heading"
        elif ref.identity in sequence_by_identity:
            chosen = sequence_by_identity[ref.identity]
            method = "monotonic_heading_sequence"
        else:
            direct = direct_by_identity.get(ref.identity, [])
            if (
                direct
                and ref.identity not in direct_alias_identities
                and direct_inside_sequence_envelope(ref, direct)
            ):
                # For deliberately capped short titles, an exact printed-page
                # mapping is stronger than the lexical prefix.  Leave that
                # entry for the editorial resolver when both are available.
                if not (
                    ref.identity in exact_prefix_identities
                    and ref.identity in editorial_by_identity
                ):
                    chosen = direct
                    method = (
                        "unique_heading_exact_title_prefix"
                        if ref.identity in exact_prefix_identities
                        else "unique_heading_title_match"
                    )
        if not chosen and ref.identity in editorial_neighbor_by_identity:
            chosen = editorial_neighbor_by_identity[ref.identity]
            method = "adjacent_heading_over_editorial_page_map"
            if editorial_page_evidence is not None:
                editorial_page_evidence = {
                    **editorial_page_evidence,
                    "disposition": "overridden_by_adjacent_explicit_heading",
                }
        if not chosen and ref.identity in editorial_by_identity:
            chosen = editorial_by_identity[ref.identity]
            method = "editorial_page_map"
        compact_candidates = _compact_candidates(ranks_by_identity.get(ref.identity, []))
        if method == "editorial_page_map" and ref.existing_target_file:
            existing = str(_resolve_path(ref.existing_target_file, files[0].parent))
            existing_heading = next(
                (
                    candidate
                    for score, candidate in ranks_by_identity.get(ref.identity, [])
                    if str(Path(candidate.file).resolve()) == existing
                    and score >= minimum_title_similarity
                ),
                None,
            )
            if existing_heading is not None:
                chosen = [existing_heading]
                method = "existing_target_heading_confirmation"
        if chosen:
            target_file = chosen[0].file
            if ref.existing_target_file:
                existing = str(_resolve_path(ref.existing_target_file, files[0].parent))
                if existing != str(Path(target_file).resolve()):
                    if not ref.existing_target_is_generic_anchor:
                        existing_score = max(
                            (
                                score
                                for score, candidate in ranks_by_identity.get(ref.identity, [])
                                if str(Path(candidate.file).resolve()) == existing
                            ),
                            default=0.0,
                        )
                        replace_unverified_legacy = (
                            ref.existing_evidence is None
                            and (
                                (
                                    method == "monotonic_heading_sequence"
                                    and best_anchor_count >= 2
                                )
                                or method == "unique_heading_title_match"
                            )
                            and existing_score < minimum_title_similarity
                        )
                        prior_evidence = ref.existing_evidence
                        replace_stale_locator = (
                            isinstance(prior_evidence, dict)
                            and prior_evidence.get("locator") == LOCATOR_NAME
                            and _locator_version(prior_evidence.get("locator_version")) < LOCATOR_VERSION
                            and not isinstance(prior_evidence.get("review"), dict)
                            and str(prior_evidence.get("status") or "").casefold() == "resolved"
                            and str(
                                _resolve_path(
                                    str(prior_evidence.get("target_file") or ""),
                                    files[0].parent,
                                )
                            )
                            == existing
                            and (
                                (
                                    method == "monotonic_heading_sequence"
                                    and best_anchor_count >= 2
                                )
                                or method in {
                                    "editorial_page_map",
                                    "unique_heading_title_match",
                                }
                            )
                            and existing_score < minimum_title_similarity
                        )
                        strong_proposed_target = (
                            method in {
                                "direct_source_heading",
                                "editorial_page_map",
                                "unique_heading_title_match",
                            }
                            or (
                                method == "monotonic_heading_sequence"
                                and best_anchor_count >= 2
                            )
                        )
                        existing_editorial_pages = editorial_pages_by_file.get(existing, set())
                        existing_page_mismatch = bool(
                            method == "editorial_page_map"
                            and ref.page_hint_int is not None
                            and existing_editorial_pages
                            and min(
                                abs(page - ref.page_hint_int)
                                for page in existing_editorial_pages
                            )
                            > 2
                        )
                        replace_invalid_legacy = (
                            ref.existing_evidence is None
                            and strong_proposed_target
                            and (
                                existing in excluded
                                or existing in dense_structural_files
                                or not Path(existing).is_file()
                                or existing_page_mismatch
                            )
                        )
                        if (
                            not replace_unverified_legacy
                            and not replace_stale_locator
                            and not replace_invalid_legacy
                        ):
                            conflict_evidence = _evidence(
                                ref,
                                segment,
                                status="ambiguous",
                                method="existing_target_conflict",
                                reason="Deterministic candidate conflicts with a non-generic existing target; no automatic replacement.",
                                files=files,
                                start=start,
                                end=end,
                                window_reason=window_reason,
                                chosen=chosen,
                                candidates=compact_candidates,
                                editorial_page=editorial_page_evidence,
                            )
                            conflict_evidence["proposed_method"] = method
                            results.append(
                                {
                                    "ref": ref,
                                    "status": "conflict",
                                    "target_file": ref.existing_target_file,
                                    "proposed_target_file": target_file,
                                    "evidence": conflict_evidence,
                                }
                            )
                            continue
                        if replace_stale_locator:
                            disposition = "replaced_stale_locator_target"
                        elif replace_invalid_legacy:
                            disposition = (
                                "replaced_missing_target"
                                if not Path(existing).is_file()
                                else "replaced_editorial_mismatch_target"
                                if existing_page_mismatch
                                else "replaced_index_page_anchor"
                            )
                        else:
                            disposition = "replaced_unverified_legacy_target"
                    else:
                        disposition = "replaced_generic_work_anchor"
                else:
                    disposition = "verified_existing"
            else:
                disposition = "added"
            evidence = _evidence(
                ref,
                segment,
                status="resolved",
                method=method,
                reason=(
                    "The entry's own source provenance contains its explicit body heading."
                    if method == "direct_source_heading"
                    else "The existing target contains a sufficiently similar structural heading; the editorial page map was retained as secondary evidence."
                    if method == "existing_target_heading_confirmation"
                    else "The entry's printed page reference maps uniquely to this high-confidence physical scan inside the safe body window."
                    if method == "editorial_page_map"
                    else "A unique explicit heading on the adjacent scan overrides the approximate editorial-page mapping."
                    if method == "adjacent_heading_over_editorial_page_map"
                    else f"The entry belongs to a monotonic local run supported by {best_anchor_count} unique title anchors."
                    if method == "monotonic_heading_sequence"
                    else "A unique same-family heading has strong title similarity and sufficient margin."
                ),
                files=files,
                start=start,
                end=end,
                window_reason=window_reason,
                chosen=chosen,
                candidates=compact_candidates,
                disposition=disposition,
                editorial_page=editorial_page_evidence,
            )
            if disposition in {
                "replaced_generic_work_anchor",
                "replaced_stale_locator_target",
                "replaced_unverified_legacy_target",
                "replaced_index_page_anchor",
                "replaced_missing_target",
                "replaced_editorial_mismatch_target",
            }:
                evidence["replaced_target_file"] = ref.existing_target_file
            if disposition == "replaced_stale_locator_target" and isinstance(
                ref.existing_evidence, dict
            ):
                evidence["superseded_evidence"] = {
                    key: ref.existing_evidence.get(key)
                    for key in (
                        "locator",
                        "locator_version",
                        "status",
                        "method",
                        "reason",
                        "target_file",
                    )
                    if ref.existing_evidence.get(key) is not None
                }
            results.append(
                {
                    "ref": ref,
                    "status": "resolved",
                    "target_file": target_file,
                    "evidence": evidence,
                }
            )
            continue
        top_score = compact_candidates[0]["similarity"] if compact_candidates else 0.0
        status = "ambiguous" if top_score >= minimum_title_similarity else "unresolved"
        results.append(
            {
                "ref": ref,
                "status": status,
                "target_file": ref.existing_target_file,
                "evidence": _evidence(
                    ref,
                    segment,
                    status=status,
                    method="bounded_structural_heading_search",
                    reason=(
                        "Candidate headings remain tied or the sequence evidence is incomplete."
                        if status == "ambiguous"
                        else "No sufficiently strong body heading was found inside the safe search window."
                    ),
                    files=files,
                    start=start,
                    end=end,
                    window_reason=window_reason,
                    candidates=compact_candidates,
                ),
            }
        )
    return results


_LOCATE_WORKER_CONTEXT: dict[str, Any] = {}


def _initialize_locate_worker(
    payload: dict[str, Any],
    files: list[Path],
    catalog: list[HeadingCandidate],
    catalog_index: dict[tuple[str, int | None], list[HeadingCandidate]],
    editorial_page_catalog: dict[int, list[dict[str, Any]]],
    exclusions: dict[int, set[str]],
    positions: dict[str, int],
    works: list[dict[str, Any]],
    work_index_by_key: dict[str, int],
    catalog_by_file: dict[str, list[HeadingCandidate]],
    dense_structural_files: set[str],
    editorial_pages_by_file: dict[str, set[int]],
    config: dict[str, float],
) -> None:
    global _LOCATE_WORKER_CONTEXT
    _LOCATE_WORKER_CONTEXT = {
        "payload": payload,
        "files": files,
        "catalog": catalog,
        "catalog_index": catalog_index,
        "editorial_page_catalog": editorial_page_catalog,
        "exclusions": exclusions,
        "positions": positions,
        "works": works,
        "work_index_by_key": work_index_by_key,
        "catalog_by_file": catalog_by_file,
        "dense_structural_files": dense_structural_files,
        "editorial_pages_by_file": editorial_pages_by_file,
        **config,
    }


def _locate_segment_worker(segment: Segment) -> list[dict[str, Any]]:
    return locate_segment(segment, **_LOCATE_WORKER_CONTEXT)


def build_repair_manifest(
    payload_path: Path,
    *,
    workers: int = 1,
    reviewed_overrides: list[dict[str, Any]] | None = None,
    minimum_sequence_coverage: float = 0.8,
    minimum_title_similarity: float = 0.58,
    minimum_unique_similarity: float = 0.82,
    minimum_unique_margin: float = 0.15,
) -> dict[str, Any]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    volume = payload.get("volume") or {}
    source_root = Path(str(volume.get("source_root") or ""))
    if not source_root.is_absolute():
        source_root = (payload_path.parents[2] / source_root).resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"source_root not found: {source_root}")
    files = sorted(source_root.glob("*.txt"), key=page_sort_key)
    if not files:
        raise FileNotFoundError(f"No OCR text files found in {source_root}")
    segments, dispositions = classify_segments(payload)
    eligible_count = sum(len(segment.entries) for segment in segments)
    page_hint_entry_count = sum(
        1 for segment in segments for ref in segment.entries if ref.page_hint_int is not None
    )
    catalog = build_heading_catalog(files, workers=workers) if eligible_count else []
    if eligible_count and page_hint_entry_count:
        editorial_page_catalog, editorial_page_summary = build_editorial_page_catalog(
            volume_id=str(volume.get("volume_id") or payload_path.stem.removesuffix("_indices")),
            source_root=source_root,
            files=files,
        )
    else:
        editorial_page_catalog = {}
        editorial_page_summary = {
            "status": "skipped_no_eligible_entries" if not eligible_count else "skipped_no_page_hints",
            "source_file_count": len(files),
            "accepted_high_confidence_files": 0,
            "mapped_editorial_pages": 0,
            "summary": {},
        }
    catalog_index: dict[tuple[str, int | None], list[HeadingCandidate]] = {}
    catalog_by_file: dict[str, list[HeadingCandidate]] = {}
    for candidate in catalog:
        catalog_index.setdefault((candidate.family, candidate.ordinal), []).append(candidate)
        catalog_by_file.setdefault(candidate.file, []).append(candidate)
    positions = {str(path.resolve()): index for index, path in enumerate(files)}
    works = [work for work in payload.get("works") or [] if isinstance(work, dict)]
    work_index_by_key: dict[str, int] = {}
    for index, work in enumerate(works):
        key = work.get("work_key")
        if key is not None:
            work_index_by_key.setdefault(str(key), index)
    dense_structural_files = {
        candidate.file for candidate in catalog if candidate.page_has_dense_structural_list
    }
    editorial_pages_by_file: dict[str, set[int]] = {}
    for editorial_page, records in editorial_page_catalog.items():
        for record in records:
            if record.get("file"):
                editorial_pages_by_file.setdefault(str(record["file"]), set()).add(
                    int(editorial_page)
                )
    exclusions = _section_exclusions(payload, files)
    results: list[dict[str, Any]] = []
    locate_config = {
        "minimum_sequence_coverage": minimum_sequence_coverage,
        "minimum_title_similarity": minimum_title_similarity,
        "minimum_unique_similarity": minimum_unique_similarity,
        "minimum_unique_margin": minimum_unique_margin,
    }
    if workers > 1 and eligible_count >= 100 and len(segments) > 1:
        with ProcessPoolExecutor(
            max_workers=min(workers, len(segments)),
            initializer=_initialize_locate_worker,
            initargs=(
                payload,
                files,
                catalog,
                catalog_index,
                editorial_page_catalog,
                exclusions,
                positions,
                works,
                work_index_by_key,
                catalog_by_file,
                dense_structural_files,
                editorial_pages_by_file,
                locate_config,
            ),
        ) as executor:
            batches = executor.map(_locate_segment_worker, segments, chunksize=1)
            results = [result for batch in batches for result in batch]
    else:
        for segment in segments:
            results.extend(
                locate_segment(
                    segment,
                    payload=payload,
                    files=files,
                    catalog=catalog,
                    catalog_index=catalog_index,
                    editorial_page_catalog=editorial_page_catalog,
                    exclusions=exclusions,
                    positions=positions,
                    works=works,
                    work_index_by_key=work_index_by_key,
                    catalog_by_file=catalog_by_file,
                    dense_structural_files=dense_structural_files,
                    editorial_pages_by_file=editorial_pages_by_file,
                    **locate_config,
                )
            )

    applied_reviewed_overrides: list[dict[str, Any]] = []
    already_applied_reviewed_overrides: list[dict[str, Any]] = []
    result_by_selector: dict[tuple[str, str | None, str], list[dict[str, Any]]] = {}
    for result in results:
        ref = result["ref"]
        selector = (ref.section_key, ref.entry_key, sha256_text(ref.entry_raw))
        result_by_selector.setdefault(selector, []).append(result)
    for override in reviewed_overrides or []:
        if str(override.get("volume_id") or "") != str(volume.get("volume_id") or ""):
            continue
        selector = (
            str(override.get("section_key") or ""),
            str(override.get("entry_key")) if override.get("entry_key") else None,
            str(override.get("entry_raw_sha256") or ""),
        )
        matches = result_by_selector.get(selector) or []
        if len(matches) != 1:
            raise ValueError(
                "reviewed override does not identify exactly one eligible entry: "
                f"{volume.get('volume_id')} {selector}"
            )
        result = matches[0]
        ref: EntryRef = result["ref"]
        after_value = override.get("after_target_file")
        after_target = str(_resolve_path(str(after_value), source_root)) if after_value else None
        expected_before = override.get("before_target_file")
        if expected_before:
            expected_before = str(_resolve_path(str(expected_before), source_root))
        actual_before = (
            str(_resolve_path(ref.existing_target_file, source_root))
            if ref.existing_target_file
            else None
        )
        if actual_before != expected_before:
            existing_review = (
                (ref.existing_evidence or {}).get("review")
                if isinstance(ref.existing_evidence, dict)
                else None
            )
            if (
                actual_before == after_target
                and isinstance(existing_review, dict)
                and existing_review.get("review_id") == override.get("review_id")
            ):
                already_applied_reviewed_overrides.append(copy.deepcopy(override))
                continue
            raise ValueError(
                f"reviewed override before-target mismatch for {ref.identity}: "
                f"expected {expected_before!r}, found {actual_before!r}"
            )
        if after_target:
            target_path = Path(after_target)
            try:
                target_path.relative_to(source_root.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"reviewed override target outside source_root for {ref.identity}"
                ) from exc
            if not target_path.is_file():
                raise ValueError(f"reviewed override target does not exist: {target_path}")
        inspected_files = []
        for value in override.get("inspected_files") or []:
            inspected = _resolve_path(str(value), source_root)
            if inspected.is_file():
                inspected_files.append(str(inspected))
        status = "resolved" if after_target else "unresolved"
        disposition = (
            "replaced_reviewed_invalid_legacy_target"
            if after_target and actual_before
            else "added_reviewed_target"
            if after_target
            else "cleared_reviewed_invalid_legacy_target"
        )
        evidence: dict[str, Any] = {
            "schema_version": 1,
            "locator": LOCATOR_NAME,
            "locator_version": LOCATOR_VERSION,
            "status": status,
            "entry_key": ref.entry_key,
            "structural_family": ref.family,
            "method": "reviewed_override",
            "query_raw": ref.entry_raw,
            "reason": str(override.get("reason") or "Manually reviewed corpus correction."),
            "inspected_files": inspected_files,
            "disposition": disposition,
            "sequence": {
                "segment_key": result.get("evidence", {}).get("sequence", {}).get("segment_key"),
                "index_ordinals": ref.ordinals,
                "matched_body_ordinals": ref.ordinals if after_target else [],
            },
            "review": {
                "kind": "explicit_corpus_override",
                "review_id": str(override.get("review_id") or ""),
            },
        }
        if after_target:
            evidence["target_file"] = after_target
            evidence["matched_heading_raw"] = str(override.get("matched_heading_raw") or "")
            if ref.existing_target_file:
                evidence["replaced_target_file"] = ref.existing_target_file
        else:
            evidence["cleared_target_file"] = ref.existing_target_file
        result.update(
            {
                "status": status,
                "target_file": after_target,
                "proposed_target_file": after_target,
                "evidence": evidence,
                "reviewed_override": True,
            }
        )
        applied_reviewed_overrides.append(copy.deepcopy(override))
    patches: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    preserved_existing: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    counts: dict[str, int] = {
        "eligible": len(results),
        "resolved": 0,
        "ambiguous": 0,
        "unresolved": 0,
        "conflict": 0,
        "already_canonical": 0,
        "preserved_existing": 0,
        "patches": 0,
    }

    def replaceable_non_resolving_evidence(ref: EntryRef) -> bool:
        evidence = ref.existing_evidence
        if not isinstance(evidence, dict) or ref.existing_target_file:
            return False
        if isinstance(evidence.get("review"), dict):
            return False
        if evidence.get("target_file"):
            return False
        return str(evidence.get("status") or "").casefold() in {
            "",
            "ambiguous",
            "unresolved",
            "unverified",
        }

    for result in results:
        ref: EntryRef = result["ref"]
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1
        target_file = result.get("target_file")
        evidence = result["evidence"]
        if result.get("reviewed_override"):
            patches.append(
                {
                    "section_index": ref.section_index,
                    "entry_index": ref.entry_index,
                    "section_key": ref.section_key,
                    "entry_key": ref.entry_key,
                    "entry_raw_sha256": sha256_text(ref.entry_raw),
                    "before_target_file": ref.existing_target_file,
                    "after_target_file": target_file,
                    "physical_target_evidence": evidence,
                }
            )
            continue
        if status == "conflict":
            conflicts.append(
                {
                    "section_index": ref.section_index,
                    "entry_index": ref.entry_index,
                    "section_key": ref.section_key,
                    "entry_key": ref.entry_key,
                    "entry_raw_sha256": sha256_text(ref.entry_raw),
                    "entry_raw": ref.entry_raw,
                    "existing_target_file": ref.existing_target_file,
                    "proposed_target_file": result.get("proposed_target_file"),
                    "physical_target_evidence": evidence,
                }
            )
            continue
        if isinstance(ref.existing_evidence, dict):
            evidence_locator = ref.existing_evidence.get("locator")
            if evidence_locator == LOCATOR_NAME and _locator_version(
                ref.existing_evidence.get("locator_version")
            ) == LOCATOR_VERSION:
                counts["already_canonical"] += 1
                continue
            replaceable_evidence = replaceable_non_resolving_evidence(ref)
            if evidence_locator != LOCATOR_NAME and not replaceable_evidence:
                counts["preserved_existing"] += 1
                preserved_existing.append(
                    {
                        "section_index": ref.section_index,
                        "entry_index": ref.entry_index,
                        "section_key": ref.section_key,
                        "entry_key": ref.entry_key,
                        "entry_raw_sha256": sha256_text(ref.entry_raw),
                        "reason": "Existing physical_target_evidence belongs to another producer and was not overwritten.",
                    }
                )
                continue
            if replaceable_evidence and status == "resolved":
                evidence["disposition"] = "replaced_unresolved_prior_evidence"
                evidence["superseded_evidence"] = {
                    key: ref.existing_evidence.get(key)
                    for key in ("locator", "status", "method", "reason")
                    if ref.existing_evidence.get(key) is not None
                }
        if status in {"ambiguous", "unresolved"} and ref.existing_target_file:
            counts["preserved_existing"] += 1
            preserved_existing.append(
                {
                    "section_index": ref.section_index,
                    "entry_index": ref.entry_index,
                    "section_key": ref.section_key,
                    "entry_key": ref.entry_key,
                    "entry_raw_sha256": sha256_text(ref.entry_raw),
                    "existing_target_file": ref.existing_target_file,
                    "reason": "The scan did not verify the legacy target, so neither target nor evidence was changed.",
                }
            )
            continue
        if status in {"ambiguous", "unresolved"}:
            observations.append(
                {
                    "section_index": ref.section_index,
                    "entry_index": ref.entry_index,
                    "section_key": ref.section_key,
                    "entry_key": ref.entry_key,
                    "entry_raw_sha256": sha256_text(ref.entry_raw),
                    "physical_target_evidence": evidence,
                }
            )
            continue
        if ref.existing_evidence == evidence and ref.existing_target_file == target_file:
            counts["already_canonical"] += 1
            continue
        patches.append(
            {
                "section_index": ref.section_index,
                "entry_index": ref.entry_index,
                "section_key": ref.section_key,
                "entry_key": ref.entry_key,
                "entry_raw_sha256": sha256_text(ref.entry_raw),
                "before_target_file": ref.existing_target_file,
                "after_target_file": target_file,
                "physical_target_evidence": evidence,
            }
        )
    counts["patches"] = len(patches)
    semantic_validation_errors: list[str] = []
    for result in results:
        if result["status"] != "resolved":
            continue
        ref = result["ref"]
        evidence = result["evidence"]
        target = Path(str(result.get("target_file") or "")).resolve()
        try:
            target.relative_to(source_root)
        except ValueError:
            semantic_validation_errors.append(f"{ref.identity}: target outside source_root")
        if not target.is_file():
            semantic_validation_errors.append(f"{ref.identity}: resolved target does not exist")
        matched = (evidence.get("sequence") or {}).get("matched_body_ordinals") or []
        if evidence.get("method") == "editorial_page_map":
            page_locator = evidence.get("editorial_page_locator") or {}
            if page_locator.get("page_ref_int") != ref.page_hint_int:
                semantic_validation_errors.append(
                    f"{ref.identity}: editorial page evidence does not match entry hint"
                )
            if page_locator.get("target_file") != str(target):
                semantic_validation_errors.append(
                    f"{ref.identity}: editorial page evidence does not match target"
                )
        elif ref.ordinals and matched != ref.ordinals:
            semantic_validation_errors.append(f"{ref.identity}: index/body ordinal mismatch")
    return {
        "schema_version": 1,
        "operation": "deterministic_structural_target_repair",
        "locator": LOCATOR_NAME,
        "locator_version": LOCATOR_VERSION,
        "algorithm_sha256": decision_algorithm_sha256(),
        "decision_config": {
            "minimum_sequence_coverage": minimum_sequence_coverage,
            "minimum_title_similarity": minimum_title_similarity,
            "minimum_unique_similarity": minimum_unique_similarity,
            "minimum_unique_margin": minimum_unique_margin,
            "minimum_editorial_page_confidence": MINIMUM_EDITORIAL_PAGE_CONFIDENCE,
            "minimum_editorial_neighbor_title_similarity": (
                MINIMUM_EDITORIAL_NEIGHBOR_TITLE_SIMILARITY
            ),
        },
        "volume_id": volume.get("volume_id"),
        "payload_file": str(payload_path.resolve()),
        "input_sha256": payload_sha256(payload_path),
        "source_root": str(source_root),
        "source_file_count": len(files),
        "heading_candidate_count": len(catalog),
        "editorial_page_catalog": editorial_page_summary,
        "editorial_page_hint_entry_count": page_hint_entry_count,
        "scan_workers": max(1, int(workers)),
        "reviewed_overrides": applied_reviewed_overrides,
        "reviewed_override_count": len(applied_reviewed_overrides),
        "reviewed_overrides_already_applied": already_applied_reviewed_overrides,
        "reviewed_override_already_applied_count": len(already_applied_reviewed_overrides),
        "segment_count": len(segments),
        "counts": counts,
        "attempted_equals_eligible": sum(counts[key] for key in ("resolved", "ambiguous", "unresolved", "conflict")) == counts["eligible"],
        "apply_ready": counts["conflict"] == 0 and not semantic_validation_errors,
        "semantic_validation_errors": semantic_validation_errors,
        "dispositions": dispositions,
        "conflicts": conflicts,
        "preserved_existing": preserved_existing,
        "observations": observations,
        "patches": patches,
    }


def apply_manifest_volume(item: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    payload_path = Path(str(item["payload_file"])).resolve()
    if payload_sha256(payload_path) != item.get("input_sha256"):
        raise ValueError(f"payload hash changed since dry-run: {payload_path}")
    if not item.get("apply_ready"):
        raise ValueError(f"manifest contains unresolved target conflicts: {payload_path}")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    for patch in item.get("patches") or []:
        section_index = int(patch["section_index"])
        entry_index = int(patch["entry_index"])
        section = payload["sections"][section_index]
        entry = section["entries"][entry_index]
        if str(section.get("section_key") or f"section[{section_index}]") != patch["section_key"]:
            raise ValueError(f"section identity changed in {payload_path}: {patch['section_key']}")
        if (str(entry.get("entry_key")) if entry.get("entry_key") else None) != patch.get("entry_key"):
            raise ValueError(f"entry_key changed in {payload_path}: {patch.get('entry_key')}")
        if sha256_text(str(entry.get("entry_raw") or "")) != patch["entry_raw_sha256"]:
            raise ValueError(f"entry_raw changed in {payload_path}: {patch.get('entry_key')}")
        before = str(entry.get("target_file")) if entry.get("target_file") else None
        if before != patch.get("before_target_file"):
            raise ValueError(f"target_file changed in {payload_path}: {patch.get('entry_key')}")
        if patch.get("after_target_file") is None:
            entry.pop("target_file", None)
        else:
            entry["target_file"] = patch["after_target_file"]
        raw_json = entry.get("raw_json")
        if not isinstance(raw_json, dict):
            raw_json = {"prior_raw_json": raw_json} if raw_json is not None else {}
            entry["raw_json"] = raw_json
        raw_json["physical_target_evidence"] = patch["physical_target_evidence"]
    return payload_path, payload


def validate_repaired_payload(
    before: dict[str, Any],
    after: dict[str, Any],
    manifest_item: dict[str, Any],
) -> None:
    if (before.get("volume") or {}).get("volume_id") != (after.get("volume") or {}).get("volume_id"):
        raise ValueError("volume identity changed")
    if len(before.get("works") or []) != len(after.get("works") or []):
        raise ValueError("work count changed")
    if len(before.get("sections") or []) != len(after.get("sections") or []):
        raise ValueError("section count changed")
    before_entries = sum(len(section.get("entries") or []) for section in before.get("sections") or [])
    after_entries = sum(len(section.get("entries") or []) for section in after.get("sections") or [])
    if before_entries != after_entries:
        raise ValueError("entry count changed")
    if not manifest_item.get("attempted_equals_eligible"):
        raise ValueError("manifest has unattempted eligible entries")
    source_root = Path(str((after.get("volume") or {}).get("source_root") or "")).resolve()
    before_guard = copy.deepcopy(before)
    after_guard = copy.deepcopy(after)
    for patch in manifest_item.get("patches") or []:
        evidence = patch.get("physical_target_evidence") or {}
        status = evidence.get("status")
        if status not in {"resolved", "ambiguous", "unresolved"}:
            raise ValueError(f"invalid evidence status: {status!r}")
        target = patch.get("after_target_file")
        if status == "resolved":
            if not target or evidence.get("target_file") != target:
                raise ValueError("resolved evidence and target_file differ")
            target_path = Path(str(target)).resolve()
            try:
                target_path.relative_to(source_root)
            except ValueError as exc:
                raise ValueError(f"target outside source_root: {target}") from exc
            if not target_path.is_file():
                raise ValueError(f"target does not exist: {target}")
        elif target != patch.get("before_target_file"):
            if not (
                status == "unresolved"
                and target is None
                and evidence.get("method") == "reviewed_override"
                and evidence.get("disposition") == "cleared_reviewed_invalid_legacy_target"
            ):
                raise ValueError("ambiguous/unresolved evidence cannot create or replace target_file")
        section_index = int(patch["section_index"])
        entry_index = int(patch["entry_index"])
        for guarded in (before_guard, after_guard):
            entry = guarded["sections"][section_index]["entries"][entry_index]
            entry.pop("target_file", None)
            raw_json = entry.get("raw_json")
            if isinstance(raw_json, dict):
                raw_json.pop("physical_target_evidence", None)
                if not raw_json:
                    entry.pop("raw_json", None)
            elif raw_json is None:
                entry.pop("raw_json", None)
    if before_guard != after_guard:
        raise ValueError("payload changed outside the allowed target/evidence fields")
