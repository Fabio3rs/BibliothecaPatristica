from __future__ import annotations

"""
Helper para localizar arquivos OCR físicos (`target_file`) a partir de entradas
de índice com nomes e páginas editoriais plausíveis.

Este módulo existe para reduzir improviso do agente LLM. Em vez de pedir ao
agente que resolva `target_file` diretamente a partir do OCR bruto, o helper:

- lê um único volume por execução
- normaliza texto e ligaturas para busca
- parseia o XML interno das páginas `.txt`
- infere paginação editorial provável a partir de cabeçalhos/rodapés e vizinhos
- ranqueia páginas candidatas com score, probabilidade e evidências

O helper não grava banco, não altera payloads e não produz o JSON final de
importação. Ele produz um JSON intermediário e explicável para o agente
consumir de forma conservadora.

Fluxo manual recomendado para validação:

1. Extrair algumas entradas reais para o JSON de entrada.
2. Rodar o helper contra um volume conhecido.
3. Comparar a saída com a localização manual conhecida.
4. Ajustar heurísticas e repetir, preservando os casos de falha como regressão.
"""

import json
import math
import re
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from rapidfuzz.distance import Levenshtein as _RapidLevenshtein
except ImportError:  # pragma: no cover - exercised only in minimal fallback environments
    _RapidLevenshtein = None

from tools.corpus_utils import page_number, page_sort_key
from tools.ocr_xml_utils import parse_ocr_page_xml_dict

_NUMBER_RE = re.compile(r"\b\d{1,4}\b")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")
_HINT_PAGE_RE = re.compile(r"\b\d{1,4}\b")
_OCR_NUMBER_TOKEN_RE = re.compile(
    r"(?<!\w)([0-9OIl|SB]{1,4})(?!\w)",
    re.IGNORECASE,
)
_OCR_NUMBER_TRANSLATION = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "i": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "b": "8",
    }
)
MAX_CANDIDATE_PAGES = 96
MIN_CANDIDATE_PAGES = 24
EXPECTED_INDEX_WINDOW = 18.0
PHYSICAL_NEAR_WINDOW = 10
EDITORIAL_HINT_WINDOW = 18
TEXT_CANDIDATE_LIMIT = 32
ONOMASTIC_COHORT_WINDOW = 8
SECTION_SCOPE_STOP_TOKENS = {
    "alphabetique",
    "alphabeticus",
    "index",
    "indices",
    "localis",
    "locorum",
    "mixed",
    "nominum",
    "onomasticus",
    "personarum",
    "personnel",
    "table",
}
MAX_COMBINED_PAGE_SIGNAL = 5.0
MATERIAL_TEXT_ANCHOR_BONUS = 2.5
MATERIAL_TEXT_EVIDENCE_KINDS = {
    "header_name_match",
    "body_name_match",
    "footer_name_match",
    "header_name_fuzzy",
    "body_name_fuzzy",
    "footer_name_fuzzy",
    "header_token_cover",
    "body_token_cover",
    "header_sequence_match",
    "header_sequence_start",
    "body_context_exact",
    "page_context_exact",
    "body_context_partial",
    "body_locator_name_cooccurrence",
    "body_locator_name_unique",
    "internal_locator_vs_editorial_sequence",
    "onomastic_neighbor_cohort",
    "onomastic_neighbor_cohort_unique",
    "onomastic_sibling_hint_family",
    "section_heading_family_match",
    "header_work_locator_match",
    "body_work_locator_match",
    "header_work_locator_fuzzy",
    "body_work_locator_fuzzy",
    "work_locator_number_cooccurrence",
}
MIN_HEADER_SEQUENCE_MATCHES = 4
MAX_HEADER_SEQUENCE_MISSING_FILES = 3
HEADER_SEQUENCE_MARKERS = {
    "elenchus",
    "index rerum",
    "ordo rerum",
    "quae in hoc tomo",
    "table des matieres",
}
FUZZY_STOP_TOKENS = {
    "a",
    "ab",
    "ad",
    "de",
    "des",
    "du",
    "et",
    "ex",
    "in",
    "la",
    "le",
    "liber",
    "libri",
    "opus",
    "s",
    "sancti",
}
_RESOLVE_WORKER_PAGES: list["PageCandidate"] | None = None
_RESOLVE_WORKER_TOP_K = 5


@dataclass(slots=True)
class Evidence:
    kind: str
    raw: str
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "raw": self.raw, "weight": round(self.weight, 4)}


@dataclass(slots=True)
class PageCandidate:
    file: str
    file_seq: int | None
    physical_index: int
    header_text: str
    body_text: str
    footer_text: str
    notes_text: str
    all_text: str
    header_norm: str
    body_norm: str
    footer_norm: str
    all_norm: str
    header_signature: str
    header_numbers: list[int]
    footer_numbers: list[int]
    body_numbers: list[int]
    raw_numbers: list[int]
    inferred_printed_page: int | None = None
    inferred_page_candidates: list[int] = field(default_factory=list)
    inferred_confidence: float = 0.0
    inferred_evidence: list[str] = field(default_factory=list)
    estimator_pages: list[int] = field(default_factory=list)
    estimator_confidence: float = 0.0
    estimator_evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CandidateAssessment:
    score: float
    evidence: list[Evidence]
    debug: dict[str, Any]
    candidate_role: str
    role_score: float
    role_reason: str


def fold_ligatures(text: str) -> str:
    return (
        text.replace("Æ", "AE")
        .replace("æ", "ae")
        .replace("Œ", "OE")
        .replace("œ", "oe")
    )


def normalize_for_search(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = fold_ligatures(text)
    text = text.casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text)
    return text.strip()


@lru_cache(maxsize=4096)
def resolve_paired_page_image(ocr_file: str) -> str | None:
    """Return the unambiguous sibling page image for one physical OCR file.

    The terminal filename number is used only to pair ``text`` and ``images``
    artifacts. It is never interpreted as an editorial page number.
    """

    text_path = Path(ocr_file).expanduser().resolve()
    sequence = page_number(text_path)
    if sequence is None:
        return None
    text_dir = text_path.parent
    volume_dir = text_dir.parent if text_dir.name == "text" else text_dir
    images_dir = volume_dir / "images"
    if not images_dir.is_dir():
        return None

    exact_candidates = [
        images_dir / f"{volume_dir.name}-{sequence:03d}.png",
        images_dir / f"{volume_dir.name}-{sequence}.png",
    ]
    exact = list(dict.fromkeys(path.resolve() for path in exact_candidates if path.is_file()))
    if len(exact) == 1:
        return str(exact[0])
    if len(exact) > 1:
        return None

    matches = sorted(
        {
            *(path.resolve() for path in images_dir.glob(f"*-{sequence:03d}.png")),
            *(path.resolve() for path in images_dir.glob(f"*-{sequence}.png")),
        }
    )
    return str(matches[0]) if len(matches) == 1 else None


def _contains_normalized_phrase(zone_norm: str, query_norm: str) -> bool:
    """Match complete normalized tokens, never an arbitrary substring.

    Short onomastic lemmata are especially vulnerable here: ``mani`` must not
    match ``manifest`` and ``leo`` must not match ``leontius``.
    """

    zone = str(zone_norm or "").strip()
    query = str(query_norm or "").strip()
    return bool(zone and query and f" {query} " in f" {zone} ")


def _logical_header_signature(text: str) -> str:
    tokens: list[str] = []
    for token in normalize_for_search(text).split():
        stripped = token.strip("[](){}")
        if (
            any(char.isdigit() for char in stripped)
            and all(char in "0123456789oilsb" for char in stripped)
        ):
            continue
        tokens.append(token)
    return " ".join(tokens)


def parse_ocr_page_xml(text: str) -> dict[str, str]:
    return parse_ocr_page_xml_dict(text)


@lru_cache(maxsize=4096)
def _read_ocr_page_text(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8", errors="replace")


@lru_cache(maxsize=4096)
def _parse_ocr_page_xml_cached(path_str: str) -> dict[str, str]:
    return parse_ocr_page_xml(_read_ocr_page_text(path_str))


def parse_ocr_page_path(path: Path) -> dict[str, str]:
    return _parse_ocr_page_xml_cached(str(path.resolve()))


def extract_zone_numbers(text: str) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for match in _OCR_NUMBER_TOKEN_RE.finditer(text or ""):
        raw = match.group(1)
        if not any(char.isdigit() for char in raw):
            continue
        normalized = raw.translate(_OCR_NUMBER_TRANSLATION)
        if not normalized.isdigit():
            continue
        value = int(normalized)
        if 0 < value < 10000 and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _candidate_pages_for_page(page: PageCandidate) -> list[int]:
    candidates: list[int] = []
    candidates.extend(page.header_numbers)
    candidates.extend(page.footer_numbers)
    if not candidates:
        candidates.extend(page.body_numbers[:4])
    return [value for value in candidates if 0 < value < 10000]


def _weighted_local_candidates(
    pages: list[PageCandidate],
    idx: int,
    adjacency_window: int,
) -> dict[int, float]:
    page = pages[idx]
    votes: dict[int, float] = {}

    def add_vote(value: int | None, weight: float) -> None:
        if value is None or value <= 0 or value >= 10000:
            return
        rounded = int(value)
        votes[rounded] = votes.get(rounded, 0.0) + weight

    for value in page.header_numbers[:4]:
        add_vote(value, 4.0)
    for value in page.footer_numbers[:4]:
        add_vote(value, 3.0)
    for value in page.body_numbers[:4]:
        add_vote(value, 1.0)

    for delta in range(1, adjacency_window + 1):
        prev_idx = idx - delta
        next_idx = idx + delta
        if prev_idx >= 0:
            prev_page = pages[prev_idx]
            prev_candidates = _candidate_pages_for_page(prev_page)
            for value in prev_candidates[:4]:
                add_vote(value + delta, 1.5 / delta)
        if next_idx < len(pages):
            next_page = pages[next_idx]
            next_candidates = _candidate_pages_for_page(next_page)
            for value in next_candidates[:4]:
                add_vote(value - delta, 1.5 / delta)
        if prev_idx >= 0 and next_idx < len(pages):
            prev_page = pages[prev_idx]
            next_page = pages[next_idx]
            prev_candidates = _candidate_pages_for_page(prev_page)
            next_candidates = _candidate_pages_for_page(next_page)
            gap = next_page.physical_index - prev_page.physical_index
            for previous_value, next_value in zip(
                prev_candidates[:4],
                next_candidates[:4],
            ):
                diff = next_value - previous_value
                if gap == diff:
                    add_vote(previous_value + delta, 2.5 / delta)
    return votes


def infer_printed_pages(pages: list[PageCandidate], adjacency_window: int = 4) -> None:
    for page in pages:
        page.inferred_evidence = []

    for idx, page in enumerate(pages):
        votes = _weighted_local_candidates(pages, idx, adjacency_window)
        if not votes:
            page.inferred_printed_page = None
            page.inferred_page_candidates = []
            page.inferred_confidence = 0.0
            continue
        best_value, best_weight = max(votes.items(), key=lambda item: item[1])
        sorted_votes = sorted(votes.items(), key=lambda item: item[1], reverse=True)
        runner_up = sorted_votes[1][1] if len(sorted_votes) > 1 else 0.0
        page.inferred_printed_page = best_value
        page.inferred_page_candidates = [
            value
            for value, weight in sorted_votes[:8]
            if weight >= best_weight * 0.60
        ]
        total_weight = sum(votes.values()) or 1.0
        margin = max(0.0, best_weight - runner_up)
        page.inferred_confidence = min(1.0, 0.15 + (best_weight / total_weight) * 0.55 + min(0.3, margin / 6.0))
        # Keep a short trace of the strongest local explanations for later review.
        top_evidence = sorted_votes[:3]
        page.inferred_evidence = [f"vote:{value}@{round(weight, 2)}" for value, weight in top_evidence]


def _estimate_expected_physical_indices(
    pages: list[PageCandidate],
    page_hints: list[int],
) -> dict[int, dict[str, Any]]:
    anchors = [
        (page.physical_index, page.inferred_printed_page)
        for page in pages
        if page.inferred_printed_page is not None
    ]
    for page in pages:
        for inferred in page.estimator_pages:
            anchors.append((page.physical_index, inferred))
    anchors.sort(key=lambda item: item[1])
    if not anchors or not page_hints:
        return {}

    out: dict[int, dict[str, Any]] = {}
    for hint in page_hints:
        below = None
        above = None
        for anchor in anchors:
            if anchor[1] <= hint:
                below = anchor
            if anchor[1] >= hint:
                above = anchor
                break

        if below and above:
            if below[1] == above[1]:
                expected_index = below[0]
                reason = f"exact_anchor:{below[1]}"
            else:
                span_pages = above[1] - below[1]
                span_files = above[0] - below[0]
                ratio = (hint - below[1]) / span_pages if span_pages else 0.0
                expected_index = below[0] + ratio * span_files
                reason = f"interp:{below[1]}->{above[1]}"
        elif below:
            expected_index = below[0] + (hint - below[1])
            reason = f"forward_from:{below[1]}"
        elif above:
            expected_index = above[0] - (above[1] - hint)
            reason = f"backward_from:{above[1]}"
        else:
            continue
        out[hint] = {"expected_index": float(expected_index), "reason": reason}
    return out


def _tokenize_query(text: str) -> list[str]:
    return [token for token in normalize_for_search(text).split() if len(token) >= 2]


def _levenshtein_distance(left: str, right: str) -> int:
    if _RapidLevenshtein is not None:
        return int(_RapidLevenshtein.distance(left, right))
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    if len(left) > len(right):
        left, right = right, left
    previous = list(range(len(left) + 1))
    for row_index, right_char in enumerate(right, start=1):
        current = [row_index]
        for column_index, left_char in enumerate(left, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column_index] + 1,
                    previous[column_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _levenshtein_similarity(
    left: str,
    right: str,
    *,
    score_cutoff: float = 0.0,
) -> float:
    if not left and not right:
        return 1.0
    if _RapidLevenshtein is not None:
        return float(
            _RapidLevenshtein.normalized_similarity(
                left,
                right,
                score_cutoff=score_cutoff,
                score_hint=score_cutoff or None,
            )
        )
    denominator = max(len(left), len(right))
    if not denominator:
        return 1.0
    similarity = 1.0 - (_levenshtein_distance(left, right) / denominator)
    return similarity if similarity >= score_cutoff else 0.0


def _minimum_fuzzy_similarity(query_norm: str, query_tokens: list[str]) -> float | None:
    if len(query_tokens) == 1:
        token_length = len(query_tokens[0])
        if token_length < 7:
            return None
        return 0.78 if token_length >= 9 else 0.82
    if len(query_tokens) < 2 or len(query_norm) < 12:
        return None
    if len(query_norm) < 20:
        return 0.9
    if len(query_norm) < 40:
        return 0.84
    return 0.78


def _best_phrase_similarity(query_norm: str, zone_norm: str) -> tuple[float, str]:
    query_tokens = query_norm.split()
    zone_tokens = zone_norm.split()
    threshold = _minimum_fuzzy_similarity(query_norm, query_tokens)
    if threshold is None or not zone_tokens:
        return 0.0, ""

    best_similarity = 0.0
    best_window = ""
    query_size = len(query_tokens)
    aligned_starts: set[int] = set()
    for zone_index, zone_token in enumerate(zone_tokens):
        for query_index, query_token in enumerate(query_tokens):
            if not _tokens_are_near(query_token, zone_token):
                continue
            base = zone_index - query_index
            aligned_starts.update((base - 1, base, base + 1))
    if not aligned_starts:
        return 0.0, ""
    window_sizes = {
        size
        for size in (query_size - 1, query_size, query_size + 1)
        if 1 <= size <= len(zone_tokens)
    }
    for window_size in sorted(window_sizes):
        for start in sorted(aligned_starts):
            if start < 0 or start + window_size > len(zone_tokens):
                continue
            window = " ".join(zone_tokens[start : start + window_size])
            denominator = max(len(query_norm), len(window))
            if not denominator:
                continue
            cutoff = max(threshold, best_similarity)
            if abs(len(query_norm) - len(window)) / denominator > 1.0 - cutoff:
                continue
            similarity = _levenshtein_similarity(
                query_norm,
                window,
                score_cutoff=cutoff,
            )
            if similarity > best_similarity:
                best_similarity = similarity
                best_window = window
                if similarity == 1.0:
                    return similarity, best_window
    if best_similarity < threshold:
        return 0.0, ""
    return best_similarity, best_window


def _tokens_are_near(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 5:
        return False
    if left[:4] == right[:4] and abs(len(left) - len(right)) <= 1:
        return True
    if min(len(left), len(right)) >= 7 and abs(len(left) - len(right)) <= 2:
        return _levenshtein_similarity(left, right, score_cutoff=0.75) >= 0.75
    return False


def _has_plausible_fuzzy_overlap(query_norm: str, zone_norm: str) -> bool:
    query_tokens = [
        token
        for token in query_norm.split()
        if len(token) >= 3 and token not in FUZZY_STOP_TOKENS
    ]
    zone_tokens = [
        token
        for token in zone_norm.split()
        if len(token) >= 3 and token not in FUZZY_STOP_TOKENS
    ]
    if not query_tokens or not zone_tokens:
        return False
    matched = sum(
        any(_tokens_are_near(query_token, zone_token) for zone_token in zone_tokens)
        for query_token in query_tokens
    )
    required = max(1, (len(query_tokens) + 1) // 2)
    return matched >= required


def _header_title_similarity(query_norm: str, header_signature: str) -> float:
    if not query_norm or not header_signature:
        return 0.0
    if any(marker in header_signature for marker in HEADER_SEQUENCE_MARKERS):
        return 0.0
    if _contains_normalized_phrase(header_signature, query_norm) or (
        len(header_signature) >= 12
        and _contains_normalized_phrase(query_norm, header_signature)
    ):
        return 1.0
    if not _has_plausible_fuzzy_overlap(query_norm, header_signature):
        return 0.0
    forward, _forward_window = _best_phrase_similarity(
        query_norm,
        header_signature,
    )
    reverse, _reverse_window = _best_phrase_similarity(
        header_signature,
        query_norm,
    )
    return max(forward, reverse)


def _header_sequences_for_entry(
    entry: dict[str, Any],
    pages: list[PageCandidate],
) -> dict[str, dict[str, Any]]:
    queries = [
        normalize_for_search(str(value))
        for value in entry.get("query_names") or []
        if str(value).strip()
    ]
    if not queries and entry.get("lemma_raw"):
        queries = [normalize_for_search(str(entry["lemma_raw"]))]
    evidence_by_file: dict[str, dict[str, Any]] = {}
    for query in (value for value in queries if value):
        matches: list[tuple[PageCandidate, float]] = []
        for page in pages:
            similarity = _header_title_similarity(query, page.header_signature)
            if similarity > 0.0:
                matches.append((page, similarity))
        groups: list[list[tuple[PageCandidate, float]]] = []
        for match in matches:
            if (
                groups
                and match[0].physical_index
                - groups[-1][-1][0].physical_index
                <= MAX_HEADER_SEQUENCE_MISSING_FILES + 1
            ):
                groups[-1].append(match)
            else:
                groups.append([match])
        for group in groups:
            if len(group) < MIN_HEADER_SEQUENCE_MATCHES:
                continue
            start_page = group[0][0]
            end_page = group[-1][0]
            sequence = {
                "query": query,
                "start_file": start_page.file,
                "end_file": end_page.file,
                "start_file_seq": start_page.file_seq,
                "end_file_seq": end_page.file_seq,
                "match_count": len(group),
                "max_missing_files": MAX_HEADER_SEQUENCE_MISSING_FILES,
            }
            for position, (page, similarity) in enumerate(group):
                candidate = {
                    **sequence,
                    "similarity": round(similarity, 4),
                    "is_sequence_start": position == 0,
                }
                previous = evidence_by_file.get(page.file)
                if previous is None or float(previous["similarity"]) < similarity:
                    evidence_by_file[page.file] = candidate
    return evidence_by_file


def _fuzzy_text_evidence(
    *,
    query: str,
    query_norm: str,
    page: PageCandidate,
    exact_zones: set[str],
) -> tuple[float, list[Evidence]]:
    evidence: list[Evidence] = []
    score = 0.0
    zones = (
        ("header", page.header_norm, 2.0),
        ("body", page.body_norm, 1.25),
        ("footer", page.footer_norm, 0.75),
    )
    for zone_name, zone_norm, max_weight in zones:
        if zone_name in exact_zones:
            continue
        if not _has_plausible_fuzzy_overlap(query_norm, zone_norm):
            continue
        similarity, matched = _best_phrase_similarity(query_norm, zone_norm)
        if similarity <= 0.0:
            continue
        weight = max_weight * similarity
        score += weight
        evidence.append(
            Evidence(
                f"{zone_name}_name_fuzzy",
                f"{query} => {matched} (levenshtein_similarity={similarity:.4f})",
                weight,
            )
        )
    return score, evidence


def _text_evidence_for_query(query: str, page: PageCandidate) -> tuple[float, list[Evidence]]:
    norm = normalize_for_search(query)
    if not norm:
        return 0.0, []

    score = 0.0
    evidence: list[Evidence] = []
    tokens = _tokenize_query(query)
    exact_zones: set[str] = set()

    if _contains_normalized_phrase(page.header_norm, norm):
        score += 4.0
        exact_zones.add("header")
        evidence.append(Evidence("header_name_match", query, 4.0))
    if _contains_normalized_phrase(page.body_norm, norm):
        score += 2.5
        exact_zones.add("body")
        evidence.append(Evidence("body_name_match", query, 2.5))
    if _contains_normalized_phrase(page.footer_norm, norm):
        score += 1.5
        exact_zones.add("footer")
        evidence.append(Evidence("footer_name_match", query, 1.5))

    if tokens:
        header_tokens = set(page.header_norm.split())
        body_tokens = set(page.body_norm.split())
        header_hits = sum(1 for token in tokens if token in header_tokens)
        body_hits = sum(1 for token in tokens if token in body_tokens)
        if header_hits and header_hits == len(tokens):
            score += 1.5
            evidence.append(Evidence("header_token_cover", " ".join(tokens), 1.5))
        if body_hits and body_hits == len(tokens):
            score += 1.0
            evidence.append(Evidence("body_token_cover", " ".join(tokens), 1.0))
    fuzzy_score, fuzzy_evidence = _fuzzy_text_evidence(
        query=query,
        query_norm=norm,
        page=page,
        exact_zones=exact_zones,
    )
    score += fuzzy_score
    evidence.extend(fuzzy_evidence)
    return score, evidence


def _context_evidence(context_raw: str, page: PageCandidate) -> tuple[float, list[Evidence]]:
    norm = normalize_for_search(context_raw)
    if not norm:
        return 0.0, []

    evidence: list[Evidence] = []
    score = 0.0
    tokens = _tokenize_query(context_raw)
    if len(tokens) < 3:
        return 0.0, []

    if _contains_normalized_phrase(page.body_norm, norm):
        score += 6.0
        evidence.append(Evidence("body_context_exact", context_raw, 6.0))
    elif _contains_normalized_phrase(page.all_norm, norm):
        score += 4.0
        evidence.append(Evidence("page_context_exact", context_raw, 4.0))
    else:
        body_tokens = set(page.body_norm.split())
        body_hits = sum(1 for token in tokens if token in body_tokens)
        if body_hits >= max(3, len(tokens) // 2):
            weight = min(3.5, 1.0 + 0.35 * body_hits)
            score += weight
            evidence.append(Evidence("body_context_partial", " ".join(tokens[: min(len(tokens), 8)]), weight))
    return score, evidence


def _page_hint_values(entry: dict[str, Any]) -> list[int]:
    values: list[int] = []
    for raw in entry.get("page_hint_ints") or []:
        try:
            values.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        for raw in entry.get("page_hints") or []:
            for match in _HINT_PAGE_RE.finditer(str(raw)):
                values.append(int(match.group(0)))
    return sorted(set(value for value in values if 0 < value < 10000))


def _page_evidence(page_hints: list[int], page: PageCandidate) -> tuple[float, list[Evidence]]:
    if not page_hints:
        return 0.0, []
    score = 0.0
    evidence: list[Evidence] = []
    number_pool = set(page.raw_numbers)
    inferred_pool = set(page.inferred_page_candidates)
    if page.inferred_printed_page is not None:
        number_pool.add(page.inferred_printed_page)
        inferred_pool.add(page.inferred_printed_page)
    number_pool.update(inferred_pool)
    estimator_pool = set(page.estimator_pages)
    number_pool.update(estimator_pool)
    body_pool = set(page.body_numbers)

    for hint in page_hints:
        if hint in body_pool:
            score += 2.5
            evidence.append(Evidence("body_locator_match", str(hint), 2.5))
        else:
            body_cer_match = next(
                (
                    value
                    for value in body_pool
                    if value != hint and _strong_numeric_cer_match(value, hint)
                ),
                None,
            )
            if body_cer_match is not None:
                score += 1.25
                evidence.append(
                    Evidence(
                        "body_locator_cer_match",
                        f"{hint}~{body_cer_match}",
                        1.25,
                    )
                )
        if hint in inferred_pool:
            score += 3.0
            evidence.append(Evidence("inferred_page_match", str(hint), 3.0))
        elif inferred_pool:
            nearest_inferred = min(inferred_pool, key=lambda value: abs(value - hint))
            distance = abs(nearest_inferred - hint)
            if distance == 1:
                score += 2.0
                evidence.append(Evidence("inferred_page_adjacent", f"{hint}~{nearest_inferred}", 2.0))
            elif distance == 2:
                score += 1.0
                evidence.append(Evidence("inferred_page_near", f"{hint}~{nearest_inferred}", 1.0))
        if hint in estimator_pool:
            score += 2.5
            evidence.append(Evidence("estimator_page_match", str(hint), 2.5))
        elif estimator_pool:
            nearest_estimator = min(abs(hint - value) for value in estimator_pool)
            if nearest_estimator == 1:
                score += 1.5
                evidence.append(Evidence("estimator_page_adjacent", f"{hint}~{sorted(estimator_pool)}", 1.5))
            elif nearest_estimator == 2:
                score += 0.75
                evidence.append(Evidence("estimator_page_near", f"{hint}~{sorted(estimator_pool)}", 0.75))
        if hint in number_pool:
            score += 1.5
            evidence.append(Evidence("raw_number_match", str(hint), 1.5))
    if page.inferred_confidence > 0 and page.inferred_printed_page is not None:
        weight = min(1.5, round(page.inferred_confidence, 4))
        score += weight
        evidence.append(Evidence("adjacent_page_consensus", ", ".join(page.inferred_evidence or [str(page.inferred_printed_page)]), weight))
    if page.estimator_confidence > 0 and page.estimator_pages:
        weight = min(1.25, round(page.estimator_confidence, 4))
        score += weight
        evidence.append(Evidence("editorial_page_estimator", ", ".join(page.estimator_evidence or [str(page.estimator_pages)]), weight))
    return score, evidence


def _page_penalty(page_hints: list[int], page: PageCandidate) -> tuple[float, list[Evidence]]:
    if not page_hints:
        return 0.0, []
    candidate_numbers: list[int] = []
    candidate_numbers.extend(page.inferred_page_candidates)
    candidate_numbers.extend(page.raw_numbers)
    candidate_numbers.extend(page.estimator_pages)
    if not candidate_numbers:
        return 0.0, []
    if any(
        _numeric_cer_distance(value, hint) <= 1
        for value in candidate_numbers
        for hint in page_hints
    ):
        return 0.0, []
    nearest = min(abs(value - hint) for value in candidate_numbers for hint in page_hints)
    if nearest <= 2:
        return 0.0, []
    if nearest <= 10:
        penalty = -1.5
    elif nearest <= 50:
        penalty = -4.0
    elif nearest <= 150:
        penalty = -7.0
    else:
        penalty = -10.0
    return penalty, [Evidence("page_hint_distance_penalty", f"{page.inferred_printed_page} vs {page_hints}", penalty)]


def _physical_position_evidence(
    page_hints: list[int],
    expected_positions: dict[int, dict[str, Any]],
    page: PageCandidate,
) -> tuple[float, list[Evidence]]:
    if not page_hints or not expected_positions:
        return 0.0, []
    score = 0.0
    evidence: list[Evidence] = []
    for hint in page_hints:
        expected = expected_positions.get(hint)
        if not expected:
            continue
        candidate_numbers = [
            *page.inferred_page_candidates,
            *page.raw_numbers,
            *page.estimator_pages,
        ]
        if (
            hint not in candidate_numbers
            and any(_numeric_cer_distance(value, hint) <= 1 for value in candidate_numbers)
        ):
            # A one-digit OCR mutation is weak editorial evidence and must not drag an otherwise
            # exact textual target toward the position inferred from the corrupt number.
            continue
        distance = abs(page.physical_index - expected["expected_index"])
        if distance <= 1:
            weight = 2.5
            evidence.append(Evidence("physical_index_match", f"{hint} via {expected['reason']}", weight))
            score += weight
        elif distance <= 3:
            weight = 1.5
            evidence.append(Evidence("physical_index_near", f"{hint} via {expected['reason']}", weight))
            score += weight
        elif distance <= 8:
            weight = 0.5
            evidence.append(Evidence("physical_index_loose", f"{hint} via {expected['reason']}", weight))
            score += weight
        elif distance > 20:
            weight = -2.0
            evidence.append(Evidence("physical_index_penalty", f"{hint} via {expected['reason']}", weight))
            score += weight
    return score, evidence


def _likely_index_page_penalty(
    entry: dict[str, Any],
    page: PageCandidate,
    page_hints: list[int],
    expected_positions: dict[int, dict[str, Any]],
    evidence: list[Evidence],
) -> tuple[float, list[Evidence]]:
    context_raw = str(entry.get("context_raw") or "").strip()
    if not context_raw:
        return 0.0, []

    norm_context = normalize_for_search(context_raw)
    exact_context_on_page = _contains_normalized_phrase(
        page.body_norm,
        norm_context,
    )
    has_hint_number_on_page = any(hint in set(page.raw_numbers) for hint in page_hints)
    far_from_hint = False
    if page_hints and page.inferred_printed_page is not None:
        far_from_hint = min(abs(page.inferred_printed_page - hint) for hint in page_hints) > 50
    far_from_expected_index = False
    if expected_positions:
        distances = [
            abs(page.physical_index - expected["expected_index"])
            for expected in expected_positions.values()
        ]
        if distances:
            far_from_expected_index = min(distances) > 20

    if exact_context_on_page and has_hint_number_on_page and (far_from_hint or far_from_expected_index):
        penalty = -6.0
        return penalty, [
            Evidence(
                "index_page_penalty",
                "page contains the index entry context itself and is far from the hinted editorial target",
                penalty,
            )
        ]
    return 0.0, []


def _candidate_role(
    entry: dict[str, Any],
    page: PageCandidate,
    evidence: list[Evidence],
    page_hints: list[int],
    expected_positions: dict[int, dict[str, Any]],
) -> tuple[str, float, str]:
    context_raw = str(entry.get("context_raw") or "").strip()
    norm_context = normalize_for_search(context_raw) if context_raw else ""
    exact_context_on_page = _contains_normalized_phrase(
        page.body_norm,
        norm_context,
    )
    nearest_hint_distance = None
    inferred_candidates = list(page.inferred_page_candidates)
    if page.inferred_printed_page is not None:
        inferred_candidates.append(page.inferred_printed_page)
    if page_hints and inferred_candidates:
        nearest_hint_distance = min(
            abs(value - hint)
            for value in inferred_candidates
            for hint in page_hints
        )
    nearest_expected_distance = None
    if expected_positions:
        candidate_numbers = [
            *page.inferred_page_candidates,
            *page.estimator_pages,
        ]
        numeric_cer_match = any(
            value != hint and _numeric_cer_distance(value, hint) <= 1
            for value in candidate_numbers
            for hint in page_hints
        )
        if not numeric_cer_match:
            nearest_expected_distance = min(
                abs(page.physical_index - expected["expected_index"])
                for expected in expected_positions.values()
            )

    target_signal = 0.0
    index_signal = 0.0
    for item in evidence:
        if item.kind in {
            "body_locator_match",
            "body_locator_cer_match",
            "header_work_locator_match",
            "body_work_locator_match",
            "header_work_locator_fuzzy",
            "body_work_locator_fuzzy",
            "work_locator_number_cooccurrence",
            "editorial_page_cer_neighbor_validated",
            "inferred_page_match",
            "inferred_page_adjacent",
            "inferred_page_near",
            "raw_number_match",
        }:
            target_signal += max(0.0, item.weight)
        if item.kind in {"body_context_exact", "body_context_partial", "index_page_penalty"}:
            index_signal += abs(item.weight)
        if item.kind in {
            "onomastic_neighbor_cohort",
            "onomastic_neighbor_cohort_unique",
            "onomastic_sibling_hint_family",
            "section_heading_family_match",
        }:
            target_signal += max(0.0, item.weight)

    if exact_context_on_page:
        index_signal += 2.0
    if nearest_hint_distance is not None:
        if nearest_hint_distance <= 2:
            target_signal += 2.0
        elif nearest_hint_distance > 50:
            index_signal += 2.0
    if nearest_expected_distance is not None:
        if nearest_expected_distance <= 2:
            target_signal += 2.0
        elif nearest_expected_distance > 20:
            index_signal += 1.5

    if target_signal >= index_signal + 1.5:
        return "target_candidate", round(target_signal - index_signal, 4), "editorial page evidence dominates text-context evidence"
    if index_signal >= target_signal + 1.5:
        return "index_page_candidate", round(index_signal - target_signal, 4), "page looks more like the index entry page than the referenced target page"
    return "mixed_signal", round(abs(target_signal - index_signal), 4), "text and editorial signals point in different directions"


def _build_reason_summary(
    candidate_role: str,
    page: PageCandidate,
    page_hints: list[int],
    evidence: list[Evidence],
) -> str:
    parts: list[str] = [candidate_role]
    if page.inferred_printed_page is not None:
        parts.append(f"inferred_page={page.inferred_printed_page}")
    if page_hints:
        parts.append(f"page_hints={page_hints}")
    strong = [item.kind for item in evidence if abs(item.weight) >= 2.5][:3]
    if strong:
        parts.append("strong=" + ",".join(strong))
    return "; ".join(parts)


def _editorial_page_decision_source(page: PageCandidate) -> str:
    traces = list(page.inferred_evidence or [])
    if any(trace.startswith("estimator_override_weak_local:") for trace in traces):
        return "estimator_replaced_weak_local"
    if any(trace.startswith("estimator:") for trace in traces):
        return "estimator_rescued_weak_local"
    if any(trace.startswith("estimator_confirm:") for trace in traces):
        return "estimator_confirmed"
    if page.estimator_pages and page.inferred_printed_page in set(page.estimator_pages):
        return "estimator_confirmed"
    return "local_only"


def _work_locator_evidence(
    entry: dict[str, Any],
    page: PageCandidate,
) -> tuple[float, list[Evidence]]:
    if entry.get("ref_kind") not in {"target_locator", "parallel_locator"}:
        return 0.0, []
    ref_raw = str(entry.get("ref_raw") or "").strip()
    if not ref_raw:
        return 0.0, []
    prefix_raw = re.split(
        r"(?:\bcap(?:ut)?\.?|\blib(?:er|ro)?\.?|\btract\.?|\bhom\.?|"
        r"\bepist\.?|\bserm\.?|§)",
        ref_raw,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    prefix = normalize_for_search(prefix_raw)
    if len(prefix) < 4 or not any(char.isalpha() for char in prefix):
        return 0.0, []
    score, generic = _text_evidence_for_query(prefix, page)
    if score <= 0:
        return 0.0, []
    mapped: list[Evidence] = []
    kind_map = {
        "header_name_match": "header_work_locator_match",
        "body_name_match": "body_work_locator_match",
        "header_name_fuzzy": "header_work_locator_fuzzy",
        "body_name_fuzzy": "body_work_locator_fuzzy",
    }
    for item in generic:
        mapped.append(
            Evidence(kind_map.get(item.kind, item.kind), item.raw, item.weight)
        )
    locator_numbers = {
        int(value)
        for value in re.findall(r"(?<!\w)\d{1,3}(?!\w)", ref_raw)
    }
    observed_numbers = set(page.body_numbers) | set(page.header_numbers)
    matched_numbers = sorted(locator_numbers & observed_numbers)
    if locator_numbers and matched_numbers:
        number_weight = min(3.0, 1.5 + 0.75 * len(matched_numbers))
        score += number_weight
        mapped.append(
            Evidence(
                "work_locator_number_cooccurrence",
                (
                    f"work={prefix!r}; locator_numbers={sorted(locator_numbers)}; "
                    f"matched={matched_numbers}"
                ),
                number_weight,
            )
        )
    return min(score, 9.0), mapped


def score_page_candidate(entry: dict[str, Any], page: PageCandidate) -> CandidateAssessment:
    total = 0.0
    evidence: list[Evidence] = []

    work_locator_score, work_locator_evidence = _work_locator_evidence(
        entry,
        page,
    )
    total += work_locator_score
    evidence.extend(work_locator_evidence)

    query_names = list(entry.get("query_names") or [])
    if not query_names and entry.get("lemma_raw"):
        query_names = [entry["lemma_raw"]]

    query_assessments = [
        _text_evidence_for_query(query, page)
        for query in query_names
    ]
    is_onomastic = str(entry.get("section_kind") or "").startswith("onomastic_")
    alternatives_are_aliases = entry.get("query_match_mode") == "best"
    alternative_match_count = sum(score > 0 for score, _ in query_assessments)
    if is_onomastic or alternatives_are_aliases:
        # Inflected spellings and ordinal expansions are alternative readings of
        # one name. Work-title variants have the same property: the full
        # contents description and its short title nucleus must not be counted
        # as independent corroborating evidence.
        query_assessments = sorted(
            query_assessments,
            key=lambda item: item[0],
            reverse=True,
        )[:1]
    for query_score, query_evidence in query_assessments:
        total += query_score
        evidence.extend(query_evidence)
    if is_onomastic and alternative_match_count > 1:
        # A second grammatical form corroborates the match, but its contribution
        # is capped so generated alternatives cannot multiply the name score.
        corroboration_weight = 2.0
        total += corroboration_weight
        evidence.append(
            Evidence(
                "onomastic_name_variant_corroboration",
                f"matched_alternative_count={alternative_match_count}",
                corroboration_weight,
            )
        )

    page_hints = _page_hint_values(entry)
    estimator_kinds = set(page.estimator_evidence)
    neighbor_validated = bool(
        estimator_kinds
        & {"neighbor_fit", "neighbor_single_fit", "sequence_consensus"}
    )
    has_name_evidence = any(
        item.kind
        in {
            "header_name_match",
            "body_name_match",
            "footer_name_match",
            "header_name_fuzzy",
            "body_name_fuzzy",
            "footer_name_fuzzy",
        }
        for item in evidence
    )
    editorial_cer_pairs = [
        (hint, editorial_page)
        for hint in page_hints
        for editorial_page in page.estimator_pages
        if hint != editorial_page
        and _strong_numeric_cer_match(hint, editorial_page)
    ]
    if (
        str(entry.get("section_kind") or "").startswith("onomastic_")
        and has_name_evidence
        and page.estimator_confidence >= 0.82
        and neighbor_validated
        and editorial_cer_pairs
    ):
        weight = 3.5
        total += weight
        evidence.append(
            Evidence(
                "editorial_page_cer_neighbor_validated",
                (
                    f"index_to_editorial={editorial_cer_pairs}; "
                    f"estimator_confidence={page.estimator_confidence:.4f}; "
                    f"validation={','.join(sorted(estimator_kinds))}"
                ),
                weight,
            )
        )

    cooccurrence = (entry.get("_body_locator_name_by_file") or {}).get(page.file)
    if isinstance(cooccurrence, dict):
        unique = bool(cooccurrence.get("unique"))
        weight = 5.0 if unique else 2.5
        total += weight
        evidence.append(
            Evidence(
                "body_locator_name_unique"
                if unique
                else "body_locator_name_cooccurrence",
                str(cooccurrence.get("detail") or "name and locator occur in body"),
                weight,
            )
        )
        estimator_disagrees = bool(
            page_hints
            and page.estimator_pages
            and all(
                abs(editorial_page - hint) > 2
                for editorial_page in page.estimator_pages
                for hint in page_hints
            )
        )
        if (
            unique
            and page.estimator_confidence >= 0.82
            and neighbor_validated
            and estimator_disagrees
        ):
            validation_weight = 3.5
            total += validation_weight
            evidence.append(
                Evidence(
                    "internal_locator_vs_editorial_sequence",
                    (
                        f"body_locator={page_hints}; "
                        f"editorial_pages={page.estimator_pages}; "
                        f"estimator_confidence={page.estimator_confidence:.4f}; "
                        f"validation={','.join(sorted(estimator_kinds))}"
                    ),
                    validation_weight,
                )
            )

    cross_hints = (entry.get("_onomastic_cross_hints_by_file") or {}).get(
        page.file
    )
    if isinstance(cross_hints, dict):
        group_count = int(cross_hints.get("neighbor_group_count") or 0)
        unique = bool(cross_hints.get("unique"))
        cohort_weight = min(5.0, 1.25 * group_count + (1.5 if unique else 0.0))
        total += cohort_weight
        evidence.append(
            Evidence(
                "onomastic_neighbor_cohort_unique"
                if unique
                else "onomastic_neighbor_cohort",
                str(cross_hints.get("detail") or "neighboring lemmata in local file family"),
                cohort_weight,
            )
        )

    sibling_family = (entry.get("_onomastic_sibling_families") or {}).get(
        _file_family(page.file)
    )
    if isinstance(sibling_family, dict):
        family_weight = 4.0
        total += family_weight
        evidence.append(
            Evidence(
                "onomastic_sibling_hint_family",
                str(sibling_family.get("detail") or "same-name sibling refs select this family"),
                family_weight,
            )
        )

    section_family = (entry.get("_section_heading_families") or {}).get(
        _file_family(page.file)
    )
    if isinstance(section_family, dict):
        scope_weight = 4.5
        total += scope_weight
        evidence.append(
            Evidence(
                "section_heading_family_match",
                str(section_family.get("detail") or "running header matches indexed work"),
                scope_weight,
            )
        )

    header_sequence = (entry.get("_header_sequence_by_file") or {}).get(page.file)
    if isinstance(header_sequence, dict):
        sequence_weight = 2.5
        total += sequence_weight
        evidence.append(
            Evidence(
                "header_sequence_match",
                (
                    f"{header_sequence.get('start_file')}.."
                    f"{header_sequence.get('end_file')}; "
                    f"matches={header_sequence.get('match_count')}; "
                    f"similarity={header_sequence.get('similarity')}"
                ),
                sequence_weight,
            )
        )
        if header_sequence.get("is_sequence_start"):
            start_weight = 1.5
            total += start_weight
            evidence.append(
                Evidence(
                    "header_sequence_start",
                    str(header_sequence.get("start_file") or page.file),
                    start_weight,
                )
            )

    page_hints = _page_hint_values(entry)
    expected_positions = entry.get("_expected_positions") or {}
    context_raw = str(entry.get("context_raw") or "").strip()
    if context_raw:
        context_score, context_evidence = _context_evidence(context_raw, page)
        total += context_score
        evidence.extend(context_evidence)

    if any(item.kind in MATERIAL_TEXT_EVIDENCE_KINDS for item in evidence):
        total += MATERIAL_TEXT_ANCHOR_BONUS
        evidence.append(
            Evidence(
                "material_text_anchor_bonus",
                "textual OCR evidence is primary under uncertain pagination",
                MATERIAL_TEXT_ANCHOR_BONUS,
            )
        )

    page_score, page_evs = _page_evidence(page_hints, page)
    # Inferred page, estimator page, raw digits, and neighbor consensus are correlated readings of
    # the same printed number. Cap their combined contribution so one corrupted digit cannot swamp
    # direct OCR text.
    total += min(page_score, MAX_COMBINED_PAGE_SIGNAL)
    evidence.extend(page_evs)
    position_score, position_evs = _physical_position_evidence(page_hints, expected_positions, page)
    total += position_score
    evidence.extend(position_evs)
    page_penalty, penalty_evs = _page_penalty(page_hints, page)
    total += page_penalty
    evidence.extend(penalty_evs)
    index_penalty, index_penalty_evs = _likely_index_page_penalty(entry, page, page_hints, expected_positions, evidence)
    total += index_penalty
    evidence.extend(index_penalty_evs)

    candidate_role, role_score, role_reason = _candidate_role(entry, page, evidence, page_hints, expected_positions)

    debug = {
        "normalized_queries": [normalize_for_search(query) for query in query_names if query],
        "page_hints_used": page_hints,
        "header_sequence": header_sequence,
    }
    return CandidateAssessment(
        score=total,
        evidence=evidence,
        debug=debug,
        candidate_role=candidate_role,
        role_score=role_score,
        role_reason=role_reason,
    )


def _softmax_probabilities(scores: list[float]) -> list[float]:
    if not scores:
        return []
    peak = max(scores)
    exps = [math.exp(score - peak) for score in scores]
    denom = sum(exps) or 1.0
    return [value / denom for value in exps]


def _load_volume_pages(source_root: Path, adjacency_window: int) -> list[PageCandidate]:
    pages: list[PageCandidate] = []
    for physical_index, path in enumerate(sorted(source_root.glob("*.txt"), key=page_sort_key)):
        parsed = parse_ocr_page_path(path)
        header_text = parsed["header_text"]
        body_text = parsed["body_text"]
        footer_text = parsed["footer_text"]
        notes_text = parsed["notes_text"]
        all_text = parsed["all_text"]
        page = PageCandidate(
            file=str(path),
            file_seq=page_number(path),
            physical_index=physical_index,
            header_text=header_text,
            body_text=body_text,
            footer_text=footer_text,
            notes_text=notes_text,
            all_text=all_text,
            header_norm=normalize_for_search(header_text),
            body_norm=normalize_for_search(body_text),
            footer_norm=normalize_for_search(footer_text),
            all_norm=normalize_for_search(all_text),
            header_signature=_logical_header_signature(header_text),
            header_numbers=extract_zone_numbers(header_text),
            footer_numbers=extract_zone_numbers(footer_text),
            body_numbers=extract_zone_numbers(body_text),
            raw_numbers=extract_zone_numbers(all_text),
        )
        pages.append(page)
    infer_printed_pages(pages, adjacency_window=adjacency_window)
    volume_id = source_root.parent.name
    collection = volume_id[:2].upper()
    if collection in {"PG", "PL"}:
        try:
            from .editorial_page_estimator import estimate_editorial_pages

            estimate_payload = estimate_editorial_pages(
                volume_id=volume_id,
                source_root=source_root,
                collection=collection,
                window=adjacency_window,
            )
            by_file = {item["file"]: item for item in estimate_payload.get("files") or []}
            for page in pages:
                est = by_file.get(page.file)
                if not est:
                    continue
                best_guess = est.get("best_guess")
                if isinstance(best_guess, list):
                    page.estimator_pages = [int(item) for item in best_guess]
                elif isinstance(best_guess, int):
                    page.estimator_pages = [best_guess]
                else:
                    page.estimator_pages = []
                page.estimator_confidence = float(est.get("confidence") or 0.0)
                page.estimator_evidence = [item.get("kind", "") for item in est.get("evidence") or [] if item.get("kind")]

                if page.inferred_printed_page is None and page.estimator_pages:
                    page.inferred_printed_page = page.estimator_pages[0]
                    page.inferred_page_candidates = list(
                        dict.fromkeys(
                            [*page.estimator_pages, *page.inferred_page_candidates]
                        )
                    )
                    page.inferred_confidence = max(page.inferred_confidence, min(0.75, page.estimator_confidence))
                    page.inferred_evidence.append(f"estimator:{page.estimator_pages}")
                    continue

                if page.inferred_printed_page is not None and page.estimator_pages:
                    nearest = min(abs(page.inferred_printed_page - value) for value in page.estimator_pages)
                    if nearest <= 1:
                        page.inferred_confidence = min(1.0, max(page.inferred_confidence, page.estimator_confidence) + 0.08)
                        page.inferred_evidence.append(f"estimator_confirm:{page.estimator_pages}")
                    elif page.inferred_confidence < 0.35 and page.estimator_confidence >= 0.88:
                        page.inferred_printed_page = page.estimator_pages[0]
                        page.inferred_page_candidates = list(
                            dict.fromkeys(
                                [*page.estimator_pages, *page.inferred_page_candidates]
                            )
                        )
                        page.inferred_confidence = max(page.inferred_confidence, min(0.82, page.estimator_confidence))
                        page.inferred_evidence.append(f"estimator_override_weak_local:{page.estimator_pages}")
        except Exception:
            # The locator still works without estimator support; keep it best-effort.
            pass
    return pages


def _request_options(request: dict[str, Any]) -> tuple[int, int, int]:
    options = request.get("options") or {}
    top_k = int(options.get("top_k", 5))
    adjacency_window = int(options.get("adjacency_window", 4))
    workers = int(options.get("workers", 1))
    return (
        max(1, top_k),
        max(1, adjacency_window),
        max(1, workers),
    )


def _progress_interval(total_entries: int) -> int:
    if total_entries <= 50:
        return 10
    if total_entries <= 250:
        return 25
    if total_entries <= 1000:
        return 50
    return 100


def _pages_near_expected_positions(
    pages: list[PageCandidate],
    expected_positions: dict[int, dict[str, Any]],
) -> list[PageCandidate]:
    if not expected_positions:
        return []
    matches: list[PageCandidate] = []
    for page in pages:
        for expected in expected_positions.values():
            if abs(page.physical_index - expected["expected_index"]) <= EXPECTED_INDEX_WINDOW:
                matches.append(page)
                break
    return matches


def _pages_near_editorial_hints(
    pages: list[PageCandidate],
    page_hints: list[int],
) -> list[PageCandidate]:
    if not page_hints:
        return []
    matches: list[PageCandidate] = []
    for page in pages:
        candidate_numbers = [
            *page.inferred_page_candidates,
            *page.estimator_pages,
        ]
        if not candidate_numbers:
            continue
        if min(abs(value - hint) for value in candidate_numbers for hint in page_hints) <= EDITORIAL_HINT_WINDOW:
            matches.append(page)
            continue
        if any(
            _numeric_cer_distance(value, hint) <= 1
            for value in candidate_numbers
            for hint in page_hints
        ):
            matches.append(page)
    return matches


def _numeric_cer_distance(left: int, right: int) -> int:
    """Return a small edit distance for OCR-corrupted decimal page labels."""
    a = str(left)
    b = str(right)
    if a == b:
        return 0
    if len(a) == len(b):
        return sum(char_a != char_b for char_a, char_b in zip(a, b, strict=True))
    if abs(len(a) - len(b)) > 1:
        return max(len(a), len(b))
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1 :] == shorter:
            return 1
    return max(len(a), len(b))


def _strong_numeric_cer_match(left: int, right: int) -> bool:
    """Allow an exact number or one same-width digit substitution.

    Dropped/inserted digits are useful for candidate recall elsewhere, but are too
    permissive for deterministic name+locator cooccurrence (for example 12~129).
    """

    a = str(left)
    b = str(right)
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b, strict=True)) <= 1


def _pages_with_exact_text_evidence(
    entry: dict[str, Any],
    pages: list[PageCandidate],
) -> list[PageCandidate]:
    queries = [str(value) for value in (entry.get("query_names") or []) if str(value).strip()]
    if not queries and entry.get("lemma_raw"):
        queries = [str(entry["lemma_raw"])]
    normalized_queries = [normalize_for_search(value) for value in queries]
    normalized_queries = [value for value in normalized_queries if value]
    context_norm = normalize_for_search(str(entry.get("context_raw") or ""))
    matches: list[tuple[int, PageCandidate]] = []
    for page in pages:
        score = sum(
            3
            if _contains_normalized_phrase(page.header_norm, query)
            else 2
            if _contains_normalized_phrase(page.body_norm, query)
            else 1
            if _contains_normalized_phrase(page.all_norm, query)
            else 0
            for query in normalized_queries
        )
        if _contains_normalized_phrase(page.all_norm, context_norm):
            score += 4
        if score:
            matches.append((score, page))
    matches.sort(key=lambda item: (-item[0], item[1].physical_index))
    return [page for _score, page in matches[:TEXT_CANDIDATE_LIMIT]]


def _index_tail_start(entry: dict[str, Any], pages: list[PageCandidate]) -> int | None:
    raw_start = str(entry.get("section_file_start") or "").strip()
    if not raw_start:
        return None
    for page in pages:
        if page.file == raw_start or Path(page.file).name == Path(raw_start).name:
            return page.physical_index
    return None


def _excluded_index_files(
    entry: dict[str, Any],
    pages: list[PageCandidate],
) -> set[str]:
    """Expand declared semantic index intervals to physical OCR files."""

    intervals = list(entry.get("excluded_index_intervals") or [])
    if not intervals and (
        entry.get("section_file_start") or entry.get("section_file_end")
    ):
        intervals = [
            {
                "index_ocr_file_start": entry.get("section_file_start"),
                "index_ocr_file_end": entry.get("section_file_end"),
            }
        ]
    by_path = {page.file: page for page in pages}
    by_name = {Path(page.file).name: page for page in pages}
    excluded: set[str] = set()
    for interval in intervals:
        if not isinstance(interval, dict):
            continue
        start_text = str(interval.get("index_ocr_file_start") or "").strip()
        end_text = str(interval.get("index_ocr_file_end") or "").strip()
        start = by_path.get(start_text) or by_name.get(Path(start_text).name)
        end = by_path.get(end_text) or by_name.get(Path(end_text).name)
        if start is not None and end is not None:
            lower, upper = sorted(
                (start.physical_index, end.physical_index)
            )
            excluded.update(
                page.file
                for page in pages
                if lower <= page.physical_index <= upper
            )
        else:
            for endpoint in (start, end):
                if endpoint is not None:
                    excluded.add(endpoint.file)
    return excluded


def _file_family(path: str) -> str:
    return re.sub(r"-\d+\.txt$", "", Path(path).name, flags=re.IGNORECASE)


def _section_heading_families(
    entry: dict[str, Any],
    pages: list[PageCandidate],
) -> dict[str, dict[str, Any]]:
    heading = normalize_for_search(str(entry.get("section_heading") or ""))
    tokens = [
        token
        for token in heading.split()
        if len(token) >= 6 and token not in SECTION_SCOPE_STOP_TOKENS
    ]
    if not tokens:
        return {}
    tail_start = _index_tail_start(entry, pages)
    hits: dict[str, dict[str, Any]] = {}
    for page in pages:
        if tail_start is not None and page.physical_index >= tail_start:
            continue
        header_tokens = set(page.header_signature.split())
        matched = sorted({token for token in tokens if token in header_tokens})
        if not matched:
            continue
        family = _file_family(page.file)
        item = hits.setdefault(family, {"files": set(), "tokens": set()})
        item["files"].add(page.file)
        item["tokens"].update(matched)
    return {
        family: {
            "matched_file_count": len(item["files"]),
            "matched_tokens": sorted(item["tokens"]),
            "detail": (
                f"family={family!r}; heading_tokens={sorted(item['tokens'])}; "
                f"repeated_header_files={len(item['files'])}"
            ),
        }
        for family, item in hits.items()
        if len(item["files"]) >= 2
    }


def _onomastic_cross_hints(
    entry: dict[str, Any],
    pages: list[PageCandidate],
) -> dict[str, dict[str, Any]]:
    """Cross an entry name with neighboring index lemmata in one OCR family."""

    if not str(entry.get("section_kind") or "").startswith("onomastic_"):
        return {}
    own_queries = [
        normalize_for_search(str(value))
        for value in entry.get("query_names") or []
        if str(value).strip()
    ]
    own_queries = [value for value in dict.fromkeys(own_queries) if len(value) >= 4]
    neighbor_groups = []
    for raw_group in entry.get("neighbor_query_groups") or []:
        if not isinstance(raw_group, list):
            continue
        group = [
            normalize_for_search(str(value))
            for value in raw_group
            if str(value).strip()
        ]
        group = [value for value in dict.fromkeys(group) if len(value) >= 4]
        if group:
            neighbor_groups.append(group)
    if not own_queries or len(neighbor_groups) < 2:
        return {}
    tail_start = _index_tail_start(entry, pages)
    eligible = [
        page
        for page in pages
        if tail_start is None or page.physical_index < tail_start
    ]
    by_family: dict[str, list[PageCandidate]] = {}
    for page in eligible:
        by_family.setdefault(_file_family(page.file), []).append(page)
    candidates: list[tuple[PageCandidate, str, list[int]]] = []
    for page in eligible:
        own_query = next(
            (
                query
                for query in own_queries
                if _contains_normalized_phrase(page.all_norm, query)
            ),
            None,
        )
        if own_query is None:
            continue
        local_pages = [
            candidate
            for candidate in by_family.get(_file_family(page.file), [])
            if (
                page.file_seq is not None
                and candidate.file_seq is not None
                and abs(candidate.file_seq - page.file_seq) <= ONOMASTIC_COHORT_WINDOW
            )
            or candidate.file == page.file
        ]
        local_text = " ".join(candidate.all_norm for candidate in local_pages)
        matched_groups = [
            group_index
            for group_index, group in enumerate(neighbor_groups)
            if any(
                _contains_normalized_phrase(local_text, query)
                for query in group
            )
        ]
        if len(matched_groups) >= 2:
            candidates.append((page, own_query, matched_groups))
    if not candidates:
        return {}
    best_count = max(len(item[2]) for item in candidates)
    strongest = [item for item in candidates if len(item[2]) == best_count]
    unique = len(strongest) == 1
    return {
        page.file: {
            "unique": unique and len(groups) == best_count,
            "neighbor_group_count": len(groups),
            "detail": (
                f"own_query={query!r}; neighbor_groups={groups}; "
                f"family={_file_family(page.file)!r}; window={ONOMASTIC_COHORT_WINDOW}; "
                f"strongest_candidate_count={len(strongest)}"
            ),
        }
        for page, query, groups in candidates
    }


def _body_locator_name_cooccurrences(
    entry: dict[str, Any],
    pages: list[PageCandidate],
) -> dict[str, dict[str, Any]]:
    """Find exact name+locator pairs outside the source index tail.

    Historical onomastic indexes sometimes cite internal paragraph/source-page
    numbers printed in the body, not the editorial number in the running header.
    Literal locator pairs take precedence; a one-digit CER pair is considered
    only when no literal pair exists in the scoped work.
    """

    if not str(entry.get("section_kind") or "").startswith("onomastic_"):
        return {}
    queries = [
        normalize_for_search(str(value))
        for value in entry.get("query_names") or []
        if str(value).strip()
    ]
    queries = [value for value in dict.fromkeys(queries) if len(value) >= 4]
    page_hints = _page_hint_values(entry)
    if not queries or not page_hints:
        return {}
    tail_start = _index_tail_start(entry, pages)
    preferred_families = set((entry.get("_onomastic_sibling_families") or {}).keys())
    if not preferred_families:
        preferred_families = set((entry.get("_section_heading_families") or {}).keys())
    exact_matches: list[tuple[PageCandidate, str, tuple[int, int, str]]] = []
    cer_matches: list[tuple[PageCandidate, str, tuple[int, int, str]]] = []
    for page in pages:
        if tail_start is not None and page.physical_index >= tail_start:
            continue
        if preferred_families and _file_family(page.file) not in preferred_families:
            continue
        matched_query = next(
            (
                query
                for query in queries
                if _contains_normalized_phrase(page.body_norm, query)
            ),
            None,
        )
        if matched_query is None:
            continue
        body_numbers = set(page.body_numbers)
        matched_pair = next(
            (
                (hint, hint, "exact")
                for hint in page_hints
                if hint in body_numbers
            ),
            None,
        )
        if matched_pair is not None:
            exact_matches.append((page, matched_query, matched_pair))
            continue
        matched_pair = next(
            (
                (hint, value, "cer")
                for hint in page_hints
                for value in body_numbers
                if hint != value and _strong_numeric_cer_match(hint, value)
            ),
            None,
        )
        if matched_pair is not None:
            cer_matches.append((page, matched_query, matched_pair))
    # A literal locator anywhere in the scoped work is stronger than all
    # one-digit OCR alternatives. CER candidates are recovery-only evidence.
    matches = exact_matches or cer_matches
    unique = len(matches) == 1
    return {
        page.file: {
            "unique": unique,
            "detail": (
                f"query={query!r}; index_locator={pair[0]}; "
                f"body_locator={pair[1]}; numeric_match={pair[2]}; "
                f"volume_candidate_count={len(matches)}"
            ),
        }
        for page, query, pair in matches
    }


def _onomastic_sibling_families(
    entry: dict[str, Any],
    pages: list[PageCandidate],
) -> dict[str, dict[str, Any]]:
    """Identify a work/file family using several refs belonging to one name."""

    if not str(entry.get("section_kind") or "").startswith("onomastic_"):
        return {}
    sibling_hints = sorted(
        {
            int(value)
            for value in entry.get("sibling_page_hint_ints") or []
            if isinstance(value, int) and not isinstance(value, bool)
        }
    )
    if len(sibling_hints) < 2:
        return {}
    queries = [
        normalize_for_search(str(value))
        for value in entry.get("query_names") or []
        if str(value).strip()
    ]
    queries = [value for value in dict.fromkeys(queries) if len(value) >= 4]
    if not queries:
        return {}
    tail_start = _index_tail_start(entry, pages)
    scoped_families = set((entry.get("_section_heading_families") or {}).keys())
    exact_families_by_hint: dict[int, set[str]] = {}
    cer_families_by_hint: dict[int, set[str]] = {}
    for page in pages:
        if tail_start is not None and page.physical_index >= tail_start:
            continue
        if not any(
            _contains_normalized_phrase(page.body_norm, query)
            for query in queries
        ):
            continue
        family = _file_family(page.file)
        if scoped_families and family not in scoped_families:
            continue
        body_numbers = set(page.body_numbers)
        for hint in sibling_hints:
            if hint in body_numbers:
                exact_families_by_hint.setdefault(hint, set()).add(family)
            elif any(_strong_numeric_cer_match(hint, value) for value in body_numbers):
                cer_families_by_hint.setdefault(hint, set()).add(family)
    hints_by_family: dict[str, set[int]] = {}
    for hint in sibling_hints:
        # Consult OCR-near digits only if this locator has no literal match in
        # any scoped family. This stops 129~109 and 132~130 from manufacturing
        # a false multi-reference family.
        families = exact_families_by_hint.get(hint) or cer_families_by_hint.get(
            hint,
            set(),
        )
        for family in families:
            hints_by_family.setdefault(family, set()).add(hint)
    if not hints_by_family:
        return {}
    best_count = max(len(hints) for hints in hints_by_family.values())
    strongest = [
        family
        for family, hints in hints_by_family.items()
        if len(hints) == best_count
    ]
    if best_count < 2 or len(strongest) != 1:
        return {}
    family = strongest[0]
    return {
        family: {
            "matched_hints": sorted(hints_by_family[family]),
            "sibling_hint_count": len(sibling_hints),
            "detail": (
                f"family={family!r}; matched_sibling_hints="
                f"{sorted(hints_by_family[family])}; "
                f"competing_family_count={len(strongest)}"
            ),
        }
    }


def _closest_pages_by_expected_index(
    pages: list[PageCandidate],
    expected_positions: dict[int, dict[str, Any]],
    limit: int,
) -> list[PageCandidate]:
    if not expected_positions or limit <= 0:
        return []
    ranked = sorted(
        pages,
        key=lambda page: min(abs(page.physical_index - expected["expected_index"]) for expected in expected_positions.values()),
    )
    return ranked[:limit]


def _closest_pages_by_editorial_hint(
    pages: list[PageCandidate],
    page_hints: list[int],
    limit: int,
) -> list[PageCandidate]:
    if not page_hints or limit <= 0:
        return []

    def distance(page: PageCandidate) -> float:
        candidate_numbers = [
            *page.inferred_page_candidates,
            *page.estimator_pages,
        ]
        if not candidate_numbers:
            return float("inf")
        return min(abs(value - hint) for value in candidate_numbers for hint in page_hints)

    ranked = sorted(pages, key=distance)
    return [page for page in ranked[:limit] if distance(page) != float("inf")]


def _candidate_pages_for_entry(entry: dict[str, Any], pages: list[PageCandidate]) -> list[PageCandidate]:
    excluded_index_files = _excluded_index_files(entry, pages)
    candidate_pool = [
        page for page in pages if page.file not in excluded_index_files
    ]
    tail_start = _index_tail_start(entry, pages)
    if (
        tail_start is not None
        and str(entry.get("section_kind") or "").startswith("onomastic_")
    ):
        candidate_pool = [
            page for page in pages if page.physical_index < tail_start
        ]
    page_hints = _page_hint_values(entry)
    expected_positions = entry.get("_expected_positions") or {}
    text_candidates = _pages_with_exact_text_evidence(entry, candidate_pool)
    text_candidate_files = {page.file for page in text_candidates}
    body_locator_files = set((entry.get("_body_locator_name_by_file") or {}).keys())
    cross_hint_files = set((entry.get("_onomastic_cross_hints_by_file") or {}).keys())
    if not page_hints and not expected_positions:
        return candidate_pool

    selected: dict[str, PageCandidate] = {}

    def add_many(items: list[PageCandidate]) -> None:
        for page in items:
            selected.setdefault(page.file, page)

    add_many(_pages_near_expected_positions(candidate_pool, expected_positions))
    add_many(_pages_near_editorial_hints(candidate_pool, page_hints))
    add_many([page for page in candidate_pool if page.file in body_locator_files])
    add_many([page for page in candidate_pool if page.file in cross_hint_files])
    header_sequence_files = set(
        (entry.get("_header_sequence_by_file") or {}).keys()
    )
    add_many([page for page in candidate_pool if page.file in header_sequence_files])
    # Page references may suffer CER. Exact textual candidates must never be excluded merely
    # because an OCR-corrupted number points to a different region of the volume.
    add_many(text_candidates)

    if len(selected) < MIN_CANDIDATE_PAGES:
        remaining = MIN_CANDIDATE_PAGES - len(selected)
        add_many(_closest_pages_by_expected_index(candidate_pool, expected_positions, remaining))
    if len(selected) < MIN_CANDIDATE_PAGES:
        remaining = MIN_CANDIDATE_PAGES - len(selected)
        add_many(_closest_pages_by_editorial_hint(candidate_pool, page_hints, remaining))

    if len(selected) < MIN_CANDIDATE_PAGES and expected_positions:
        expected_indices = sorted(int(round(expected["expected_index"])) for expected in expected_positions.values())
        for page in candidate_pool:
            if any(abs(page.physical_index - expected_index) <= PHYSICAL_NEAR_WINDOW for expected_index in expected_indices):
                selected.setdefault(page.file, page)
                if len(selected) >= MIN_CANDIDATE_PAGES:
                    break

    if not selected:
        return candidate_pool

    narrowed = list(selected.values())
    if len(narrowed) <= MAX_CANDIDATE_PAGES:
        return narrowed

    ranked = sorted(
        narrowed,
        key=lambda page: (
            0 if page.file in body_locator_files else 1,
            0 if page.file in cross_hint_files else 1,
            0 if page.file in text_candidate_files else 1,
            min(
                [
                    *(abs(page.physical_index - expected["expected_index"]) for expected in expected_positions.values()),
                    *(
                        min(
                            [
                                abs(value - hint)
                                for value in page.inferred_page_candidates
                                + page.estimator_pages
                            ]
                        )
                        for hint in page_hints
                        if page.inferred_page_candidates + page.estimator_pages
                    ),
                ]
                or [float("inf")]
            ),
            -(page.inferred_confidence or 0.0),
            page.file_seq or 0,
        ),
    )
    return ranked[:MAX_CANDIDATE_PAGES]


def resolve_entry_candidates(entry: dict[str, Any], pages: list[PageCandidate], top_k: int) -> dict[str, Any]:
    scoring_entry = dict(entry)
    scoring_entry["_section_heading_families"] = _section_heading_families(
        entry,
        pages,
    )
    scoring_entry["_onomastic_sibling_families"] = _onomastic_sibling_families(
        scoring_entry,
        pages,
    )
    scoring_entry["_body_locator_name_by_file"] = _body_locator_name_cooccurrences(
        scoring_entry,
        pages,
    )
    scoring_entry["_onomastic_cross_hints_by_file"] = _onomastic_cross_hints(
        entry,
        pages,
    )
    scoring_entry["_header_sequence_by_file"] = _header_sequences_for_entry(
        entry,
        pages,
    )
    candidate_pages = _candidate_pages_for_entry(scoring_entry, pages)
    scored: list[tuple[PageCandidate, CandidateAssessment]] = []
    for page in candidate_pages:
        assessment = score_page_candidate(scoring_entry, page)
        if assessment.score <= 0:
            continue
        scored.append((page, assessment))

    scored.sort(
        key=lambda item: (
            item[1].score,
            1 if item[1].candidate_role == "target_candidate" else 0,
            item[0].inferred_confidence,
            item[0].file_seq or 0,
        ),
        reverse=True,
    )
    top = scored[:top_k]
    probabilities = _softmax_probabilities([item[1].score for item in top])

    candidates: list[dict[str, Any]] = []
    for rank, ((page, assessment), probability) in enumerate(zip(top, probabilities), start=1):
        reason_summary = _build_reason_summary(
            assessment.candidate_role,
            page,
            assessment.debug["page_hints_used"],
            assessment.evidence,
        )
        decision_source = _editorial_page_decision_source(page)
        candidates.append(
            {
                "rank": rank,
                "file": page.file,
                "image_file": resolve_paired_page_image(page.file),
                "file_seq": page.file_seq,
                "score": round(assessment.score, 4),
                "probability": round(probability, 6),
                "inferred_printed_page": page.inferred_printed_page,
                "inferred_page_candidates": page.inferred_page_candidates,
                "estimator_pages": page.estimator_pages,
                "estimator_confidence": round(page.estimator_confidence, 4),
                "editorial_page_decision_source": decision_source,
                "candidate_role": assessment.candidate_role,
                "role_score": assessment.role_score,
                "role_reason": assessment.role_reason,
                "reason_summary": reason_summary,
                "header_sequence": assessment.debug.get("header_sequence"),
                "evidence": [item.to_dict() for item in assessment.evidence],
            }
        )

    if not candidates:
        return {
            "entry_id": entry.get("entry_id"),
            "status": "unresolved",
            "best_candidate": None,
            "candidates": [],
            "debug": {
                "normalized_queries": [normalize_for_search(query) for query in (entry.get("query_names") or [])],
                "page_hints_used": _page_hint_values(entry),
            },
        }

    best = candidates[0]
    status = "resolved"
    ambiguity_reason = None
    if len(candidates) > 1:
        gap = best["probability"] - candidates[1]["probability"]
        if gap < 0.15:
            status = "ambiguous"
            ambiguity_reason = "candidate_probability_gap"
    best_evidence_kinds = {
        str(item.get("kind") or "")
        for item in best.get("evidence") or []
        if isinstance(item, dict)
    }
    if not (best_evidence_kinds & MATERIAL_TEXT_EVIDENCE_KINDS):
        status = "ambiguous"
        ambiguity_reason = "numeric_only_evidence"
    if best.get("candidate_role") == "index_page_candidate":
        status = "ambiguous"
        ambiguity_reason = "non_target_candidate_role"

    return {
        "entry_id": entry.get("entry_id"),
        "status": status,
        "ambiguity_reason": ambiguity_reason,
        "best_candidate": {
            "file": best["file"],
            "image_file": best["image_file"],
            "file_seq": best["file_seq"],
            "score": best["score"],
            "probability": best["probability"],
            "editorial_page_decision_source": best["editorial_page_decision_source"],
            "candidate_role": best["candidate_role"],
            "reason_summary": best["reason_summary"],
        },
        "candidates": candidates,
        "debug": {
            "normalized_queries": [normalize_for_search(query) for query in (entry.get("query_names") or [])],
            "page_hints_used": _page_hint_values(entry),
        },
    }


def _resolve_entry(
    entry: dict[str, Any],
    pages: list[PageCandidate],
    *,
    top_k: int,
) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("request.entries must contain only objects")
    entry_local = dict(entry)
    entry_local["_expected_positions"] = _estimate_expected_physical_indices(
        pages,
        _page_hint_values(entry_local),
    )
    return resolve_entry_candidates(entry_local, pages, top_k=top_k)


def _initialize_resolve_worker(
    pages: list[PageCandidate],
    top_k: int,
) -> None:
    global _RESOLVE_WORKER_PAGES, _RESOLVE_WORKER_TOP_K
    _RESOLVE_WORKER_PAGES = pages
    _RESOLVE_WORKER_TOP_K = top_k


def _resolve_entry_worker(
    indexed_entry: tuple[int, dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    index, entry = indexed_entry
    if _RESOLVE_WORKER_PAGES is None:
        raise RuntimeError("target locator worker was not initialized")
    return (
        index,
        _resolve_entry(
            entry,
            _RESOLVE_WORKER_PAGES,
            top_k=_RESOLVE_WORKER_TOP_K,
        ),
    )


def resolve_index_targets(
    request: dict[str, Any],
    *,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    volume_id = str(request.get("volume_id") or "").strip()
    source_root = Path(str(request.get("source_root") or "")).expanduser()
    if not volume_id:
        raise ValueError("request.volume_id is required")
    if not source_root:
        raise ValueError("request.source_root is required")
    if not source_root.exists():
        raise FileNotFoundError(f"source_root not found: {source_root}")

    top_k, adjacency_window, requested_workers = _request_options(request)
    pages = _load_volume_pages(source_root, adjacency_window=adjacency_window)

    entries = list(request.get("entries") or [])
    total_entries = len(entries)
    worker_count = min(requested_workers, total_entries or 1)
    if progress_callback is not None:
        progress_callback(
            {
                "stage": "load_pages_done",
                "volume_id": volume_id,
                "page_count": len(pages),
                "entry_count": total_entries,
                "worker_count": worker_count,
            }
        )

    results: list[dict[str, Any] | None] = [None] * total_entries
    interval = _progress_interval(total_entries)
    processed_entries = 0

    def report_progress() -> None:
        if progress_callback is not None and (
            processed_entries == 1
            or processed_entries == total_entries
            or processed_entries % interval == 0
        ):
            progress_callback(
                {
                    "stage": "entries_progress",
                    "volume_id": volume_id,
                    "processed_entries": processed_entries,
                    "total_entries": total_entries,
                    "worker_count": worker_count,
                }
            )

    if worker_count == 1:
        for index, entry in enumerate(entries):
            results[index] = _resolve_entry(entry, pages, top_k=top_k)
            processed_entries += 1
            report_progress()
    else:
        with ProcessPoolExecutor(
            max_workers=worker_count,
            initializer=_initialize_resolve_worker,
            initargs=(pages, top_k),
        ) as executor:
            futures = {
                executor.submit(_resolve_entry_worker, (index, entry)): index
                for index, entry in enumerate(entries)
            }
            for future in as_completed(futures):
                index, result = future.result()
                results[index] = result
                processed_entries += 1
                report_progress()

    return {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options_used": {
            "top_k": top_k,
            "adjacency_window": adjacency_window,
            "workers": worker_count,
            "executor": "process" if worker_count > 1 else "serial",
            "fuzzy_backend": (
                "rapidfuzz"
                if _RapidLevenshtein is not None
                else "python_fallback"
            ),
            "editorial_page_estimator": True,
        },
        "entries": [result for result in results if result is not None],
    }


def load_request_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
