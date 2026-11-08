import json
from pathlib import Path
import sqlite3

import pytest

from tools.indexing.bounded_index_recovery import parse, run, VERSION
from tools.indexing.entry_parser_experiment import digest


def spec(start='Victor, 1.', end='Zwinglius, 3.', kind='cabecalho'):
    return {'volume': 'PL058', 'physical': 611, 'section_id': 'gennadius',
            'regions': [{'start_quote': start, 'end_quote': end, 'block_types': [kind]}]}


def test_header_override_is_local_and_does_not_consume_ordo():
    raw = ('<bloco tipo="cabecalho">1213 ORDO RERUM 1214\nVictor, 1.\n'
           'Vigilius, 2,\n  118.\nZ\nZwinglius, 3.</bloco>'
           '<bloco tipo="texto_principal">ORDO RERUM\nEpistola, 999.</bloco>')
    page = parse(raw, spec(), 'fixture')
    assert len(page['entries']) == 3
    vigilius = page['entries'][1]
    assert vigilius['references_raw'] == '2,\n  118.'
    assert 'numeric_list_wrap' in vigilius['flags']
    assert all('ORDO' not in e['entry_raw'] for e in page['entries'])
    assert not page['residuals']
    assert page['accounting']['editorial'] == 1
    for entry in page['entries']:
        assert raw[entry['char_start']:entry['char_end']] == entry['entry_raw']
        for group in entry['reference_groups']:
            assert raw[group['char_start']:group['char_end']] == group['raw']
            s = group['owner_span']
            assert raw[s['char_start']:s['char_end']] == group['owner_text_raw']


def test_rows_preserve_crossrefs_ibid_question_and_open_tail():
    text = ('Abbas, 96. Sacris debet initiari, ibid.\n'
            'Abbenza. Vid. Abbensis.\nAbsalon, an episcopus? 121, 122.\n'
            'Adrumetum, 59. Dicitur Sissa')
    raw = f'<bloco tipo="texto_principal">{text}</bloco>'
    page = parse(raw, spec(text.splitlines()[0], text.splitlines()[-1], 'texto_principal'), 'fixture')
    assert len(page['entries']) == 4
    assert [g['raw'] for g in page['entries'][0]['reference_groups']] == ['96.', 'ibid.']
    assert page['entries'][0]['references_raw'] is None
    assert page['entries'][1]['entry_type'] == 'cross_reference'
    assert page['entries'][1]['reference_groups'] == []
    assert page['entries'][2]['references_raw'] == '121, 122.'
    assert page['entries'][2]['text_raw'].endswith('?')
    assert 'open_fragment_at_region_end' in page['entries'][3]['flags']
    assert page['entries'][3]['quality']['hierarchy'] == 'not_inferred'


def test_numeric_orphans_and_blank_lines_do_not_acquire_owner():
    raw = '<bloco tipo="texto_principal">Victor, 1,\n\n118.\nZwinglius, 3.</bloco>'
    page = parse(raw, spec('Victor, 1,', 'Zwinglius, 3.', 'texto_principal'), 'fixture')
    assert len(page['entries']) == 2
    assert page['residuals'][0]['raw'] == '118.'
    assert all('118' not in e['entry_raw'] for e in page['entries'])


@pytest.mark.parametrize('raw', [
    '<bloco tipo="cabecalho">Victor, 1. Victor, 1. Zwinglius, 3.</bloco>',
    '<bloco tipo="cabecalho">Zwinglius, 3. Victor, 1.</bloco>',
])
def test_ambiguous_or_reversed_boundaries_fail(raw):
    with pytest.raises(ValueError):
        parse(raw, spec(), 'fixture')


def test_isolated_database_and_source_changes(tmp_path):
    root = tmp_path/'teste'
    text = root/'PL058/text'
    images = root/'PL058/images'
    text.mkdir(parents=True)
    images.mkdir()
    source = text/'fixture-611.txt'
    source.write_text('<bloco tipo="cabecalho">Victor, 1.\nZwinglius, 3.</bloco>')
    image = images/'PL058-611.png'
    image.write_bytes(b'reviewed-image')
    config = spec()
    config.update(ocr_name=source.name, image_name=image.name, visual_review=True,
                  image_sha256=digest(image.read_bytes()), reviewed_ocr_sha256=digest(source.read_bytes()))
    profile = tmp_path/'profile.json'
    profile.write_text(json.dumps({'version': VERSION, 'pages': [config]}))
    survey = tmp_path/'survey'
    survey.mkdir()
    (survey/'groups.jsonl').write_text('')
    before = source.read_bytes()
    out = tmp_path/'out'
    summary = run(profile, root, survey, out)
    assert summary['entries'] == 2
    assert summary['db_updates'] == 0
    assert source.read_bytes() == before
    with sqlite3.connect(out/'experiment.sqlite') as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert db.execute('SELECT count(*) FROM candidates').fetchone()[0] == 2
    with pytest.raises(ValueError, match='new output'):
        run(profile, root, survey, out)
    source.write_text(source.read_text().replace('<bloco', '\n<bloco'))
    run(profile, root, survey, tmp_path/'replay')
    page = json.loads((tmp_path/'replay/pages.json').read_text())[0]
    assert page['review']['ocr_changed']
    image.write_bytes(b'changed-image')
    with pytest.raises(ValueError, match='image changed'):
        run(profile, root, survey, tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_real_pl058_regions_keep_nine_header_entries_and_exclude_chronology():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root/'docs/data/pl058_boundary_entry_recovery_v1.json').read_text())
    pages = []
    for p in config['pages']:
        source = root/'teste'/p['volume']/'text'/p['ocr_name']
        pages.append(parse(source.read_text(), p, str(source)))
    assert len(pages[1]['entries']) == 9
    assert pages[1]['entries'][1]['text_raw'] == 'Vigilius martyr'
    assert '118.' in pages[1]['entries'][1]['references_raw']
    assert all('Anno ' not in e['entry_raw'] for e in pages[0]['entries'])
    assert any(e['entry_type'] == 'cross_reference' for e in pages[0]['entries'])
    assert pages[0]['entries'][-1]['entry_raw'].endswith('Dicitur Sissa')
