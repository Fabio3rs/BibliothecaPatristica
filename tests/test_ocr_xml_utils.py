from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patristica_pipeline.ocr_xml_utils import clean_visible_text, parse_ocr_xml_page


def test_clean_visible_text_preserves_non_hyphen_linebreaks() -> None:
    text = "Adam 12\nAbel 14\n\nCaïn 18"

    assert clean_visible_text(text) == text


def test_clean_visible_text_joins_only_linebreak_hyphenation() -> None:
    text = "Meri-\nba 12-14\nGen. 510_2-3\nligne - suite"

    assert clean_visible_text(text) == "Meriba 12-14\nGen. 510_2-3\nligne - suite"


def test_parse_ocr_xml_page_keeps_clean_xml_shape_and_block_lines() -> None:
    xml = """
    <pagina estado="com_texto" tipo="indice">
      <bloco tipo="cabecalho" script="latino" bbox="0,0,10,10">TABLE</bloco>
      <bloco tipo="texto_principal" script="latino" bbox="1,1,9,9">Meri-
ba 12
Adam 14</bloco>
    </pagina>
    """

    page = parse_ocr_xml_page(xml)

    assert page.estado == "com_texto"
    assert page.parse_ok is True
    assert page.body_text == "Meriba 12\nAdam 14"
    assert '<bloco tipo="texto_principal" script="latino" bbox="1,1,9,9">' in page.to_clean_xml()
    assert "Meriba 12\n    Adam 14" in page.to_clean_xml()


def test_parse_ocr_xml_page_regex_fallback_preserves_lines() -> None:
    xml = """
    <pagina estado="com_texto">
      <bloco tipo="texto_principal" script="latino">Alpha 1
Beta 2</bloco>
    """

    page = parse_ocr_xml_page(xml)

    assert page.parse_ok is False
    assert page.body_text == "Alpha 1\nBeta 2"


def test_read_ocr_page_text_cli_outputs_clean_xml(tmp_path: Path) -> None:
    page_path = tmp_path / "page-001.txt"
    page_path.write_text(
        """
        <pagina estado="com_texto">
          <bloco tipo="texto_principal" script="latino">Meri-
ba 12
Adam 14</bloco>
        </pagina>
        """,
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "scripts/read_ocr_page_text.py", str(page_path), "--view", "xml", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["body_text"] == "Meriba 12\nAdam 14"


def test_read_ocr_page_text_cli_outputs_json_array_for_multiple_files(tmp_path: Path) -> None:
    for idx in (1, 2):
        (tmp_path / f"page-{idx:03d}.txt").write_text(
            f'<pagina estado="com_texto"><bloco tipo="texto_principal">Linha {idx}</bloco></pagina>',
            encoding="utf-8",
        )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/read_ocr_page_text.py",
            str(tmp_path / "page-001.txt"),
            str(tmp_path / "page-002.txt"),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert [item["body_text"] for item in data] == ["Linha 1", "Linha 2"]
