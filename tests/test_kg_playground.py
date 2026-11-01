from __future__ import annotations

import io
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from resumo_v2 import sha256_text
from tools.knowledge_graph.playground.common import KIT, digest, dumps, read_json, readonly
from tools.knowledge_graph.playground.snapshot_v2 import load_page, demo_snapshot, benchmark_catalog, guard_holdout, load_benchmark
from tools.knowledge_graph.playground.ocr_adapter import parse_blocks
from tools.knowledge_graph.playground.storage import Store
from tools.knowledge_graph.playground.experiment_runner import prepare, plan_experiment, execute_experiment, execute_run
from tools.knowledge_graph.playground.comparison import compare_runs
from tools.knowledge_graph.playground.reporting import report, export_jsonl, csv_report


@pytest.fixture
def corpus(tmp_path):
    db=tmp_path/'summary.db'; root=tmp_path/'corpus'; text=root/'PG026/text'; text.mkdir(parents=True)
    raw='<pagina><cabecalho>Caput</cabecalho><bloco tipo="texto_principal" script="grego" bbox="10,20,400,500">λόγος\nκαὶ οὐσία</bloco><notas>Nota editoris.</notas><bloco tipo="alienum" script="latino">Gratia.</bloco></pagina>'
    (text/'PG026-071.txt').write_text(raw,encoding='utf-8')
    segments=[{'order':1,'kind':'body','work_key':'work:1','summary':'Primeira obra e primeiro autor.'},
              {'order':2,'kind':'editorial_note','work_key':'work:2','summary':'Segunda obra e voz do editor.'}]
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE resumo_generations(id INTEGER PRIMARY KEY,run_id INTEGER,documento TEXT,pagina_num INTEGER,pagina_file TEXT,is_current INTEGER,status TEXT,tainted_by_page INTEGER,segments_json TEXT,page_kinds_json TEXT,summary_display_pt TEXT,cumulative_summary TEXT,static_analysis_json TEXT,source_conflicts_json TEXT,contributors_json TEXT,source_hash TEXT,output_hash TEXT,previous_generation_id INTEGER,ocr_result_id INTEGER)')
        con.execute('INSERT INTO resumo_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (1,10,'PG026',71,'PG026-071.txt',0,'valid',None,json.dumps(segments,ensure_ascii=False),
                     '["body","editorial_note"]','Display','Previous context',json.dumps({'facsimile_score':3,'containing_work_candidates':[{'work_key':'work:1','author_original':'A'},{'work_key':'work:2','author_original':'B'}]}),'[]','[]','opaque-source',sha256_text('Display','Previous context',json.dumps(segments,ensure_ascii=False,sort_keys=True)),None,9))
    versions=tmp_path/'ocr.db'
    with sqlite3.connect(versions) as con:
        con.execute('CREATE TABLE ocr_results(id INTEGER PRIMARY KEY,volume_id TEXT,page_num INTEGER,text_content TEXT)')
        con.execute('INSERT INTO ocr_results VALUES(9,?,?,?)',('PG026',71,raw))
    return db,root,versions


def test_real_schema_readonly_multiwork_unicode_and_fallback(corpus):
    db,root,versions=corpus
    before=digest(db.read_bytes())
    snap=load_page(db,root,'PG026:71',ocr_versions_db=versions)
    assert digest(db.read_bytes())==before
    assert snap['source']['scope']=='shadow'
    assert snap['source']['selection_policy']=='latest_eligible_shadow_fallback'
    assert snap['source']['historical_ocr_matches']
    assert len(snap['v2']['segments'])==2
    assert {s['work_key'] for s in snap['v2']['segments']}=={'work:1','work:2'}
    assert {w['author_original'] for w in snap['v2']['metadata_context']['static_analysis']['containing_work_candidates']}=={'A','B'}
    assert any(b['text']=='λόγος\nκαὶ οὐσία' for b in snap['ocr']['blocks'])
    assert any(b['type']=='alienum' for b in snap['ocr']['blocks'])
    assert any(b['type']=='notas' for b in snap['ocr']['blocks'])
    assert not snap['facsimile']['available']
    assert 'summary' not in snap['v2']['context_only']
    with readonly(db) as con:
        with pytest.raises(sqlite3.OperationalError):con.execute('DELETE FROM resumo_generations')


def test_current_preferred_then_latest_eligible_shadow(corpus):
    db,root,_=corpus
    with sqlite3.connect(db) as con:
        con.execute('UPDATE resumo_generations SET is_current=1')
        con.execute('INSERT INTO resumo_generations SELECT 2,11,documento,pagina_num,pagina_file,0,status,tainted_by_page,segments_json,page_kinds_json,summary_display_pt,cumulative_summary,static_analysis_json,source_conflicts_json,contributors_json,source_hash,output_hash,previous_generation_id,ocr_result_id FROM resumo_generations')
    assert load_page(db,root,'PG026:71')['source']['v2_generation_id']==1
    assert load_page(db,root,'PG026:71',generation_id=2)['source']['scope']=='shadow'
    assert load_page(db,root,'PG026:71',run_id=11)['source']['v2_generation_id']==2
    with sqlite3.connect(db) as con:con.execute('UPDATE resumo_generations SET is_current=0')
    assert load_page(db,root,'PG026:71')['source']['v2_generation_id']==2
    with sqlite3.connect(db) as con:con.execute("UPDATE resumo_generations SET status='context_provisional' WHERE id=2")
    assert load_page(db,root,'PG026:71')['source']['v2_generation_id']==1
    with pytest.raises(ValueError):load_page(db,root,'PG026:71',generation_id=2)


@pytest.mark.parametrize('change', ['missing','ambiguous','mismatch','hash','utf8','symlink'])
def test_source_failures_are_explicit(corpus, tmp_path, change):
    db,root,versions=corpus
    p=root/'PG026/text/PG026-071.txt'
    if change=='missing':p.unlink()
    elif change=='ambiguous':
        raw=p.read_bytes();p.unlink();(p.parent/'uuid-a-071.txt').write_bytes(raw);(p.parent/'uuid-b-071.txt').write_bytes(raw)
    elif change=='mismatch':p.write_text('Changed OCR')
    elif change=='hash':
        with sqlite3.connect(db) as con:con.execute("UPDATE resumo_generations SET output_hash='bad'")
    elif change=='utf8':p.write_bytes(b'\xff')
    elif change=='symlink':
        outside=tmp_path/'outside.txt';outside.write_bytes(p.read_bytes());p.unlink();p.symlink_to(outside)
    with pytest.raises((ValueError,FileNotFoundError,UnicodeDecodeError)):
        load_page(db,root,'PG026:71',ocr_versions_db=versions)


def test_numerical_fallback_needs_historical_proof(corpus):
    db,root,versions=corpus
    p=root/'PG026/text/PG026-071.txt';p.rename(p.with_name('uuid-071.txt'))
    with pytest.raises(ValueError,match='historical'):load_page(db,root,'PG026:71')
    assert load_page(db,root,'PG026:71',ocr_versions_db=versions)['source']['locator_status']=='numeric_fallback'


def test_drift_requires_explicit_override(corpus):
    db,root,versions=corpus
    (root/'PG026/text/PG026-071.txt').write_text('Changed OCR')
    snap=load_page(db,root,'PG026:71',ocr_versions_db=versions,allow_source_drift=True)
    assert snap['source']['historical_ocr_matches'] is False
    assert any('SOURCE_CONFLICT' in w for w in snap['ocr']['warnings'])


@pytest.mark.parametrize('raw,expected', [
    ('plain λόγος\nLatin text',['plain λόγος\nLatin text']),
    ('<pagina><bloco tipo="novo">gratia</bloco><notas><nota>λόγος</nota></notas><estranho>natura</estranho></pagina>',['gratia','λόγος','natura']),
    ('<pagina><bloco>broken & text</bloco><bloco>tail',['broken & text','tail']),
    ('<pagina>leading<bloco>inter-\npretatio</bloco>trailing</pagina>',['leading','inter-\npretatio','trailing']),
    ('<pagina><bloco bbox="nan,0,3,4">x</bloco></pagina>',['x'])])
def test_tolerant_ocr_preserves_all_text(raw,expected):
    parsed=parse_blocks(raw)
    assert [b['text'] for b in parsed['blocks']]==expected
    assert [b['id'] for b in parsed['blocks']]==[f'b{i+1}' for i in range(len(expected))]
    assert parse_blocks(raw)==parsed
    if 'broken' in raw:assert any('malformed' in w for w in parsed['warnings'])
    if 'nan' in raw:assert parsed['blocks'][0]['bbox'] is None


def test_empty_ocr_has_warning():
    assert parse_blocks('')['warnings']


def test_store_rejects_production_and_unrelated_databases(corpus,tmp_path):
    db,_,_=corpus
    before=digest(db.read_bytes())
    with pytest.raises(ValueError):Store(db)
    assert digest(db.read_bytes())==before
    with pytest.raises(ValueError):Store(db,protected_paths=[db])
    link=tmp_path/'other-name.db';link.hardlink_to(db)
    with pytest.raises(ValueError):Store(link,protected_paths=[db])


def fake_result(config,request):
    return {'raw_response':dumps(read_json(KIT/'page-output.demo.json')),'usage':{'input_tokens':100,'output_tokens':50},'latency_seconds':.2,'attempts':1,'finish_reason':'stop'}


def test_storage_dry_run_execution_review_cache_and_repetitions(tmp_path):
    store=Store(tmp_path/'lab.db');case=store.case(demo_snapshot())
    config={'provider':'ollama','model':'test'}
    exp=plan_experiment(store,[case],[config])
    row=store.list_runs(exp)[0]
    assert row['status']=='planned'
    assert store.get_run(row['id'])['result'] is None
    calls=[]
    def caller(*args):calls.append(1);return fake_result(*args)
    execute_experiment(store,exp,caller=caller)
    run=store.get_run(row['id']);assert run['validation']['valid'] and len(calls)==1
    with pytest.raises(ValueError):store.start(run['id'])
    store.review(run['id'],'r1','uncertain','stance','Check the quoted adversary')
    assert store.get_run(run['id'])['result']==run['result']
    exp2=plan_experiment(store,[case],[config]);execute_experiment(store,exp2,caller=caller)
    assert len(calls)==1
    cached=store.get_run(store.list_runs(exp2)[0]['id'])
    assert cached['status']=='cached' and cached['result']['cost_usd']==0
    exp3=plan_experiment(store,[case],[config],repetitions=3)
    execute_experiment(store,exp3,concurrency=2,caller=caller)
    assert len(calls)==4
    execute_experiment(store,exp3,resume=True,caller=caller)
    assert len(calls)==4
    exported=json.loads(next(export_jsonl(store,exp)))
    assert exported['run']['reviews'][0]['decision']=='uncertain'


def test_cache_invalidation_prompt_schema_summary_ocr_model_configuration(tmp_path,monkeypatch):
    store=Store(tmp_path/'lab.db');snap=demo_snapshot();case=store.case(snap)
    config={'provider':'ollama','model':'test'}
    baseline=prepare(case,config)['input_hash'];hashes=[]
    for field in ['model','temperature','num_ctx']:
        value={'model':'other','temperature':.3,'num_ctx':4096}[field]
        hashes.append(prepare(case,dict(config,**{field:value}))['input_hash'])
    hashes.append(prepare(case,config,system_override='Different system prompt')['input_hash'])
    hashes.append(prepare(case,config,variant='concepts-v1-fewshot')['input_hash'])
    for section in ['ocr','v2']:
        changed=deepcopy(snap)
        if section=='ocr':changed['ocr']['blocks'][0]['text']+=' new'
        else:changed['v2']['segments'][0]['summary']+=' nova'
        hashes.append(prepare(store.case(changed),config)['input_hash'])
    from tools.knowledge_graph.playground import prompt_registry
    schema=read_json(prompt_registry.SCHEMA_PATH);schema['description']='Schema revision'
    file=tmp_path/'schema.json';file.write_text(dumps(schema));monkeypatch.setattr(prompt_registry,'SCHEMA_PATH',file)
    hashes.append(prepare(case,config)['input_hash'])
    assert all(h!=baseline for h in hashes) and len(set(hashes))==len(hashes)


def test_contract_clarifications_are_a_separate_versioned_prompt(tmp_path):
    case = Store(tmp_path/'lab.db').case(demo_snapshot())
    config = {'provider':'ollama','model':'test:cloud'}
    original = prepare(case, config, variant='concepts-v1')
    revised = prepare(case, config, variant='concepts-v1.1')
    assert revised['schema_hash'] == original['schema_hash']
    assert revised['input_hash'] != original['input_hash']
    assert revised['system_hash'] != original['system_hash']
    assert 'evidence: ev1, ev2' in revised['system_prompt']
    assert 'revision 1.1' not in original['system_prompt']


def test_fenced_response_revalidation_preserves_original_run_and_typed_reviews(tmp_path):
    from tools.knowledge_graph.playground.validation import evaluate
    store = Store(tmp_path/'lab.db');case = store.case(demo_snapshot())
    exp = plan_experiment(store,[case],[{'provider':'ollama','model':'test:cloud'}])
    identifier = store.list_runs(exp)[0]['id']
    data = read_json(KIT/'page-output.demo.json')
    old_id = data['evidence'][0]['id'];data['evidence'][0]['id'] = 'e1'
    for group in ('concepts','entities','claims','events','relations'):
        for obj in data[group]:
            obj['evidence_ids'] = ['e1' if ref == old_id else ref for ref in obj['evidence_ids']]
    raw = '```json\n' + dumps(data) + '\n```'
    execute_run(store,identifier,caller=lambda *args: dict(fake_result(*args),raw_response=raw))
    before = store.get_run(identifier)
    assert before['status'] == 'invalid' and before['validation']['graph_inspectable']
    assert before['result']['raw_response'] == raw
    assert before['result']['parsed_json'] == data
    checked = evaluate(case['snapshot'],raw)
    store.add_validation(identifier,checked)
    after = store.get_run(identifier)
    assert after['result'] == before['result'] and after['status'] == before['status']
    assert len(after['validation_history']) == 2
    with pytest.raises(ValueError): store.review(identifier,'e1','uncertain')
    store.review(identifier,'entities/e1','uncertain')
    store.review(identifier,'evidence/e1','incorrect')
    assert len(store.get_run(identifier)['reviews']) == 2


def test_facsimile_snapshot_attachment_and_hash_change(corpus,tmp_path):
    from PIL import Image
    db,root,versions=corpus
    images=root/'PG026/images';images.mkdir();p=images/'PG026-071.png'
    Image.new('RGB',(100,100),'white').save(p)
    store=Store(tmp_path/'lab.db');snap=load_page(db,root,'PG026:71',ocr_versions_db=versions);case=store.case(snap)
    config={'provider':'ollama','model':'test','vision':True}
    first=prepare(case,config,facsimile='auto')
    assert first['facsimile_selected'] and first['image_transport_sha256']
    assert not prepare(case,dict(config,vision=False),facsimile='on')['facsimile_selected']
    Image.new('RGB',(100,100),'black').save(p)
    new=store.case(load_page(db,root,'PG026:71',ocr_versions_db=versions))
    assert prepare(new,config,facsimile='auto')['input_hash']!=first['input_hash']
    assert prepare(case,config,facsimile='auto')['input_hash']==first['input_hash']
    Path(case['snapshot']['facsimile']['archived_path']).write_bytes(b'changed')
    with pytest.raises(ValueError):prepare(case,config,facsimile='on')


def test_resume_interrupted_creates_new_run_without_overwriting(tmp_path):
    store=Store(tmp_path/'lab.db');case=store.case(demo_snapshot());exp=plan_experiment(store,[case],[{'provider':'fixture','model':'demo'}])
    old=store.list_runs(exp)[0]['id'];store.start(old)
    execute_experiment(store,exp,resume=True)
    rows=store.list_runs(exp)
    assert len(rows)==2 and store.get_run(old)['status']=='interrupted'
    assert rows[0]['status']=='completed'
    assert store.get_run(rows[0]['id'])['prepared']['retry_of']==old


def test_invalid_response_retained_not_retried(tmp_path):
    store=Store(tmp_path/'lab.db');case=store.case(demo_snapshot());exp=plan_experiment(store,[case],[{'provider':'ollama','model':'test'}])
    calls=[]
    def caller(*args):calls.append(1);return dict(fake_result(*args),raw_response='not json')
    execute_experiment(store,exp,caller=caller)
    execute_experiment(store,exp,resume=True,caller=caller)
    run=store.get_run(store.list_runs(exp)[0]['id'])
    assert len(calls)==1 and run['status']=='invalid' and run['result']['raw_response']=='not json'


def test_token_limit_diagnostic_progress_reporting_and_no_automatic_retry(tmp_path):
    store=Store(tmp_path/'lab.db');case=store.case(demo_snapshot())
    exp=plan_experiment(store,[case],[{'provider':'meta','model':'muse-spark-1.3-contributor','max_output_tokens':8192}])
    calls=[];events=[]
    def truncated(*args):
        calls.append(1)
        return dict(fake_result(*args),raw_response='{"evidence":[{"quote":"unfinished',finish_reason='length',
                    usage={'input_tokens':9412,'output_tokens':8192,'reasoning_tokens':6824})
    execute_experiment(store,exp,caller=truncated,progress=events.append)
    execute_experiment(store,exp,resume=True,retry_errors=True,caller=truncated)
    run=store.get_run(store.list_runs(exp)[0]['id'])
    assert len(calls)==1 and run['status']=='invalid'
    assert events[0]['completion']['output_limit_reached']
    assert events[0]['completion']['visible_output_tokens']==1368
    assert '--max-output-tokens' in events[0]['message']
    metrics=report(store,exp)['runs'][0]
    assert metrics['reasoning_tokens']==6824 and metrics['visible_output_tokens']==1368
    assert metrics['output_limit_reached'] and metrics['max_output_tokens']==8192
    changed=prepare(case,dict(run['config'],max_output_tokens=32768))
    assert changed['input_hash']!=run['input_hash']


def test_cli_default_and_explicit_output_budget():
    from scripts.playgrounds.kg_extractor_playground import parser
    assert parser().parse_args(['run']).max_output_tokens==32768
    assert parser().parse_args(['run','--max-output-tokens','16384']).max_output_tokens==16384


def test_comparison_uses_meanings_and_evidence_not_ids(tmp_path):
    store=Store(tmp_path/'lab.db');case=store.case(demo_snapshot());cfg={'provider':'ollama','model':'test'}
    a=plan_experiment(store,[case],[cfg],bypass_cache=True);execute_experiment(store,a,caller=fake_result)
    def alternate(*args):
        result=fake_result(*args);data=json.loads(result['raw_response']);data['concepts'][0]['id']='a-different-id'
        result['raw_response']=dumps(data);return result
    b=plan_experiment(store,[case],[dict(cfg,model='other')]);execute_experiment(store,b,caller=alternate)
    ids=[store.list_runs(a)[0]['id'],store.list_runs(b)[0]['id']]
    comp=compare_runs(store,ids)
    assert any(m['left']=='c1' and m['right']=='a-different-id' for m in comp['pairs'][0]['matched'])
    assert not comp['pairs'][0]['relations_only_left']
    report_data=report(store,a)
    assert report_data['precision_recall'] is None
    assert report_data['summary']['estimated_corpus_cost_usd'] is None  # synthetic excluded


def test_benchmark_filters_and_freezes(corpus,tmp_path):
    db,root,_=corpus;bench=tmp_path/'benchmark';bench.mkdir();(bench/'sources').mkdir()
    con=readonly(db);row=dict(con.execute('SELECT * FROM resumo_generations').fetchone());con.close()
    gp=bench/'sources/generation.json';gp.write_text(dumps(row));op=bench/'sources/ocr.txt';op.write_bytes((root/'PG026/text/PG026-071.txt').read_bytes())
    record={'benchmark_id':'B1','split':'blind_holdout','source':{'documento':'PG026','pagina_num':71,'generation_id':1,'scope':'shadow','primary_page_kind':'body','work_key':'work:1','generation_snapshot':'sources/generation.json','generation_snapshot_sha256':digest(gp.read_bytes()),'ocr_snapshot':'sources/ocr.txt','ocr_sha256':digest(op.read_bytes())},'selection':{'categories':['claim']}}
    (bench/'selected_pages.jsonl').write_text(dumps(record)+'\n')
    assert not benchmark_catalog(bench)
    assert len(benchmark_catalog(bench,True))==1
    with pytest.raises(ValueError):guard_holdout(bench,'PG026:72')
    with pytest.raises(ValueError):load_benchmark(bench,'B1',root)
    snap=load_benchmark(bench,'B1',root,True)
    assert snap['source']['frozen_benchmark'] and snap['source']['split']=='blind_holdout'
    op.write_text('changed')
    with pytest.raises(ValueError,match='hash'):load_benchmark(bench,'B1',root,True)


def test_failed_matrix_preparation_rolls_back_entire_plan(tmp_path):
    store=Store(tmp_path/'lab.db');case=store.case(demo_snapshot())
    with pytest.raises(ValueError):
        plan_experiment(store,[case],[{'provider':'fixture','model':'demo'}],variants=['concepts-v1','missing-variant'])
    assert store.list_experiments()==[] and store.list_runs()==[]


def test_retry_error_does_not_repeat_successful_recovery(tmp_path):
    from tools.knowledge_graph.playground.providers import ProviderError
    store=Store(tmp_path/'lab.db');case=store.case(demo_snapshot())
    exp=plan_experiment(store,[case],[{'provider':'ollama','model':'test'}])
    def failure(*args):raise ProviderError('temporary failure')
    execute_experiment(store,exp,caller=failure)
    execute_experiment(store,exp,resume=True,retry_errors=True,caller=fake_result)
    assert len(store.list_runs(exp))==2
    execute_experiment(store,exp,resume=True,retry_errors=True,caller=fake_result)
    assert len(store.list_runs(exp))==2


def test_comparison_retains_duplicate_relation_occurrences():
    from tools.knowledge_graph.playground.comparison import compare_pair
    from tools.knowledge_graph.playground.validation import evaluate
    a=read_json(KIT/'page-output.demo.json');b=deepcopy(a)
    b['relations'].append(dict(b['relations'][0],id='r_duplicate'))
    comp=compare_pair({'id':1,'validation':evaluate(demo_snapshot(),a)}, {'id':2,'validation':evaluate(demo_snapshot(),b)})
    assert len(comp['relations_only_right'])==1
    assert comp['relations_only_right'][0]['id']=='r_duplicate'
