from pathlib import Path
import argparse
import json
import sqlite3
from copy import deepcopy

import pytest

from tools.indexing.entry_parser_literal_rows import parse_page
from tools.indexing import regional_row_experiment as runner
from tools.indexing.entry_parser_bulk import open_experiment
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.index_conservation_audit import coverage, anomalies


def parse(text, volume='PG046', scopes=None):
    raw = f'<bloco tipo="texto_principal">{text}</bloco>'
    scopes = scopes or [{'span':[0,len(raw)],'section_id':'s'}]
    return raw, parse_page(raw,source='fixture',physical=1,volume=volume,scopes=scopes)


def test_semicolon_ranges_and_letter_subdivisions_do_not_split_entries():
    raw,p = parse('Quadraginta martyrum laudes, III, 730 et seqq.; 758 et seqq.; 774 et seqq.\n'
                  'Quaternarius numerus quid denotet, III, 1154, C. D.')
    assert len(p['entries']) == 2
    assert p['entries'][0]['references_raw'] == 'III, 730 et seqq.; 758 et seqq.; 774 et seqq.'
    assert p['entries'][1]['references_raw'] == 'III, 1154, C. D.'
    for e in p['entries']:
        assert raw[e['char_start']:e['char_end']] == e['entry_raw']
        assert 'target_file' not in e


def test_internal_clauses_keep_distinct_owners_and_do_not_flatten():
    raw,p = parse('Quies est exitium animæ, II, 203, A. Quiete et motu res oriuntur, I, 127, D.')
    e, = p['entries']
    assert e['references_raw'] is None and e['reference_span'] is None
    assert [(g['owner_text_raw'],g['raw']) for g in e['reference_groups']] == [
        ('Quies est exitium animæ','II, 203, A.'), ('Quiete et motu res oriuntur','I, 127, D.')]
    assert not anomalies(raw,e,{e['id']})
    audit = coverage(raw,[{'span':[0,len(raw)]}],p['entries'])
    assert all(n['status']=='reference_group' for n in audit['numeric_tokens'])
    assert audit['characters']['residual'] == 0
    e['reference_groups'][1]['owner_span']['text_sha256']='stale'
    assert 'reference_owner_span_invalid_or_outside_entry' in {f['kind'] for f in anomalies(raw,e,{e['id']})}


def test_unowned_number_and_duplicate_incipits_are_not_reassigned():
    raw,p = parse('Cum inter vos, I, 285.\n6.\nCum inter vos, V, 5.',volume='PL217')
    assert len(p['entries']) == 2
    assert p['entries'][0]['id'] != p['entries'][1]['id']
    assert p['residuals'][0]['raw'] == '6.'
    assert p['entries'][0]['references_raw'] == 'I, 285.'


@pytest.mark.parametrize('literal',['Suppl. 196.','Registr. ad neg. imp. 105.',
                                    'Registr. de neg. imp. 178.','XVI, append. 6.'])
def test_epistle_namespaces_are_preserved_without_page_assumption(literal):
    _,p = parse('Cum nostris dudum. '+literal,volume='PL217')
    assert p['entries'][0]['references_raw'] == literal
    assert p['entries'][0]['reference_groups'][0]['namespace_status']=='literal_uninterpreted'


@pytest.mark.parametrize('literal',[
    'Supplement. 166.', 'Supplem. 89 bis.', 'Supplementum 59.',
    'XIV. Append. 11.', 'XVI, Appen-\ndix 7.',
    'Registr. de imp. 180.', 'Registr. de neg.\nImp. 26.',
    'Registr. de neg\n. imp. 127.', 'XIV.\nAppend. 2.',
])
def test_epistle_observed_variants_and_namespace_wraps(literal):
    raw,p = parse('Graven contra te. '+literal,volume='PL217')
    e, = p['entries']
    assert not p['residuals']
    assert e['references_raw'] == literal
    assert e['text_raw'] == 'Graven contra te.'
    assert not anomalies(raw,e,{e['id']})
    assert raw[e['char_start']:e['char_end']] == e['entry_raw']


@pytest.mark.parametrize('text',[
    'His quae ad ecclesiarum, XV,\n157.',
    'Illa te cordis. Registr. de neg. imp.\n23.',
    'Insinuante venerabili. Supplem.\n154.',
    'Fragmentum. XIV.\n\nAppend. 2.',
    'Fragmentum. Registr. de neg.\n\nImp. 26.',
    'Habuisse bajulos, 15, 86.',
    'Graven ad nos. XII, 115.:',
])
def test_epistle_ambiguous_numbers_and_gaps_remain_pending(text):
    _,p = parse(text,volume='PL217')
    assert not p['entries']
    assert p['residuals']


def test_incomplete_namespace_never_absorbs_new_incipit():
    _,p = parse('Fragmentum. XIV.\nCum nostris dudum. Supplement. 166.',volume='PL217')
    e, = p['entries']
    assert e['entry_raw']=='Cum nostris dudum. Supplement. 166.'
    assert p['residuals']


def test_namespace_continuation_does_not_cross_blocks():
    raw = ('<bloco tipo="texto_principal">Fragmentum. Registr. de neg.</bloco>'
           '<bloco tipo="texto_principal">Imp. 26.</bloco>')
    p = parse_page(raw,source='fixture',physical=1,volume='PL217',
                   scopes=[{'span':[0,len(raw)],'section_id':'s'}])
    assert not p['entries']


def test_numeric_join_is_only_a_proposal_with_original_spans():
    raw,p = parse('His quae ad ecclesiarum, XV,\n157.',volume='PL217')
    proposal, = p['continuation_proposals']
    assert not p['entries'] and len(p['residuals'])==2
    assert not proposal['applied'] and proposal['status']=='requires_layout_review'
    assert proposal['proposed_reference_raw']=='XV,\n157.'
    for part in ['fragment','number']:
        item=proposal[part]
        assert raw[item['char_start']:item['char_end']]==item['raw']
    audit=coverage(raw,[{'span':[0,len(raw)]}],p['entries'])
    assert audit['characters']['consumed_by_candidate']==0


@pytest.mark.parametrize('text',[
    'Cum inter vos, I, 285.\n6.',
    'Fragmentum sine numero\n157.',
    'His quae ad ecclesiarum, XV,\n\n157.',
    'His quae ad ecclesiarum, XV,\n67. Cum electionis. XIV,',
])
def test_numeric_join_proposals_require_unbroken_namespace(text):
    _,p = parse(text,volume='PL217')
    assert not p['continuation_proposals']


def test_numeric_join_proposals_cannot_cross_column_or_region():
    raw=('<bloco tipo="texto_principal">His quae, XV,</bloco>'
         '<bloco tipo="texto_principal">157.</bloco>')
    p=parse_page(raw,source='fixture',physical=1,volume='PL217',
                 scopes=[{'span':[0,len(raw)],'section_id':'s'}])
    assert not p['continuation_proposals']
    text='His quae, XV,\n157.'
    wrapped=f'<bloco tipo="texto_principal">{text}</bloco>'
    cut=wrapped.index('157.')
    _,p=parse(text,volume='PL217',scopes=[{'span':[0,cut],'section_id':'s'},
                                          {'span':[cut,len(wrapped)],'section_id':'t'}])
    assert not p['continuation_proposals']


def test_scoped_mixed_page_and_column_end_do_not_swallow_following_index():
    text = 'Quærere, I, 719.\nORDO RERUM\nCatalogus, 50.'
    raw = f'<bloco tipo="cabecalho">ORDO RERUM</bloco><bloco tipo="texto_principal">{text}</bloco>'
    end = raw.index('ORDO RERUM',raw.index('texto_principal'))
    p = parse_page(raw,source='fixture',physical=1,volume='PG046',
                   scopes=[{'span':[0,end],'section_id':'s'}])
    assert [e['entry_raw'] for e in p['entries']] == ['Quærere, I, 719.']
    _,p = parse('Quærere et per-\ndere quid sit, I, 719.\nFragmentum sine fine')
    assert len(p['entries'])==1
    assert 'joined_ocr_lines_require_segmentation_review' in p['entries'][0]['flags']
    assert p['residuals'][0]['reason']=='open_fragment_at_region_or_column_end'


def test_unsupported_row_stays_residual_not_attached_to_next_lemma():
    _,p = parse('Remissio. Vide ignotum.\nQuærere, I, 719.')
    assert p['entries'][0]['entry_raw']=='Quærere, I, 719.'
    assert p['residuals'][0]['reason']=='unsupported_finished_row_before_new_start'


def test_displaced_epistle_number_is_not_transferred_to_next_incipit():
    _,p = parse('67. Cum electionis confirmatio. XIV,\nCum emergentium. I, 108.',volume='PL217')
    assert [e['entry_raw'] for e in p['entries']]==['Cum emergentium. I, 108.']
    assert p['residuals'][0]['reason']=='displaced_locator_and_incipit_require_review'
    _,p = parse('Cum ex piæ recordationis. VII\n226. Cum facti simus. II, 250.',volume='PL217')
    assert not p['entries']
    assert len(p['residuals'])==2


def test_incomplete_numeric_list_does_not_end_row_at_comma():
    _,p = parse('Oculi quid, I, 834, 835,\n919.')
    assert len(p['entries'])==1
    assert p['entries'][0]['references_raw']=='I, 834, 835,\n919.'


def test_wrapped_analytic_layout_is_explicit_residue_without_false_segmentation():
    _,p = parse('Oculi sa-\npientis, I, 294. Alia con-\ntinuatio, I, 295.')
    assert not p['entries']
    assert p['residuals'][0]['reason']=='wrapped_layout_requires_paragraph_segmentation'


def test_blank_paragraphs_recover_wrapped_text_with_pending_printed_boundaries():
    raw,p = parse('Spiritus sanctus non in creatu-\nras quæri, II, 558. Spi-\nritus quid, III, 194.\n\n'
                  'Splendor, III, 559, C.D.\n\nT\n\nTabellæ cordis, I, 1075, B.')
    assert len(p['entries'])==3
    first=p['entries'][0]
    assert first['unit_kind']=='ocr_paragraph_group'
    assert first['quality']['segmentation']=='pending_printed_boundaries'
    assert len(first['reference_groups'])==2
    assert first['reference_groups'][1]['owner_text_raw']=='Spi-\nritus quid'
    audit=coverage(raw,[{'span':[0,len(raw)]}],p['entries'])
    assert not audit['invalid_candidate_ids']
    assert audit['characters']['duplicate_consumption']==0
    assert p['events'][0]['letter']=='T'


def test_paragraph_group_does_not_certify_multiple_lemmas_as_one():
    _,p = parse('Ocu-\nli quid, I, 294.\nAlia con-\ntinuatio, I, 295.\n\nFragmentum sine fine')
    assert len(p['entries'])==1
    assert 'ocr_paragraph_not_confirmed_printed_entry' in p['entries'][0]['flags']
    assert p['residuals'][0]['reason']=='incomplete_or_unsupported_locator'


def test_region_overlap_and_unknown_profile_rejected():
    with pytest.raises(ValueError,match='overlapping'):
        parse('Quærere, I, 719.',scopes=[{'span':[0,10],'section_id':'s'},{'span':[5,15],'section_id':'t'}])
    with pytest.raises(ValueError,match='No audited'):
        parse('Quærere, I, 719.',volume='PO025')


def test_material_regression_detects_changed_reference_owner_even_with_same_counts():
    _,page=parse('Quærere, I, 719.')
    before=page['entries']
    after=deepcopy(before)
    after[0]['id']='new-version-id'
    assert runner.material_comparison(before,after)['retained_unchanged']==1
    after[0]['reference_groups'][0]['owner_text_raw']='Different owner'
    diff=runner.material_comparison(before,after)
    assert diff['retained_unchanged']==0
    assert diff['changed'][0]['fields']==['reference_groups']
    assert not diff['not_retained_spans'] and not diff['added_spans']


def test_runner_reads_baseline_without_mutation_and_includes_missing_pages(tmp_path):
    source_db = tmp_path/'main.db'
    sqlite3.connect(source_db).close()
    baseline = tmp_path/'baseline.sqlite'
    db = open_experiment(baseline,source_db)
    db.execute('INSERT INTO runs VALUES(?,?,?,?)',('old','now','{}','completed'))
    db.commit();db.close()
    raw,_ = parse('Quærere, I, 719.')
    src = tmp_path/'fixture-1.txt';src.write_text(raw)
    a,b = raw.index('Quærere'),raw.index('</bloco>')
    recovery = tmp_path/'recovery';recovery.mkdir()
    span = {'source':str(src),'source_sha256':digest(raw.encode()),'span':[a,b]}
    section = {'section_id':'s','volume':'PG046','spans':[span],
               'kind':'analytic_index','opening_status':'unknown_first_observed'}
    page = {'source':str(src),'source_sha256':digest(raw.encode()),'physical':1,'volume':'PG046',
            'pieces':[{'span':[0,len(raw)],'rows':[{'span':[a,b]}]}]}
    (recovery/'sections.jsonl').write_text(json.dumps(section)+'\n')
    (recovery/'pages.jsonl').write_text(json.dumps(page)+'\n')
    args=argparse.Namespace(recovery=recovery,baseline_run='old',baseline_db=baseline,source_db=source_db,
        experiment_db=tmp_path/'new.sqlite',volume=['PG046'],workers=2,verbose=False,out=tmp_path/'out')
    hashes = {p:digest(p.read_bytes()) for p in [source_db,baseline]}
    result = runner.run(args)
    assert result['previously_unparsed_pages']==1 and result['after']['entries']==1
    assert result['after']['characters']['residual']==0
    assert result['db_updates']==0 and not result['import_ready']
    assert (args.out/'entries.jsonl').exists() and (args.out/'report.md').exists()
    assert all(digest(p.read_bytes())==h for p,h in hashes.items())
    with sqlite3.connect(args.experiment_db) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
    args.out=tmp_path/'out2';args.experiment_db=baseline
    with pytest.raises(ValueError,match='differ'):
        runner.run(args)
