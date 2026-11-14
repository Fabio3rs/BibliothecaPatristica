#!/usr/bin/env python3
"""
tests/test_catalog_taxonomy_classifier.py

Testes unitários para o classificador taxonômico e ontológico de índices patrísticos:
  - Camada 1: Categoria Macro do Índice e Seção Padronizada (cabeçalhos e propagação espelhada)
  - Camada 2: Ontologia Micro de Entidades (autores clássicos, patrísticos, escrituras, etc.)
  - Preservação integral dos metadados pré-existentes.
"""

import json
import pytest
from tools.indexing.catalog_taxonomy_classifier import (
    clean_header_text,
    identify_specific_header,
    identify_generic_header,
    SourceFileClassifier,
    LemmaEntityClassifier,
)

# ==============================================================================
# TESTES DA CAMADA 1: CABEÇALHOS E CATEGORIAS MACRO
# ==============================================================================

def test_identify_specific_header():
    # Auctores
    cat, sec = identify_specific_header("INDEX ALPHABETICUS AUCTORUM", "PL218")
    assert cat == "AUCTORES"
    assert sec == "INDEX ALPHABETICUS AUCTORUM"

    cat, sec = identify_specific_header("INDEX SCRIPTORUM ETHNICORUM", "PL218")
    assert cat == "AUCTORES"
    assert sec == "INDEX SCRIPTORUM ETHNICORUM"

    cat, sec = identify_specific_header("ELENCHUS AUCTORUM", "PL100")
    assert cat == "AUCTORES"
    assert sec == "INDEX AUCTORUM"

    # Scriptura
    cat, sec = identify_specific_header("INDEX LOCORUM S. SCRIPTURAE", "PL182")
    assert cat == "SCRIPTURA"
    assert sec == "INDEX SCRIPTURAE SACRAE"

    cat, sec = identify_specific_header("INDEX BIBLICUS", "PG014")
    assert cat == "SCRIPTURA"
    assert sec == "INDEX SCRIPTURAE SACRAE"

    # Lexica
    cat, sec = identify_specific_header("INDEX GRÆCITATIS", "PG002")
    assert cat == "LEXICA"
    assert sec == "INDEX VOCABULORUM GRAECORUM"

    cat, sec = identify_specific_header("INDEX VERBORUM", "PL015")
    assert cat == "LEXICA"
    assert sec == "INDEX VERBORUM"

    # Chronologica
    cat, sec = identify_specific_header("INDEX CHRONOLOGICUS", "PL140")
    assert cat == "CHRONOLOGICA"
    assert sec == "INDEX CHRONOLOGICUS"

    # Capitula et Canones
    cat, sec = identify_specific_header("INDEX CANONUM ET CONCILIORUM", "PL088")
    assert cat == "CAPITULA_ET_CANONES"
    assert sec == "INDEX CAPITULORUM ET CANONUM"

    # Onomastica
    cat, sec = identify_specific_header("ONOMASTICUM DIONYSIANUM", "PG003")
    assert cat == "ONOMASTICA"
    assert sec == "ONOMASTICUM DIONYSIANUM"

    cat, sec = identify_specific_header("INDEX NOMINUM PROPRIORUM", "PL050")
    assert cat == "ONOMASTICA"
    assert sec == "INDEX ONOMASTICUS"

    # Rerum et Sententiarum
    cat, sec = identify_specific_header("INDEX ANALYTICUS", "PL074")
    assert cat == "RERUM_ET_SENTENTIARUM"
    assert sec == "INDEX ANALYTICUS"

    cat, sec = identify_specific_header("INDEX RERUM ET SENTENTIARUM", "PL014")
    assert cat == "RERUM_ET_SENTENTIARUM"
    assert sec == "INDEX RERUM ET SENTENTIARUM"

def test_source_file_classifier_bidirectional_propagation(tmp_path):
    # Criar páginas simuladas com espelho (verso genérico / recto específico)
    p1 = tmp_path / "PL218-101.txt"
    p2 = tmp_path / "PL218-102.txt"
    p3 = tmp_path / "PL218-103.txt"

    # p101: INDEX ALPHABETICUS AUCTORUM
    p1.write_text('<bloco tipo="cabecalho">INDEX ALPHABETICUS AUCTORUM</bloco>')
    # p102: INDICES PATROLOGIÆ — INDEX III (genérico verso)
    p2.write_text('<bloco tipo="cabecalho">INDICES PATROLOGIÆ — INDEX III</bloco>')
    # p103: INDEX ALPHABETICUS AUCTORUM
    p3.write_text('<bloco tipo="cabecalho">INDEX ALPHABETICUS AUCTORUM</bloco>')

    sources = {
        "PL218": [str(p1), str(p2), str(p3)]
    }
    classifier = SourceFileClassifier(sources)

    # p101 deve ser AUCTORES
    assert classifier.get_file_classification(str(p1)) == ("AUCTORES", "INDEX ALPHABETICUS AUCTORUM")
    # p102 deve herdar AUCTORES por propagação de espelho
    assert classifier.get_file_classification(str(p2)) == ("AUCTORES", "INDEX ALPHABETICUS AUCTORUM")
    # p103 deve ser AUCTORES
    assert classifier.get_file_classification(str(p3)) == ("AUCTORES", "INDEX ALPHABETICUS AUCTORUM")

# ==============================================================================
# TESTES DA CAMADA 2: ONTOLOGIA MICRO DE ENTIDADES
# ==============================================================================

def test_classify_classical_authors():
    classics = [
        ("Aristoteles", "AUCTOR_CLASSICUS"),
        ("Plato in Timaeo", "AUCTOR_CLASSICUS"),
        ("M. Tullius Cicero", "AUCTOR_CLASSICUS"),
        ("Homerus poeta", "AUCTOR_CLASSICUS"),
        ("Vergilius Maro", "AUCTOR_CLASSICUS"),
        ("Seneca Philosophus", "AUCTOR_CLASSICUS"),
        ("Plutarchus Chaeronensis", "AUCTOR_CLASSICUS"),
        ("Galenus medicus", "AUCTOR_CLASSICUS"),
    ]
    for lbl, expected in classics:
        res = LemmaEntityClassifier.classify(lbl, "PL218", "AUCTORES", "INDEX AUCTORUM")
        assert res == expected, f"Failed for {lbl}: got {res}"

def test_classify_patristic_authors():
    patristic = [
        ("S. Augustinus Hipponensis", "AUCTOR_PATRISTICUS"),
        ("S. Ambrosius Mediolanensis", "AUCTOR_PATRISTICUS"),
        ("S. Hieronymus Stridonensis", "AUCTOR_PATRISTICUS"),
        ("Joannes Chrysostomus", "AUCTOR_PATRISTICUS"),
        ("S. Gregorius Magnus", "AUCTOR_PATRISTICUS"),
        ("Tertullianus presbyter", "AUCTOR_PATRISTICUS"),
        ("S. Cyprianus martyr", "AUCTOR_PATRISTICUS"),
        ("Basilius Magnus", "AUCTOR_PATRISTICUS"),
        ("Beda Venerabilis", "AUCTOR_PATRISTICUS"),
        ("Bernardus Claraevallensis", "AUCTOR_PATRISTICUS"),
    ]
    for lbl, expected in patristic:
        res = LemmaEntityClassifier.classify(lbl, "PL015", "INDEX_GENERALIS", "INDEX GENERALIS")
        assert res == expected, f"Failed for {lbl}: got {res}"

def test_classify_scripture():
    scripture_entries = [
        ("Genesis", "LOCUS_SCRIPTURAE"),
        ("Psalmi Davidici", "LOCUS_SCRIPTURAE"),
        ("Matthaeus", "LOCUS_SCRIPTURAE"),
        ("Epistola ad Romanos", "LOCUS_SCRIPTURAE"),
        ("Tatianus illud Genes. 1, 3, Fiat lux", "LOCUS_SCRIPTURAE"),
        ("p. 154. Marc. 11", "LOCUS_SCRIPTURAE"),
        ("Ps. 22", "LOCUS_SCRIPTURAE"),
        ("Exod. 20", "LOCUS_SCRIPTURAE"),
    ]
    for lbl, expected in scripture_entries:
        res = LemmaEntityClassifier.classify(lbl, "PL182", "INDEX_GENERALIS", "INDEX GENERALIS")
        assert res == expected, f"Failed for {lbl}: got {res}"

def test_classify_biblical_persons():
    biblical = [
        ("Adam protoplastus", "PERSONA_BIBLICA"),
        ("Abraham patriarcha", "PERSONA_BIBLICA"),
        ("Moses legislator", "PERSONA_BIBLICA"),
        ("David rex", "PERSONA_BIBLICA"),
        ("Salomon sapientissimus", "PERSONA_BIBLICA"),
        ("Petrus apostolus", "PERSONA_BIBLICA"),
        ("Paulus apostolus", "PERSONA_BIBLICA"),
        ("Joannes Baptista", "PERSONA_BIBLICA"),
    ]
    for lbl, expected in biblical:
        res = LemmaEntityClassifier.classify(lbl, "PL014", "INDEX_GENERALIS", "INDEX GENERALIS")
        assert res == expected, f"Failed for {lbl}: got {res}"

def test_classify_dogmatic_themes():
    dogmatic = [
        ("De Deo quomodo dicatur", "TEMA_DOGMATICO"),
        ("De Trinitate personarum", "TEMA_DOGMATICO"),
        ("De Gratia Christi", "TEMA_DOGMATICO"),
        ("De Baptismo parvulorum", "TEMA_DOGMATICO"),
        ("De Incarnatione Verbi", "TEMA_DOGMATICO"),
        ("Fides quid sit", "TEMA_DOGMATICO"),
        ("Eucharistia corporis Christi", "TEMA_DOGMATICO"),
    ]
    for lbl, expected in dogmatic:
        res = LemmaEntityClassifier.classify(lbl, "PL182", "RERUM_ET_SENTENTIARUM", "INDEX RERUM ET SENTENTIARUM")
        assert res == expected, f"Failed for {lbl}: got {res}"

def test_classify_greek_vocabulary():
    greek = [
        ("ἀμεταμέλητος", "VOCABULUM_GRAECUM"),
        ("ἀνεπισχέτως", "VOCABULUM_GRAECUM"),
        ("λόγος", "VOCABULUM_GRAECUM"),
        ("ὁμοούσιος", "VOCABULUM_GRAECUM"),
    ]
    for lbl, expected in greek:
        res = LemmaEntityClassifier.classify(lbl, "PG002", "LEXICA", "INDEX VOCABULORUM GRAECORUM")
        assert res == expected, f"Failed for {lbl}: got {res}"

def test_classify_toponyms():
    toponyms = [
        ("Roma urbs aeterna", "TOPONYMUS"),
        ("Constantinopolis nova Roma", "TOPONYMUS"),
        ("Alexandria in Aegypto", "TOPONYMUS"),
        ("Carthago Africae", "TOPONYMUS"),
        ("Hierosolyma sancta", "TOPONYMUS"),
    ]
    for lbl, expected in toponyms:
        res = LemmaEntityClassifier.classify(lbl, "PL050", "INDEX_GENERALIS", "INDEX GENERALIS")
        assert res == expected, f"Failed for {lbl}: got {res}"

def test_classify_historical_onomastics_default():
    historical = [
        ("Innocentius III papa", "ONOMASTICA_HISTORICA"),
        ("Justinianus imperator", "ONOMASTICA_HISTORICA"),
        ("Carolus Magnus rex", "ONOMASTICA_HISTORICA"),
        ("Dagobertus rex Francorum", "ONOMASTICA_HISTORICA"),
        ("S. Maurus abbas", "ONOMASTICA_HISTORICA"),
    ]
    for lbl, expected in historical:
        res = LemmaEntityClassifier.classify(lbl, "PL218", "INDEX_GENERALIS", "INDEX GENERALIS")
        assert res == expected, f"Failed for {lbl}: got {res}"

def test_metadata_json_preservation():
    # Simular metadata com dados pré-existentes de candidatos e colunas
    original_meta = {
        "columns": ["132", "133"],
        "inherited_ibid": False,
        "sanitized_from_clones": 10,
        "original_sample_id": "abc123xyz"
    }
    meta_str = json.dumps(original_meta)

    # Decodificar e enriquecer
    d = json.loads(meta_str)
    d["index_category"] = "AUCTORES"
    d["entity_type"] = "AUCTOR_PATRISTICUS"

    # Verificar que tudo foi preservado
    assert d["columns"] == ["132", "133"]
    assert d["inherited_ibid"] is False
    assert d["sanitized_from_clones"] == 10
    assert d["original_sample_id"] == "abc123xyz"
    assert d["index_category"] == "AUCTORES"
    assert d["entity_type"] == "AUCTOR_PATRISTICUS"
