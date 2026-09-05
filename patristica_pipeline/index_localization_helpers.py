from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from .common import page_sort_key
from .index_target_locator import parse_ocr_page_path, resolve_index_targets

HELPER_TOP_K = 5
HELPER_ADJACENCY_WINDOW = 4
HELPER_SECTION_CONTEXT_BEFORE = 4
HELPER_GENERAL_SECTION_AFTER = 24
HELPER_ALPHABETICAL_SECTION_AFTER = 64
INDEX_LINE_RE = re.compile(r"\d{1,4}")
PAGE_HINT_TOKEN_RE = re.compile(r"\b\d{1,4}(?:\s*[-–—]\s*\d{1,4})?\b(?:\s*(?:seq\.?|seqq\.?))?", re.IGNORECASE)
REFERENCE_TAIL_RE = re.compile(
    r"(?:\.{2,}|[,;:]\s*|\s)"
    r"(?:\d{1,4}|[IVXLCDM]{1,8})"
    r"(?:\s*[-–—,]\s*(?:\d{1,4}|[IVXLCDM]{1,8}))*"
    r"(?:\s*(?:seq\.?|seqq\.?|fin\.?))?\s*$",
    re.IGNORECASE,
)
NON_ENTRY_RE = re.compile(
    r"\b(?:DIGITIZED\s+BY|GOOGLE|PERMIS\s+D['’]IMPRIMER|PARIS,\s+LE|"
    r"EX\s+TYPIS|IMPRIMATUR|COPYRIGHT|PATROLOGI[AE]|PATR\.\s*OR)\b",
    re.IGNORECASE,
)
EDITORIAL_TITLE_RE = re.compile(
    r"^(?:"
    r"CAP\.\s*[IVXLCDM0-9]+"
    r"|INDEX(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r"|INDICES(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r"|TABLE(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r"|ORDO(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r"|ELENCHUS(?:\s+[A-ZÆŒÀ-Ÿ][A-ZÆŒÀ-Ÿ\.\-]*)*"
    r")\b",
    re.IGNORECASE,
)


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _is_index_line(stripped: str) -> bool:
    if not stripped or len(stripped) > 240:
        return False
    if not re.search(r"[A-Za-zÆŒÀ-ÿ]", stripped):
        return False
    if EDITORIAL_TITLE_RE.match(stripped):
        return False
    if not INDEX_LINE_RE.search(stripped):
        return False
    if re.match(r"^\s*\d{1,4}\s+[A-ZÆŒ]", stripped):
        return False
    if stripped.upper().startswith(("INDEX ", "TABLE ", "ELENCHUS ", "ORDO ", "NOTES ", "OBS ", "OCR ")):
        return False
    if NON_ENTRY_RE.search(stripped):
        return False
    return bool(REFERENCE_TAIL_RE.search(stripped))


def _extract_page_hints(text: str) -> list[str]:
    return [match.group(0).strip() for match in PAGE_HINT_TOKEN_RE.finditer(text or "")]


def _derive_lemma_raw(text: str) -> str:
    lemma = re.split(r"\b\d{1,4}\b", text, maxsplit=1)[0].strip()
    lemma = re.sub(r"\s+", " ", lemma)
    lemma = lemma.strip(" ,;:.–—-")
    return lemma


def _looks_material_lemma(lemma: str) -> bool:
    if len(lemma) < 3 or NON_ENTRY_RE.search(lemma):
        return False
    tokens = re.findall(r"[A-Za-zÆŒÀ-ÿ]{2,}", lemma)
    if not tokens or max(len(token) for token in tokens) < 3:
        return False
    return not all(len(token) <= 2 for token in tokens)


def _candidate_files_near_sections(
    *,
    source_root: Path,
    filtered_pages: dict[str, Any],
) -> list[Path]:
    source_files = sorted(source_root.glob("*.txt"), key=page_sort_key)
    position_by_path = {str(path.resolve()): index for index, path in enumerate(source_files)}
    section_positions: list[int] = []
    for section in filtered_pages.get("candidate_sections") or []:
        if not isinstance(section, dict) or not section.get("file"):
            continue
        position = position_by_path.get(str(Path(str(section["file"])).resolve()))
        if position is not None:
            section_positions.append(position)
    if not section_positions:
        return []
    profile = str(filtered_pages.get("profile") or "alphabetical")
    after = (
        HELPER_GENERAL_SECTION_AFTER
        if profile == "general"
        else HELPER_ALPHABETICAL_SECTION_AFTER
    )
    selected: list[Path] = []
    seen: set[str] = set()
    for position in sorted(set(section_positions)):
        start = max(0, position - HELPER_SECTION_CONTEXT_BEFORE)
        end = min(len(source_files), position + after + 1)
        for path in source_files[start:end]:
            key = str(path)
            if key not in seen:
                seen.add(key)
                selected.append(path)
    return selected


def _derive_query_names(lemma_raw: str, context_raw: str) -> list[str]:
    candidates: list[str] = []
    cleaned = re.sub(r"\s*\((.*?)\)\s*$", "", lemma_raw).strip()
    if cleaned:
        candidates.append(cleaned)
    if lemma_raw and lemma_raw not in candidates:
        candidates.append(lemma_raw)
    first_clause = re.split(r"\s*[;,]\s*|\s{2,}", cleaned or lemma_raw, maxsplit=1)[0].strip()
    if first_clause and first_clause not in candidates:
        candidates.append(first_clause)
    context_prefix = context_raw.strip()
    context_prefix = re.sub(r"\s+", " ", context_prefix)
    if len(context_prefix) > 160:
        context_prefix = ""
    if context_prefix and len(context_prefix.split()) > 16:
        context_prefix = ""
    if context_prefix and context_prefix not in candidates:
        candidates.append(context_prefix)
    return _unique_preserve_order(candidates)[:4]


def _looks_structural_title(text: str) -> bool:
    compact = re.sub(r"\s+", " ", (text or "")).strip()
    if not compact:
        return False
    if EDITORIAL_TITLE_RE.match(compact):
        return True
    if compact.upper().startswith(("CAP. ", "INDEX ", "INDICES ", "TABLE ", "ORDO ", "ELENCHUS ")):
        return True
    return False


def _build_context_window(lines: list[str], index: int, *, width: int = 1) -> str:
    start = max(0, index - width)
    end = min(len(lines), index + width + 1)
    window = [line.strip() for line in lines[start:end] if line.strip()]
    return "\n".join(window)


def _join_wrapped_index_line(lines: list[str], index: int) -> str:
    current = lines[index].strip()
    if index <= 0:
        return current
    previous = lines[index - 1].strip()
    if not previous or _extract_page_hints(previous):
        return current
    continuation_shape = (
        previous.endswith((",", ";", ":", "-", "–", "—"))
        or bool(re.match(r"^[a-zæœà-ÿ]", current))
        or bool(re.match(r"^(?:[A-ZÆŒ]\.\s*){2,}", current))
    )
    if not continuation_shape:
        return current
    joined = f"{previous} {current}"
    return joined if len(joined) <= 360 else current


def build_helper_request_artifact(
    *,
    volume_id: str,
    source_root: Path,
    filtered_pages: dict[str, Any],
    helper_request_json: Path,
    workplan: dict[str, Any] | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    candidate_files: list[Path] = []
    if isinstance(workplan, dict):
        seen_workplan_files: set[str] = set()
        for section in workplan.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for value in section.get("physical_files") or []:
                path = Path(str(value))
                key = str(path)
                if path.is_file() and key not in seen_workplan_files:
                    seen_workplan_files.add(key)
                    candidate_files.append(path)
    if not candidate_files:
        candidate_files = _candidate_files_near_sections(
            source_root=source_root,
            filtered_pages=filtered_pages,
        )
    if not candidate_files:
        candidate_files = [Path(item) for item in (filtered_pages.get("candidate_files") or [])]
    if not candidate_files:
        candidate_files = [Path(item) for item in (filtered_pages.get("tail_files") or [])]
    if not candidate_files:
        candidate_files = sorted(source_root.glob("*.txt"))

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()

    for file_path in candidate_files:
        if not file_path.exists():
            continue
        parsed = parse_ocr_page_path(file_path)
        lines = [line.strip() for line in parsed["all_text"].splitlines() if line.strip()]
        for idx, line in enumerate(lines):
            entry_line = _join_wrapped_index_line(lines, idx)
            if not _is_index_line(entry_line):
                continue
            if not _extract_page_hints(entry_line):
                continue
            lemma_raw = _derive_lemma_raw(entry_line)
            if not _looks_material_lemma(lemma_raw):
                continue
            if _looks_structural_title(lemma_raw):
                continue
            context_raw = _build_context_window(lines, idx, width=1)
            if _looks_structural_title(context_raw.splitlines()[0] if context_raw else ""):
                if lemma_raw == (context_raw.splitlines()[0] if context_raw else ""):
                    continue
            request_key = (str(file_path), idx + 1, lemma_raw)
            if request_key in seen:
                continue
            seen.add(request_key)
            entries.append(
                {
                    "entry_id": f"{volume_id.lower()}_{file_path.stem}_{idx + 1:04d}",
                    "lemma_raw": lemma_raw,
                    "query_names": _derive_query_names(lemma_raw, context_raw),
                    "page_hints": _extract_page_hints(entry_line),
                    "page_hint_ints": [
                        int(match.group(0))
                        for match in INDEX_LINE_RE.finditer(entry_line)
                    ],
                    "context_raw": context_raw or entry_line,
                }
            )

    request = {
        "volume_id": volume_id,
        "source_root": str(source_root),
        "options": {
            "top_k": HELPER_TOP_K,
            "adjacency_window": HELPER_ADJACENCY_WINDOW,
            "workers": max(1, int(workers)),
        },
        "entries": entries,
    }
    helper_request_json.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return request


def run_helper_locator(request: dict[str, Any], helper_output_json: Path) -> dict[str, Any]:
    start = time.monotonic()

    def progress(payload: dict[str, Any]) -> None:
        stage = payload.get("stage")
        elapsed = time.monotonic() - start
        if stage == "load_pages_done":
            print(
                "[INFO] helper loaded "
                f"{payload.get('page_count', 0)} pages and "
                f"{payload.get('entry_count', 0)} entries with "
                f"{payload.get('worker_count', 1)} worker(s) in {elapsed:.1f}s",
                file=sys.stdout,
                flush=True,
            )
            return
        if stage == "entries_progress":
            processed = int(payload.get("processed_entries", 0))
            total = int(payload.get("total_entries", 0))
            if total <= 0:
                return
            pct = (processed / total) * 100.0
            print(
                f"[INFO] helper progress {processed}/{total} ({pct:.1f}%) elapsed={elapsed:.1f}s",
                file=sys.stdout,
                flush=True,
            )

    result = resolve_index_targets(request, progress_callback=progress)
    helper_output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
