import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "poc_scripture_reference_payloads.py"
SPEC = importlib.util.spec_from_file_location("poc_scripture_reference_payloads", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
extract_ocr_header_title = MODULE.extract_ocr_header_title


def test_extract_ocr_header_title_from_combined_block() -> None:
    raw = """
    <pagina estado="com_texto">
      <bloco tipo="cabecalho">101 DE MYSTERIIS 102</bloco>
      <bloco tipo="texto_principal">Corpus.</bloco>
    </pagina>
    """

    assert extract_ocr_header_title(raw) == "DE MYSTERIIS"


def test_extract_ocr_header_title_from_split_blocks() -> None:
    raw = """
    <pagina estado="com_texto">
      <bloco tipo="cabecalho">101</bloco>
      <bloco tipo="cabecalho">DE MYSTERIIS</bloco>
      <bloco tipo="cabecalho">102</bloco>
    </pagina>
    """

    assert extract_ocr_header_title(raw) == "DE MYSTERIIS"


def test_extract_ocr_header_title_preserves_internal_number() -> None:
    raw = """
    <pagina estado="com_texto">
      <bloco tipo="cabecalho">121 HOMILIA III IN GENESIM 122</bloco>
    </pagina>
    """

    assert extract_ocr_header_title(raw) == "HOMILIA III IN GENESIM"


def test_extract_ocr_header_title_discards_nested_nbsp_noise() -> None:
    raw = """
    <pagina estado="com_texto">
      <bloco tipo="cabecalho">&amp;nbsp;&amp;nbsp; 121 DE TRINITATE 122</bloco>
    </pagina>
    """

    assert extract_ocr_header_title(raw) == "DE TRINITATE"


def test_extract_ocr_header_title_stops_when_header_block_swallowed_body() -> None:
    raw = """
    <pagina estado="com_texto">
      <bloco tipo="cabecalho">
        621
        SERMO XLII.
        Veritas et proprietas detalis. Spiritus carnem et ossa non habet.
        SERMO XLIII.
      </bloco>
    </pagina>
    """

    assert extract_ocr_header_title(raw) == "SERMO XLII"


def test_extract_ocr_header_title_rejects_unseparated_body_sized_block() -> None:
    raw = (
        '<pagina><bloco tipo="cabecalho">EPISTOLAE. '
        + "corpus textus " * 40
        + "</bloco></pagina>"
    )

    assert extract_ocr_header_title(raw) is None
