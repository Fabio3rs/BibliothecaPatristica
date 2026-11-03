"""No network: all paid endpoints are replaced with local fakes."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest
import requests

from scripts import validate_index_jev as jev
from scripts.alphabetical_index_db import connect_db, init_schema


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected network access")
    monkeypatch.setattr(requests.Session, "post", forbidden)
    monkeypatch.setattr(requests.Session, "get", forbidden)


@pytest.fixture
def material(tmp_path):
    root = tmp_path / 'PG001' / 'text'
    root.mkdir(parents=True)
    target = root / 'page-002.txt'
    target.write_text('<pagina><bloco tipo="cabecalho">101 HISTORIA 102</bloco>'
                      '<bloco tipo="texto_principal">Athanasius contra Arianos disputavit. '
                      'Non est lex talionis necessaria.</bloco></pagina>')
    row = {'volume_id': 'PG001', 'ref_id': 1, 'entry_key': 'PG001:e', 'section_key': 'PG001:s',
           'lemma_raw': 'Athanasius', 'entry_raw': 'Athanasius 101', 'ref_raw': '101',
           'page_ref_int': 101, 'ref_kind': 'editorial_page', 'section_kind': 'analytic_subject'}
    spec = {'row': row, 'files': [{'file': str(target)}], 'root': str(root), 'intervals': [],
            'category': 'analytic-no-literal', 'stale': False, 'blocked': None,
            'marker': None, 'visual_check': None, 'numbering_scope': None,
            'report': 'fixture.json', 'report_sha256': 'abc',
            'locator_reason': 'no_body_text_match', 'proposal': False}
    args = jev.parser().parse_args(['--no-api', '--log', str(tmp_path / 'result.jsonl')])
    args.corrected_report_dir = tmp_path / 'corrected'
    args.diagnosis = tmp_path / 'diagnosis.json'
    args.diagnosis.write_text('{}')
    args.marker_report = tmp_path / 'markers.json'
    args.marker_report.write_text(json.dumps({'results': [], 'profile': {'volume': 'PG040'}}))
    return spec, args


def fake_session(monkeypatch, responses):
    calls = []
    def post(self, url, **kwargs):
        calls.append((url, kwargs))
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(requests.Session, 'post', post)
    return calls


def response(p=.93, status=200, **extras):
    body = {'answers': {'topic_present': {'probability': p}}, **extras}
    return SimpleNamespace(status_code=status, json=lambda: body)


def test_payload_exactly_one_boolean_and_complete_ocr(material):
    spec, args = material
    out, payload = jev.prepare(spec, args)
    original = Path(spec['files'][0]['file']).read_text()
    assert payload['state'].endswith(original)
    assert list(payload['questions']) == ['topic_present']
    assert payload['questions']['topic_present']['type'] == 'boolean'
    assert 'Athanasius' in payload['questions']['topic_present']['instructions']
    assert out['status'] == 'prepared'
    assert 'payload' not in out and 'ocr_text' not in out
    args.include_payload = args.include_text = True
    detailed, _ = jev.prepare(spec, args)
    assert detailed['payload'] == payload and detailed['ocr_text'] == original
    assert detailed['ocr_sha256'] == hashlib.sha256(original.encode()).hexdigest()


@pytest.mark.parametrize('p', [0, 1, .9341234567890123])
def test_probability_precision(p):
    assert jev.parse_answer(response(p).json()) == p


@pytest.mark.parametrize('p', [None, True, '0.5', -1, 1.1, float('nan'), float('inf')])
def test_invalid_probability(p):
    with pytest.raises(ValueError, match='invalid_probability'):
        jev.parse_answer(response(p).json())


@pytest.mark.parametrize('body', [None, [], {}, {'answers': {'topic_present': {'noul': .8}}}])
def test_missing_probability(body):
    with pytest.raises(ValueError, match='missing_probability'):
        jev.parse_answer(body)


@pytest.mark.parametrize('change,status,reason', [
    ('missing', 'ocr_unavailable', 'missing_or_invalid_utf8_ocr'),
    ('multiple', 'candidate_ambiguous', 'multiple_physical_pages'),
    ('none', 'candidate_not_found', 'no_supported_physical_page'),
    ('large', 'ocr_unavailable', 'ocr_too_large_no_truncation'),
    ('stale', 'candidate_not_found', 'report_does_not_match_current_db'),
    ('grouped', 'candidate_ambiguous', 'known_grouped_entry'),
    ('marker', 'candidate_ambiguous', 'marker_span_not_verified'),
    ('changed', 'candidate_not_found', 'candidate_ocr_changed_since_location'),
    ('unreadable', 'ocr_unavailable', 'insufficient_readable_body'),
])
def test_preflight_failure_states(material, change, status, reason):
    spec, args = material
    if change == 'missing':
        Path(spec['files'][0]['file']).unlink()
    elif change == 'multiple':
        spec['files'].append({'file': 'second.txt'})
    elif change == 'none':
        spec['files'] = []
    elif change == 'large':
        args.max_input_bytes = 10
    elif change == 'stale':
        spec['stale'] = True
    elif change == 'grouped':
        spec['blocked'] = 'known_grouped_entry'
    elif change == 'marker':
        spec['marker'] = {'status': 'unique'}
    elif change == 'changed':
        spec['files'][0]['expected_sha256'] = 'wrong'
    elif change == 'unreadable':
        Path(spec['files'][0]['file']).write_text('<pagina><bloco tipo="cabecalho">101 102</bloco></pagina>')
    out, payload = jev.prepare(spec, args)
    assert payload is None
    assert out['status'] == status and out['skip_reason'] == reason


def test_neighbor_evidence_without_target_header(material):
    spec, args = material
    root = Path(spec['root'])
    target = root / 'page-005.txt'
    target.write_text('<pagina><bloco tipo="texto_principal">Athanasius contra Arianos disputavit.</bloco></pagina>')
    anchors = []
    for seq in [3, 4, 6, 7]:
        nums = [101 + 2*(seq-5), 102 + 2*(seq-5)]
        path = root / f'page-{seq:03d}.txt'
        path.write_text(f'<pagina><bloco tipo="cabecalho">{nums[0]} HISTORIA {nums[1]}</bloco></pagina>')
        anchors.append({'file': str(path), 'observed_pages': nums})
    spec['files'] = [{'file': str(target), 'sequence_validation': {
        'validated': True, 'method': 'bracketed_header_sequence', 'pages': [101, 102], 'anchors': anchors}}]
    out, payload = jev.prepare(spec, args)
    assert payload and out['candidate_source'] == 'neighbors' and out['printed_header'] == ''
    Path(anchors[0]['file']).write_text('<pagina><bloco tipo="cabecalho">1 2</bloco></pagina>')
    out, payload = jev.prepare(spec, args)
    assert payload is None and out['skip_reason'] == 'stale_or_conflicting_neighbor_evidence'


def test_gateway_retry_counts_global_budget_and_usage(material, monkeypatch):
    _, args = material
    args.limit = 2
    calls = fake_session(monkeypatch, [response(status=429), response(.9123456789012345,
        usage={'inputTokens': 321}, providerMetadata={'gateway': {'cost': '0.00001234', 'marketCost': .00002}})])
    monkeypatch.setattr(jev.time, 'sleep', lambda _: None)
    client = jev.Gateway(args, time.monotonic()+10, 'TEST_SECRET')
    attempts = client.evaluate({'questions': {}})
    assert client.calls == len(calls) == 2
    assert attempts[-1]['probability'] == .9123456789012345
    assert attempts[-1]['input_tokens'] == 321 and attempts[-1]['output_tokens'] is None
    assert attempts[-1]['cost_usd'] == '0.00001234'
    assert client.evaluate({}) == []
    assert 'TEST_SECRET' not in json.dumps(attempts)
    assert calls[0][1]['headers']['Authorization'] == 'Bearer TEST_SECRET'
    assert calls[0][1]['allow_redirects'] is False


@pytest.mark.parametrize('failure,error', [
    (requests.Timeout('secret'), 'timeout'), (requests.ConnectionError('secret'), 'transport_error'),
    (response(status=401), 'http_error'), (response(status=302), 'http_error'),
    (SimpleNamespace(status_code=200, json=lambda: {}), 'missing_probability'),
    (response(None), 'invalid_probability'),
])
def test_gateway_errors_sanitized(material, monkeypatch, failure, error):
    _, args = material
    fake_session(monkeypatch, [failure])
    client = jev.Gateway(args, time.monotonic()+10, 'secret')
    out = client.evaluate({})
    assert out[-1]['error'] == error and 'secret' not in json.dumps(out)
    assert client.calls == 1


def test_invalid_json(material, monkeypatch):
    _, args = material
    def invalid():
        raise ValueError('server echoed secret')
    fake_session(monkeypatch, [SimpleNamespace(status_code=200, json=invalid)])
    out = jev.Gateway(args, time.monotonic()+10, 'secret').evaluate({})
    assert out[0]['error'] == 'missing_probability'
    assert 'secret' not in json.dumps(out)


def run_fixture(material, monkeypatch, specs):
    _, args = material
    monkeypatch.setattr(jev, 'collect_specs', lambda *_: (specs, []))
    return args


def test_no_api_without_key_and_preview_limit(material, monkeypatch):
    spec, _ = material
    args = run_fixture(material, monkeypatch, [spec, spec, spec])
    monkeypatch.delenv('AI_GATEWAY_API_KEY', raising=False)
    args.limit = 2
    summary = jev.run(args)
    assert summary['calls'] == 0 and summary['prepared'] == summary['examined'] == 2


def test_limit_skips_dont_consume_calls_near_threshold_review(material, monkeypatch):
    spec, _ = material
    skipped = deepcopy(spec)
    skipped['files'] = []
    args = run_fixture(material, monkeypatch, [skipped, spec, spec, spec])
    args.no_api = False
    args.limit = 2
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'secret')
    calls = fake_session(monkeypatch, [response(.5), response(.1)])
    summary = jev.run(args)
    assert summary['calls'] == len(calls) == 2 and summary['examined'] == 3
    assert summary['yes'] == summary['no'] == 1
    logs = [json.loads(line) for line in args.log.read_text().splitlines()]
    assert 'secret' not in args.log.read_text()
    rows = [r for r in logs if r['record_type'] == 'association']
    assert rows[1]['needs_review'] and 'near_experimental_threshold' in rows[1]['review_reasons']
    assert summary['calls_missing_cost'] == 2 and summary['cost_usd'] is None


def test_no_key_and_existing_log_protected(material, monkeypatch):
    spec, args = material
    args.no_api = False
    monkeypatch.delenv('AI_GATEWAY_API_KEY', raising=False)
    with pytest.raises(ValueError, match='AI_GATEWAY_API_KEY'):
        jev.run(args)
    args.no_api = True
    args.log.write_text('KEEP')
    with pytest.raises(FileExistsError):
        jev.run(args)
    assert args.log.read_text() == 'KEEP'


def test_mixed_sampling_reproducible():
    specs = [{'category': cat, 'id': i} for cat in ('internal-marker', 'analytic-no-literal', 'mechanical') for i in range(10)]
    one = jev.mixed_order(deepcopy(specs), 42, 'mixed')
    assert one == jev.mixed_order(deepcopy(specs), 42, 'mixed')
    assert one != jev.mixed_order(deepcopy(specs), 3, 'mixed')
    assert len({s['category'] for s in one[:3]}) == 3


def test_readonly_report_adapter_and_stale_db(material, tmp_path):
    spec, args = material
    args.db = tmp_path/'indices.db'
    args.report_dir = tmp_path/'reports'
    args.report_dir.mkdir()
    con = connect_db(args.db)
    init_schema(con)
    con.execute("INSERT INTO alphabetical_volumes (volume_id,collection,source_root,created_at,updated_at) VALUES ('PG001','PG',?,'now','now')", (spec['root'],))
    con.execute("INSERT INTO alphabetical_sections (section_key,volume_id,section_order,section_kind,heading_raw,file_start,file_end,raw_json) VALUES ('PG001:s','PG001',1,'analytic_subject','INDEX',?,?,'{}')", (str(Path(spec['root'])/'page-099.txt'),)*2)
    con.execute("INSERT INTO alphabetical_entries (entry_key,section_key,entry_order,entry_kind,lemma_raw,entry_raw,raw_json) VALUES ('PG001:e','PG001:s',1,'lemma','Athanasius','Athanasius 101','{}')")
    con.execute("INSERT INTO alphabetical_refs (entry_key,ref_order,ref_kind,ref_raw,page_ref_int,raw_json) VALUES ('PG001:e',1,'editorial_page','101',101,'{}')")
    con.commit()
    con.row_factory = sqlite3.Row
    row = dict(con.execute(jev.QUERY, ('PG001',)).fetchone())
    report = {'job': {'volume_id': 'PG001', 'source_root': spec['root'], 'rows': [row]},
              'records': [{'ref_id': row['ref_id'], 'reason': 'no_body_text_match', 'locator_result': {
                  'file_search': {'complete_for_literal_headers': True, 'candidates': spec['files']}}}]}
    (args.report_dir/'PG001.json').write_text(json.dumps(report))
    con.close()
    before = args.db.read_bytes()
    specs, reports = jev.collect_specs(args, time.monotonic()+10)
    assert len(specs) == 1 and specs[0]['category'] == 'analytic-no-literal'
    assert jev.prepare(specs[0], args)[1]
    assert args.db.read_bytes() == before and reports[0]['sha256']
    report['job']['rows'][0]['lemma_raw'] = 'Wrong snapshot'
    (args.report_dir/'PG001.json').write_text(json.dumps(report))
    stale, _ = jev.collect_specs(args, time.monotonic()+10)
    assert jev.prepare(stale[0], args)[0]['skip_reason'] == 'report_does_not_match_current_db'
    args.corrected_report_dir.mkdir()
    row['lemma_raw'] = 'Athanasius'
    report['job']['rows'][0] = row
    (args.corrected_report_dir/'PG001.json').write_text(json.dumps(report))
    corrected, _ = jev.collect_specs(args, time.monotonic()+10)
    assert len(corrected) == 1 and not corrected[0]['stale']
    assert corrected[0]['report'] == str(args.corrected_report_dir/'PG001.json')
    with sqlite3.connect(args.db) as con:
        con.execute("UPDATE alphabetical_refs SET ref_kind='target_locator'")
    filtered, _ = jev.collect_specs(args, time.monotonic()+10)
    assert filtered == []


@pytest.mark.parametrize('argv', [['--limit','-1'], ['--threshold','nan'], ['--max-seconds','inf'], ['--max-retries','6']])
def test_invalid_cli_options(argv):
    with pytest.raises(SystemExit) as exc:
        jev.main(argv)
    assert exc.value.code == 2


def test_reviewed_marker_payload(material):
    spec, args = material
    source = Path(spec['root']) / 'index-099.txt'
    source.write_text('Numeri signant paginas editionis Antverpiensis.')
    spec['marker'] = {'status': 'unique', 'reference': 101}
    spec['visual_check'] = {'target_physical_sequence': 2, 'cited_page': 101}
    spec['numbering_scope'] = {'source_file': str(source), 'source_sha256': jev.sha(source.read_bytes())}
    out, payload = jev.prepare(spec, args)
    assert payload and out['candidate_source'] == 'internal-marker'
    source.write_text('changed')
    out, payload = jev.prepare(spec, args)
    assert payload is None and out['skip_reason'] == 'numbering_scope_source_changed'


def test_payload_size_guard_does_not_truncate(material):
    spec, args = material
    args.max_input_bytes = Path(spec['files'][0]['file']).stat().st_size + 10
    out, payload = jev.prepare(spec, args)
    assert payload is None and out['skip_reason'] == 'payload_too_large_no_truncation'


def test_expired_budget_never_calls_http(material):
    _, args = material
    client = jev.Gateway(args, time.monotonic()-1, 'secret')
    assert client.evaluate({}) == [] and client.calls == 0


def test_mixed_prioritizes_reviewed_marker():
    specs = [{'category': 'internal-marker', 'visual_check': None, 'id': i} for i in range(100)]
    reviewed = {'category': 'internal-marker', 'visual_check': {'ref_id': 1}, 'id': 101}
    specs.append(reviewed)
    assert jev.mixed_order(specs, 42, 'mixed')[0] == reviewed


@pytest.mark.parametrize('status,reason', [(401, 'authentication_error'), (402, 'billing_error'), (403, 'authorization_error')])
def test_terminal_http_error_details_and_stop(material, monkeypatch, capsys, status, reason):
    spec, _ = material
    args = run_fixture(material, monkeypatch, [spec, spec])
    args.no_api = False
    args.verbose = True
    args.limit = 1
    key = 'TEST_GATEWAY_SECRET'
    monkeypatch.setenv('AI_GATEWAY_API_KEY', key)
    body = {'error': {'code': 'access_denied', 'message': f'Request denied for Bearer {key}'}}
    reply = SimpleNamespace(status_code=status, json=lambda: body,
                            headers={'x-request-id': 'req-test', 'authorization': key})
    calls = fake_session(monkeypatch, [reply])
    summary = jev.run(args)
    assert len(calls) == 1 and summary['stop_reason'] == reason
    log = args.log.read_text()
    output = capsys.readouterr().out
    assert key not in log + output
    assert 'access_denied' in log and 'req-test' in log
    assert f'HTTP: {status}' in output and 'Request denied' in output
    row = next(json.loads(line) for line in log.splitlines() if json.loads(line).get('record_type') == 'association')
    assert row['review_reasons'] == ['header_only_location_without_neighbor_confirmation', reason]
    assert row['gateway_error']['code'] == 'access_denied'


def test_non_json_error_is_bounded_and_redacted_before_truncation():
    key = 'A' * 1800
    reply = SimpleNamespace(text=f'Forbidden Bearer {key}\napi_key="ANOTHER_SECRET" ' + 'x'*3000)
    out = jev.safe_gateway_error(reply, None, key)
    assert len(out['message']) <= 1500
    assert key not in str(out) and 'A'*100 not in str(out) and 'ANOTHER_SECRET' not in str(out)
    assert '[REDACTED]' in out['message']


def test_check_credits_only_gets_metadata(material, monkeypatch, capsys):
    _, args = material
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'secret')
    calls = []
    def get(self, url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {'balance': '0.00', 'total_used': '5.00', 'unexpected': 'secret'})
    monkeypatch.setattr(requests.Session, 'get', get)
    jev.main(['--check-credits', '--log', str(args.log), '--db', '/does/not/exist'])
    output = json.loads(capsys.readouterr().out)
    assert output['balance'] == '0.00' and output['inference_calls'] == 0
    assert 'secret' not in str(output) and not args.log.exists()
    assert len(calls) == 1 and calls[0][0].endswith('/v1/credits')
    assert calls[0][1]['allow_redirects'] is False


def test_check_credits_error_sanitized(material, monkeypatch, capsys):
    _, args = material
    monkeypatch.setenv('AI_GATEWAY_API_KEY', 'secret')
    monkeypatch.setattr(requests.Session, 'get', lambda *a, **kw: SimpleNamespace(
        status_code=403, json=lambda: {'error': {'code': 'access_denied', 'message': 'Denied secret'}}))
    out = jev.check_credits(args)
    assert out['http_status'] == 403 and out['gateway_error']['code'] == 'access_denied'
    assert 'secret' not in capsys.readouterr().out


def test_no_api_blocks_credit_lookup():
    with pytest.raises(SystemExit) as exc:
        jev.main(['--no-api', '--check-credits'])
    assert exc.value.code == 2
