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
