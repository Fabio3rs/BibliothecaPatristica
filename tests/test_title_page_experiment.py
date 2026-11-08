import argparse
import hashlib
import json
import sqlite3

import pytest

from tools.indexing.title_page_experiment import extract_title_page, xref_proposals, run
from tools.indexing.parser_target_experiment import volume_bindings


def title_raw(tome='I', author='HILARII', year='1844'):
    return '<pagina>'+''.join(f'<bloco tipo="texto_principal">{text}</bloco>' for text in [
        'SANCTI', author, 'PICTAVIENSIS EPISCOPI', 'OPERA OMNIA,',
        'JUXTA EDITIONEM MONACHORUM ORDINIS', 'SANCTI BENEDICTI',
        f'TOMUS {tome}.', 'PARISIIS, EXCUDEBAT VRAYET,', f'{year}.'])+'</pagina>'


def parse(raw, volume='PL009', source='page-7.txt'):
    return extract_title_page(raw,source=source,volume=volume,physical=7)


def test_literal_fields_keep_provenance_and_separate_work_tome_and_volume():
    raw = title_raw('II ET ULTIMUS',year='1845')
    r = parse(raw,volume='PL010')
    assert r['status'] == 'structured_candidate'
    assert r['work_tome_roman'] == 'II'
    assert r['collection_tome_roman'] is None
    assert r['collection_volume'] == 'PL010'
    assert r['fields']['publication_year']['text'] == '1845.'
    assert 'Verona' not in json.dumps(r)
    for b in r['blocks']:
        assert raw[b['char_start']:b['char_end']] == b['raw']
        assert hashlib.sha256(b['raw'].encode()).hexdigest() == b['text_sha256']
    assert not r['verified']


def test_collection_title_not_interpreted_as_work_tome():
    r = parse(title_raw().replace('SANCTI','PATROLOGIA LATINA',1))
    assert r['status'] == 'collection_title_requires_review'
    assert r['work_tome_roman'] is None


def test_unsupported_or_conflicting_tomes_remain_pending():
    for raw in [title_raw().replace('OPERA OMNIA,','HOMILIAE'),
                title_raw().replace('</pagina>','<bloco tipo="texto_principal">TOMUS II.</bloco></pagina>')]:
        assert parse(raw)['status'] == 'unrecognized'


def test_xref_group_requires_author_and_keeps_duplicate_tomes_ambiguous():
    a = parse(title_raw())
    b = parse(title_raw('II',year='1845'),volume='PL010')
    c = parse(title_raw(author='CYRILLI'),volume='PG069')
    props = xref_proposals([a,b,c])
    assert len(props) == 1
    assert set(props[0]['targets_by_work_tome']) == {'I','II'}
    assert not props[0]['automatic_application']
    duplicate = parse(title_raw(),volume='PL011',source='other.txt')
    assert xref_proposals([a,b,duplicate])[0]['ambiguous_tomes'] == ['I']


def binding_fixture(tmp_path):
    p = tmp_path/'PL009/text/page-7.txt'
    p.parent.mkdir(parents=True)
    p.write_text(title_raw())
    proof = dict(path='PL009/text/page-7.txt',sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                 quotes=['HILARII','TOMUS I.'],role='work_title_page')
    profile = dict(volume='PL010',notation='explicit_tome_page',notice={'quote':'Romans indicate Hilary tome'},
                   index_physical_start=462,index_physical_end=520)
    spec = dict(key='I',id='Hilarius:I:Verona',volume='PL009',tome='I',cited_work='Hilarius Opera omnia',
                cited_edition='Verona',binding_evidence=[proof],physical_start=1,physical_end=500)
    return profile,spec,p


def test_binding_checks_structured_title_and_retains_independent_notice(tmp_path):
    profile,spec,p = binding_fixture(tmp_path)
    bindings,sources = volume_bindings(profile,[spec],tmp_path)
    b = bindings['I']
    assert b['index_volume'] == 'PL010' and b['target_volume'] == 'PL009'
    assert b['cited_edition'] == 'Verona'
    title = b['binding_evidence'][0]['structured_title_page']
    assert title['work_tome_roman'] == 'I' and title['physical'] == 7
    assert title['fields']['publication_year']['text'] == '1844.'
    assert b['editorial_notice'] == profile['notice']
    assert str(p.resolve()) in sources


def test_same_named_work_cannot_silently_bind_different_title_authors(tmp_path):
    profile,spec,p = binding_fixture(tmp_path)
    second = tmp_path/'PL010/text/page-7.txt'
    second.parent.mkdir(parents=True)
    second.write_text(title_raw('II',author='CYRILLI'))
    other = dict(spec,key='II',tome='II',volume='PL010',id='other')
    other['binding_evidence'] = [dict(role='work_title_page',path='PL010/text/page-7.txt',
        sha256=hashlib.sha256(second.read_bytes()).hexdigest(),quotes=['CYRILLI','TOMUS II.'])]
    with pytest.raises(ValueError,match='conflicting title-page author/work'):
        volume_bindings(profile,[spec,other],tmp_path)


@pytest.mark.parametrize('change', ['missing_evidence','stale','quote_missing','wrong_volume','wrong_tome','key_conflict'])
def test_binding_refuses_missing_changed_or_conflicting_evidence(tmp_path,change):
    profile,spec,p = binding_fixture(tmp_path)
    if change == 'missing_evidence': spec.pop('binding_evidence')
    if change == 'stale': p.write_text(p.read_text()+'changed')
    if change == 'quote_missing': spec['binding_evidence'][0]['quotes'] = ['invented text']
    if change == 'wrong_volume': spec['volume'] = 'PL010'
    if change == 'wrong_tome': spec.update(key='II',tome='II')
    if change == 'key_conflict': spec['tome'] = 'II'
    with pytest.raises(ValueError):
        volume_bindings(profile,[spec],tmp_path)


def test_cli_experiment_only_and_missing_sources_recorded(tmp_path):
    profile,spec,p = binding_fixture(tmp_path)
    args = argparse.Namespace(volume=['PL009'], page=[7,8],front_pages=12,root=tmp_path,
                              experiment_db=tmp_path/'experiment.sqlite',out=tmp_path/'out')
    result = run(args)
    assert result['statuses'] == {'structured_candidate': 1}
    assert result['issues'][0]['physical'] == 8
    con = sqlite3.connect(args.experiment_db)
    assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert con.execute('SELECT count(*) FROM title_page_records').fetchone()[0] == 1
    con.close()
    unrelated=tmp_path/'production.sqlite'
    con=sqlite3.connect(unrelated)
    con.execute('CREATE TABLE valuable(x)')
    con.commit(); con.close()
    before=unrelated.read_bytes()
    args.experiment_db=unrelated
    args.out=tmp_path/'otherout'
    with pytest.raises(ValueError,match='unrelated'):
        run(args)
    assert unrelated.read_bytes()==before
    assert not args.out.exists()


def test_all_volume_survey_parallel_errors_and_order_are_preserved(tmp_path):
    for v in ['PL010','PL009','PO022']:
        text = tmp_path/v/'text'
        text.mkdir(parents=True)
        (text/'page-1.txt').write_text(title_raw('I' if v == 'PL009' else 'II'))
    (tmp_path/'PL010/text/duplicate-1.txt').write_text('duplicate')
    (tmp_path/'PO022/text/page-2.txt').write_bytes(b'\xff')
    (tmp_path/'unrelated/text').mkdir(parents=True)
    args = argparse.Namespace(volume=None, all_volumes=True, workers=3, page=None, front_pages=2,
                              root=tmp_path,experiment_db=tmp_path/'e.sqlite',out=tmp_path/'out')
    result = run(args)
    assert result['volumes'] == 3
    assert result['pages'] == 2
    assert result['issue_counts'] == {'missing_or_ambiguous_source':3,'source_read_or_parse_error':1}
    assert result['volumes_with_structured_candidates'] == 2
    records=json.loads((args.out/'records.json').read_text())
    assert [r['collection_volume'] for r in records] == ['PL009','PO022']
    assert (args.out/'volumes.csv').read_text().splitlines()[1].startswith('PL009,')
    assert 'PL010' in (args.out/'report.md').read_text()


def test_diagnostics_do_not_promote_unsupported_layout():
    from tools.indexing.title_page_experiment import format_diagnostics
    r = parse(title_raw('UNICUS').replace('1844.', '1844'))
    assert r['status'] == 'unrecognized'
    audit = format_diagnostics([r])
    assert audit['counts']['tome_outside_initial_roman_grammar'] == 1
    assert audit['counts']['year_without_final_period'] == 1
    assert r['status'] == 'unrecognized' and not r['verified']


def test_split_volume_directories_are_not_omitted(tmp_path):
    for v in ['PG086.01','PL115-1']:
        text=tmp_path/v/'text'
        text.mkdir(parents=True)
        (text/'page-7.txt').write_text(title_raw())
    args=argparse.Namespace(volume=None,all_volumes=True,workers=2,page=[7],front_pages=12,
                           root=tmp_path,experiment_db=tmp_path/'e.sqlite',out=tmp_path/'out')
    assert run(args)['volumes']==2
    cfg=json.loads((args.out/'config.json').read_text())
    assert cfg['volumes']==['PG086.01','PL115-1']
