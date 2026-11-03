from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest
import requests

from scripts import validate_index_jev as jev
from scripts.alphabetical_index_db import connect_db, init_schema
from scripts.jev_index_results import ResolutionWriter, read_evaluations, matching_evaluation


@pytest.fixture(autouse=True)
def forbid_http(monkeypatch):
    def fail(*a, **kw):
        raise AssertionError('Unexpected HTTP')
    monkeypatch.setattr(requests.Session, 'post', fail)
    monkeypatch.setattr(requests.Session, 'get', fail)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path/'PG001'/'text'
    root.mkdir(parents=True)
    target = root/'page-002.txt'
    target.write_text('<pagina><bloco tipo="cabecalho">101 HISTORIA 102</bloco>'
                      '<bloco tipo="texto_principal">Athanasius contra Arianos disputavit.</bloco></pagina>')
    index = root/'page-099.txt'
    index.write_text('Athanasius 101')
    args = jev.parser().parse_args(['--db', str(tmp_path/'db.sqlite'), '--log', str(tmp_path/'run.jsonl')])
    with closing(connect_db(args.db)) as con:
        init_schema(con)
        con.execute("INSERT INTO alphabetical_volumes (volume_id,collection,source_root,created_at,updated_at) VALUES ('PG001','PG',?,'now','now')", (str(root),))
        con.execute("INSERT INTO alphabetical_sections (section_key,volume_id,section_order,section_kind,heading_raw,file_start,file_end,raw_json) VALUES ('s','PG001',1,'onomastic_person','INDEX',?,?,'{}')", (str(index), str(index)))
        con.execute("INSERT INTO alphabetical_entries (entry_key,section_key,entry_order,entry_kind,lemma_raw,entry_raw,raw_json) VALUES ('e','s',1,'lemma','Athanasius','Athanasius 101','{}')")
        con.execute("INSERT INTO alphabetical_refs (entry_key,ref_order,ref_kind,ref_raw,page_ref_int,raw_json,locator_status) VALUES ('e',1,'editorial_page','101',101,'{}','unresolved')")
        con.commit()
    with closing(jev.connect_readonly(args.db)) as con:
        row = dict(con.execute(jev.QUERY, ('PG001',)).fetchone())
    spec = {'row': row, 'files': [{'file': str(target)}], 'root': str(root), 'intervals': [],
            'category': 'mechanical', 'stale': False, 'blocked': None, 'marker': None,
            'visual_check': None, 'numbering_scope': None, 'report': 'fixture.json',
            'report_sha256': 'abc', 'locator_reason': 'accepted', 'proposal': True}
    result, _ = jev.prepare(spec, args)
    jev.classify(result, .98, args)
    return args, spec, result


def test_apply_backup_evidence_and_idempotence(bundle):
    args, spec, result = bundle
    writer = ResolutionWriter(args, jev.prepare)
    assert writer.apply(result, spec)['status'] == 'applied'
    with sqlite3.connect(args.db) as con:
        status, target, score, raw = con.execute('SELECT locator_status,target_file,target_file_probability,raw_json FROM alphabetical_refs').fetchone()
        assert status == 'resolved' and target == result['ocr_file'] and score is None
        proof = json.loads(raw)['jev_validation']
        assert proof['probability'] == .98 and proof['resolution_level'] == 'ocr_file'
        assert proof['position_status'] == 'not_resolved'
        from tools.export_alphabetical_indices import target_has_verified_evidence
        assert target_has_verified_evidence({'locator_status': status, 'ref_raw_json': raw})
        assert con.execute("SELECT count(*) FROM alphabetical_locator_evidence WHERE evidence_kind='jev_topic_present'").fetchone()[0] == 1
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    with sqlite3.connect(writer.backup) as con:
        assert con.execute('SELECT locator_status FROM alphabetical_refs').fetchone()[0] == 'unresolved'
    assert writer.apply(result, spec)['status'] == 'already_resolved'
    assert writer.applied == 1


@pytest.mark.parametrize('change,status', [('negative', 'not_promotable'), ('review', 'not_promotable'),
    ('near', 'threshold_or_review_band'), ('text', 'stale_or_unverified_evaluation'),
    ('prompt', 'stale_or_unverified_evaluation'), ('row', 'db_changed_since_selection'),
    ('target', 'existing_resolution_preserved')])
def test_unsafe_results_not_applied(bundle, change, status):
    args, spec, result = bundle
    if change == 'negative':result['decision'] = False
    elif change == 'review':result['needs_review'] = True
    elif change == 'near':result['probability'] = .55
    elif change == 'text':Path(result['ocr_file']).write_text('changed')
    elif change == 'prompt':result['payload_sha256'] = 'different'
    elif change == 'row':
        with sqlite3.connect(args.db) as con:con.execute("UPDATE alphabetical_entries SET lemma_raw='Different'")
    elif change == 'target':
        with sqlite3.connect(args.db) as con:con.execute("UPDATE alphabetical_refs SET target_file='/elsewhere.txt',locator_status='resolved'")
    before = args.db.read_bytes()
    writer = ResolutionWriter(args, jev.prepare)
    assert writer.apply(result, spec)['status'] == status
    assert args.db.read_bytes() == before and writer.backup is None


def test_transaction_rolls_back_and_keeps_backup(bundle, monkeypatch):
    args, spec, result = bundle
    from scripts import jev_index_results as storage
    def fail(*a):raise ValueError('forced')
    monkeypatch.setattr(storage, 'refresh_volume_quality', fail)
    writer = ResolutionWriter(args, jev.prepare)
    with pytest.raises(ValueError, match='forced'):writer.apply(result, spec)
    with sqlite3.connect(args.db) as con:
        assert con.execute('SELECT locator_status FROM alphabetical_refs').fetchone()[0] == 'unresolved'
        assert con.execute('SELECT count(*) FROM alphabetical_locator_evidence').fetchone()[0] == 0
    assert Path(writer.backup).exists()


def test_offline_replay_no_key_no_api(bundle, tmp_path, monkeypatch):
    args, spec, result = bundle
    previous = tmp_path/'previous.jsonl'
    previous.write_text(json.dumps(result)+'\n')
    args.apply_log = [previous]
    monkeypatch.delenv('AI_GATEWAY_API_KEY', raising=False)
    monkeypatch.setattr(jev, 'collect_specs', lambda *a: ([spec], []))
    summary = jev.apply_logs(args)
    assert summary['calls'] == 0 and summary['applied'] == 1
    assert 'record_sha256' in args.log.read_text()


def test_replay_torn_tail_and_match_guard(bundle, tmp_path):
    _, _, result = bundle
    path = tmp_path/'old.jsonl'
    path.write_text(json.dumps(result)+'\n{"torn":')
    cached = next(iter(read_evaluations([path]).values()))
    assert matching_evaluation(cached, result)
    changed = {**result, 'ocr_sha256': 'new'}
    assert not matching_evaluation(cached, changed)
    path.write_text('{broken}\n')
    with pytest.raises(ValueError, match='Invalid JSONL'):read_evaluations([path])


def test_resume_negative_without_calling_api(bundle, tmp_path, monkeypatch):
    args, spec, result = bundle
    jev.classify(result, .1, args)
    previous = tmp_path/'previous.jsonl'
    previous.write_text(json.dumps(result)+'\n')
    args.resume_log = [previous]
    args.diagnosis = tmp_path/'diagnosis.json'
    args.marker_report = tmp_path/'markers.json'
    args.diagnosis.write_text('{}'); args.marker_report.write_text('{}')
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'fake-key')
    monkeypatch.setattr(jev, 'collect_specs', lambda *a: ([spec], []))
    summary = jev.run(args)
    assert summary['calls'] == 0 and summary['cache_hits'] == 1 and summary['no'] == 1
    assert summary['cost_usd'] is None and summary['applied'] == 0


@pytest.mark.parametrize('args', [['--no-api','--apply'], ['--no-api','--apply-log','x'],
                                ['--max-cost-usd','nan'], ['--apply-log','x','--apply']])
def test_invalid_modes(args):
    with pytest.raises(SystemExit) as exc:jev.main(args)
    assert exc.value.code == 2


def test_apply_during_run_and_resume_no_double_charge(bundle, tmp_path, monkeypatch):
    args, spec, result = bundle
    args.apply = True
    args.diagnosis = tmp_path/'diagnosis.json'; args.diagnosis.write_text('{}')
    args.marker_report = tmp_path/'markers.json'; args.marker_report.write_text('{}')
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'fake-key')
    monkeypatch.setattr(jev, 'collect_specs', lambda *a: ([spec], []))
    from types import SimpleNamespace
    calls = []
    def post(*a, **kw):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: {'answers': {'topic_present': {'probability': .97}}, 'providerMetadata': {'gateway': {'cost': '0.001'}}})
    monkeypatch.setattr(requests.Session, 'post', post)
    summary = jev.run(args)
    assert summary['applied'] == 1 and summary['calls'] == 1
    previous = args.log
    args.log = tmp_path/'resume.jsonl'
    args.resume_log = [previous]
    with closing(jev.connect_readonly(args.db)) as con:
        spec['row'] = dict(con.execute(jev.QUERY, ('PG001',)).fetchone())
    resumed = jev.run(args)
    assert resumed['calls'] == 0 and resumed['applied'] == 0 and len(calls) == 1
    assert resumed['statuses'] == {'already_resolved': 1}


@pytest.mark.parametrize('cost,reason', [('0.01','cost_budget'), (None,'cost_unavailable')])
def test_cost_guard_stops_before_next_request(bundle, tmp_path, monkeypatch, cost, reason):
    args, spec, result = bundle
    args.max_cost_usd = .005
    args.diagnosis = tmp_path/'diagnosis.json'; args.diagnosis.write_text('{}')
    args.marker_report = tmp_path/'markers.json'; args.marker_report.write_text('{}')
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'fake-key')
    monkeypatch.setattr(jev, 'collect_specs', lambda *a: ([spec, spec], []))
    from types import SimpleNamespace
    calls = []
    def post(*a, **kw):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: {'answers': {'topic_present': {'probability': .97}}, 'providerMetadata': {'gateway': {'cost': cost}}})
    monkeypatch.setattr(requests.Session, 'post', post)
    summary = jev.run(args)
    assert summary['calls'] == 1 and summary['stop_reason'] == reason and len(calls) == 1
