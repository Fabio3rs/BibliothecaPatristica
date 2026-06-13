import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import main2

# Atalho para a função sob teste
escape = main2._escape_bare_ampersands


# ---------------------------------------------------------------------------
# Casos que NÃO devem ser alterados (entidades XML válidas)
# ---------------------------------------------------------------------------


def test_entidade_nomeada_amp_preservada():
    assert escape("a &amp; b") == "a &amp; b"


def test_entidade_nomeada_lt_preservada():
    assert escape("sinal &lt; menor") == "sinal &lt; menor"


def test_entidade_nomeada_gt_preservada():
    assert escape("sinal &gt; maior") == "sinal &gt; maior"


def test_entidade_nomeada_apos_preservada():
    assert escape("it&apos;s") == "it&apos;s"


def test_entidade_nomeada_quot_preservada():
    assert escape("&quot;texto&quot;") == "&quot;texto&quot;"


def test_entidade_decimal_preservada():
    assert escape("&#123;") == "&#123;"


def test_entidade_decimal_simples_preservada():
    assert escape("&#9;") == "&#9;"


def test_entidade_hexadecimal_minuscula_preservada():
    assert escape("&#x1f;") == "&#x1f;"


def test_entidade_hexadecimal_maiuscula_preservada():
    assert escape("&#xABCD;") == "&#xABCD;"


def test_entidade_unicode_hex_preservada():
    assert escape("&#x263A;") == "&#x263A;"


# ---------------------------------------------------------------------------
# Casos que DEVEM ser escapados (& solto — saída típica de LLM)
# ---------------------------------------------------------------------------


def test_ampersand_solto_simples():
    assert escape("Joao & Maria") == "Joao &amp; Maria"


def test_ampersand_solto_no_inicio():
    assert escape("& o começo") == "&amp; o começo"


def test_ampersand_solto_no_fim():
    assert escape("o fim &") == "o fim &amp;"


def test_ampersand_solto_multiplos():
    result = escape("A & B & C")
    assert result == "A &amp; B &amp; C"


def test_ampersand_solto_sem_espaco():
    # & imediatamente seguido de texto que não fecha com ';'
    assert escape("AT&T") == "AT&amp;T"


def test_ampersand_antes_de_numero_sem_ponto_virgula():
    # &#123 sem o ';' final — não é entidade válida, deve escapar
    assert escape("&#123 sem ponto-virgula") == "&amp;#123 sem ponto-virgula"


def test_ampersand_antes_de_texto_sem_ponto_virgula():
    # &nome sem ';' — não é entidade válida
    assert escape("&nome sem ponto-virgula") == "&amp;nome sem ponto-virgula"


# ---------------------------------------------------------------------------
# Casos mistos (válidos e inválidos no mesmo texto)
# ---------------------------------------------------------------------------


def test_mistura_validos_e_invalidos():
    src = "A &amp; B e também C & D"
    assert escape(src) == "A &amp; B e também C &amp; D"


def test_mistura_decimal_e_solto():
    src = "ref &#42; e solto & aqui"
    assert escape(src) == "ref &#42; e solto &amp; aqui"


def test_mistura_hex_e_solto():
    src = "sym &#x2022; e & solto"
    assert escape(src) == "sym &#x2022; e &amp; solto"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_texto_sem_ampersand():
    src = "texto sem nenhum e-comercial"
    assert escape(src) == src


def test_string_vazia():
    assert escape("") == ""


def test_apenas_ampersand():
    assert escape("&") == "&amp;"


def test_ampersands_consecutivos():
    assert escape("&&") == "&amp;&amp;"


def test_xml_completo_com_ampersand_solto():
    """Simula saída real de LLM: XML quase correto mas com & solto no conteúdo."""
    src = '<pagina><bloco>Joao & Maria vieram</bloco></pagina>'
    result = escape(src)
    assert "&amp;" in result
    assert "Joao &amp; Maria" in result
    # Tags não devem ser alteradas
    assert "<pagina>" in result
    assert "<bloco>" in result
