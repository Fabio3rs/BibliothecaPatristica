from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_check_index_target_locator_examples_reports_ok(tmp_path: Path) -> None:
    text_root = tmp_path / "PGT" / "text"
    text_root.mkdir(parents=True)
    (text_root / "page-001.txt").write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">543 INDEX</bloco>
          <bloco tipo="texto_principal" script="latino">Dionysius Hierotheo.</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )
    request = {
        "volume_id": "PGT",
        "source_root": str(text_root),
        "entries": [
            {
                "entry_id": "e1",
                "query_names": ["Dionysius", "Hierotheo"],
                "page_hint_ints": [543],
                "expected_manual": {
                    "target_file": str(text_root / "page-001.txt"),
                },
            }
        ],
    }
    request_path = tmp_path / "example.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/check_index_target_locator_examples.py",
            "--input",
            str(request_path),
            "--json",
        ],
        cwd="/homessddata/Projects/pdfocr",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report[0]["ok"] == 1
    assert report[0]["fail"] == 0
    assert report[0]["comparisons"][0]["status"] == "ok"
    assert report[0]["comparisons"][0]["section_start_status"] == "ok"
    assert report[0]["comparisons"][0]["editorial_anchor_status"] == "ok"


def test_check_index_target_locator_examples_accepts_multiple_targets(tmp_path: Path) -> None:
    text_root = tmp_path / "PGU" / "text"
    text_root.mkdir(parents=True)
    page_path = text_root / "page-002.txt"
    page_path.write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">295 INDEX</bloco>
          <bloco tipo="texto_principal" script="latino">Udalricus Brigantinus.</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )
    request = {
        "volume_id": "PGU",
        "source_root": str(text_root),
        "entries": [
            {
                "entry_id": "e1",
                "query_names": ["Udalricus Brigantinus"],
                "page_hint_ints": [295],
                "expected_manual": {
                    "target_file": str(text_root / "page-001.txt"),
                    "accepted_targets": [str(page_path)],
                },
            }
        ],
    }
    request_path = tmp_path / "example_multi.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/check_index_target_locator_examples.py",
            "--input",
            str(request_path),
            "--json",
        ],
        cwd="/homessddata/Projects/pdfocr",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report[0]["comparisons"][0]["status"] == "ok"
    assert str(page_path) in report[0]["comparisons"][0]["accepted_section_start_targets"]


def test_check_index_target_locator_examples_separates_section_start_and_anchor(tmp_path: Path) -> None:
    text_root = tmp_path / "PGS" / "text"
    text_root.mkdir(parents=True)
    section_path = text_root / "page-001.txt"
    anchor_path = text_root / "page-002.txt"
    section_path.write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">309 ORATIO XXXVIII 310</bloco>
          <bloco tipo="texto_principal" script="latino">In Theophania sive Natalitia Salvatoris.</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )
    anchor_path.write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="cabecalho" script="latino">663 ORATIO XXXVIII 664</bloco>
          <bloco tipo="texto_principal" script="latino">Epiphaniarum die.</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )
    request = {
        "volume_id": "PGS",
        "source_root": str(text_root),
        "entries": [
            {
                "entry_id": "e1",
                "query_names": ["In Theophania", "ORATIO XXXVIII"],
                "page_hint_ints": [663],
                "expected_manual": {
                    "section_start_file": str(section_path),
                    "editorial_anchor_file": str(anchor_path),
                },
            }
        ],
    }
    request_path = tmp_path / "example_split.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/check_index_target_locator_examples.py",
            "--input",
            str(request_path),
            "--json",
        ],
        cwd="/homessddata/Projects/pdfocr",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    comparison = report[0]["comparisons"][0]
    assert comparison["status"] == "ok"
    assert comparison["section_start_status"] == "fail"
    assert comparison["editorial_anchor_status"] == "ok"
