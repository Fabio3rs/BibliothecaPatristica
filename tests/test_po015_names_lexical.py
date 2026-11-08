import json
import sqlite3

import pytest

from scripts.audit_po015_names_lexical import (
    audit_versions, create_search, fts_pages, match_tokens, normalize, page_matches,
    read_history_text, read_page, run, tokens,
)
from scripts.catalog_po015_names import sha


def test_normalization_preserves_raw_and_handles_polytonic_ligatures():
    assert normalize('Ἀαρὼν') == normalize('Ἀαρών') == 'ααρων'
    assert normalize('ᾳ') == 'α'
    assert normalize('œ Æ ﬁ ς ϲ') == 'oe ae fi σ σ'
    raw = 'τῷ Α\u0313βραὰμ.'
    for t in tokens(raw):
        assert raw[t['start']:t['end']] == t['raw']


def test_exact_normalized_and_fuzzy_are_separate():
    assert match_tokens('Ἀαρών', list(tokens('Ἀαρών')))[0]['method'] == 'exact'
    assert match_tokens('Ἀαρών', list(tokens('Ἀαρὼν')))[0]['method'] == 'normalized'
    assert match_tokens('Ζαχρίας', list(tokens('Ζαχαρίας')))[0]['method'] == 'fuzzy'


def test_short_names_substrings_and_cross_script_not_fuzzy_matches():
    assert not match_tokens('Ὥρ', list(tokens('ὥρα')))
    assert not match_tokens('Or', list(tokens('Ordre')))
    assert not match_tokens('ααρον', list(tokens('aaron')))
    assert not match_tokens('Ἀλέξανδρος', list(tokens('Ψευδαλέξανδρος')))


def test_compound_qualifiers_are_required_for_full_match():
    assert not match_tokens('Πτολεμαῖος φιλάδελφος', list(tokens('Πτολεμαῖος εὐεργέτης')))


def test_reader_preserves_spans_separates_notes_and_never_certifies_line(tmp_path):
    p = tmp_path / 'page.txt'
    p.write_text('<bloco tipo="cabecalho">Ἀαρών</bloco>'
                 '<bloco tipo="texto_principal" script="grego">\nἈαρὼν\nδεύτερον\n</bloco>'
                 '<bloco tipo="rodape">1. Ἀαρών</bloco>')
    page = read_page(p)
    hits = page_matches(page, 'Ἀαρών', 'full_label', 0.8)
    assert len(hits) == 2
    assert hits[0]['role'] == 'notes'
    assert hits[1]['ocr_greek_line_ordinals'] == [1]
    for hit in hits:
        assert page['raw'][hit['char_start']:hit['char_end']] == hit['text_raw']


def test_fts_normalizes_greek_and_trigram_is_only_substring_retrieval(tmp_path):
    p = tmp_path / 'page.txt'
    p.write_text('<bloco tipo="texto_principal" script="grego">Ἀαρὼν καὶ Πτολεμαῖος</bloco>')
    with sqlite3.connect(':memory:') as con:
        create_search(con, {str(p): read_page(p)})
        assert fts_pages(con, 'Ἀαρών') == [1]
        assert fts_pages(con, 'Πτολεμαῖος φιλάδελφος') == []
        assert con.execute("SELECT rowid FROM pages_trigram WHERE pages_trigram MATCH ?", ('"τολε"',)).fetchall() == [(1,)]
        assert con.execute("SELECT rowid FROM pages_trigram WHERE pages_trigram MATCH ?", ('"αρ"',)).fetchall() == []


def test_stale_sources_are_rejected_before_writing(tmp_path):
    catalog = tmp_path / 'catalog'
    catalog.mkdir()
    p = tmp_path / 'source.txt'
    p.write_text('changed')
    (catalog / 'entries.json').write_text(json.dumps([
        {'source': str(p), 'source_sha256': 'old', 'references': []}]))
    with pytest.raises(ValueError, match='Stale catalog source'):
        run(catalog, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_export_keeps_mapping_and_qualifier_ambiguity_and_is_readonly(tmp_path):
    catalog = tmp_path / 'catalog'
    catalog.mkdir()
    p = tmp_path / 'page.txt'
    p.write_text('<bloco tipo="texto_principal" script="grego">Πτολεμαῖος εὐεργέτης</bloco>')
    entries = [{'id': 'e1', 'source': str(p), 'source_sha256': sha(p), 'name_raw': 'Πτολεμαῖος φιλάδελφος',
                'physical': 297, 'ordinal': 1, 'references': [{'id': 'r1', 'ref_raw': '98, 10', 'page': 98,
                'lines': [10], 'target_files': [{'file': str(p), 'sha256': sha(p), 'physical': 276}]}]}]
    (catalog / 'entries.json').write_text(json.dumps(entries))
    before = sha(catalog / 'entries.json'), sha(p)
    out = tmp_path / 'out'
    summary = run(catalog, out)
    row = json.loads((out / 'evidence.json').read_text())[0]
    assert row['method'] == 'exact' and 'qualifiers_not_matched' in row['ambiguities']
    assert not row['certified']
    assert summary['new_page_links'] == summary['certified_references'] == 0
    assert before == (sha(catalog / 'entries.json'), sha(p))
    with sqlite3.connect(out / 'lexical.sqlite') as con:
        assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_nested_markup_is_not_searched_as_content(tmp_path):
    p = tmp_path / 'page.txt'
    p.write_text('<bloco tipo="texto_principal"><span title="Ἀαρών">outro</span></bloco>')
    with pytest.raises(ValueError, match='Nested markup'):
        read_page(p)


def test_plain_history_preserves_spans_and_does_not_invent_layout():
    raw = 'texte < καὶ πάλιν\nΣινά > suite'
    page = read_history_text(raw)
    hit = page_matches(page, 'Σινᾶ', 'full_label', 0.8)[0]
    assert hit['role'] == 'unsegmented' and hit['ocr_greek_line_ordinals'] == []
    assert raw[hit['char_start']:hit['char_end']] == 'Σινά'
    with pytest.raises(ValueError, match='Unrecognized markup'):
        read_history_text('<div title="Σινά">other</div>')


def test_history_searches_reencoded_images_but_excludes_corrupt_and_identical_text(tmp_path):
    import hashlib
    images = tmp_path / 'teste/PO015/images'
    images.mkdir(parents=True)
    image = images / 'PO015-217.png'
    image.write_bytes(b'image')
    db = tmp_path / 'history.sqlite'
    text = 'Σινά'
    h = hashlib.sha256(text.encode()).hexdigest()
    with sqlite3.connect(db) as con:
        con.executescript('''CREATE TABLE ocr_engine_versions(id,engine,model);
            INSERT INTO ocr_engine_versions VALUES(1,'tesseract','migne');
            CREATE TABLE ocr_results(id,volume_id,page_num,image_hash,text_hash,text_content,
                is_current,created_at,engine_version_id);''')
        for ident, ih, th in [(1, sha(image), h), (2, 'other', h), (3, sha(image), 'bad')]:
            con.execute('INSERT INTO ocr_results VALUES(?,?,?,?,?,?,?,?,?)',
                        (ident, 'PO015', 217, ih, th, text, 0, 'date', 1))
        con.execute('INSERT INTO ocr_results VALUES(?,?,?,?,?,?,?,?,?)',
                    (4, 'PO015', 217, sha(image), hashlib.sha256(b'current').hexdigest(), 'current', 1, 'date', 1))
    before = sha(db)
    out = tmp_path / 'out'
    out.mkdir()
    rows = [{'ref_id': 'r', 'entry_id': 'e', 'name_raw': 'Σινᾶ', 'ref_raw': '39,6',
             'method': 'absent', 'target_files': [{'physical': 217, 'sha256': hashlib.sha256(b'current').hexdigest()}]}]
    summary = audit_versions(db, rows, out, root=tmp_path)
    assert sha(db) == before
    assert summary['baseline_absent_with_alternative_evidence'] == 1
    assert summary['version_statuses'] == {'searched': 2,
                                          'excluded_text_hash_mismatch': 1, 'identical_to_catalog_target': 1}
    row = json.loads((out / 'version_evidence.json').read_text())[0]
    assert [a['version_id'] for a in row['alternatives']] == [1, 2]
    assert row['alternatives'][1]['page_identity'] == 'volume_page_registration'
    assert not row['certified']
