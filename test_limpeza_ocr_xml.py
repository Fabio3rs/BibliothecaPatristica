#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Testes unitários de limpeza OCR com o novo XML."""

import pytest

from test_limpeza_ocr import (
    clean_ocr_text_optimized,
    extract_text_from_ocr_xml,
    classify_page_noise,
    should_drop_page_from_similarity,
)


def test_extract_blocks_latino_only_keeps_blocks_and_ignores_notas():
    xml = """
    <pagina estado="com_texto">
      <bloco tipo="texto_principal" script="latino" bbox="0,0,10,10">Apenas texto latino.</bloco>
      <notas>Esta nota deve ser ignorada.</notas>
    </pagina>
    """

    meta = extract_text_from_ocr_xml(xml)

    assert meta["is_xml"] is True
    assert meta["text"].strip() == "Apenas texto latino."
    assert meta["scripts_kept"] == ["latino"]
    assert meta["total_blocks"] == 1
    assert meta["used_all_scripts"] is False


def test_extract_blocks_few_latino_falls_back_to_all_scripts():
    xml = """
    <pagina estado="com_texto">
      <bloco tipo="texto_principal" script="grego">καλός λόγος</bloco>
      <bloco tipo="texto_principal" script="grego">ἄλλος στίχος</bloco>
      <bloco tipo="texto_principal" script="latino">um</bloco>
    </pagina>
    """

    meta = extract_text_from_ocr_xml(xml)

    assert meta["used_all_scripts"] is True  # latino < 50%
    # contém ambos idiomas concatenados
    assert "καλός" in meta["text"]
    assert "um" in meta["text"]


def test_blocks_without_script_are_classified_as_misto_if_latin_ratio_high():
    xml = """
    <pagina estado="com_texto">
      <bloco tipo="texto_principal">Texto latino sem atributo.</bloco>
      <bloco tipo="texto_principal">Outro parágrafo.</bloco>
    </pagina>
    """

    meta = extract_text_from_ocr_xml(xml)

    assert meta["scripts_kept"] == ["misto", "misto"]
    assert meta["used_all_scripts"] is False


def test_non_latin_only_not_dropped_by_alpha_rules():
    xml = """
    <pagina estado="com_texto">
      <bloco tipo="texto_principal" script="grego">Καλημέρα κόσμε</bloco>
      <bloco tipo="texto_principal" script="grego">Ψηφία</bloco>
    </pagina>
    """

    texto_limpo, meta = clean_ocr_text_optimized(xml)
    quality = classify_page_noise(xml, texto_limpo, meta=meta)

    assert quality["drop_original_embedding"] is False
    assert meta["non_latin_only"] is True


def test_xml_estado_vazio_returns_empty_and_discards():
    xml = """
    <pagina estado="vazio" tipo="capa_ou_guarda">
      <bloco tipo="outro" script="latino">rastro</bloco>
    </pagina>
    """

    texto_limpo, meta = clean_ocr_text_optimized(xml)
    assert texto_limpo == ""
    assert meta["estado"] == "vazio"

    quality = classify_page_noise(xml, texto_limpo, meta=meta)
    assert quality["drop_original_embedding"] is True
    assert quality["reason"] == "xml_pagina_vazia"

    sim = should_drop_page_from_similarity(
        ocr_clean=texto_limpo,
        resumo_pagina_clean="",
        meta=meta,
        page_quality=quality,
    )
    assert sim["drop"] is True
    assert sim["reason"] == "xml_pagina_vazia"


def test_plain_text_legacy_path_unchanged():
    texto = "Linha simples sem XML."
    cleaned, meta = clean_ocr_text_optimized(texto)
    assert cleaned.startswith("Linha simples")
    assert meta["is_xml"] is False


def test_malformed_xml_fallback_does_not_crash():
    xml = """
    <pagina estado="com_texto">
      <bloco script="latino">texto aberto sem fechar
    </pagina>
    """

    texto_limpo, meta = clean_ocr_text_optimized(xml)
    # fallback pode não recuperar texto, mas não deve falhar
    assert meta["is_xml"] is True
    assert meta["parse_ok"] is False
    assert isinstance(texto_limpo, str)


# pytest needs a main guard only for direct execution, omitted here on purpose
