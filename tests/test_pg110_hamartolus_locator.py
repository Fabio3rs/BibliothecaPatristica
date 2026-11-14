"""
tests/test_pg110_hamartolus_locator.py

Testes unitários para o localizador do Glossário de Muralt em PG110 (Georgius Hamartolus).
"""

import pytest
from tools.indexing.pg110_hamartolus_locator import PG110HamartolusLocator

def test_locator_init_and_columns():
    locator = PG110HamartolusLocator()
    assert len(locator.col_to_page) > 1000
    # Verificação de colunas conhecidas
    assert 137 in locator.col_to_page
    assert 399 in locator.col_to_page
    assert 767 in locator.col_to_page
    assert 853 in locator.col_to_page

def test_resolve_migne_columns():
    locator = PG110HamartolusLocator()
    # Teste 1: Coluna com vírgula e linha: "767, 8" -> Página 387/388
    res1 = locator.resolve_item("test-1", "ἀνάγω ἐν", "767, 8", "dummy.txt", {})
    assert res1 is not None
    assert res1['column'] == 767
    assert res1['physical'] in (387, 388)
    assert res1['score'] >= 45.0

    # Teste 2: Coluna com Nota: "399 Nota" -> Página 204
    res2 = locator.resolve_item("test-2", "ἀποδίδωμι τινά τι", "399 Nota", "dummy.txt", {})
    assert res2 is not None
    assert res2['column'] == 399
    assert res2['physical'] == 204

def test_resolve_soursouboules_special_case():
    locator = PG110HamartolusLocator()
    # Teste: "1830" no verbete Soursouboulē
    res = locator.resolve_item("test-soursouboule", "πρωτοσπαθάριος — Σουρσουβούλη", "1830", "dummy.txt", {})
    assert res is not None
    assert res['column'] == 830
    assert res['physical'] == 589

def test_reject_leo_external_citations():
    locator = PG110HamartolusLocator()
    # Citações externas à edição de Bonn devem retornar None (não associar falsamente a PG110)
    res = locator.resolve_item("test-leo", "ἀπασχολέω", "Leo 8, 8", "dummy.txt", {})
    assert res is None

    res2 = locator.resolve_item("test-leo-2", "διακινήσων", "Leo 220, 9", "dummy.txt", {})
    assert res2 is None
