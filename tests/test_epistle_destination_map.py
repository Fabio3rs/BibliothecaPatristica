import json
from pathlib import Path

import pytest

from tools.indexing.epistle_destination_map import build_destination_map, parse_page, roman_number


def xml(header, body):
    return f'<pagina><bloco tipo="cabecalho">{header}</bloco>{body}</pagina>'


def block(text, kind='texto_principal'):
    return f'<bloco tipo="{kind}">{text}</bloco>'


def parse(raw, **kwargs):
    return parse_page(raw, file='/source.txt', volume='PL216', physical=314, **kwargs)


def test_number_rubric_and_opening_extent():
    raw = xml('REGESTORUM LIB. XV.', block('CIV.\nMARIE REGINÆ.\nGravem dilecti filii.\nCV.\nARCHIEPISCOPO.\nDilectus filius.'))
    anchors, context = parse(raw)
    assert [(a['book'], a['epistle']) for a in anchors] == [(15, 104), (15, 105)]
    assert 'Gravem dilecti' in anchors[0]['opening_text']
    assert 'Dilectus' not in anchors[0]['opening_text']
    for anchor in anchors:
        for span in anchor['spans']:
            assert raw[span['start']:span['end']] == span['raw']


def test_split_rubric_ignores_margins_and_notes():
    raw = xml('REGESTORUM LIB. I.', block('C', 'nota_marginal') + block('III.')
              + block('ABBATIBUS ET ALIIS.') + block('III. ABBATIBUS.', 'nota') + block('Textus.'))
    anchors, _ = parse(raw)
    assert [a['epistle'] for a in anchors] == [3]
    assert 'III. ABBATIBUS.' not in anchors[0]['opening_text']


def test_explicit_range_and_separate_imperial_namespace():
    anchors, _ = parse(xml('SUPPLEMENTUM AD REGESTA INNOCENTII III.', block('CXXI-CXXII.\nAd abbatem ecclesiæ.\nContra gravamina.')))
    assert [(a['series'], a['epistle']) for a in anchors] == [('supplement', 121), ('supplement', 122)]
    assert anchors[0]['spans'] == anchors[1]['spans']
    anchors, _ = parse(xml('REGISTRUM DE NEGOTIO ROMANI IMPERII.', block('LXXVIII. COLONIENSI ARCHIEPISCOPO.\nContumaciam.')))
    assert (anchors[0]['series'], anchors[0]['book']) == ('imperial_register', None)


def test_table_of_contents_cannot_become_destinations():
    anchors, context = parse(xml('QUÆ IN HOC TOMO CONTINENTUR.', block('LIBER DECIMUS QUINTUS.\nI. CAPITULO LINGONENSI. 539')))
    assert anchors == [] and context is None


def test_mixed_page_does_not_apply_new_header_before_section():
    old = {'series': 'register', 'book': 14, 'file': '/previous.txt', 'physical': 313, 'spans': []}
    raw = xml('REGESTORUM LIB. XV.', block('CLX.\nEPISCOPO PARMENSI.\nFinis.')
              + block('REGESTORUM SIVE EPISTOLARUM\nLIBER DECIMUS QUINTUS.')
              + block('I.\nCAPITULO LINGONENSI.\nTextus.'))
    anchors, _ = parse(raw, inherited_context=old)
    assert [(a['book'], a['epistle']) for a in anchors] == [(14, 160), (15, 1)]


def test_appendix_separate_namespace_on_mixed_page():
    raw = xml('REGESTORUM LIB. XIV.', block('CLXV.\nABBATI SANCTI PAULI.\nFinis.')
              + block('APPENDIX AD LIBRUM XIV.') + block('I.\nINNOCENTIUS EPISCOPUS.\nDilecta in Christo.'))
    old = {'series': 'register', 'book': 14, 'file': '/previous.txt', 'physical': 313, 'spans': []}
    anchors, _ = parse(raw, inherited_context=old)
    assert [(a['series'], a['book'], a['epistle']) for a in anchors] == [('register', 14, 165), ('appendix', 14, 1)]


def test_volume_and_physical_gap_reset_context(tmp_path):
    path = tmp_path / 'teste' / 'PL214' / 'text'
    path.mkdir(parents=True)
    (path / 'a-001.txt').write_text(xml('REGESTORUM LIB. I.', block('I.\nABBATI SANCTI.\nTextus.')))
    (path / 'a-003.txt').write_text(xml('PONTIFICATUS ANNO I.', block('II.\nABBATI SANCTI.\nTextus.')))
    assert len(build_destination_map(tmp_path, ('PL214',))) == 1


def test_roman_validation():
    assert roman_number('CIV') == 104
    assert roman_number('IIX') is None


def test_real_visual_fixture_gravem_dilecti():
    file = Path('teste/PL216/text/ccef0cc5-f7b1-433f-8bd3-fd747a0072ab-314.txt')
    if not file.exists():
        return
    anchors, _ = parse(file.read_text())
    target = next(a for a in anchors if a['epistle'] == 104)
    assert target['book'] == 15 and 'Gravem dilecti' in target['opening_text']
    assert 'Dilectus filius' not in target['opening_text']


@pytest.mark.parametrize('case', json.loads(Path('tests/fixtures/pl217_destination_visual.json').read_text())['cases'],
                         ids=lambda case: case['case_id'])
def test_reviewed_destination_openings(case):
    target = case['destination']
    file = Path(target['ocr_path'])
    if not file.exists():
        pytest.skip('Local corpus fixture unavailable')
    physical = target['physical_page']
    # Context is obtained from actual adjacent headers, not injected from gold.
    context = None
    anchors = []
    for page in range(physical - 2, physical + 1):
        paths = list(file.parent.glob(f'*-{page:03}.txt'))
        if len(paths) != 1:
            continue
        source = paths[0]
        anchors, context = parse_page(source.read_text(), file=str(source.resolve()),
                                      volume=target['volume_id'], physical=page,
                                      inherited_context=context)
    series = {'regular': 'register', 'book_appendix': 'appendix'}.get(case['namespace'], case['namespace'])
    matching = [a for a in anchors if a['series'] == series and a['book'] == case['book']
                and a['epistle'] == int(case['epistle'])]
    assert matching
    assert any(target['quote'] in a['opening_text'] for a in matching)


@pytest.mark.parametrize(('raw', 'number'), [('CCCCXCVI', 496), ('DCCCC', 900),
                                             ('LXXXX', 90), ('VIIII', 9)])
def test_additive_roman_numerals_are_literal_editorial_variants(raw, number):
    assert roman_number(raw) == number
    anchors, _ = parse(xml('REGESTORUM LIB. I.', block(f'{raw}.\nEPISCOPO PARMENSI.\nTextus.')))
    assert anchors[0]['epistle'] == number
    assert 'additive_roman_number' in anchors[0]['uncertainties']


@pytest.mark.parametrize('raw', ['IIX', 'CCCCC', 'VV', 'IC', 'IL', 'XXXXX'])
def test_invalid_roman_not_repaired_by_additive_rule(raw):
    assert roman_number(raw) is None


def test_explicit_book_title_retains_conflicting_running_head_as_alternative():
    _, context = parse(xml('REGESTORUM LIB. II.',
                           block('REGESTORUM SIVE EPISTOLARUM\nLIBER SECUNDUS.')))
    raw = xml('REGESTORUM LIB. I.', block('LXXXIV.\nREGI FRANCORUM.\nVentilata diutius.'))
    anchors, context = parse(raw, inherited_context=context)
    assert {(a['book'], a['epistle']) for a in anchors} == {(1, 84), (2, 84)}
    assert all('running_header_conflicts_with_explicit_book_title' in a['uncertainties'] for a in anchors)
    second = next(a for a in anchors if a['book'] == 2)
    assert second['evidence']['context']['spans'][0]['raw'].endswith('LIBER SECUNDUS.')
    assert 'LIB. I.' in second['evidence']['conflicting_running_header']['spans'][0]['raw']
    anchors, _ = parse(xml('REGESTORUM LIB. III.',
                          block('REGESTORUM SIVE EPISTOLARUM\nLIBER TERTIUS.')
                          + block('I.\nEPISCOPO PARMENSI.\nTextus.')), inherited_context=context)
    assert [(a['book'], a['epistle']) for a in anchors] == [(3, 1)]


def test_internal_letter_heading_classified_as_cabecalho_is_retained():
    raw = xml('REGESTORUM LIB. I.', block('XXII.\nMEDIOLANENSI ARCHIEPISCOPO.', 'cabecalho')
              + block('Pastoralis officii debitum.'))
    anchors, _ = parse(raw)
    assert [(a['book'], a['epistle']) for a in anchors] == [(1, 22)]
    assert 'Pastoralis officii' in anchors[0]['opening_text']
    assert 'REGESTORUM' not in anchors[0]['opening_text']
    for span in anchors[0]['spans']:
        assert raw[span['start']:span['end']] == span['raw']


def test_abbreviated_letter_formula_across_blocks():
    raw = xml('REGESTORUM LIB. II.', block('LXXXV.\n(Datum eadem.)')
              + block('In eundem modum comitissæ et filio ejus.'))
    anchors, _ = parse(raw)
    assert [(a['book'], a['epistle']) for a in anchors] == [(2, 85)]
    assert 'abbreviated_letter_without_repeated_recipient' in anchors[0]['uncertainties']
    # A numbered prose paragraph is insufficient to claim a letter opening.
    rejected, _ = parse(xml('REGESTORUM LIB. II.', block('LXXXV.\nDe hac re dicendum est.')))
    assert rejected == []


@pytest.mark.parametrize(('page', 'epistles'), [(133, [22]), (354, [496, 497, 498, 499]),
                                               (442, [84, 85, 86, 87, 88])])
def test_real_books12_format_recovery(page, epistles):
    files = list(Path('teste/PL214/text').glob(f'*-{page:03}.txt'))
    if len(files) != 1:
        pytest.skip('Local corpus fixture unavailable')
    context = None
    for physical in range(page - 2, page + 1):
        sources = list(files[0].parent.glob(f'*-{physical:03}.txt'))
        if len(sources) != 1:
            continue
        source = sources[0]
        anchors, context = parse_page(source.read_text(), file=str(source), volume='PL214',
                                      physical=physical, inherited_context=context)
    assert set(epistles).issubset({a['epistle'] for a in anchors})


def test_forward_running_head_ends_old_title_even_when_new_title_is_absent():
    _, context = parse(xml('REGESTORUM LIB. III.',
                           block('REGESTORUM SIVE EPISTOLARUM\nLIBER TERTIUS.')))
    anchors, context = parse(xml('REGESTORUM LIB. V.',
                                block('I.\nEPISCOPO PARMENSI.\nTextus.')), inherited_context=context)
    assert [(a['book'], a['epistle']) for a in anchors] == [(5, 1)]
    assert not context.get('section_context')
    # A later defective header must not revive the obsolete title III.
    anchors, _ = parse(xml('REGESTORUM LIB. II.',
                          block('II.\nEPISCOPO PARMENSI.\nTextus.')), inherited_context=context)
    assert [(a['book'], a['epistle']) for a in anchors] == [(2, 2)]
