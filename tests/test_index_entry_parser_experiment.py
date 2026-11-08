import json
import sqlite3
from pathlib import Path

import pytest

from tools.indexing.entry_parser_experiment import parse_page, display, db_diff, check_examples

ROOT = Path(__file__).resolve().parents[1]


def body(text):
    return f'<bloco tipo="texto_principal" script="latino">{text}</bloco>'


def test_wrapped_phrase_and_two_units_on_one_line_preserve_exact_spans():
    raw = body('Sepulta in Bethlehem, 207. Rachel formosa\ntypus Ecclesiæ, 123, 124, 130, 184.')
    p = parse_page(raw)
    assert len(p['entries']) == 2
    assert p['entries'][1]['text_display'] == 'Rachel formosa typus Ecclesiæ'
    assert p['entries'][1]['references_raw'] == '123, 124, 130, 184'
    for e in p['entries']:
        assert raw[e['char_start']:e['char_end']] == e['entry_raw']
        s = e['reference_span']
        assert raw[s['char_start']:s['char_end']] == e['references_raw']


def test_repeated_reference_occurrences_are_not_deduplicated():
    e = parse_page(body('Rachel, 123, 123, 124.'))['entries'][0]
    assert e['references_raw'] == '123, 123, 124'


@pytest.mark.parametrize('refs', ['82 et seqq.', '12-18', 'ibid.', '198 seq., 203'])
def test_open_ranges_and_ibid_remain_literal(refs):
    raw = f'Abraham, {refs.rstrip(".")}.'
    e = parse_page(body(raw))['entries'][0]
    assert e['entry_raw'] == raw
    assert 'qualified_reference_preserved' in e['flags']
    assert 'page_ref_int' not in e


def test_cross_reference_not_material_and_abbreviations_not_sentence_boundaries():
    p = parse_page(body('Vide Christum. S. Cyrillus, 23.'))
    assert [e['entry_type'] for e in p['entries']] == ['cross_reference', 'material']
    assert p['entries'][0]['references_raw'] is None
    assert p['entries'][1]['text_display'] == 'S. Cyrillus'


def test_wrapped_remission_does_not_swallow_following_entry():
    p = parse_page(body('Passionis Christi meditatio. Vide Chri-\nstus. Pastores sunt patres, 121.'))
    assert len(p['entries']) == 2
    assert p['entries'][0]['entry_type'] == 'cross_reference'
    assert p['entries'][0]['references_raw'] is None
    assert p['entries'][1]['text_display'] == 'Pastores sunt patres'


def test_missing_period_does_not_create_a_confident_join(tmp_path):
    db = tmp_path / 'db.sqlite'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE alphabetical_entries(entry_key,entry_order,entry_raw,lemma_raw,context_raw,heading_letter,index_entry_source_ocr_file)')
    c.executemany('INSERT INTO alphabetical_entries VALUES (?,?,?,?,?,?,?)', [
        ('a', 1, 'Templum, 177', 'Templum', None, 'T', 'source'),
        ('b', 2, 'Tempus, 78.', 'Tempus', None, 'T', 'source')])
    c.commit()
    c.close()
    p = parse_page(body('Templum, 177\nTempus, 78.'), source='source')
    d = db_diff([p], db)[0]
    assert d['literal_alignment'] == 'split_in_database_candidate'
    assert d['status'] == 'parser_ambiguity_requires_review'
    assert 'numeric_text_before_reference_group' in d['flags']


def test_block_continuation_stays_pending():
    p = parse_page(body('Baptismus, 204. Christiani conversi ad occidentem, cum e contrario Deum') +
                   body('precarentur spectantes orientem. Baptismus peccata remittit, 144.'))
    assert len(p['residuals']) == 1
    assert 'leading_continuation_requires_review' in p['entries'][1]['flags']
    assert p['entries'][1]['context_candidate_id'] is None


def test_context_is_a_reviewable_link_and_blank_paragraph_resets_it():
    p = parse_page(body('Mandragoræ esus, 135. Data Racheli qui\nsignificet, 137.\n\nQuid significet, 14.'))
    assert p['entries'][1]['context_candidate_id'] == p['entries'][0]['id']
    assert 'context_requires_review' in p['entries'][1]['flags']
    assert p['entries'][2]['context_candidate_id'] is None


def test_orphan_tail_is_preserved_without_guessing_next_page():
    p = parse_page(body('Pax, 160. Rachel formosa'))
    assert len(p['entries']) == 1
    assert p['residuals'][0]['raw'].strip() == 'Rachel formosa'
    assert p['coverage']['syntactic_fraction'] < 1


def test_boundary_within_block_and_section_change():
    p = parse_page(body('Pax, 160.\nORDO RERUM\nLiber, 42.') + body('Aliud, 44.'))
    assert len(p['entries']) == 1
    assert any(e['kind'] == 'boundary' for e in p['events'])
    assert any(r['reason'] == 'outside_owned_boundary' for r in p['residuals'])
    p = parse_page(body('Pax, 160.') + '<bloco tipo="cabecalho">INDEX AD S. CYRILLI FRAGMENTA</bloco>' + body('Aqua, 241.'))
    assert p['entries'][0]['section_candidate'] != p['entries'][1]['section_candidate']


def test_xml_letters_are_events_not_initials_of_every_entry():
    p = parse_page('<bloco tipo="nota_marginal">R</bloco>' + body('Rachel, 123. Sepulta, 207.'))
    assert [e['observed_letter'] for e in p['entries']] == ['R', 'R']


def test_type_alias_and_no_bbox_dependency():
    a = '<bloco type="texto_principal" bbox="invented">Pax, 160.</bloco>'
    assert parse_page(a)['entries'][0]['references_raw'] == '160'


def test_no_universal_page_line_or_tome_interpretation():
    p = parse_page(body('Pax Leo 770, 9. Aliud I, 757.'))
    assert all(e['flags'] for e in p['entries'])
    assert all('page_ref_int' not in e for e in p['entries'])
    with pytest.raises(ValueError, match='PG069'):
        parse_page(body('Pax, 770, 9.'), volume='PG110')


def test_diff_only_proposes_join_and_does_not_write_sqlite(tmp_path):
    db = tmp_path / 'db.sqlite'
    c = sqlite3.connect(db)
    c.execute('CREATE TABLE alphabetical_entries(entry_key,entry_order,entry_raw,lemma_raw,context_raw,heading_letter,index_entry_source_ocr_file)')
    c.executemany('INSERT INTO alphabetical_entries VALUES (?,?,?,?,?,?,?)', [
        ('a', 1, 'Rachel formosa', 'Rachel formosa', None, 'S', 'source'),
        ('b', 2, 'typus Ecclesiæ, 123.', 'typus Ecclesiæ', None, 'S', 'source')])
    c.commit()
    c.close()
    before = db.read_bytes()
    p = parse_page('<bloco tipo="nota_marginal">R</bloco>' + body('Rachel formosa\ntypus Ecclesiæ, 123.'), source='source')
    d = db_diff([p], db)
    assert db.read_bytes() == before
    assert d[0]['status'] == 'split_in_database_candidate'
    assert d[0]['letter_disagreement'] and not d[0]['application_supported']


def test_visually_checked_pg069_source_cases():
    source = next((ROOT / 'teste/PG069/text').glob('*-743.txt'))
    p = parse_page(source.read_text(), source=str(source), physical=743)
    checks = check_examples([p], ROOT / 'tests/fixtures/pg069_parser_checks.json')
    assert checks['checked'] == checks['exact_segmentation'] == checks['literal_reference_assignment'] == 10
    assert checks['line_baseline_full_unit'] < 10
    rachel = next(e for e in p['entries'] if e['text_display'].startswith('Rachel formosa'))
    assert rachel['observed_letter'] == 'R'
    death = next(e for e in p['entries'] if e['text_display'].startswith('Rachel in partu'))
    assert death['text_display'] == 'Rachel in partu Benjamin est mortua'
    assert 'Benja-\nmin' in death['entry_raw']
