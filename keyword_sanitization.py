"""Sanitização determinística compartilhada para keywords patrísticas.

Este módulo não conhece provedores LLM, SQLite ou o corpus. Ele concentra as
regras que precisam produzir o mesmo resultado na geração, revisão e ingestão.
A grafia de exibição é preservada sempre que possível; a chave de armazenamento
é mais agressiva e inclui folding de ligaturas para busca tolerante.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


EDGE_STRIP_CHARS = " \"'«»“”‘’[]{}|\\/–—-:;.,!?·•*&"

NON_SEARCHABLE_EDITORIAL_LOCATOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)^\s*(?:num\.|nm\.?|col\.?|coluna|column[ae]?)\s+"
        r"(?:\d{1,4}|[ivxlcdm]{1,12})\s*$"
    ),
)

RESPONSE_PREFIX_RE = re.compile(
    r"(?i)^\s*(?:temas?|keywords?|palavras?[ -]chave|termos? principais|"
    r"obras? citadas?)\s*:+\s*"
)
REVIEW_MARKER_RE = re.compile(r"(?i)[\[(]?\s*revis[aã]o necess[aá]ria\b")
CHANGE_TO_PROTOCOL_RE = re.compile(
    r"^\s*(?P<source>[^|]+?)\s*\|\s*change_to\s*\|\s*(?P<target>[^|]+?)\s*$",
    re.IGNORECASE,
)

EXACT_META_KEYS = {
    "(nao aplicavel nesta pagina)",
    "(nao ha conteudo patristico)",
    "(nao ha obras citadas no conteudo)",
    "(nao ha)",
    "(nao incluidas nas regras de agrupamento)",
    "(nao mencionadas diretamente no texto desta pagina)",
    "(sic)",
    "conteudo ilegivel",
    "none",
    "null",
    "n/a",
}

META_ANNOTATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\(\s*n[aã]o aplic[aá]vel(?:\b|[,)])"),
    re.compile(r"(?i)\bmencionad[oa]s?\s+no\s+resumo(?:_global|\s+global)?\b"),
    re.compile(r"(?i)\bn[aã]o\s+(?:est[aá]|presente|consta)\s+(?:na|no)\s+p[aá]gina\b"),
    re.compile(r"(?i)\bexclu[ií]d[oa]s?\b.*\b(?:categoria|p[aá]gina|texto_original)\b"),
    re.compile(r"(?i)\bfalta\s+de\s+texto_original\b"),
)

HARD_SANITIZATION_ISSUES = frozenset(
    {
        "removed_ambiguous_pipe",
        "removed_meta_placeholder",
        "removed_requires_review",
        "removed_suspect_ocr_symbol",
        "removed_unmatched_edge_delimiter",
    }
)


@dataclass(frozen=True)
class SanitizedKeyword:
    value: str | None
    issues: tuple[str, ...] = ()


def strip_markdown_wrappers(text: str) -> str:
    """Remove wrappers Markdown simples sem interpretar o conteúdo."""
    value = (text or "").strip()
    value = re.sub(r"^```[\w-]*\s*|\s*```$", "", value, flags=re.DOTALL)
    value = re.sub(r"`{1,3}([^`]+?)`{1,3}", r"\1", value)
    value = re.sub(r"^(?:[>#]+|\*+|[-+\u2022•]+|#+)\s*", "", value)
    value = re.sub(r"\*{1,3}([^*]+?)\*{1,3}", r"\1", value)
    value = re.sub(r"_{1,3}([^_]+?)_{1,3}", r"\1", value)
    while len(value) >= 2 and value[0] == value[-1] and value[0] in "*_`'\"":
        value = value[1:-1].strip()
    return value


def remove_unicode_format_chars(text: str) -> tuple[str, bool]:
    """Remove caracteres invisíveis de formato Unicode (categoria ``Cf``)."""
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return cleaned, cleaned != text


def clean_keyword_text(text: str) -> tuple[str, tuple[str, ...]]:
    """Limpa a grafia de exibição sem dobrar ligaturas latinas."""
    value = strip_markdown_wrappers(text)
    value = unicodedata.normalize("NFKC", value or "")
    value, removed_format = remove_unicode_format_chars(value)
    value = value.replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = value.strip(EDGE_STRIP_CHARS)
    issues = ("normalized_unicode_format",) if removed_format else ()
    return value, issues


def keyword_normalization_key(text: str) -> str:
    """Chave para unicidade/busca: sem acentos e tolerante a ``æ/œ``."""
    value, _ = clean_keyword_text(text)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = unicodedata.normalize("NFKC", value).casefold()
    value = (
        value.replace("æ", "ae")
        .replace("ǽ", "ae")
        .replace("œ", "oe")
    )
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(EDGE_STRIP_CHARS)


def _is_meta_annotation(value: str) -> bool:
    key = keyword_normalization_key(value)
    if key in EXACT_META_KEYS:
        return True
    return any(pattern.search(value) for pattern in META_ANNOTATION_PATTERNS)


def sanitize_keyword_item(text: str, *, _protocol_depth: int = 0) -> SanitizedKeyword:
    """Sanitiza um item isolado; casos ambíguos são retirados da busca.

    O texto bruto continua preservado no ``keywords_json`` de origem. Retornar
    ``None`` significa somente que o item não deve avançar como keyword limpa.
    """
    value, base_issues = clean_keyword_text(text)
    issues = list(base_issues)
    if not value:
        return SanitizedKeyword(None, tuple(sorted(set(issues + ["removed_empty"]))))

    if _protocol_depth == 0:
        protocol = CHANGE_TO_PROTOCOL_RE.fullmatch(value)
        if protocol:
            repaired = sanitize_keyword_item(protocol.group("target"), _protocol_depth=1)
            return SanitizedKeyword(
                repaired.value,
                tuple(sorted(set(issues + list(repaired.issues) + ["repaired_change_to_protocol"]))),
            )

        pipe_parts = [part.strip() for part in value.split("|")]
        if len(pipe_parts) == 3 and pipe_parts[0] and len(set(pipe_parts)) == 1:
            repaired = sanitize_keyword_item(pipe_parts[0], _protocol_depth=1)
            return SanitizedKeyword(
                repaired.value,
                tuple(sorted(set(issues + list(repaired.issues) + ["collapsed_repeated_protocol"]))),
            )
        if "|" in value:
            return SanitizedKeyword(
                None, tuple(sorted(set(issues + ["removed_ambiguous_pipe"])))
            )

    prefix_match = RESPONSE_PREFIX_RE.match(value)
    if prefix_match:
        value = value[prefix_match.end() :].strip()
        issues.append("normalized_response_prefix")
        if not value:
            return SanitizedKeyword(
                None, tuple(sorted(set(issues + ["removed_empty"])))
            )

    if REVIEW_MARKER_RE.search(value):
        return SanitizedKeyword(
            None, tuple(sorted(set(issues + ["removed_requires_review"])))
        )

    if _is_meta_annotation(value):
        return SanitizedKeyword(
            None, tuple(sorted(set(issues + ["removed_meta_placeholder"])))
        )

    if value.startswith("$"):
        if len(value) > 1 and value[1].isspace():
            value = value[1:].strip()
            issues.append("normalized_leading_ocr_symbol")
        else:
            return SanitizedKeyword(
                None, tuple(sorted(set(issues + ["removed_suspect_ocr_symbol"])))
            )
    if "$" in value or "€" in value:
        return SanitizedKeyword(
            None, tuple(sorted(set(issues + ["removed_suspect_ocr_symbol"])))
        )

    if value[:1] in ")]}" or value[-1:] in "([{":
        return SanitizedKeyword(
            None, tuple(sorted(set(issues + ["removed_unmatched_edge_delimiter"])))
        )

    if any(pattern.fullmatch(value) for pattern in NON_SEARCHABLE_EDITORIAL_LOCATOR_PATTERNS):
        return SanitizedKeyword(
            None, tuple(sorted(set(issues + ["removed_editorial_locator"])))
        )

    return SanitizedKeyword(value, tuple(sorted(set(issues))))


__all__ = [
    "EDGE_STRIP_CHARS",
    "HARD_SANITIZATION_ISSUES",
    "NON_SEARCHABLE_EDITORIAL_LOCATOR_PATTERNS",
    "SanitizedKeyword",
    "clean_keyword_text",
    "keyword_normalization_key",
    "remove_unicode_format_chars",
    "sanitize_keyword_item",
    "strip_markdown_wrappers",
]
