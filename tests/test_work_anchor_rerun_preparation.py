from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCRIPT = ROOT / "scripts" / "build_work_anchor_rerun_manifest.py"
CLEANUP_SCRIPT = ROOT / "scripts" / "remove_rerun_volumes_from_db.py"
RECONCILE_SCRIPT = ROOT / "scripts" / "reconcile_work_index_anchors.py"


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE volumes (
                volume_id TEXT PRIMARY KEY,
                collection TEXT,
                source_root TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE works (
                work_key TEXT PRIMARY KEY,
                volume_id TEXT REFERENCES volumes(volume_id) ON DELETE CASCADE
            );
            CREATE TABLE index_sections (
                section_key TEXT PRIMARY KEY,
                volume_id TEXT REFERENCES volumes(volume_id) ON DELETE CASCADE,
                work_key TEXT REFERENCES works(work_key) ON DELETE SET NULL
            );
            CREATE TABLE index_entries (
                id INTEGER PRIMARY KEY,
                section_key TEXT REFERENCES index_sections(section_key) ON DELETE CASCADE
            );
            CREATE TABLE runs (
                run_id INTEGER PRIMARY KEY,
                volume_id TEXT
            );
            """
        )
        for volume_id in ("PG001", "PG002"):
            conn.execute(
                "INSERT INTO volumes VALUES (?, 'PG', ?, '', '')",
                (volume_id, f"/tmp/{volume_id}/text"),
            )
            conn.execute(
                "INSERT INTO works VALUES (?, ?)",
                (f"{volume_id}:work", volume_id),
            )
            conn.execute(
                "INSERT INTO index_sections VALUES (?, ?, ?)",
                (f"{volume_id}:section", volume_id, f"{volume_id}:work"),
            )
            conn.execute(
                "INSERT INTO index_entries(section_key) VALUES (?)",
                (f"{volume_id}:section",),
            )
            conn.execute(
                "INSERT INTO runs(volume_id) VALUES (?)",
                (volume_id,),
            )


def test_manifest_classifies_markers_invalid_ranges_and_clean_changes(
    tmp_path: Path,
) -> None:
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    db_path = tmp_path / "indices.db"
    _make_db(db_path)
    (payload_dir / "PG001_indices.json").write_text(
        json.dumps(
            {
                "volume": {"volume_id": "PG001"},
                "works": [
                    {
                        "work_key": "PG001:work",
                        "start_page": 9,
                        "end_page": 4,
                        "raw_json": {
                            "work_anchor_rerun": {
                                "status": "ambiguous",
                                "reason": "candidate_probability_gap",
                            }
                        },
                    }
                ],
                "sections": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )
    (payload_dir / "PG002_indices.json").write_text(
        json.dumps(
            {
                "volume": {"volume_id": "PG002"},
                "works": [],
                "sections": [],
                "notes": [],
            }
        ),
        encoding="utf-8",
    )
    audit = tmp_path / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "reports": [
                    {"volume_id": "PG001", "changed_count": 1},
                    {"volume_id": "PG002", "changed_count": 2},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "manifest.json"

    subprocess.run(
        [
            sys.executable,
            str(MANIFEST_SCRIPT),
            "--payload-dir",
            str(payload_dir),
            "--db",
            str(db_path),
            "--audit-report",
            str(audit),
            "--output",
            str(output),
        ],
        check=True,
        cwd=ROOT,
    )

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["rerun_volume_ids"] == ["PG001"]
    assert manifest["clean_corrected_volume_ids"] == ["PG002"]
    assert manifest["rerun_marker_count"] == 1
    assert manifest["invalid_range_volume_count"] == 1


def test_cleanup_removes_only_manifest_volumes_and_children(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "indices.db"
    backup = tmp_path / "indices.backup.db"
    _make_db(db_path)
    shutil.copy2(db_path, backup)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"rerun_volume_ids": ["PG001"]}),
        encoding="utf-8",
    )
    report = tmp_path / "cleanup.json"

    subprocess.run(
        [
            sys.executable,
            str(CLEANUP_SCRIPT),
            "--manifest",
            str(manifest),
            "--db",
            str(db_path),
            "--backup",
            str(backup),
            "--report",
            str(report),
            "--apply",
        ],
        check=True,
        cwd=ROOT,
    )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT volume_id FROM volumes").fetchall() == [
            ("PG002",)
        ]
        assert conn.execute("SELECT volume_id FROM works").fetchall() == [
            ("PG002",)
        ]
        assert conn.execute(
            "SELECT volume_id FROM index_sections"
        ).fetchall() == [("PG002",)]
        assert conn.execute("SELECT COUNT(*) FROM index_entries").fetchone()[0] == 1
        assert conn.execute("SELECT volume_id FROM runs").fetchall() == [
            ("PG002",)
        ]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_reconciler_uses_multiprocess_workers(tmp_path: Path) -> None:
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    for volume_id in ("PO001", "PO002"):
        (payload_dir / f"{volume_id}_indices.json").write_text(
            json.dumps(
                {
                    "volume": {
                        "volume_id": volume_id,
                        "collection": "PO",
                        "source_root": str(tmp_path / volume_id / "text"),
                    },
                    "works": [],
                    "sections": [],
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )
    report = tmp_path / "report.json"

    subprocess.run(
        [
            sys.executable,
            str(RECONCILE_SCRIPT),
            "--all-volumes",
            "--payload-dir",
            str(payload_dir),
            "--workers",
            "13",
            "--report-json",
            str(report),
        ],
        check=True,
        cwd=ROOT,
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["workers"] == 13
    assert payload["payload_count"] == 2
    assert payload["failure_count"] == 0
