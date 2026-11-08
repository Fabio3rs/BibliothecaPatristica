"""Printed-convention tests: source offsets, namespaces and false friends."""
from pathlib import Path

import pytest

from tools.indexing.pg104_opening_grammar import analyze_opening, parse_index_page


TITLE = 'INDEX RERUM ET VERBORUM MEMORABILIUM QUÆ IN PHOTII BIBLIOTHECA REPERIUNTUR.'
NOTICE = 'Revocatur lector ad numeros grandiores textui Latino intermistos.'


def xml(body, title=TITLE, notice=NOTICE):
    return f'<pagina><bloco tipo="cabecalho">{title}</bloco><bloco tipo="nota">{notice}</bloco><bloco tipo="texto_principal">{body}</bloco></pagina>'


def test_notice_is_numbering_evidence_not_a_page_or_named_edition():
    raw = xml('Abaris, 574 a.')
    opening = analyze_opening(raw, 'PG104/opening.txt')
    assert opening['status'] == 'recognized'
    assert opening['namespace'] == 'Photius:Bibliotheca:Latin-internal-number-ab'
    assert opening['edition'] is None and opening['letter_semantics'] is None
    for name in ('title', 'notice'):
        span = opening[name + '_span']
        assert raw[span['char_start']:span['char_end']] == opening[name + '_raw']


@pytest.mark.parametrize('body, refs', [
    ('Abaris, 574 a.', ['574 a.']),
    ('Acacius, 17 b, 81 b.', ['17 b, 81 b.']),
    ('Abderus frater, 150 b. Herculis amasius, 117 b.', ['150 b.', '117 b.']),
    ('Abaris, 574 a, b.', ['574 a, b.']),
])
def test_literal_ab_lists_and_separate_clauses(body, refs):
    raw = xml(body)
    page = parse_index_page(raw, 'opening.txt', 777)
    assert [e['references_raw'] for e in page['entries']] == refs
    for entry in page['entries']:
        assert raw[entry['char_start']:entry['char_end']] == entry['entry_raw']
        assert entry['targets'] == []
    accounting = page['accounting']
    assert accounting['body_characters'] == accounting['claimed_characters'] + accounting['residual_characters']
    assert sum(len(r['raw']) for r in page['residuals']) == accounting['residual_characters']


@pytest.mark.parametrize('body', [
    'Abaris, 17 a Phoca devictus.',
    'Abaris cod. 152 a.',
    'Abaris, ibid. Gloria, 21 a.',
    'Abaris 1448 mortuus, 21 a.',
    'Abaris, 21 a, 22.',
])
def test_ambiguous_clauses_remain_residual(body):
    page = parse_index_page(xml(body), 'opening.txt', 777)
    assert page['entries'] == []
    assert any(body in r['raw'] for r in page['residuals'])


def test_bare_letter_not_silently_inherited():
    page = parse_index_page(xml('Abaris, 574 a, b.'), 'opening.txt', 777)
    entry = page['entries'][0]
    assert entry['reference_tokens'][-1]['number'] is None
    assert 'bare_subdivision_requires_context' in entry['flags']


def test_codex_in_previous_paragraph_does_not_poison_next_clause():
    page = parse_index_page(xml('Agapius hæresiarcha cod., 179.\n \nAgapius philosophus, 352 a.'), '778.txt', 778)
    assert len(page['entries']) == 1
    assert page['entries'][0]['owner_raw'] == 'Agapius philosophus'
    assert any('cod., 179.' in r['raw'] for r in page['residuals'])


def test_header_alone_and_prose_quotation_cannot_create_context():
    assert analyze_opening(xml('Text', notice=''))['status'] == 'not_recognized'
    assert analyze_opening(xml(NOTICE, title='INDICES ANALYTICI.', notice=''))['status'] == 'not_recognized'


def test_continuation_requires_opening_but_new_family_replaces_it():
    opening = analyze_opening(xml('Text'), '777.txt')
    continuation = xml('Abaris, 574 a.', title='INDICES ANALYTICI.', notice='')
    assert parse_index_page(continuation, '778.txt', 778)['entries'] == []
    assert len(parse_index_page(continuation, '778.txt', 778, opening)['entries']) == 1
    syntagma = xml('Abbas presbyter, 135.',
        title='INDEX ANALYTICUS RERUM NOTABILIORUM QUÆ IN SYNTAGMATE INVENIUNTUR.',
        notice='Revocatur lector ad numeros crassiores textui Latino insertos.')
    page = parse_index_page(syntagma, '806.txt', 806, opening)
    assert page['profile'] == 'syntagma_internal_numbers'
    assert page['entries'][0]['reference_namespace'] == 'PG104:Syntagma:Latin-internal-number'
    assert page['entries'][0]['references_raw'] == '135'


def test_ordo_rerum_closes_context():
    opening = analyze_opening(xml('Text'), '777.txt')
    raw = xml('Caput I, 21 a.', title='ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.', notice='')
    page = parse_index_page(raw, '810.txt', 810, opening)
    assert page['opening']['status'] == 'boundary'
    assert page['entries'] == []


def test_real_openings_when_corpus_available():
    root = Path(__file__).resolve().parents[1] / 'teste/PG104/text'
    paths = {n: list(root.glob(f'*-{n}.txt')) for n in (777, 806, 810)}
    if any(len(files) != 1 for files in paths.values()):
        pytest.skip('Local PG104 OCR unavailable')
    openings = {n: analyze_opening(files[0].read_text(), str(files[0])) for n, files in paths.items()}
    assert openings[777]['family'] == 'photius_bibliotheca_ab'
    assert openings[806]['family'] == 'syntagma_internal_numbers'
    assert openings[810]['status'] == 'boundary'
