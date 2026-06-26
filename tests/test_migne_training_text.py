from __future__ import annotations

import sqlite3
from pathlib import Path

import scripts.migne_training_text as mtt


def test_classify_unicode_script() -> None:
    assert mtt.classify_unicode_script("Salve κόσμε") == "misto"
    assert mtt.classify_unicode_script("κόσμε") == "grego"
    assert mtt.classify_unicode_script("Salve mundo") == "latino"
    assert mtt.classify_unicode_script("12345") == "desconhecido"


def test_script_priority_order() -> None:
    lines = [
        "Salve mundo",
        "κόσμε",
        "Salve κόσμε",
    ]
    ordered = sorted(lines, key=lambda line: mtt.script_priority(mtt.classify_unicode_script(line)))
    assert ordered == ["κόσμε", "Salve κόσμε", "Salve mundo"]


def _make_test_db(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE ocr_results (is_current INTEGER, text_content TEXT)"
        )
        con.execute(
            "INSERT INTO ocr_results VALUES (?, ?)",
            (
                1,
                """
                <pagina>
                    <bloco script="latino">Salve mundo</bloco>
                    <bloco script="grego">κόσμε</bloco>
                    <bloco script="misto">Salve κόσμε</bloco>
                </pagina>
                """,
            ),
        )
        con.commit()
    finally:
        con.close()


def test_prioritize_greek_mixed_with_dry_run(tmp_path, capsys) -> None:
    db_path = tmp_path / "ocr_versions.db"
    _make_test_db(db_path)

    out_path = tmp_path / "training.txt"

    import sys

    old_argv = sys.argv
    sys.argv = [
        "migne_training_text.py",
        "--db",
        str(db_path),
        "--out",
        str(out_path),
        "--dry-run",
        "--prioritize-greek-mixed",
    ]
    try:
        mtt.main()
    finally:
        sys.argv = old_argv

    captured = capsys.readouterr().out
    assert "Linhas extraídas: 2" in captured


def test_greek_mixed_only_filters_latin(tmp_path, capsys) -> None:
    db_path = tmp_path / "ocr_versions.db"
    _make_test_db(db_path)

    out_path = tmp_path / "training.txt"

    import sys

    old_argv = sys.argv
    sys.argv = [
        "migne_training_text.py",
        "--db",
        str(db_path),
        "--out",
        str(out_path),
        "--greek-mixed-only",
    ]
    try:
        mtt.main()
    finally:
        sys.argv = old_argv

    capsys.readouterr()
    content = out_path.read_text(encoding="utf-8").splitlines()
    assert content == ["Salve κόσμε"]
