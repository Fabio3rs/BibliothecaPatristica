from copy import deepcopy
import argparse
import json
import sqlite3

import pytest

from tools.indexing.continuation_reviews import VERSION, load_reviews
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.entry_parser_literal_rows import parse_page
from tools.indexing.index_conservation_audit import coverage
from tools.indexing.entry_parser_bulk import open_experiment
from tools.indexing import regional_row_experiment as runner


def fixture(tmp_path):
    raw='<bloco tipo="texto_principal">His quae, XV,\n157.\nAlius, I, 2.</bloco>'
    scope=[{'span':[0,len(raw)],'section_id':'s'}]
    args=dict(source='fixture',volume='PL217',physical=1,scopes=scope)
    page=parse_page(raw,**args)
    review=deepcopy(page['continuation_proposals'][0])
    image=tmp_path/'image.png'; image.write_bytes(b'fixture-image')
    review['visual_review']={'reviewer':'assistant','human_reviewed':False,
        'layout_status':'supported','reference_reading':'matches_ocr',
        'image':str(image),'image_sha256':digest(image.read_bytes()),
        'image_locator':'left column, before Alius','note':'Fixture review of continuation.'}
    return raw,args,review


def test_review_is_opt_in_and_keeps_original_reference_and_ownership(tmp_path):
    raw,args,review=fixture(tmp_path)
    baseline=parse_page(raw,**args)
    assert len(baseline['entries'])==1 and len(baseline['residuals'])==2
    page=parse_page(raw,**args,reviewed_joins=[review])
    assert len(page['entries'])==2 and not page['residuals']
    entry=page['entries'][0]
    assert entry['entry_raw']=='His quae, XV,\n157.'
    assert entry['references_raw']=='XV,\n157.'
    assert entry['quality']['destination']=='not_attempted'
    assert entry['visual_review']['human_reviewed'] is False
    assert page['continuation_proposals'][0]['applied'] is True
    assert page['entries'][1]==baseline['entries'][0]
    result=coverage(raw,args['scopes'],page['entries'])
    assert not result['invalid_candidate_ids']
    assert result['characters']['duplicate_consumption']==0


@pytest.mark.parametrize('reading',['uncertain','conflicts_with_ocr'])
def test_reading_conflict_never_changes_number_or_consumes_residue(tmp_path,reading):
    raw,args,review=fixture(tmp_path)
    review['visual_review']['reference_reading']=reading
    review['visual_review']['visual_reference_proposal']='XV, 175.'
    page=parse_page(raw,**args,reviewed_joins=[review])
    assert len(page['entries'])==1 and len(page['residuals'])==2
    assert page['continuation_proposals'][0]['status']=='reviewed_reading_pending'
    assert page['continuation_proposals'][0]['applied'] is False


@pytest.mark.parametrize('layout',['uncertain','rejected'])
def test_uncertain_layout_blocks_even_matching_digits(tmp_path,layout):
    raw,args,review=fixture(tmp_path)
    review['visual_review']['layout_status']=layout
    page=parse_page(raw,**args,reviewed_joins=[review])
    assert len(page['entries'])==1


@pytest.mark.parametrize('corruption',['source','source_sha256','number','proposed_reference_raw','image','evidence','duplicate','region','false_human_review'])
def test_stale_or_unmatched_reviews_fail_closed(tmp_path,corruption):
    raw,args,review=fixture(tmp_path)
    reviews=[review]
    if corruption=='source': review['source']='other'
    if corruption=='source_sha256': review['source_sha256']='stale'
    if corruption=='number': review['number']['raw']='175.'
    if corruption=='proposed_reference_raw': review['proposed_reference_raw']='XV, 175.'
    if corruption=='image': review['visual_review']['image_sha256']='stale'
    if corruption=='evidence': review['visual_review']['note']=''
    if corruption=='duplicate': reviews.append(deepcopy(review))
    if corruption=='region': args['scopes'][0]['span'][1]=raw.index('157.')
    if corruption=='false_human_review': review['visual_review']['human_reviewed']=True
    with pytest.raises(ValueError):
        parse_page(raw,**args,reviewed_joins=reviews)


def test_manifest_version_is_required(tmp_path):
    file=tmp_path/'reviews.json'
    file.write_text(json.dumps({'version':VERSION,'reviews':[]}))
    assert load_reviews(file)==[]
    file.write_text(json.dumps({'version':'wrong','reviews':[]}))
    with pytest.raises(ValueError): load_reviews(file)


def test_runner_persists_review_provenance_and_does_not_mutate_sources(tmp_path):
    raw,_,review=fixture(tmp_path)
    source=tmp_path/'page-1.txt'; source.write_text(raw)
    review['source']=str(source)
    manifest=tmp_path/'reviews.json'
    manifest.write_text(json.dumps({'version':VERSION,'reviews':[review]}))
    source_db=tmp_path/'main.sqlite'; sqlite3.connect(source_db).close()
    baseline=tmp_path/'baseline.sqlite'
    db=open_experiment(baseline,source_db)
    db.execute('INSERT INTO runs VALUES(?,?,?,?)',('old','now','{}','completed'))
    db.commit(); db.close()
    recovery=tmp_path/'recovery'; recovery.mkdir()
    span=[raw.index('His'),raw.index('</bloco>')]
    section={'section_id':'s','volume':'PL217','kind':'incipit_index','opening_status':'candidate',
             'spans':[{'source':str(source),'source_sha256':digest(raw.encode()),'span':span}]}
    page={'source':str(source),'source_sha256':digest(raw.encode()),'physical':1,'volume':'PL217',
          'pieces':[{'span':[0,len(raw)],'rows':[{'span':span}]}]}
    (recovery/'sections.jsonl').write_text(json.dumps(section)+'\n')
    (recovery/'pages.jsonl').write_text(json.dumps(page)+'\n')
    args=argparse.Namespace(recovery=recovery,baseline_run='old',baseline_db=baseline,
        source_db=source_db,experiment_db=tmp_path/'experiment.sqlite',volume=['PL217'],
        workers=2,verbose=False,out=tmp_path/'out',continuation_reviews=manifest)
    protected={p:digest(p.read_bytes()) for p in [source,source_db,baseline]}
    result=runner.run(args)
    assert result['reviewed_joins_staged']==1 and result['continuation_proposals_pending']==0
    assert result['after']['entries']==2 and result['db_updates']==0
    assert (args.out/'continuation_reviews.json').read_bytes()==manifest.read_bytes()
    config=json.loads((args.out/'manifest.json').read_text())
    assert config['review_manifest_sha256']==digest(manifest.read_bytes())
    with sqlite3.connect(args.experiment_db) as db:
        task=json.loads(db.execute('SELECT result_json FROM tasks').fetchone()[0])
        assert task['page']['entries'][0]['visual_review']==review['visual_review']
    assert all(digest(p.read_bytes())==sha for p,sha in protected.items())
