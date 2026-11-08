import argparse
import json
import sqlite3

from tools.indexing.ocr_variant_audit import tokens,compare,run
from tools.indexing.entry_parser_experiment import digest


def test_hyphen_normalization_preserves_source_span_and_numeric_disagreement():
    raw='Superhu-\nmeralia, I, 387, B.'
    t=tokens(raw)
    assert t[0]['token']=='superhumeralia'
    assert raw[slice(*t[0]['span'])]=='Superhu-\nmeralia'
    assert not compare(raw,'Superhumeralia, I, 387, B.')
    changes=compare(raw,'Superhumeralia, I, 587, B.')
    assert len(changes)==1 and changes[0]['numeric_disagreement']
    assert changes[0]['current']['quote']=='387'
    assert not changes[0]['automatic_repair_allowed']


def test_xml_roles_and_linebreak_tags_do_not_create_fictitious_tokens():
    raw='<bloco tipo="cabecalho">INDEX</bloco><bloco tipo="texto_principal">Oliva[br/]ferax 566.</bloco>'
    assert [t['token'] for t in tokens(raw)]==['oliva','ferax','566']


def test_inserted_letter_is_proposal_with_empty_current_span():
    change,=compare('Oliva, I, 566.','Oliva, I, 566, A.')
    assert change['operation']=='insert' and change['single_letter_disagreement']
    a,b=change['current']['span']
    assert a==b and change['current']['text_sha256']==digest(b'')
    assert change['status']=='variant_disagreement_requires_review'


def test_readonly_databases_hash_gate_and_cache_snapshot(tmp_path):
    root=tmp_path/'teste';text=root/'PG046/text';images=root/'PG046/images'
    text.mkdir(parents=True);images.mkdir()
    raw='Oliva, I, 566.';image=b'fixture-image'
    (text/'fixture-635.txt').write_text(raw)
    (images/'PG046-635.png').write_bytes(image)
    db=tmp_path/'versions.db';cache=tmp_path/'tesseract.db'
    c=sqlite3.connect(db)
    c.executescript('''CREATE TABLE ocr_engine_versions(id INTEGER,engine TEXT,model TEXT);
        CREATE TABLE ocr_results(id INTEGER,volume_id TEXT,page_num INTEGER,image_path TEXT,image_hash TEXT,
        engine_version_id INTEGER,text_content TEXT,text_hash TEXT,is_current INTEGER,created_at TEXT);
        INSERT INTO ocr_engine_versions VALUES(1,'test','test');''')
    for i,img,hash_ok in [(1,digest(image),True),(2,'different-image',True),(3,digest(image),False)]:
        variant='Oliva, I, 566, A.'
        c.execute('INSERT INTO ocr_results VALUES(?,?,?,?,?,?,?,?,?,?)',
            (i,'PG046',635,'old-path',img,1,variant,digest(variant.encode()) if hash_ok else 'stale',0,'now'))
    c.commit();c.close()
    c=sqlite3.connect(cache)
    c.execute('CREATE TABLE tesseract_cache(image_hash TEXT,imgpath TEXT,lang TEXT,result TEXT,created_at TEXT)')
    c.execute('INSERT INTO tesseract_cache VALUES(?,?,?,?,?)',(digest(image),'old-path','lat','{"text":"Oliva"}','now'))
    c.commit();c.close()
    hashes={p:digest(p.read_bytes()) for p in [db,cache,text/'fixture-635.txt']}
    out=tmp_path/'out'
    summary=run(argparse.Namespace(volume='PG046',page=[635],root=root,versions_db=db,tesseract_db=cache,out=out))
    assert summary['comparisons']=={'compared':1,'excluded_hash_mismatch':2}
    assert summary['automatic_repairs']==0 and summary['tesseract_cache_hits']==1
    assert all(digest(p.read_bytes())==h for p,h in hashes.items())
    changes=[json.loads(x) for x in (out/'variant_differences.jsonl').read_text().splitlines()]
    assert len(changes)==1 and changes[0]['version_id']==1
