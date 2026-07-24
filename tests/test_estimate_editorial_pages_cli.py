from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_estimate_editorial_pages_cli_emits_json(tmp_path: Path) -> None:
    text_root = tmp_path / "PGZ" / "text"
    text_root.mkdir(parents=True)
    (text_root / "page-001.txt").write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">543 ORATIO. 544</bloco>
          <bloco tipo="texto_principal" script="latino">Dionysius.</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/estimate_editorial_pages.py",
            "--source-root",
            str(text_root),
            "--collection",
            "PG",
            "--pretty",
        ],
        cwd="/homessddata/Projects/pdfocr",
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["collection"] == "PG"
    assert payload["files"][0]["best_left_page"] == 543
    assert payload["files"][0]["best_right_page"] == 544


def test_estimate_editorial_pages_cli_rejects_po(tmp_path: Path) -> None:
    text_root = tmp_path / "POX" / "text"
    text_root.mkdir(parents=True)
    (text_root / "page-001.txt").write_text("<pagina estado=\"com_texto\"></pagina>", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/estimate_editorial_pages.py",
            "--source-root",
            str(text_root),
            "--collection",
            "PO",
        ],
        cwd="/homessddata/Projects/pdfocr",
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "unsupported collection" in completed.stderr


def test_estimate_editorial_pages_cli_sets_and_lists_override(tmp_path: Path) -> None:
    text_root = tmp_path / "PGX" / "text"
    text_root.mkdir(parents=True)
    page = text_root / "page-001.txt"
    page.write_text("<pagina estado=\"com_texto\"></pagina>", encoding="utf-8")
    db_path = tmp_path / "editorial_pages.db"

    set_completed = subprocess.run(
        [
            sys.executable,
            "scripts/estimate_editorial_pages.py",
            "--db",
            str(db_path),
            "--volume-id",
            "PGX",
            "--set-override",
            str(page),
            "201",
            "202",
            "--pretty",
        ],
        cwd="/homessddata/Projects/pdfocr",
        text=True,
        capture_output=True,
        check=False,
    )
    assert set_completed.returncode == 0, set_completed.stderr

    list_completed = subprocess.run(
        [
            sys.executable,
            "scripts/estimate_editorial_pages.py",
            "--db",
            str(db_path),
            "--volume-id",
            "PGX",
            "--list-overrides",
            "--pretty",
        ],
        cwd="/homessddata/Projects/pdfocr",
        text=True,
        capture_output=True,
        check=False,
    )
    assert list_completed.returncode == 0, list_completed.stderr
    payload = json.loads(list_completed.stdout)
    assert payload["overrides"][0]["pages"] == [201, 202]
