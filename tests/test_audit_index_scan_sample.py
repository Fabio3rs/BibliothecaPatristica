from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_index_scan_sample.py"


def test_scan_audit_fails_when_manual_heading_is_missed(tmp_path: Path) -> None:
    text_root = tmp_path / "teste" / "PO001" / "text"
    text_root.mkdir(parents=True)
    (text_root / "po-001.txt").write_text(
        '<pagina><bloco tipo="cabecalho">ordinary text</bloco></pagina>',
        encoding="utf-8",
    )
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "seed": 1,
                "records": [
                    {
                        "volume_id": "PO001",
                        "expected_heading_files": ["po-001.txt"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path / "teste"),
            "--reference",
            str(reference),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert '"heading_recall": 0.0' in result.stdout


def test_scan_audit_fails_when_reviewed_non_index_file_is_selected(tmp_path: Path) -> None:
    text_root = tmp_path / "teste" / "PL001" / "text"
    text_root.mkdir(parents=True)
    (text_root / "pl-001.txt").write_text(
        '<pagina><bloco tipo="cabecalho">INDEX GENERALIS</bloco></pagina>',
        encoding="utf-8",
    )
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "volume_id": "PL001",
                        "expected_heading_files": [],
                        "reviewed_non_index_files": ["pl-001.txt"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path / "teste"),
            "--reference",
            str(reference),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert '"false_positive_count": 1' in result.stdout
