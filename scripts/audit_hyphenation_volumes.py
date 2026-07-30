#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿĀ-ſ\u0370-\u03FF\u1F00-\u1FFF"
LINEBREAK_HYPHEN_RE = re.compile(rf"[{WORD_CHARS}]\-\s+[{WORD_CHARS}]")


ALPHABETICAL_COLUMNS: dict[str, tuple[str, list[str]]] = {
    "alphabetical_sections": ("section_key", ["heading_raw", "heading_norm"]),
    "alphabetical_entries": (
        "section_key",
        ["entry_raw", "lemma_raw", "lemma_display", "lemma_norm", "lemma_sort", "context_raw"],
    ),
    "alphabetical_nodes": ("section_key", ["label_raw", "label_norm", "label_sort"]),
    "alphabetical_refs": ("entry_key", ["ref_raw", "page_ref_raw", "line_ref_raw", "range_start_raw", "range_end_raw"]),
    "alphabetical_scripture_refs": ("entry_key", ["ref_raw", "ref_norm", "book_raw", "book_norm"]),
}

PATRISTIC_COLUMNS: dict[str, tuple[str, list[str]]] = {
    "works": ("volume_id", ["author_raw", "title_raw", "title_norm"]),
    "index_sections": ("volume_id", ["heading_raw", "heading_norm"]),
    "index_entries": ("section_key", ["entry_raw", "target_raw", "page_ref_raw", "note_raw", "normalized_target"]),
}


def collapse_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def looks_like_hyphen_artifact(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = collapse_ws(value)
    if not text:
        return None
    if text.endswith("-"):
        return "terminal_hyphen"
    if LINEBREAK_HYPHEN_RE.search(text):
        return "linebreak_hyphen"
    return None


def scan_table(
    con: sqlite3.Connection,
    table: str,
    volume_resolver: Callable[[sqlite3.Row], str | None],
    columns: list[str],
    result: dict[str, Any],
) -> None:
    query = f"SELECT rowid AS rid, * FROM {table}"
    for row in con.execute(query):
        volume_id = volume_resolver(row)
        if not volume_id:
            continue
        for column in columns:
            value = row[column]
            kind = looks_like_hyphen_artifact(value)
            if not kind:
                continue
            volume_bucket = result[volume_id]
            volume_bucket["cell_hits"] += 1
            volume_bucket["kind_counts"][kind] += 1
            table_bucket = volume_bucket["table_counts"].setdefault(table, {"cell_hits": 0, "kinds": defaultdict(int)})
            table_bucket["cell_hits"] += 1
            table_bucket["kinds"][kind] += 1
            if len(volume_bucket["examples"]) < 5:
                preview = collapse_ws(value)
                if len(preview) > 160:
                    preview = preview[:157] + "..."
                volume_bucket["examples"].append(
                    {
                        "table": table,
                        "rowid": row["rid"],
                        "column": column,
                        "kind": kind,
                        "value": preview,
                    }
                )


def scan_alphabetical(db_path: Path) -> dict[str, Any]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    section_to_volume = dict(con.execute("SELECT section_key, volume_id FROM alphabetical_sections"))
    entry_to_section = dict(con.execute("SELECT entry_key, section_key FROM alphabetical_entries"))

    def volume_from_section(row: sqlite3.Row) -> str | None:
        return section_to_volume.get(str(row["section_key"]))

    def volume_from_entry_section(row: sqlite3.Row) -> str | None:
        section_key = entry_to_section.get(str(row["entry_key"]))
        if section_key is None:
            return None
        return section_to_volume.get(section_key)

    result: dict[str, Any] = defaultdict(
        lambda: {
            "cell_hits": 0,
            "kind_counts": defaultdict(int),
            "table_counts": {},
            "examples": [],
        }
    )
    scan_table(con, "alphabetical_sections", volume_from_section, ALPHABETICAL_COLUMNS["alphabetical_sections"][1], result)
    scan_table(con, "alphabetical_entries", volume_from_section, ALPHABETICAL_COLUMNS["alphabetical_entries"][1], result)
    scan_table(con, "alphabetical_nodes", volume_from_section, ALPHABETICAL_COLUMNS["alphabetical_nodes"][1], result)
    scan_table(con, "alphabetical_refs", volume_from_entry_section, ALPHABETICAL_COLUMNS["alphabetical_refs"][1], result)
    scan_table(con, "alphabetical_scripture_refs", volume_from_entry_section, ALPHABETICAL_COLUMNS["alphabetical_scripture_refs"][1], result)
    con.close()
    return result


def scan_patristic(db_path: Path) -> dict[str, Any]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    section_to_volume = dict(con.execute("SELECT section_key, volume_id FROM index_sections"))

    def volume_from_volume_row(row: sqlite3.Row) -> str | None:
        return str(row["volume_id"])

    def volume_from_section(row: sqlite3.Row) -> str | None:
        return section_to_volume.get(str(row["section_key"]))

    result: dict[str, Any] = defaultdict(
        lambda: {
            "cell_hits": 0,
            "kind_counts": defaultdict(int),
            "table_counts": {},
            "examples": [],
        }
    )
    scan_table(con, "works", volume_from_volume_row, PATRISTIC_COLUMNS["works"][1], result)
    scan_table(con, "index_sections", volume_from_volume_row, PATRISTIC_COLUMNS["index_sections"][1], result)
    scan_table(con, "index_entries", volume_from_section, PATRISTIC_COLUMNS["index_entries"][1], result)
    con.close()
    return result


def finalize_report(db_path: Path, label: str, raw_result: dict[str, Any]) -> dict[str, Any]:
    volumes = []
    for volume_id in sorted(raw_result):
        data = raw_result[volume_id]
        table_counts = {
            table: {
                "cell_hits": stats["cell_hits"],
                "kinds": dict(sorted(stats["kinds"].items())),
            }
            for table, stats in sorted(data["table_counts"].items())
        }
        volumes.append(
            {
                "volume_id": volume_id,
                "cell_hits": data["cell_hits"],
                "kind_counts": dict(sorted(data["kind_counts"].items())),
                "table_counts": table_counts,
                "examples": data["examples"],
            }
        )

    return {
        "db": str(db_path),
        "label": label,
        "volume_count": len(volumes),
        "linebreak_volume_count": sum(1 for volume in volumes if volume["kind_counts"].get("linebreak_hyphen", 0)),
        "terminal_only_volume_count": sum(
            1
            for volume in volumes
            if volume["kind_counts"].get("terminal_hyphen", 0)
            and not volume["kind_counts"].get("linebreak_hyphen", 0)
        ),
        "volumes": volumes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic audit for OCR hyphenation artifacts in the index SQLite databases.")
    ap.add_argument(
        "--db",
        dest="dbs",
        action="append",
        type=Path,
        help="SQLite database to audit. Repeat to audit more than one DB.",
    )
    ap.add_argument(
        "--report-json",
        type=Path,
        help="Optional path for the full JSON report.",
    )
    ap.add_argument(
        "--volume-list",
        type=Path,
        help="Optional TSV path with db label, volume_id, total hits, linebreak hits, and terminal hits.",
    )
    args = ap.parse_args()

    dbs = args.dbs or [Path("data/alphabetical_indices.db"), Path("data/patristic_indices.db")]
    reports = []
    summary = []
    for db_path in dbs:
        label = "alphabetical" if db_path.name.startswith("alphabetical_") else "patristic"
        raw_result = scan_alphabetical(db_path) if label == "alphabetical" else scan_patristic(db_path)
        report = finalize_report(db_path, label, raw_result)
        reports.append(report)
        summary.append(
            {
                "db": str(db_path),
                "label": label,
                "problematic_volumes": report["volume_count"],
                "linebreak_volumes": report["linebreak_volume_count"],
                "terminal_only_volumes": report["terminal_only_volume_count"],
                "total_cell_hits": sum(volume["cell_hits"] for volume in report["volumes"]),
                "volumes": [
                    {
                        "volume_id": volume["volume_id"],
                        "cell_hits": volume["cell_hits"],
                        "kind_counts": volume["kind_counts"],
                    }
                    for volume in report["volumes"]
                ],
            }
        )

    output = {"summary": summary, "reports": reports}
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))
    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.volume_list:
        lines = ["label\tvolume_id\tcell_hits\tlinebreak_hyphen\tterminal_hyphen"]
        for report in reports:
            for volume in report["volumes"]:
                counts = volume["kind_counts"]
                lines.append(
                    "\t".join(
                        [
                            report["label"],
                            volume["volume_id"],
                            str(volume["cell_hits"]),
                            str(counts.get("linebreak_hyphen", 0)),
                            str(counts.get("terminal_hyphen", 0)),
                        ]
                    )
                )
        args.volume_list.parent.mkdir(parents=True, exist_ok=True)
        args.volume_list.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
