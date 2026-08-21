import pytest

from resumo_serial import is_page_administrative


@pytest.mark.parametrize(
    "summary",
    [
        "Conteúdo administrativo",
        "conteudo administrativo",
        "  CONTEÚDO   ADMINISTRATIVO\n",
        "Conteu\u0301do administrativo",
    ],
)
def test_exact_administrative_marker_is_recognized(summary):
    assert is_page_administrative(summary) is True


@pytest.mark.parametrize(
    "summary",
    [
        "A página descreve atividades administrativas do mosteiro.",
        "Conteúdo administrativo e histórico.",
        "Texto administrativamente relevante.",
        "administrativo",
        "",
    ],
)
def test_descriptive_use_of_administrative_word_is_not_a_marker(summary):
    assert is_page_administrative(summary) is False
