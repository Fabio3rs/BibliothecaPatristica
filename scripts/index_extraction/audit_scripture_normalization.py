#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripture_ref_normalizer import LIVROS_PT, extract_citations_from_value_cached

DEFAULT_DB = PROJECT_ROOT / "data" / "alphabetical_indices.db"

SCRIPTURE_SECTION_KINDS = ("scripture_index", "pericope_index", "concordance_index")
SUSPICIOUS_BOOK_PREFIXES = ("index ", "indices ", "table ", "tables ", "ordo ", "elenchus ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audita alphabetical_scripture_refs, tenta normalizacao canonica e "
            "lista volumes/formas problematicas."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Caminho do SQLite.")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Salva o relatorio completo em JSON.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Quantidade de itens exibidos por ranking no stdout.",
    )
    return parser.parse_args()


def strip_accents(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKD", text)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return unicodedata.normalize("NFKC", value)


def fold_text(text: str | None) -> str:
    value = strip_accents(text).lower().strip()
    value = re.sub(r"\s+", " ", value)
    return value


CANONICAL_BOOK_FOLDS = {fold_text(name): name for name in LIVROS_PT}


def is_canonical_book_name(book_norm: str | None) -> bool:
    return fold_text(book_norm) in CANONICAL_BOOK_FOLDS


def looks_suspicious_book_label(book_norm: str | None) -> bool:
    folded = fold_text(book_norm)
    if not folded:
        return False
    return folded.startswith(SUSPICIOUS_BOOK_PREFIXES)


def looks_oversized_ref_raw(ref_raw: str | None, *, threshold: int = 200) -> bool:
    if not ref_raw:
        return False
    return len(ref_raw) > threshold


def looks_page_dump(ref_raw: str | None) -> bool:
    if not ref_raw:
        return False
    text = ref_raw.strip()
    if len(text) > 200:
        return True
    if text.count("\n") >= 2:
        return True
    if text.count(";") >= 6:
        return True
    if len(re.findall(r"\b\d{1,4}\b", text)) >= 8:
        return True
    if len(re.findall(r"[A-Za-zÀ-ÿ]{4,}", text)) >= 18:
        return True
    return False


def compose_structured_candidate(row: sqlite3.Row) -> str | None:
    book = (row["book_raw"] or row["book_norm"] or "").strip()
    if not book:
        return None
    chapter_start = row["chapter_start"]
    verse_start = row["verse_start"]
    chapter_end = row["chapter_end"]
    verse_end = row["verse_end"]
    is_range = bool(row["is_range"])
    if chapter_start is None:
        return book
    if verse_start is None:
        return f"{book} {chapter_start}"
    candidate = f"{book} {chapter_start}:{verse_start}"
    if is_range:
        if chapter_end and chapter_end != chapter_start and verse_end:
            candidate = f"{candidate}-{chapter_end}:{verse_end}"
        elif verse_end:
            candidate = f"{candidate}-{verse_end}"
    return candidate


def try_normalize_text(raw_value: str | None, source_path: str) -> dict[str, Any] | None:
    if not raw_value:
        return None
    records = extract_citations_from_value_cached(
        raw_value,
        source_kind="alphabetical_index",
        source_path=source_path,
        support_mode=False,
    )
    if not records:
        return None
    return records[0]


def attempt_normalization(row: sqlite3.Row) -> tuple[dict[str, Any] | None, str | None]:
    structured_candidate = compose_structured_candidate(row)
    if structured_candidate:
        record = try_normalize_text(structured_candidate, f"{row['entry_key']}:structured")
        if record:
            return record, "structured"
    ref_raw = (row["ref_raw"] or "").strip()
    if ref_raw and len(ref_raw) <= 160:
        record = try_normalize_text(ref_raw, f"{row['entry_key']}:ref_raw")
        if record:
            return record, "ref_raw"
    book_candidate = (row["book_raw"] or row["book_norm"] or "").strip()
    if book_candidate:
        record = try_normalize_text(book_candidate, f"{row['entry_key']}:book")
        if record:
            return record, "book"
    return None, None


def connect_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def fetch_scripture_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            sr.entry_key,
            sr.ref_order,
            sr.ref_role,
            sr.ref_raw,
            sr.ref_norm,
            sr.book_raw,
            sr.book_norm,
            sr.chapter_start,
            sr.verse_start,
            sr.chapter_end,
            sr.verse_end,
            sr.is_range,
            sr.confidence,
            e.section_key,
            e.entry_kind,
            e.lemma_raw,
            e.lemma_display,
            e.entry_raw,
            s.volume_id,
            s.section_kind,
            s.heading_raw
        FROM alphabetical_scripture_refs sr
        JOIN alphabetical_entries e ON e.entry_key = sr.entry_key
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        ORDER BY s.volume_id, sr.entry_key, sr.ref_order
        """
    ).fetchall()


def fetch_scripture_entries_missing_refs(con: sqlite3.Connection) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in SCRIPTURE_SECTION_KINDS)
    return con.execute(
        f"""
        SELECT
            s.volume_id,
            s.section_kind,
            e.entry_key,
            e.entry_kind,
            e.lemma_raw,
            e.lemma_display,
            e.entry_raw
        FROM alphabetical_entries e
        JOIN alphabetical_sections s ON s.section_key = e.section_key
        LEFT JOIN alphabetical_scripture_refs sr ON sr.entry_key = e.entry_key
        WHERE s.section_kind IN ({placeholders})
          AND sr.entry_key IS NULL
        ORDER BY s.volume_id, e.entry_key
        """,
        SCRIPTURE_SECTION_KINDS,
    ).fetchall()


def short_text(text: str | None, limit: int = 140) -> str:
    if not text:
        return ""
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def build_problem_counts(problem_rows: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(row["problem"] for row in problem_rows)
    return dict(counter.most_common())


def build_problematic_form_rows(form_map: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for (issue, form), payload in form_map.items():
        rows.append(
            {
                "issue": issue,
                "form": form,
                "count": payload["count"],
                "volumes": sorted(payload["volumes"]),
                "canonical_candidates": sorted(payload["canonical_candidates"]),
            }
        )
    rows.sort(key=lambda item: (-item["count"], item["issue"], item["form"]))
    return rows


def build_problematic_volume_rows(volume_problem_counter: dict[str, Counter]) -> list[dict[str, Any]]:
    rows = []
    for volume_id, counter in volume_problem_counter.items():
        total = sum(counter.values())
        rows.append(
            {
                "volume_id": volume_id,
                "total_problems": total,
                "problems": dict(counter.most_common()),
            }
        )
    rows.sort(key=lambda item: (-item["total_problems"], item["volume_id"]))
    return rows


def audit_db(con: sqlite3.Connection) -> dict[str, Any]:
    scripture_rows = fetch_scripture_rows(con)
    missing_rows = fetch_scripture_entries_missing_refs(con)

    normalized_ok = 0
    normalized_from: Counter[str] = Counter()
    problem_rows: list[dict[str, Any]] = []
    form_map: dict[tuple[str, str], dict[str, Any]] = {}
    volume_problem_counter: dict[str, Counter] = defaultdict(Counter)
    volume_row_counter: Counter[str] = Counter()

    def record_problem(
        *,
        problem: str,
        row: sqlite3.Row,
        detail: str | None = None,
        form: str | None = None,
        canonical_candidate: str | None = None,
    ) -> None:
        item = {
            "problem": problem,
            "volume_id": row["volume_id"],
            "entry_key": row["entry_key"],
            "ref_order": row["ref_order"],
            "book_raw": row["book_raw"],
            "book_norm": row["book_norm"],
            "ref_raw_preview": short_text(row["ref_raw"]),
            "detail": detail,
        }
        problem_rows.append(item)
        volume_problem_counter[row["volume_id"]][problem] += 1
        if form:
            key = (problem, form)
            bucket = form_map.setdefault(
                key,
                {
                    "count": 0,
                    "volumes": set(),
                    "canonical_candidates": set(),
                },
            )
            bucket["count"] += 1
            bucket["volumes"].add(row["volume_id"])
            if canonical_candidate:
                bucket["canonical_candidates"].add(canonical_candidate)

    for row in scripture_rows:
        volume_row_counter[row["volume_id"]] += 1
        normalized_record, normalized_source = attempt_normalization(row)
        if normalized_record:
            normalized_ok += 1
            normalized_from[normalized_source or "unknown"] += 1

        existing_ref_norm = (row["ref_norm"] or "").strip()
        if not existing_ref_norm:
            record_problem(problem="missing_ref_norm", row=row)

        if row["chapter_start"] is None:
            record_problem(problem="missing_chapter_start", row=row)
        if row["verse_start"] is None:
            record_problem(problem="missing_verse_start", row=row)

        if looks_oversized_ref_raw(row["ref_raw"]):
            record_problem(
                problem="oversized_ref_raw",
                row=row,
                detail=f"len={len(row['ref_raw'] or '')}",
            )
        if looks_page_dump(row["ref_raw"]):
            record_problem(problem="ref_raw_page_dump", row=row)

        book_norm = (row["book_norm"] or "").strip()
        canonical_candidate = normalized_record["book_canonical"] if normalized_record else None

        if book_norm:
            if looks_suspicious_book_label(book_norm):
                record_problem(
                    problem="suspicious_book_norm_label",
                    row=row,
                    form=book_norm,
                    canonical_candidate=canonical_candidate,
                )
            elif not is_canonical_book_name(book_norm):
                record_problem(
                    problem="noncanonical_book_norm",
                    row=row,
                    form=book_norm,
                    canonical_candidate=canonical_candidate,
                )

        if normalized_record is None:
            record_problem(
                problem="normalization_failed",
                row=row,
                detail="no normalization candidate recovered",
            )
        else:
            if canonical_candidate and book_norm and fold_text(book_norm) != fold_text(canonical_candidate):
                record_problem(
                    problem="book_norm_canonical_mismatch",
                    row=row,
                    form=book_norm,
                    canonical_candidate=canonical_candidate,
                    detail=f"expected={canonical_candidate}",
                )

    missing_entry_rows = []
    for row in missing_rows:
        volume_problem_counter[row["volume_id"]]["entry_missing_scripture_ref"] += 1
        missing_entry_rows.append(
            {
                "volume_id": row["volume_id"],
                "section_kind": row["section_kind"],
                "entry_key": row["entry_key"],
                "entry_kind": row["entry_kind"],
                "lemma_raw": row["lemma_raw"],
                "lemma_display": row["lemma_display"],
                "entry_raw_preview": short_text(row["entry_raw"]),
            }
        )

    summary = {
        "scripture_ref_rows": len(scripture_rows),
        "scripture_ref_volumes": len(volume_row_counter),
        "rows_with_normalization_attempt_success": normalized_ok,
        "normalization_success_rate": round(normalized_ok / len(scripture_rows), 4) if scripture_rows else 0.0,
        "normalized_from": dict(normalized_from.most_common()),
        "entries_in_scripture_sections_missing_refs": len(missing_rows),
        "problem_counts": build_problem_counts(problem_rows) | {"entry_missing_scripture_ref": len(missing_rows)},
    }

    return {
        "summary": summary,
        "problematic_volumes": build_problematic_volume_rows(volume_problem_counter),
        "problematic_forms": build_problematic_form_rows(form_map),
        "problems": problem_rows,
        "missing_scripture_ref_entries": missing_entry_rows,
    }


def print_report(report: dict[str, Any], *, top: int) -> None:
    summary = report["summary"]
    print("== Summary ==")
    print(f"scripture_ref_rows: {summary['scripture_ref_rows']}")
    print(f"scripture_ref_volumes: {summary['scripture_ref_volumes']}")
    print(f"rows_with_normalization_attempt_success: {summary['rows_with_normalization_attempt_success']}")
    print(f"normalization_success_rate: {summary['normalization_success_rate']}")
    print(f"entries_in_scripture_sections_missing_refs: {summary['entries_in_scripture_sections_missing_refs']}")
    print()

    print("== Problem Counts ==")
    for problem, count in summary["problem_counts"].items():
        print(f"{problem}: {count}")
    print()

    print("== Top Problematic Volumes ==")
    for row in report["problematic_volumes"][:top]:
        print(f"{row['volume_id']}: total={row['total_problems']} problems={row['problems']}")
    print()

    print("== Problematic Forms ==")
    for row in report["problematic_forms"][:top]:
        print(
            f"{row['issue']}: {row['form']} | count={row['count']} "
            f"| volumes={','.join(row['volumes'])} "
            f"| canonical={','.join(row['canonical_candidates']) or '-'}"
        )
    print()

    print("== Missing Scripture Ref Entries By Volume ==")
    by_volume = Counter(item["volume_id"] for item in report["missing_scripture_ref_entries"])
    for volume_id, count in by_volume.most_common(top):
        print(f"{volume_id}: {count}")


def main() -> int:
    args = parse_args()
    con = connect_db(args.db)
    try:
        report = audit_db(con)
    finally:
        con.close()

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print_report(report, top=args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
