from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.corpus_utils import PROJECT_ROOT, now_iso, page_number, page_sort_key
from .index_target_locator import parse_ocr_page_xml

COLLECTIONS_SUPPORTED = {"PG", "PL"}
DEFAULT_ESTIMATOR_DB = PROJECT_ROOT / "data" / "editorial_page_estimator.db"
OBSERVATION_CACHE_VERSION = 3
NOISE_RE = re.compile(
    r"(?i)\b(?:digitized by google|patrol\.|patrologia|patrologie|tom\.|vol\.|page\s+\d+)\b"
)
PAIR_RE = re.compile(r"(?<!\d)(\d{1,4})\s+(.{3,200}?)\s+(\d{1,4})(?!\d)")
NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4})(?!\d)")
OCR_NUMBER_TOKEN_RE = re.compile(r"(?<!\w)([0-9OIl|SB]{1,4})(?!\w)", re.IGNORECASE)
OCR_NUMBER_TRANSLATION = str.maketrans(
    {"O": "0", "o": "0", "I": "1", "i": "1", "l": "1", "|": "1", "S": "5", "s": "5", "B": "8", "b": "8"}
)


@dataclass(slots=True)
class Evidence:
    kind: str
    detail: str
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "weight": round(self.weight, 4),
        }


@dataclass(slots=True)
class Hypothesis:
    pages: tuple[int, ...]
    score: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)

    def add(self, weight: float, kind: str, detail: str) -> None:
        self.score += weight
        self.evidence.append(Evidence(kind=kind, detail=detail, weight=weight))

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages": list(self.pages),
            "score": round(self.score, 4),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(slots=True)
class FileObservation:
    path: Path
    file_seq: int | None
    physical_index: int
    header_text: str
    footer_text: str
    body_top_text: str
    header_pairs: list[tuple[int, int]]
    header_singles: list[int]
    footer_pairs: list[tuple[int, int]]
    footer_singles: list[int]
    body_singles: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "file_seq": self.file_seq,
            "physical_index": self.physical_index,
            "header_text": self.header_text,
            "footer_text": self.footer_text,
            "body_top_text": self.body_top_text,
            "header_pairs": [list(item) for item in self.header_pairs],
            "header_singles": self.header_singles,
            "footer_pairs": [list(item) for item in self.footer_pairs],
            "footer_singles": self.footer_singles,
            "body_singles": self.body_singles,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileObservation:
        return cls(
            path=Path(data["path"]),
            file_seq=data.get("file_seq"),
            physical_index=int(data["physical_index"]),
            header_text=data.get("header_text") or "",
            footer_text=data.get("footer_text") or "",
            body_top_text=data.get("body_top_text") or "",
            header_pairs=[(int(item[0]), int(item[1])) for item in data.get("header_pairs") or []],
            header_singles=[int(item) for item in data.get("header_singles") or []],
            footer_pairs=[(int(item[0]), int(item[1])) for item in data.get("footer_pairs") or []],
            footer_singles=[int(item) for item in data.get("footer_singles") or []],
            body_singles=[int(item) for item in data.get("body_singles") or []],
        )


def _connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_observation_cache (
            file_path TEXT PRIMARY KEY,
            volume_id TEXT,
            collection TEXT,
            source_root TEXT,
            file_mtime_ns INTEGER NOT NULL,
            file_size INTEGER NOT NULL,
            observation_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(file_observation_cache)").fetchall()
    }
    if "observation_version" not in columns:
        conn.execute(
            "ALTER TABLE file_observation_cache "
            "ADD COLUMN observation_version INTEGER NOT NULL DEFAULT 1"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS page_overrides (
            file_path TEXT PRIMARY KEY,
            volume_id TEXT,
            pages_json TEXT NOT NULL,
            note TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def _read_cached_observation(
    conn: sqlite3.Connection,
    *,
    path: Path,
) -> FileObservation | None:
    stat = path.stat()
    row = conn.execute(
        """
        SELECT observation_json
        FROM file_observation_cache
        WHERE file_path = ? AND file_mtime_ns = ? AND file_size = ?
          AND observation_version = ?
        """,
        (
            str(path),
            stat.st_mtime_ns,
            stat.st_size,
            OBSERVATION_CACHE_VERSION,
        ),
    ).fetchone()
    if row is None:
        return None
    return FileObservation.from_dict(json.loads(row["observation_json"]))


def _write_cached_observation(
    conn: sqlite3.Connection,
    *,
    volume_id: str | None,
    collection: str,
    source_root: Path | None,
    observation: FileObservation,
) -> None:
    stat = observation.path.stat()
    conn.execute(
        """
        INSERT INTO file_observation_cache (
            file_path, volume_id, collection, source_root, file_mtime_ns, file_size,
            observation_json, updated_at, observation_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(file_path) DO UPDATE SET
            volume_id = excluded.volume_id,
            collection = excluded.collection,
            source_root = excluded.source_root,
            file_mtime_ns = excluded.file_mtime_ns,
            file_size = excluded.file_size,
            observation_json = excluded.observation_json,
            updated_at = excluded.updated_at,
            observation_version = excluded.observation_version
        """,
        (
            str(observation.path),
            volume_id,
            collection,
            str(source_root) if source_root else None,
            stat.st_mtime_ns,
            stat.st_size,
            json.dumps(observation.to_dict(), ensure_ascii=False),
            now_iso(),
            OBSERVATION_CACHE_VERSION,
        ),
    )


def _load_overrides(conn: sqlite3.Connection, *, volume_id: str | None) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT file_path, volume_id, pages_json, note, updated_at
        FROM page_overrides
        WHERE volume_id IS NULL OR volume_id = ?
        """,
        (volume_id,),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[row["file_path"]] = {
            "pages": [int(item) for item in json.loads(row["pages_json"])],
            "note": row["note"],
            "updated_at": row["updated_at"],
        }
    return out


def set_page_override(
    *,
    db_path: Path | None = None,
    file_path: Path,
    pages: list[int],
    volume_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    resolved_db = db_path or DEFAULT_ESTIMATOR_DB
    conn = _connect_db(resolved_db)
    try:
        conn.execute(
            """
            INSERT INTO page_overrides (file_path, volume_id, pages_json, note, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                volume_id = excluded.volume_id,
                pages_json = excluded.pages_json,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (str(file_path), volume_id, json.dumps(pages), note, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "file_path": str(file_path), "pages": pages, "db": str(resolved_db)}


def clear_page_override(*, db_path: Path | None = None, file_path: Path) -> dict[str, Any]:
    resolved_db = db_path or DEFAULT_ESTIMATOR_DB
    conn = _connect_db(resolved_db)
    try:
        conn.execute("DELETE FROM page_overrides WHERE file_path = ?", (str(file_path),))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "file_path": str(file_path), "db": str(resolved_db), "action": "cleared"}


def list_page_overrides(*, db_path: Path | None = None, volume_id: str | None = None) -> dict[str, Any]:
    resolved_db = db_path or DEFAULT_ESTIMATOR_DB
    conn = _connect_db(resolved_db)
    try:
        rows = conn.execute(
            """
            SELECT file_path, volume_id, pages_json, note, updated_at
            FROM page_overrides
            WHERE (? IS NULL OR volume_id = ?)
            ORDER BY file_path
            """,
            (volume_id, volume_id),
        ).fetchall()
    finally:
        conn.close()
    return {
        "db": str(resolved_db),
        "overrides": [
            {
                "file_path": row["file_path"],
                "volume_id": row["volume_id"],
                "pages": json.loads(row["pages_json"]),
                "note": row["note"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ],
    }


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _infer_collection(volume_id: str | None, source_root: Path | None, files: list[Path] | None) -> str | None:
    candidates: list[str] = []
    if volume_id:
        candidates.append(volume_id[:2].upper())
    if source_root:
        candidates.append(source_root.parent.name[:2].upper())
    if files:
        for path in files:
            for parent in path.parents:
                name = parent.name.upper()
                if len(name) >= 2 and name[:2] in {"PG", "PL", "PO"}:
                    candidates.append(name[:2])
                    break
    for item in candidates:
        if item in {"PG", "PL", "PO"}:
            return item
    return None


def _infer_volume_id(source_root: Path | None, files: list[Path] | None) -> str | None:
    if source_root:
        return source_root.parent.name
    if files:
        for path in files:
            for parent in path.parents:
                if re.match(r"^(PG|PL|PO)\d+", parent.name):
                    return parent.name
    return None


def _resolve_source_root(volume_id: str | None, source_root: Path | None, files: list[Path] | None) -> Path | None:
    if source_root:
        return source_root
    if volume_id:
        candidate = PROJECT_ROOT / "teste" / volume_id / "text"
        if candidate.is_dir():
            return candidate
    if files:
        first = files[0]
        for parent in first.parents:
            if parent.name == "text":
                return parent
    return None


def _load_files(source_root: Path | None, files: list[Path] | None) -> list[Path]:
    if files:
        return sorted(files, key=page_sort_key)
    if not source_root:
        return []
    return sorted(source_root.glob("*.txt"), key=page_sort_key)


def _extract_pair_candidates(text: str) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    raw_lines = list(text.splitlines())
    candidate_lines = [*raw_lines, _normalize_space(text)]
    for raw_line in candidate_lines:
        line = _normalize_space(raw_line)
        if not line or NOISE_RE.search(line):
            continue
        for match in PAIR_RE.finditer(line):
            left = int(match.group(1))
            right = int(match.group(3))
            middle = match.group(2)
            if not re.search(r"[A-Za-zÆŒæœΑ-Ωα-ω]", middle):
                continue
            if 0 < left < 10000 and 0 < right < 10000 and left <= right:
                pair = (left, right)
                if pair not in pairs:
                    pairs.append(pair)
    cer_values: list[int] = []
    for match in OCR_NUMBER_TOKEN_RE.finditer(text or ""):
        raw = match.group(1)
        if not any(char.isdigit() for char in raw):
            continue
        normalized = raw.translate(OCR_NUMBER_TRANSLATION)
        if normalized.isdigit():
            value = int(normalized)
            if 0 < value < 10000:
                cer_values.append(value)
    if len(cer_values) >= 2:
        pair = (cer_values[0], cer_values[-1])
        if pair[0] <= pair[1] and pair not in pairs:
            pairs.append(pair)
    return pairs


def _extract_single_candidates(text: str, *, allow_pair_numbers: bool = False) -> list[int]:
    singles: list[int] = []
    seen: set[int] = set()
    for raw_line in text.splitlines():
        line = _normalize_space(raw_line)
        if not line or NOISE_RE.search(line):
            continue
        if not allow_pair_numbers and PAIR_RE.search(line):
            continue
        matches = [
            match.group(1)
            for match in OCR_NUMBER_TOKEN_RE.finditer(line)
            if any(char.isdigit() for char in match.group(1))
        ]
        normalized_matches = [
            raw.translate(OCR_NUMBER_TRANSLATION)
            for raw in matches
        ]
        normalized_matches = [
            raw for raw in normalized_matches if raw.isdigit()
        ]
        if not normalized_matches:
            continue
        if len(normalized_matches) > 2 and not allow_pair_numbers:
            continue
        values = [int(item) for item in normalized_matches]
        preferred = [values[0], values[-1]] if len(values) > 1 else [values[0]]
        for value in preferred:
            if 0 < value < 10000 and value not in seen:
                seen.add(value)
                singles.append(value)
    return singles


def _extract_body_top_singles(text: str) -> list[int]:
    body_top = "\n".join((text or "").splitlines()[:4])
    values: list[int] = []
    seen: set[int] = set()
    for match in OCR_NUMBER_TOKEN_RE.finditer(body_top):
        raw = match.group(1)
        if not any(char.isdigit() for char in raw):
            continue
        normalized = raw.translate(OCR_NUMBER_TRANSLATION)
        if not normalized.isdigit():
            continue
        value = int(normalized)
        if value in seen or value <= 0 or value >= 10000:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= 2:
            break
    return values


def _observe_file(path: Path, physical_index: int) -> FileObservation:
    parsed = parse_ocr_page_xml(path.read_text(encoding="utf-8", errors="replace"))
    header_text = parsed.get("header_text") or ""
    footer_text = parsed.get("footer_text") or ""
    body_text = parsed.get("body_text") or ""
    body_top = "\n".join(body_text.splitlines()[:6])
    return FileObservation(
        path=path,
        file_seq=page_number(path),
        physical_index=physical_index,
        header_text=header_text,
        footer_text=footer_text,
        body_top_text=body_top,
        header_pairs=_extract_pair_candidates(header_text),
        header_singles=_extract_single_candidates(header_text),
        footer_pairs=_extract_pair_candidates(footer_text),
        footer_singles=_extract_single_candidates(footer_text),
        body_singles=_extract_body_top_singles(body_top),
    )


def _hypothesis_map() -> dict[tuple[int, ...], Hypothesis]:
    return {}


def _get_hypothesis(hypotheses: dict[tuple[int, ...], Hypothesis], pages: tuple[int, ...]) -> Hypothesis:
    item = hypotheses.get(pages)
    if item is None:
        item = Hypothesis(pages=pages)
        hypotheses[pages] = item
    return item


def _add_local_hypotheses(obs: FileObservation) -> dict[tuple[int, ...], Hypothesis]:
    hypotheses = _hypothesis_map()

    for left, right in obs.header_pairs:
        if right == left + 1:
            _get_hypothesis(hypotheses, (left, right)).add(
                5.0, "header_pair", f"{left}-{right}"
            )
        else:
            _get_hypothesis(hypotheses, (left, right)).add(
                0.45,
                "header_pair_nonconsecutive",
                f"{left}-{right}",
            )

    for left, right in obs.footer_pairs:
        weight = 2.2 if right == left + 1 else 1.2
        _get_hypothesis(hypotheses, (left, right)).add(weight, "footer_pair", f"{left}-{right}")

    if not hypotheses:
        for value in obs.header_singles:
            _get_hypothesis(hypotheses, (value,)).add(2.3, "header_single", str(value))
        for value in obs.footer_singles:
            _get_hypothesis(hypotheses, (value,)).add(1.1, "footer_single", str(value))

    if not hypotheses:
        for value in obs.body_singles:
            _get_hypothesis(hypotheses, (value,)).add(0.35, "body_top_fallback", str(value))

    return hypotheses


def _expected_pair_from_neighbor(pair: tuple[int, int], delta: int) -> tuple[int, int]:
    return (pair[0] + (2 * delta), pair[1] + (2 * delta))


def _best_pair(hypotheses: dict[tuple[int, ...], Hypothesis]) -> tuple[int, int] | None:
    pairs = [
        item
        for item in hypotheses.values()
        if len(item.pages) == 2 and item.pages[1] == item.pages[0] + 1
    ]
    if not pairs:
        return None
    best = max(pairs, key=lambda item: item.score)
    return (best.pages[0], best.pages[1])


def _best_single(hypotheses: dict[tuple[int, ...], Hypothesis]) -> int | None:
    singles = [item for item in hypotheses.values() if len(item.pages) == 1]
    if not singles:
        return None
    return max(singles, key=lambda item: item.score).pages[0]


def _propagate_neighbor_pairs(
    observations: list[FileObservation],
    all_hypotheses: list[dict[tuple[int, ...], Hypothesis]],
    window: int,
) -> None:
    for idx, hypotheses in enumerate(all_hypotheses):
        for delta in range(1, window + 1):
            prev_idx = idx - delta
            if prev_idx >= 0:
                pair = _best_pair(all_hypotheses[prev_idx])
                if pair:
                    expected = _expected_pair_from_neighbor(pair, delta)
                    weight = 1.4 / delta
                    if expected[1] == expected[0] + 1:
                        weight += 0.4
                    _get_hypothesis(hypotheses, expected).add(
                        weight,
                        "neighbor_fit",
                        f"{observations[prev_idx].path.name}+{delta}",
                    )
                else:
                    single = _best_single(all_hypotheses[prev_idx])
                    if single:
                        _get_hypothesis(hypotheses, (single + delta,)).add(
                            1.2 / delta,
                            "neighbor_single_fit",
                            f"{observations[prev_idx].path.name}+{delta}",
                        )
            next_idx = idx + delta
            if next_idx < len(all_hypotheses):
                pair = _best_pair(all_hypotheses[next_idx])
                if pair:
                    expected = _expected_pair_from_neighbor(pair, -delta)
                    weight = 1.4 / delta
                    if expected[1] == expected[0] + 1:
                        weight += 0.4
                    _get_hypothesis(hypotheses, expected).add(
                        weight,
                        "neighbor_fit",
                        f"{observations[next_idx].path.name}-{delta}",
                    )
                else:
                    single = _best_single(all_hypotheses[next_idx])
                    if single:
                        _get_hypothesis(hypotheses, (single - delta,)).add(
                            1.2 / delta,
                            "neighbor_single_fit",
                            f"{observations[next_idx].path.name}-{delta}",
                        )


def _apply_bracketed_sequence_consensus(
    observations: list[FileObservation],
    all_hypotheses: list[dict[tuple[int, ...], Hypothesis]],
    *,
    window: int = 16,
) -> None:
    anchors: list[tuple[int, int, str]] = []
    for idx, observation in enumerate(observations):
        for left, right in observation.header_pairs:
            if right == left + 1:
                anchors.append((idx, left - (2 * idx), observation.path.name))

    for idx, hypotheses in enumerate(all_hypotheses):
        buckets: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for anchor_idx, offset, name in anchors:
            if abs(anchor_idx - idx) <= window:
                buckets[offset].append((anchor_idx, name))

        candidates: list[tuple[int, int, int, list[tuple[int, str]]]] = []
        for offset, members in buckets.items():
            before = [item for item in members if item[0] < idx]
            after = [item for item in members if item[0] > idx]
            if not before or not after or len(members) < 3:
                continue
            nearest_span = min(idx - item[0] for item in before) + min(
                item[0] - idx for item in after
            )
            candidates.append((len(members), -nearest_span, offset, members))
        if not candidates:
            continue

        _count, _negative_span, offset, members = max(candidates)
        expected = (2 * idx + offset, 2 * idx + offset + 1)
        if expected[0] <= 0 or expected[1] >= 10000:
            continue
        support = sorted(members, key=lambda item: abs(item[0] - idx))[:6]
        detail = ", ".join(name for _anchor_idx, name in support)
        weight = 8.0 + min(2.0, 0.25 * len(members))
        _get_hypothesis(hypotheses, expected).add(
            weight,
            "sequence_consensus",
            detail,
        )


def _apply_override(
    hypotheses: dict[tuple[int, ...], Hypothesis],
    *,
    override: dict[str, Any],
) -> None:
    pages = tuple(int(item) for item in override["pages"])
    item = _get_hypothesis(hypotheses, pages)
    item.add(20.0, "override_rule", override.get("note") or "sqlite override")


def _finalize_file_result(obs: FileObservation, hypotheses: dict[tuple[int, ...], Hypothesis]) -> dict[str, Any]:
    items = sorted(hypotheses.values(), key=lambda item: (-item.score, item.pages))
    if not items:
        return {
            "file": str(obs.path),
            "file_seq": obs.file_seq,
            "candidate_editorial_pages": [],
            "best_guess": None,
            "best_left_page": None,
            "best_right_page": None,
            "best_single_page": None,
            "confidence": 0.0,
            "confidence_label": "low",
            "evidence": [],
            "warnings": ["no_editorial_page_signal"],
        }

    best = items[0]
    total = sum(max(item.score, 0.0) for item in items)
    confidence = best.score / total if total > 0 else 0.0
    evidence_kinds = {ev.kind for ev in best.evidence}
    if (
        "header_pair" in evidence_kinds
        and len(best.pages) == 2
        and best.pages[1] == best.pages[0] + 1
    ):
        confidence = max(confidence, 0.88 if best.pages and len(best.pages) == 2 else 0.72)
    elif "header_pair_nonconsecutive" in evidence_kinds:
        confidence = min(confidence, 0.35)
    elif "override_rule" in evidence_kinds:
        confidence = max(confidence, 0.97)
    elif "sequence_consensus" in evidence_kinds:
        confidence = max(confidence, 0.9)
    elif "header_single" in evidence_kinds:
        confidence = min(max(confidence, 0.55), 0.78)
    elif evidence_kinds & {"neighbor_fit", "neighbor_single_fit"}:
        neighbor_count = sum(
            ev.kind in {"neighbor_fit", "neighbor_single_fit"} for ev in best.evidence
        )
        confidence = min(max(confidence, 0.55), 0.70 if neighbor_count >= 2 else 0.58)
    elif "footer_pair" in evidence_kinds:
        confidence = min(confidence, 0.68)
    elif "footer_single" in evidence_kinds:
        confidence = min(confidence, 0.48)
    elif "body_top_fallback" in evidence_kinds:
        confidence = min(confidence, 0.35)

    confidence_label = "low"
    if confidence >= 0.82:
        confidence_label = "high"
    elif confidence >= 0.55:
        confidence_label = "medium"

    warnings: list[str] = []
    if not any(ev.kind.startswith("header") for ev in best.evidence):
        warnings.append("missing_strong_header_signal")
    if len(items) > 1 and items[1].score >= best.score * 0.85:
        warnings.append("competing_hypotheses_close")
    if any(value for value in obs.body_singles) and not any(ev.kind == "header_pair" for ev in best.evidence):
        warnings.append("body_numbers_may_be_citations")
    if any(ev.kind == "header_pair_nonconsecutive" for ev in best.evidence):
        warnings.append("nonconsecutive_header_pair_suspected_ocr")
    if any(ev.kind == "override_rule" for ev in best.evidence):
        warnings.append("override_applied")
    if (
        any(ev.kind == "sequence_consensus" for ev in best.evidence)
        and obs.header_pairs
        and best.pages not in obs.header_pairs
    ):
        warnings.append("header_pair_overridden_by_sequence")

    best_guess: list[int] | int | None
    best_left_page: int | None = None
    best_right_page: int | None = None
    best_single_page: int | None = None
    if len(best.pages) == 2:
        best_guess = [best.pages[0], best.pages[1]]
        best_left_page, best_right_page = best.pages
    else:
        best_guess = best.pages[0]
        best_single_page = best.pages[0]

    return {
        "file": str(obs.path),
        "file_seq": obs.file_seq,
        "candidate_editorial_pages": [item.to_dict() for item in items[:5]],
        "best_guess": best_guess,
        "best_left_page": best_left_page,
        "best_right_page": best_right_page,
        "best_single_page": best_single_page,
        "confidence": round(min(confidence, 0.9999), 4),
        "confidence_label": confidence_label,
        "evidence": [item.to_dict() for item in best.evidence],
        "warnings": warnings,
    }


def best_guess_pages(best_guess: list[int] | int | None) -> list[int]:
    if isinstance(best_guess, list):
        out: list[int] = []
        for item in best_guess:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if 0 < value < 10000 and value not in out:
                out.append(value)
        return out
    if isinstance(best_guess, int) and 0 < best_guess < 10000:
        return [best_guess]
    return []


def build_estimator_page_map(
    *,
    volume_id: str,
    collection: str,
    source_root: Path,
    window: int = 4,
    db_path: Path | None = None,
    use_cache: bool = True,
) -> dict[int, str]:
    payload = estimate_editorial_pages(
        volume_id=volume_id,
        source_root=source_root,
        collection=collection,
        window=window,
        db_path=db_path,
        use_cache=use_cache,
    )
    mapping: dict[int, str] = {}
    for item in payload.get("files") or []:
        file_path = str(item.get("file") or "")
        if not file_path:
            continue
        for page in best_guess_pages(item.get("best_guess")):
            mapping.setdefault(page, file_path)
    return mapping


def estimate_editorial_pages(
    *,
    volume_id: str | None = None,
    source_root: Path | None = None,
    files: list[Path] | None = None,
    collection: str | None = None,
    window: int = 4,
    db_path: Path | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    resolved_source_root = _resolve_source_root(volume_id, source_root, files)
    loaded_files = _load_files(resolved_source_root, files)
    inferred_volume_id = volume_id or _infer_volume_id(resolved_source_root, loaded_files)
    inferred_collection = (collection or _infer_collection(inferred_volume_id, resolved_source_root, loaded_files) or "").upper()

    if inferred_collection not in COLLECTIONS_SUPPORTED:
        supported = ", ".join(sorted(COLLECTIONS_SUPPORTED))
        raise ValueError(f"unsupported collection {inferred_collection!r}; supported collections: {supported}")

    resolved_db = db_path or DEFAULT_ESTIMATOR_DB
    conn = _connect_db(resolved_db)
    try:
        observations: list[FileObservation] = []
        uncached_observations: list[FileObservation] = []
        for idx, path in enumerate(loaded_files):
            cached = _read_cached_observation(conn, path=path) if use_cache else None
            if cached is not None:
                cached.physical_index = idx
                observations.append(cached)
                continue
            observed = _observe_file(path, idx)
            observations.append(observed)
            if use_cache:
                uncached_observations.append(observed)
        # Do all filesystem reads before opening SQLite's write transaction.
        # This matters when callers parallelize independent volumes: the old
        # loop held the single WAL writer lock while parsing the remainder of
        # a cold volume, effectively serializing all workers.
        if use_cache:
            for observed in uncached_observations:
                _write_cached_observation(
                    conn,
                    volume_id=inferred_volume_id,
                    collection=inferred_collection,
                    source_root=resolved_source_root,
                    observation=observed,
                )
            conn.commit()
        overrides = _load_overrides(conn, volume_id=inferred_volume_id)
    finally:
        conn.close()

    all_hypotheses = [_add_local_hypotheses(item) for item in observations]
    _propagate_neighbor_pairs(observations, all_hypotheses, window=max(1, window))
    _apply_bracketed_sequence_consensus(observations, all_hypotheses)
    for obs, hypotheses in zip(observations, all_hypotheses):
        override = overrides.get(str(obs.path))
        if override:
            _apply_override(hypotheses, override=override)
    file_results = [
        _finalize_file_result(obs, hypotheses)
        for obs, hypotheses in zip(observations, all_hypotheses)
    ]

    summary_counts = defaultdict(int)
    for item in file_results:
        summary_counts[item["confidence_label"]] += 1
        if item["warnings"]:
            summary_counts["with_warnings"] += 1

    return {
        "volume_id": inferred_volume_id,
        "collection": inferred_collection,
        "source_root": str(resolved_source_root) if resolved_source_root else None,
        "db": str(resolved_db),
        "use_cache": use_cache,
        "window": window,
        "file_count": len(loaded_files),
        "summary": {
            "high_confidence_files": summary_counts["high"],
            "medium_confidence_files": summary_counts["medium"],
            "low_confidence_files": summary_counts["low"],
            "files_with_warnings": summary_counts["with_warnings"],
            "override_applied_files": sum(1 for item in file_results if "override_applied" in item["warnings"]),
        },
        "files": file_results,
    }


def estimate_editorial_pages_json(
    *,
    volume_id: str | None = None,
    source_root: Path | None = None,
    files: list[Path] | None = None,
    collection: str | None = None,
    window: int = 2,
    db_path: Path | None = None,
    use_cache: bool = True,
    pretty: bool = False,
) -> str:
    payload = estimate_editorial_pages(
        volume_id=volume_id,
        source_root=source_root,
        files=files,
        collection=collection,
        window=window,
        db_path=db_path,
        use_cache=use_cache,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None)
