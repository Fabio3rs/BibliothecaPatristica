import pytest

from keyword_integrity import KeywordIntegrityChecker, IntegrityStatus


def test_french_stopword_de():
    checker = KeywordIntegrityChecker()
    ev = checker.check_keyword_integrity("de")
    assert ev.status in {IntegrityStatus.STOPWORD, IntegrityStatus.SUSPECT, IntegrityStatus.VALID}


def test_english_vocab_kingdom():
    checker = KeywordIntegrityChecker()
    ev = checker.check_keyword_integrity("kingdom")
    assert ev.status in {IntegrityStatus.VALID, IntegrityStatus.SUSPECT}


def test_arabic_script_graceful():
    checker = KeywordIntegrityChecker()
    ev = checker.check_keyword_integrity("كتاب")
    assert ev.status != IntegrityStatus.NOISE


def test_syriac_script_graceful():
    checker = KeywordIntegrityChecker()
    ev = checker.check_keyword_integrity("ܡܠܟܘܬܐ")
    assert ev.status != IntegrityStatus.NOISE


def test_canon_name_bypass():
    checker = KeywordIntegrityChecker()
    ev = checker.check_keyword_integrity("Athanasius", is_canon_name=True)
    assert ev.status == IntegrityStatus.VALID


def test_language_hint_forces_latin():
    checker = KeywordIntegrityChecker()
    ev = checker.check_keyword_integrity("episcopatui", language_hint="lat")
    # with hint, VALID is preferred; SUSPECT is acceptable if cltk missing
    assert ev.status in {IntegrityStatus.VALID, IntegrityStatus.SUSPECT}
