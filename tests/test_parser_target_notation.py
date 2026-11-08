import copy
import json

import pytest

from tools.indexing.parser_target_notation import interpret
from tools.indexing.parser_target_experiment import build_header_series, locate_header, markdown_cell, run, sha
from test_parser_target_experiment import fixture


def literal(raw, **kwargs):
    return dict(entry_raw=raw, char_start=100, entry_type='material', flags=[], **kwargs)


def test_page_line_and_leo_are_separate_namespaces():
    raw = 'λόγος 770, 9, Leo 45,\n18.'
    result = interpret(literal(raw), 'page_line_leo_simple')
    assert result['query_text'] == 'λόγος'
    refs = result['interpreted_references']
    assert [(r['number'], r['line_raw'], r['series_key']) for r in refs] == [
        (770, '9', 'petropolitana'), (45, '18', 'leo')]
    for r in refs:
        a, b = r['source_span']
        assert raw[a-100:b-100] == r['ref_raw']
        assert r['line_location_status'] == 'not_located'


@pytest.mark.parametrize('raw', ['λόγος 770, 9, 10.', 'λόγος 770, 9, 780, 11.',
                               'λόγος 770-772, 9.', 'λόγος 0, 9.'])
def test_complex_page_line_notation_not_flattened(raw):
    assert interpret(literal(raw), 'page_line_leo_simple') is None


def test_tome_explicit_and_literal_not_inherited():
    for tome in ['I', 'II']:
        r = interpret(literal(f'Fides animæ et cordis officium est, {tome}, 205.'), 'explicit_tome_page')
        assert r['interpreted_references'][0]['series_key'] == tome
        assert r['interpreted_references'][0]['number'] == 205
    for raw in ['Fides 205.', 'Fides I, 205, 206.', 'Fides ibid.', 'Fides. Alius I, 205.']:
        assert interpret(literal(raw), 'explicit_tome_page') is None


def test_tome_list_preserves_occurrences_ranges_and_inheritance_evidence():
    raw = 'Fides I, 205, 205, 206–208; II, 409, 410.'
    entry = literal(raw)
    entry['flags'] = ['qualified_reference_preserved']
    refs = interpret(entry, 'explicit_tome_list')['interpreted_references']
    assert [(r['tome'], r['number'], r['range_end']) for r in refs] == [
        ('I', 205, None), ('I', 205, None), ('I', 206, 208), ('II', 409, None), ('II', 410, None)]
    assert refs[1]['tome_basis'] == 'same_entry_numeric_list'
    assert refs[3]['tome_basis'] == 'explicit'
    for ref in refs:
        a, b = ref['source_span']
        assert raw[a-100:b-100] == ref['ref_raw']
        a, b = ref['tome_source_span']
        assert raw[a-100:b-100] == ref['tome']


@pytest.mark.parametrize('raw', ['Fides 205, 206.', 'Fides I, 208–206.', 'Fides I, 205 seq.',
    'Fides I, 205, ibid.', 'Fides I, 205–.', 'Fides I, 205. Alius II, 409.',
    'Fides I, 205\nAlius II, 409.', 'Fides I, 205,', 'Fides III, 205.', 'Fides I, 0.'])
def test_tome_lists_do_not_guess_qualifiers_or_other_entries(raw):
    assert interpret(literal(raw), 'explicit_tome_list') is None


def test_po_preserves_subscripts_notes_and_printed_repetitions():
    e = literal('λόγος 29₈, 29₈, 9 n. 1', text_display='λόγος')
    e['flags'] = ['experimental_po_profile', 'subscript_semantics_unverified']
    e['locator_parts'] = []
    for raw, start, kind, page, sub, note in [('29₈', 6, 'page_subscript', '29', '₈', None),
            ('29₈', 11, 'page_subscript', '29', '₈', None),
            ('9 n. 1', 16, 'page_note', '9', None, '1')]:
        e['locator_parts'].append(dict(raw=raw, char_start=100+start, char_end=100+start+len(raw),
                                     kind=kind, page_raw=page, subscript_raw=sub, note_raw=note))
    result = interpret(e, 'po_page_parts')
    refs = result['interpreted_references']
    assert [r['number'] for r in refs] == [29, 29, 9]
    assert refs[0]['subscript_raw'] == '₈'
    assert refs[2]['note_raw'] == '1'
    assert all(r['subscript_interpretation'] == 'unverified' for r in refs)
    bad = copy.deepcopy(e)
    bad['locator_parts'][0]['char_start'] += 1
    with pytest.raises(ValueError, match='span'):
        interpret(bad, 'po_page_parts')
    bad = copy.deepcopy(e)
    bad['locator_parts'][0]['kind'] = 'range'
    assert interpret(bad, 'po_page_parts') is None
    bad = copy.deepcopy(e)
    bad['flags'].append('unattached_note')
    assert interpret(bad, 'po_page_parts') is None


def test_bracket_headers_scoped_to_work_and_never_interpolated(tmp_path):
    text = tmp_path/'PO022'/'text'
    text.mkdir(parents=True)
    for i in range(1, 21):
        header = f'[{i if i < 11 else i-10}]'
        if i == 15:
            header = 'unreadable'
        (text/f'page-{i}.txt').write_text(
            f'<pagina><bloco tipo="cabecalho">{header}</bloco>'
            '<bloco tipo="texto_principal">[5] 999</bloco></pagina>')
    s = build_header_series(dict(volume='PO022', id='work2', physical_start=11, physical_end=20), tmp_path)
    assert [f['physical'] for f in locate_header(s, 4)['files']] == [14]
    assert locate_header(s, 5)['status'] == 'missing_supported_header'
    assert locate_header(s, 999)['files'] == []
    s['nodes'][4].add(16)
    s['supported'][4,16] = {'chain_length': 7}
    assert locate_header(s, 4)['status'] == 'ambiguous_header_pages'
    assert len(locate_header(s, 4)['files']) == 2


def test_cross_volume_tomes_and_unmapped_namespace_never_fallback(tmp_path, monkeypatch):
    args, source, production = fixture(tmp_path)
    profile = json.loads(args.profile.read_text())
    spec = profile['series']
    profile['series'] = [dict(spec, key='I'), dict(spec, volume='PL010', key='II')]
    text = args.root/'PL010'/'text'
    text.mkdir(parents=True)
    for i in range(1,10):
        (text/f'page-{i}.txt').write_text(f'<bloco tipo="texto_principal">{i} body</bloco>')
    title = text/'page-50.txt'
    title.write_text('<bloco tipo="texto_principal">Hilarius TOMUS II.</bloco>')
    profile['series'][1].update(cited_work='Hilarius', cited_edition='Verona',
        binding_evidence=[dict(path='PL010/text/page-50.txt', sha256=sha(title.read_bytes()), quotes=['TOMUS II.'])])
    args.profile.write_text(json.dumps(profile))
    raw = source.read_text()
    literal_entry = 'Rachel formosa, 4, 4.'
    start = raw.index(literal_entry)
    candidate = dict(fingerprint='fp', source=str(source), source_sha256=sha(source.read_bytes()),
        volume='PG069', physical=740,
        entry=dict(id='e', text_display='Fides', entry_raw=literal_entry,
                   char_start=start, char_end=start+len(literal_entry)), numbers=[4,4,4],
        query_text='Fides', interpreted_references=[dict(number=4, series_key=k) for k in ['I','II','leo']])
    monkeypatch.setattr('tools.indexing.parser_target_experiment.load_candidates', lambda *a: ([candidate], {}, {}))
    run(args)
    record = json.loads((args.out/'results.json').read_text())[0]
    a,b,c = record['references']
    assert '/PG069/' in a['files'][0]['file']
    assert '/PL010/' in b['files'][0]['file']
    assert c['status'] == 'namespace_not_mapped' and c['files'] == []
    assert a['index_volume'] == b['index_volume'] == 'PG069'
    assert b['target_volume'] == 'PL010' and b['cross_volume'] is True
    assert a['cross_volume'] is False
    assert c['target_volume'] is None and c['binding_id'] is None
    assert str(title.resolve()) in json.loads((args.out/'sources.json').read_text())
    assert json.loads((args.out/'volume_bindings.json').read_text())['II']['binding_id'] == b['binding_id']
    assert production.read_bytes() == b'untouched'


def test_markdown_summary_does_not_break_on_multiline_literal():
    assert markdown_cell('Leo 45,\n18 | x') == 'Leo 45, 18 \\| x'
