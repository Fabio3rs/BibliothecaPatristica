"""Run in NativeLab: actual HTTP + spawned pool, only a local fake Gateway."""
import json
from pathlib import Path

from test_alphabetical_semantic_check import sample, run
from test_jev_gateway_pool import server
from tools.indexing import alphabetical_semantic_check as sem


def test_stage_real_http_body_auth_and_apply(sample,server,monkeypatch):
    endpoint,state=server
    monkeypatch.setattr(sem,'ENDPOINT',endpoint)
    monkeypatch.setenv('AI_GATEWAY_API_KEY','local-test-key')
    summary=run(sample,sem.JevOptions(workers=2,apply=True))
    assert summary['calls']==1 and summary['applied']==1
    assert state['requests'][0]['state'].endswith(sample.target.read_text())
    assert state['authorizations']==['Bearer local-test-key']
    assert list(state['requests'][0]['questions'])==['topic_present']
    assert summary['cost_usd']==.01
    assert 'local-test-key' not in Path(summary['log']).read_text()


def test_stage_real_http_retry_limit(sample,server,monkeypatch):
    endpoint,state=server
    state['status']=429
    monkeypatch.setattr(sem,'ENDPOINT',endpoint)
    monkeypatch.setenv('AI_GATEWAY_API_KEY','local-test-key')
    summary=run(sample,sem.JevOptions(workers=2,limit=2,max_retries=5,apply=True))
    assert summary['calls']==2 and summary['applied']==0
    assert len(state['requests'])==2
    assert summary['stop_reason']=='limit'
    assert summary['statuses']=={'api_error':1}
