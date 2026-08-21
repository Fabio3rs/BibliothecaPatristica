from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / ".codex/skills/patristic-index-extractor/scripts/import_index_json.py"


def test_importer_does_not_infer_physical_file_from_editorial_page(tmp_path: Path) -> None:
    source_root = tmp_path / "PL001" / "text"
    source_root.mkdir(parents=True)
    physical_file = source_root / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa-916.txt"
    physical_file.write_text("header 915 ... 916", encoding="utf-8")
    payload_path = tmp_path / "payload.json"
    db_path = tmp_path / "indices.db"
    payload_path.write_text(
        json.dumps(
            {
                "volume": {
                    "volume_id": "PL001",
                    "collection": "PL",
                    "source_root": str(source_root),
                },
                "works": [],
                "sections": [
                    {
                        "section_key": "PL001:index:1",
                        "heading_raw": "INDEX RERUM",
                        "page_start": 916,
                        "page_end": 916,
                        "entries": [
                            {
                                "entry_raw": "AARON, 916",
                                "page_ref_raw": "916",
                                "page_ref_int": 916,
                            }
                        ],
                    }
                ],
                "notes": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, str(IMPORTER), "--db", str(db_path), "--input", str(payload_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(db_path) as con:
        section_files = con.execute(
            "SELECT file_start, file_end FROM index_sections"
        ).fetchone()
        target_file = con.execute("SELECT target_file FROM index_entries").fetchone()[0]

    assert section_files == (None, None)
    assert target_file is None


def test_importer_rejects_inverted_work_editorial_range(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "volume": {
                    "volume_id": "PG001",
                    "collection": "PG",
                    "source_root": str(tmp_path / "PG001" / "text"),
                },
                "works": [
                    {
                        "work_key": "PG001:work:virgines",
                        "title_raw": "EPISTOLAE DUAE AD VIRGINES",
                        "start_page": 579,
                        "end_page": 508,
                    }
                ],
                "sections": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(IMPORTER),
            "--db",
            str(tmp_path / "indices.db"),
            "--input",
            str(payload_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "start_page > end_page" in result.stderr


def test_importer_refuses_duplicate_volume_without_replace(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    db_path = tmp_path / "indices.db"
    payload_path.write_text(
        json.dumps(
            {
                "volume": {
                    "volume_id": "PG001",
                    "collection": "PG",
                    "source_root": str(tmp_path / "PG001" / "text"),
                },
                "works": [],
                "sections": [
                    {
                        "section_key": "PG001:index:1",
                        "heading_raw": "ELENCHUS",
                        "page_start": 1,
                        "page_end": 1,
                        "entries": [{"entry_raw": "OPUS. 15"}],
                    }
                ],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(IMPORTER),
        "--db",
        str(db_path),
        "--input",
        str(payload_path),
    ]

    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    duplicate = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    with sqlite3.connect(db_path) as con:
        entry_count = con.execute("SELECT COUNT(*) FROM index_entries").fetchone()[0]
        run_count = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    assert duplicate.returncode != 0
    assert "Refusing to append duplicate index entries" in duplicate.stderr
    assert entry_count == 1
    assert run_count == 1

    subprocess.run(
        [*command, "--replace"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM index_entries").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
