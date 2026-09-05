"""IR estrutural pequena para experimentos de embedding sobre o OCR.

O modulo deliberadamente nao decide a taxonomia editorial definitiva do
corpus. Ele preserva a evidencia do XML, produz uma inferencia conservadora e
permite montar representacoes comparaveis sem perder a origem fisica.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from patristica_pipeline.ocr_xml_utils import OcrBlock, OcrPage, normalize_visible_text
from scripts.limpeza_ocr import strip_google_boilerplate


IR_VERSION = "ocr-embedding-ir-v1"
SUPPORTED_VARIANTS = ("v0", "v1", "v2")

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"(?:<br\s*/?>|\[br\s*/?\])", re.IGNORECASE)
_HORIZONTAL_SPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)*")
_LINEBREAK_HYPHEN_RE = re.compile(
    r"(?<=[^\W_])[-\u00ad\u2010]\s*\n\s*(?=[^\W_])", re.UNICODE
)
_DIGITS_RE = re.compile(r"\d+")
_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)
_MARGINAL_MARKER_RE = re.compile(r"^(?:[A-ZΑ-Ω]\s*){1,6}$", re.UNICODE)
_ONLY_LOCATOR_RE = re.compile(
    r"^(?:p(?:ag(?:ina)?)?\.?\s*)?(?:\d+|[ivxlcdm]+)"
    r"(?:\s*[-–—]\s*(?:\d+|[ivxlcdm]+))?$",
    re.IGNORECASE,
)
_ARGUMENTATIVE_HEADER_RE = re.compile(
    r"\b(?:responsio|obiectio|objectio|quaestio|qu[aæ]stio|argumentum|"
    r"solutio|conclusio|distinctio|demonstratio|ἀπόκρισις|ἀποκρισις|"
    r"ἐρώτησις|ἐρωτησις)\b",
    re.IGNORECASE,
)
_SCAN_NOISE_RE = re.compile(
    r"(?:digitized\s+by\s+google|google(?:tm)?\s+books?|books\.google\.com|"
    r"распознавание\s+текста|правила\s+использования|"
    r"tipo\s+de\s+p[aá]gina\s*:)",
    re.IGNORECASE,
)
_SCRIPTURE_BOOK_RE = re.compile(
    r"\b(?:gen(?:es)?|genes(?:is)?|exod?|lev(?:it)?|num(?:er)?|deut|jos|"
    r"judic|reg(?:um)?|paralip|job|iob|ps(?:al(?:m)?)?|prov|eccl|cant|sap|"
    r"isa|jer|ier|ezech|dan|os(?:ee)?|joel|amos|abd|jon|mich|nah|hab|"
    r"soph|agg|zach|malach|matth?|marc|luc|joan|ioan|act|rom|cor|gal|"
    r"eph|phil|col|thess|tim|tit|philem|hebr|jac|pet|jud|apoc)\.?(?=\s)",
    re.IGNORECASE,
)
_CHAPTER_VERSE_RE = re.compile(
    r"\b(?:[ivxlcdm]{1,10}|\d{1,3})\s*[,.:]\s*\d{1,3}\b",
    re.IGNORECASE,
)
_APPARATUS_STRONG_RE = re.compile(
    r"\b(?:sic|mss?|cod(?:ex|ices)?|edit(?:i|io)?|omitt|legunt|deest|"
    r"ibid(?:em)?|sequer(?:ian)?|seguer(?:ian)?|gobl(?:er)?|"
    r"felc(?:k)?m?)\b",
    re.IGNORECASE,
)
_APPARATUS_WEAK_RE = re.compile(r"\b(?:alii|addit|habet)\b", re.IGNORECASE)
_NOTE_CALL_RE = re.compile(r"(?:^|\s)\(\d{1,3}\)")
_SECTION_RE = re.compile(r"^\s*(?P<section>\d{1,3})\.\s+", re.UNICODE)


def stable_hash(*values: object) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_xml_type(block: OcrBlock) -> str:
    value = (block.tipo or block.tag_name or "").strip().casefold()
    folded = unicodedata.normalize("NFKD", value)
    return "".join(char for char in folded if not unicodedata.combining(char))


def canonical_declared_script(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    aliases = {
        "latin": "latin",
        "latino": "latin",
        "latim": "latin",
        "greek": "greek",
        "greco": "greek",
        "grego": "greek",
        "gregο": "greek",
        "siriaco": "syriac",
        "syriac": "syriac",
        "syr": "syriac",
        "hebraico": "hebrew",
        "hebrew": "hebrew",
        "arabe": "arabic",
        "arabic": "arabic",
        "cirilico": "cyrillic",
        "cyrillic": "cyrillic",
        "copta": "coptic",
        "coptic": "coptic",
        "armenio": "armenian",
        "armenian": "armenian",
        "misto": "mixed",
        "mixed": "mixed",
    }
    return aliases.get(folded, folded or "unknown")


def _unicode_script(char: str) -> str | None:
    code = ord(char)
    if 0x0370 <= code <= 0x03FF or 0x1F00 <= code <= 0x1FFF:
        return "greek"
    if 0x0700 <= code <= 0x074F or 0x0860 <= code <= 0x086F:
        return "syriac"
    if 0x0590 <= code <= 0x05FF:
        return "hebrew"
    if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F:
        return "arabic"
    if 0x0400 <= code <= 0x052F:
        return "cyrillic"
    if 0x0530 <= code <= 0x058F:
        return "armenian"
    if 0x2C80 <= code <= 0x2CFF:
        return "coptic"
    if "LATIN" in unicodedata.name(char, ""):
        return "latin"
    return None


def infer_script(text: str, declared: str) -> tuple[str, float]:
    declared_norm = canonical_declared_script(declared)
    counts = Counter(filter(None, (_unicode_script(char) for char in text if char.isalpha())))
    total = sum(counts.values())
    if total == 0:
        return declared_norm, 0.45 if declared_norm != "unknown" else 0.0
    ranked = counts.most_common()
    primary, primary_count = ranked[0]
    secondary_count = ranked[1][1] if len(ranked) > 1 else 0
    if secondary_count >= 8 and secondary_count / total >= 0.12:
        return "mixed", min(0.99, (primary_count + secondary_count) / total)
    observed_confidence = primary_count / total
    if declared_norm not in {"unknown", "mixed"} and declared_norm == primary:
        return primary, max(0.80, observed_confidence)
    if declared_norm not in {"unknown", "mixed"} and observed_confidence < 0.75:
        return declared_norm, 0.55
    return primary, observed_confidence


def structural_visible_text(text: str) -> str:
    """Decodifica o bloco sem destruir linhas vazias ou unir hifenizacao."""
    value = normalize_visible_text(text or "")
    for _ in range(6):
        decoded = html.unescape(value)
        if decoded == value:
            break
        value = decoded
    value = unicodedata.normalize("NFC", value)
    value = _BR_RE.sub("\n", value)
    value = _TAG_RE.sub(" ", value)
    value = value.replace("\x00", " ")
    value = strip_google_boilerplate(value)
    lines = [_HORIZONTAL_SPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    value = "\n".join(lines).strip()
    return _BLANK_LINES_RE.sub("\n\n", value)


def embedding_visible_text(text: str) -> str:
    """Normaliza somente depois de preservar e analisar a estrutura de linhas."""
    value = structural_visible_text(text)
    value = _LINEBREAK_HYPHEN_RE.sub("", value)
    paragraphs = []
    for paragraph in _BLANK_LINES_RE.split(value):
        joined = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
        joined = _HORIZONTAL_SPACE_RE.sub(" ", joined).strip()
        if joined:
            paragraphs.append(joined)
    return unicodedata.normalize("NFC", "\n\n".join(paragraphs))


def split_structural_parts(text: str) -> tuple[str, ...]:
    value = structural_visible_text(text)
    return tuple(part.strip() for part in _BLANK_LINES_RE.split(value) if part.strip())


def header_fingerprint(text: str) -> str:
    value = embedding_visible_text(text).casefold()
    value = _DIGITS_RE.sub("#", value)
    return _NON_WORD_RE.sub(" ", value).strip()


def repeated_running_fingerprints(
    pages: Sequence[OcrPage], min_pages: int, min_ratio: float
) -> set[str]:
    occurrences: Counter[str] = Counter()
    for page in pages:
        seen: set[str] = set()
        for block in page.blocks:
            xml_type = canonical_xml_type(block)
            if block.tag_name not in {"cabecalho", "rodape"} and xml_type not in {
                "cabecalho",
                "rodape",
            }:
                continue
            text = embedding_visible_text(block.content_raw)
            if not text or len(text) > 240:
                continue
            fingerprint = header_fingerprint(text)
            if fingerprint:
                seen.add(fingerprint)
        occurrences.update(seen)
    threshold = max(min_pages, int(len(pages) * min_ratio + 0.999999))
    return {fingerprint for fingerprint, count in occurrences.items() if count >= threshold}


def _scripture_score(text: str) -> int:
    return min(len(_SCRIPTURE_BOOK_RE.findall(text)), len(_CHAPTER_VERSE_RE.findall(text)))


def infer_role(block: OcrBlock, text: str) -> tuple[str, float, tuple[str, ...]]:
    xml_type = canonical_xml_type(block)
    flags: list[str] = []
    scripture_score = _scripture_score(text)
    apparatus_strong_score = len(_APPARATUS_STRONG_RE.findall(text))
    apparatus_weak_score = len(_APPARATUS_WEAK_RE.findall(text))
    note_call_score = len(_NOTE_CALL_RE.findall(text))
    compact_length = len(embedding_visible_text(text))

    if _SCAN_NOISE_RE.search(text):
        return "boilerplate", 0.99, ("scan_noise",)
    if _MARGINAL_MARKER_RE.fullmatch(embedding_visible_text(text)):
        return "marginal_anchor", 0.99, ("isolated_marginal_marker",)
    if _ONLY_LOCATOR_RE.fullmatch(embedding_visible_text(text)):
        return "locator", 0.97, ("isolated_locator",)
    if block.tag_name == "cabecalho" or xml_type == "cabecalho":
        return "header", 0.96, ()
    reference_list = scripture_score >= 1 and (
        compact_length <= 700
        or (
            scripture_score >= 3
            and compact_length / scripture_score <= 180
            and apparatus_strong_score + apparatus_weak_score <= 2
        )
    )
    if reference_list:
        if xml_type == "texto_principal":
            flags.append("role_overrides_xml_type")
        return "scripture_references", min(0.98, 0.78 + scripture_score * 0.06), tuple(flags)
    apparatus_by_text = compact_length <= 1800 and (
        apparatus_strong_score >= 2
        or (apparatus_strong_score >= 1 and note_call_score >= 1)
        or (
            apparatus_weak_score >= 1
            and note_call_score >= 1
            and compact_length <= 700
        )
    )
    if "aparato" in xml_type or "apparat" in xml_type or apparatus_by_text:
        if xml_type == "texto_principal":
            flags.append("role_overrides_xml_type")
        apparatus_score = (
            apparatus_strong_score
            + min(apparatus_weak_score, 2)
            + min(note_call_score, 3)
        )
        return "critical_apparatus", min(0.98, 0.76 + apparatus_score * 0.04), tuple(flags)
    if xml_type in {"nota_marginal", "nota marginal"}:
        return "marginal_note", 0.93, ()
    if xml_type == "nota" or xml_type.startswith("nota_"):
        return "note", 0.82, ()
    if xml_type in {"texto_principal", "texto principal", ""}:
        return "body", 0.88, ()
    if block.tag_name == "rodape" or xml_type == "rodape":
        return "footer", 0.70, ("unclassified_footer",)
    return "editorial_other", 0.45, ("unknown_editorial_role",)


@dataclass(frozen=True, slots=True)
class LogicalSegment:
    segment_key: str
    volume_id: str
    work_label: str
    page_num: int
    source_file: str
    source_hash: str
    block_index: int
    part_index: int
    tag_name: str
    xml_type: str
    declared_script: str
    inferred_script: str
    script_confidence: float
    inferred_role: str
    role_confidence: float
    bbox: str
    section: str
    raw_text: str
    normalized_text: str
    included_v0: bool
    drop_reason: str
    flags: tuple[str, ...]

    def to_dict(self, *, include_text: bool = True) -> dict[str, object]:
        data = asdict(self)
        data["flags"] = list(self.flags)
        data["raw_hash"] = stable_hash(self.raw_text)
        data["normalized_hash"] = stable_hash(self.normalized_text)
        if not include_text:
            data.pop("raw_text")
            data.pop("normalized_text")
        return data


@dataclass(frozen=True, slots=True)
class ChunkSource:
    segment_key: str
    page_num: int
    source_file: str
    source_order: int
    segment_char_start: int
    segment_char_end: int


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    chunk_key: str
    variant_id: str
    volume_id: str
    anchor_page_num: int
    unit_key: str
    stream_id: str
    role: str
    script: str
    chunk_index: int
    text: str
    sources: tuple[ChunkSource, ...]

    @property
    def text_hash(self) -> str:
        return stable_hash(self.text)


def extract_page_segments(
    *,
    volume_id: str,
    work_label: str = "",
    page_num: int,
    source_file: str,
    source_hash: str,
    page: OcrPage,
    repeated_fingerprints: set[str],
) -> list[LogicalSegment]:
    segments: list[LogicalSegment] = []
    page_repair_flags = tuple(
        f"source_xml_repair:{repair}={count}"
        for repair, count in sorted(page.repairs.items())
    )
    for block_index, block in enumerate(page.blocks):
        parts = split_structural_parts(block.content_raw)
        for part_index, raw_part in enumerate(parts):
            normalized = embedding_visible_text(raw_part)
            if not normalized:
                continue
            role, role_confidence, flags = infer_role(block, raw_part)
            flags = (*flags, *page_repair_flags)
            script, script_confidence = infer_script(raw_part, block.script)
            repeated = (
                role in {"header", "footer"}
                and len(normalized) <= 240
                and header_fingerprint(raw_part) in repeated_fingerprints
                and not _ARGUMENTATIVE_HEADER_RE.search(normalized)
            )
            drop_reason = ""
            included_v0 = True
            if repeated:
                included_v0 = False
                drop_reason = "repeated_running_text"
                flags = (*flags, "repeated_running_text")
            elif role in {"boilerplate", "marginal_anchor", "locator"}:
                included_v0 = False
                drop_reason = role
            section_match = _SECTION_RE.match(normalized) if role == "body" else None
            section = section_match.group("section") if section_match else ""
            segment_key = stable_hash(
                IR_VERSION,
                volume_id,
                page_num,
                source_file,
                source_hash,
                block_index,
                part_index,
                raw_part,
            )
            segments.append(
                LogicalSegment(
                    segment_key=segment_key,
                    volume_id=volume_id,
                    work_label=work_label,
                    page_num=page_num,
                    source_file=source_file,
                    source_hash=source_hash,
                    block_index=block_index,
                    part_index=part_index,
                    tag_name=block.tag_name,
                    xml_type=canonical_xml_type(block),
                    declared_script=canonical_declared_script(block.script),
                    inferred_script=script,
                    script_confidence=script_confidence,
                    inferred_role=role,
                    role_confidence=role_confidence,
                    bbox=block.bbox,
                    section=section,
                    raw_text=raw_part,
                    normalized_text=normalized,
                    included_v0=included_v0,
                    drop_reason=drop_reason,
                    flags=flags,
                )
            )
    return segments


def split_text_windows_with_offsets(
    text: str, max_chars: int, overlap_chars: int
) -> tuple[tuple[int, int, str], ...]:
    text = text.strip()
    if not text:
        return ()
    if len(text) <= max_chars:
        return ((0, len(text), text),)
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        hard_end = min(start + max_chars, len(text))
        end = hard_end
        if hard_end < len(text):
            paragraph = text.rfind("\n\n", start + max_chars // 2, hard_end)
            boundary = paragraph if paragraph > start else text.rfind(" ", start + max_chars // 2, hard_end)
            if boundary > start:
                end = boundary
        raw_chunk = text[start:end]
        left_trim = len(raw_chunk) - len(raw_chunk.lstrip())
        right_trimmed = raw_chunk.rstrip()
        chunk_start = start + left_trim
        chunk_end = start + len(right_trimmed)
        if chunk_end > chunk_start:
            chunks.append((chunk_start, chunk_end, text[chunk_start:chunk_end]))
        if end >= len(text):
            break
        next_start = max(0, end - overlap_chars)
        if next_start > 0:
            boundary = text.find(" ", next_start, end)
            if boundary >= 0:
                next_start = boundary + 1
        if next_start <= start:
            next_start = end
        start = next_start
    return tuple(chunks)


def _assemble_segments(
    segments: Sequence[LogicalSegment],
) -> tuple[str, list[tuple[int, int, LogicalSegment]]]:
    pieces: list[str] = []
    spans: list[tuple[int, int, LogicalSegment]] = []
    cursor = 0
    for segment in segments:
        if pieces:
            pieces.append("\n\n")
            cursor += 2
        start = cursor
        pieces.append(segment.normalized_text)
        cursor += len(segment.normalized_text)
        spans.append((start, cursor, segment))
    return "".join(pieces), spans


def _chunk_unit(
    *,
    variant_id: str,
    volume_id: str,
    page_num: int,
    unit_key: str,
    stream_id: str,
    role: str,
    script: str,
    segments: Sequence[LogicalSegment],
    max_chars: int,
    overlap_chars: int,
) -> list[PreparedChunk]:
    assembled, spans = _assemble_segments(segments)
    chunks: list[PreparedChunk] = []
    for chunk_index, (start, end, text) in enumerate(
        split_text_windows_with_offsets(assembled, max_chars, overlap_chars)
    ):
        sources: list[ChunkSource] = []
        for segment_start, segment_end, segment in spans:
            overlap_start = max(start, segment_start)
            overlap_end = min(end, segment_end)
            if overlap_end <= overlap_start:
                continue
            sources.append(
                ChunkSource(
                    segment_key=segment.segment_key,
                    page_num=segment.page_num,
                    source_file=segment.source_file,
                    source_order=len(sources),
                    segment_char_start=overlap_start - segment_start,
                    segment_char_end=overlap_end - segment_start,
                )
            )
        chunk_key = stable_hash(
            IR_VERSION,
            variant_id,
            unit_key,
            chunk_index,
            text,
            [source.segment_key for source in sources],
        )
        chunks.append(
            PreparedChunk(
                chunk_key=chunk_key,
                variant_id=variant_id,
                volume_id=volume_id,
                anchor_page_num=page_num,
                unit_key=unit_key,
                stream_id=stream_id,
                role=role,
                script=script,
                chunk_index=chunk_index,
                text=text,
                sources=tuple(sources),
            )
        )
    return chunks


def build_variant_chunks(
    segments: Sequence[LogicalSegment],
    *,
    variants: Sequence[str],
    max_chars: int,
    overlap_chars: int,
) -> list[PreparedChunk]:
    unknown = sorted(set(variants) - set(SUPPORTED_VARIANTS))
    if unknown:
        raise ValueError(f"Variantes desconhecidas: {', '.join(unknown)}")
    by_page: dict[tuple[str, int], list[LogicalSegment]] = defaultdict(list)
    for segment in segments:
        by_page[(segment.volume_id, segment.page_num)].append(segment)

    result: list[PreparedChunk] = []
    for (volume_id, page_num), page_segments in sorted(by_page.items()):
        ordered = sorted(page_segments, key=lambda item: (item.block_index, item.part_index))
        baseline = [segment for segment in ordered if segment.included_v0]
        body = [segment for segment in baseline if segment.inferred_role == "body"]
        work_label = next((segment.work_label for segment in ordered if segment.work_label), "")
        stream_prefix = f"{volume_id}|{work_label or '[work-unknown]'}"
        if "v0" in variants and baseline:
            result.extend(
                _chunk_unit(
                    variant_id="v0",
                    volume_id=volume_id,
                    page_num=page_num,
                    unit_key=f"{volume_id}:p{page_num}:all",
                    stream_id=f"{stream_prefix}|all|mixed",
                    role="all",
                    script="mixed",
                    segments=baseline,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )
            )
        if "v1" in variants and body:
            result.extend(
                _chunk_unit(
                    variant_id="v1",
                    volume_id=volume_id,
                    page_num=page_num,
                    unit_key=f"{volume_id}:p{page_num}:body",
                    stream_id=f"{stream_prefix}|body|mixed",
                    role="body",
                    script="mixed",
                    segments=body,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )
            )
        if "v2" in variants:
            by_script: dict[str, list[LogicalSegment]] = defaultdict(list)
            for segment in body:
                by_script[segment.inferred_script].append(segment)
            for script, script_segments in sorted(by_script.items()):
                result.extend(
                    _chunk_unit(
                        variant_id="v2",
                        volume_id=volume_id,
                        page_num=page_num,
                        unit_key=f"{volume_id}:p{page_num}:body:{script}",
                        stream_id=f"{stream_prefix}|body|{script}",
                        role="body",
                        script=script,
                        segments=script_segments,
                        max_chars=max_chars,
                        overlap_chars=overlap_chars,
                    )
                )
    return result


def write_ir_jsonl(path: Path, segments: Iterable[LogicalSegment]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for segment in segments:
            handle.write(json.dumps(segment.to_dict(include_text=True), ensure_ascii=False))
            handle.write("\n")
            count += 1
    return count


__all__ = [
    "IR_VERSION",
    "SUPPORTED_VARIANTS",
    "ChunkSource",
    "LogicalSegment",
    "PreparedChunk",
    "build_variant_chunks",
    "canonical_declared_script",
    "embedding_visible_text",
    "extract_page_segments",
    "infer_role",
    "infer_script",
    "repeated_running_fingerprints",
    "split_structural_parts",
    "split_text_windows_with_offsets",
    "stable_hash",
    "structural_visible_text",
    "write_ir_jsonl",
]
