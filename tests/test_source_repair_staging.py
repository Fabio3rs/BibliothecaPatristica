import json
import sqlite3
import pytest
from tools.indexing.entry_parser_experiment import digest
from tools.indexing.source_repair_staging import run, validate


def fixture(tmp_path):
    source=tmp_path/'current';source.write_text('Other entry')
    image=tmp_path/'image';image.write_bytes(b'fixture-image')
    alt=tmp_path/'alternative';alt.write_text('Dacibyza, 1, 544.')
    sh,ih,ah=[digest(p.read_bytes()) for p in (source,image,alt)]
    version={'id':1,'image_hash':ih,'text_hash':ah,'snapshot':str(alt),'comparison_status':'compared'}
    inventory=tmp_path/'inventory.json'
    inventory.write_text(json.dumps([{'source':str(source),'source_sha256':sh,'image_sha256':ih,'versions':[version]}]))
    page={'volume':'PG122','physical':773,'source':str(source),'source_sha256':sh,'image':str(image),
          'image_sha256':ih,'inventory':str(inventory),'inventory_sha256':digest(inventory.read_bytes())}
    p={'id':'one','volume':'PG122','physical':773,'current_source':str(source),'current_source_sha256':sh,
       'image':str(image),'image_sha256':ih,'current_span':None,'applied':False,
       'status':'proposal_only_visual_reviewed','lemma':'Dacibyza','conflicts':[{'type':'I_vs_1'}],
       'visual_reading':{'literal':'Dacibyza, I, 544.','location':'left','method':'fixture-review'},
       'alternative_spans':[{'version_id':1,'snapshot':str(alt),'snapshot_sha256':ah,
                             'char_start':0,'char_end':len(alt.read_text()),'literal':alt.read_text()}]}
    review={'schema':'source-repair-visual-review-v1','page_provenance':[page],'proposals':[p]}
    path=tmp_path/'review.json';path.write_text(json.dumps(review))
    return path,review,alt,source,image


def test_readings_are_not_rewritten_and_visual_offsets_are_separate(tmp_path):
    path,review,alt,source,image=fixture(tmp_path)
    before=[p.read_bytes() for p in (alt,source,image)]
    result=run(path,tmp_path/'out')
    assert result['occurrences']==result['readings']==result['reference_groups']==1
    assert result['production_db_updates']==0
    with sqlite3.connect(tmp_path/'out/repairs.sqlite') as c:
        assert c.execute('SELECT literal,agrees_with_visual FROM readings').fetchone()==('Dacibyza, 1, 544.',0)
        assert c.execute('SELECT reference_raw,coordinate_space FROM reference_groups').fetchone()==('I, 544.','visual_transcription')
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert before==[p.read_bytes() for p in (alt,source,image)]
    doc=json.loads((tmp_path/'out/documents.jsonl').read_text())
    assert doc['current_span'] is None and not doc['quality']['source_repaired']


@pytest.mark.parametrize('tamper',['source','image','alternative','span','current_span','duplicate','inventory_image'])
def test_evidence_changes_and_duplicate_claims_fail(tmp_path,tamper):
    _,review,alt,source,image=fixture(tmp_path)
    p=review['proposals'][0]
    if tamper in ('source','image','alternative'):
        {'source':source,'image':image,'alternative':alt}[tamper].write_text('changed')
    elif tamper=='span':p['alternative_spans'][0]['char_end']-=1
    elif tamper=='current_span':p['current_span']=[0,10]
    elif tamper=='duplicate':review['proposals']*=2
    else:
        page=review['page_provenance'][0]
        inv=json.loads(open(page['inventory']).read());inv[0]['versions'][0]['image_hash']='different'
        from pathlib import Path
        path=Path(page['inventory']);path.write_text(json.dumps(inv));page['inventory_sha256']=digest(path.read_bytes())
    with pytest.raises(ValueError):validate(review)
