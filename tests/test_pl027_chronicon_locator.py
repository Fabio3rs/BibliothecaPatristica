#!/usr/bin/env python3
"""
tests/test_pl027_chronicon_locator.py

Testes unitários para o localizador do Chronicon em PL027 e PL051:
  - Des-hifenização de quebras de linha em consules e nomes próprios
  - Extração de radicais com desinências flexionais latinas e normalização fonética
  - Herança de lemas-pai para subentradas dependentes (Consul, Interficitur)
  - Resolução precisa de anos abraâmicos (<= 2393 em PL027, > 2393 em PL051)
  - Correção de corrupções clássicas de OCR em dígitos de anos abraâmicos
"""

import pytest
from tools.indexing.pl027_chronicon_locator import (
    dehyphenate,
    extract_stems,
    PL027ChroniconLocator
)

def test_dehyphenate():
    raw = "VALENTINIANO AUGUSTO V ET ANA-\n    THOLIO COSS."
    res = dehyphenate(raw)
    assert "ANATHOLIO" in res
    assert "ANA-" not in res

def test_extract_stems():
    stems = extract_stems("Ascalonitarum æra")
    assert "ascalonit" in stems

    stems_albinus = extract_stems("Albinus consul")
    assert "albin" in stems_albinus

    stems_cynezius = extract_stems("Cynezius consul")
    assert "cynegi" in stems_cynezius or "cynezi" in stems_cynezius

    stems_fravita = extract_stems("Fravita consul")
    assert "fravit" in stems_fravita

def test_parent_headword_inheritance():
    locator = PL027ChroniconLocator()
    # Simular texto do índice na página 582 de PL027
    locator.source_lines["dummy_582.txt"] = [
        "Aetius ex agricola magister militum, 2445.",
        "Consul, 2448, 2453. Deposita potestate, vivit privatus, 2448.",
        "Profugit ad Hunnos, ibid."
    ]
    parent = locator.get_parent_headword("dummy_582.txt", "Consul")
    assert "Aetius" in parent

def test_resolve_jerome_chronicon_entry():
    locator = PL027ChroniconLocator()
    # Achilleus Ægyptum obtinet (ref 3301 -> OCR para 2301, Diocletianus em PL027)
    res = locator.resolve_entry(
        item_id="item_achilleus",
        label="Achilleus Ægyptum obtinet",
        ref_raw="3301",
        source_path="dummy.txt"
    )
    assert res is not None
    assert res["target_volume"] == "PL027"
    assert "achille" in res["matched_stems"]

def test_resolve_prosper_continuation_entry():
    locator = PL027ChroniconLocator()
    # Alaricus Gothorum dux (ref 2416 -> PL051 Próspero)
    res = locator.resolve_entry(
        item_id="item_alaricus",
        label="Alaricus Gothorum dux",
        ref_raw="2416",
        source_path="dummy.txt"
    )
    assert res is not None
    assert res["target_volume"] == "PL051"
    assert "alaric" in res["matched_stems"]

def test_resolve_with_parent_inheritance():
    locator = PL027ChroniconLocator()
    # Interficitur sob Athaulfus rex Gothorum (ref 2451 -> PL051)
    locator.source_lines["teste/PL027/text/83f9d54b-52f6-497d-9156-2f1f12cdac64-584.txt"] = [
        "Athaulfus rex Gothorum, 2128.",
        "Interficitur, 2451."
    ]
    res = locator.resolve_entry(
        item_id="item_interficitur",
        label="Interficitur",
        ref_raw="2451",
        source_path="teste/PL027/text/83f9d54b-52f6-497d-9156-2f1f12cdac64-584.txt"
    )
    assert res is not None
    assert res["target_volume"] == "PL051"
    assert "ataulf" in res["matched_stems"] or "athaulf" in res["matched_stems"]

def test_resolve_ocr_digit_confusion():
    locator = PL027ChroniconLocator()
    # Bacauda com ref 2503 (OCR de 2303) deve resolver em PL027 na página 250
    res = locator.resolve_entry(
        item_id="item_bacauda",
        label="Bacauda",
        ref_raw="2503",
        source_path="dummy.txt"
    )
    assert res is not None
    assert res["target_volume"] == "PL027"
    assert "bacaud" in res["matched_stems"]
