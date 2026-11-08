from pathlib import Path

from tools.indexing.pl_corpus_index_grammar import parse_region_page, references

CTX = {'s': {'family': 'pl_corpus_author_date_tome_column', 'reference_namespace': 'pl_corpus_tome_column'}}


def parse(text, context=CTX):
    return parse_region_page(text, source='fixture.txt', volume='PL218', physical=101,
                             scopes=[{'span': [0, len(text)], 'section_id': 's'}], section_context=context)


def test_dates_are_not_column_locators():
    result = parse('Abælardus (P.) abbas Ruyensis, 1142, CLXXVIII, 9.\nAbbaudus abbas, 1130, CLXVI, 1340.')
    a, b = result['entries']
    assert a['owner_raw'] == 'Abælardus (P.) abbas Ruyensis'
    assert a['biographical_date_evidence']['raw'] == '1142'
    assert [(x['target_volume'], x['column_value']) for x in a['locators']] == [('PL178', 9)]
    assert b['locators'][0]['column_value'] == 1340


def test_wrapped_lists_and_explicit_tome_change():
    result = parse('Adalbertus archiep Mogunt., 1156, CLXXII, 1355;\nCLXIII, 1386, 1390; CLXXIX, 657.\nAdalgerus episc.\n964, CXXXIV, 916.')
    assert len(result['entries']) == 2
    assert [(x['tome_value'], x['column_value']) for x in result['entries'][0]['locators']] == [(172,1355),(163,1386),(163,1390),(179,657)]
    assert result['entries'][1]['biographical_date_evidence']['raw'] == '964'


def test_cross_reference_not_concatenated_into_next_owner():
    a, b = parse('Autpertus Ambros. Vide Ambrosius.\nAutpertus Cassinensis abbas, 855, CXXIX, 1025.')['entries']
    assert a['owner_raw'] == 'Autpertus Ambros'
    assert not a['locators']
    assert a['textual_cross_reference']['raw'] == 'Vide Ambrosius.'
    assert b['owner_raw'] == 'Autpertus Cassinensis abbas'


def test_volume_range_is_not_fabricated_column():
    e = parse('Hugo de S. Victore Paris., 1142, CLXXV-CLXXVII.')['entries'][0]
    assert e['locators'][0]['relation'] == 'tome_range_without_column'
    assert 'column_value' not in e['locators'][0]


def test_misclassified_header_entries_are_recovered():
    text = '<bloco tipo="cabecalho">173 INDEX CHRONOLOGICUS AUCTORUM. 174\nZ\nZeno episc., 381, XI, 9.</bloco>'
    e = parse(text)['entries'][0]
    assert e['owner_raw'] == 'Zeno episc.'
    assert 'index_row_in_misclassified_ocr_header' in e['flags']


def test_guard_and_provenance():
    text = 'Abbo, 700, LXXXVII, 217, 262.'
    assert not parse(text, {})['entries']
    entry = parse(text)['entries'][0]
    for key, rawkey in [('owner_span','owner_raw'), ('reference_span','reference_raw')]:
        assert text[slice(*entry[key])] == entry[rawkey]
    for atom in entry['locators']:
        assert text[slice(*atom['span'])] == atom['raw']


def test_invalid_roman_does_not_get_silently_corrected():
    e = parse('Algerus Leod. canon., 1110, CCLXXX, 727.')['entries'][0]
    assert 'target_volume' not in e['locators'][0]
    assert e['locators'][0]['uncertainty'] == 'tome_outside_printed_corpus'
    assert not references('Alfredus, 1167, CXCX, 195.', offset=0, source='x')


def test_ibid_only_same_row_preserves_evidence():
    a, b = parse('Abbo, 700, LXXXVII, 217; ibid., 262.\nAdalgerus, ibid.')['entries']
    assert a['locators'][-1]['column_value'] == 262
    assert a['locators'][-1]['inherited_reference_evidence']['raw'] == 'LXXXVII, 217'
    assert b['locators'][0]['uncertainty'] == 'ibid_without_same_row_antecedent'


def test_repeated_ocr_rows_are_preserved_and_flagged():
    entries = parse('Abbo, 700, LXXXVII, 217.\nAbbo, 700, LXXXVII, 217.')['entries']
    assert len(entries) == 2
    assert all('repeated_literal_ocr_row' in e['flags'] for e in entries)


def test_century_not_mistaken_for_tome():
    e = parse('Abbo, sæc. VI, 700, LXXXVII, 217.')['entries'][0]
    assert [(x['tome_value'], x['column_value']) for x in e['locators']] == [(87, 217)]


def test_real_closing_page_mixed_header():
    paths = list(Path('teste/PL218/text').glob('*-121.txt'))
    if not paths:
        return
    raw = paths[0].read_text()
    stop = raw.index('IV.\n\nINDEX CHRONOLOGICUS')
    result = parse(raw[:stop])
    assert len(result['entries']) == 9
    assert result['entries'][-1]['owner_raw'] == 'Zosimus papa'


def test_fragmented_roman_and_preface_not_resolved_as_different_tome():
    for text in ['Durandus, 1088, CXI IX, 1375.', 'Willelmus, 1175, CXC, Præfat. XI, 111.', 'Hugo, 1158, ( LXXXV, 1409.']:
        e = parse(text)['entries'][0]
        assert 'target_volume' not in e['locators'][0]
        assert e['locators'][0]['uncertainty'] == 'preceding_roman_fragment_or_preface_namespace'


def test_semicolon_and_whitespace_pair_separators():
    a, b = parse('Pulcheria Augusta, 455, LIV; 905.\nWolferus canonicus, 1058, CXLI 1159.')['entries']
    assert a['locators'][0]['target_volume'] == 'PL054'
    assert b['locators'][0]['target_volume'] == 'PL141'
