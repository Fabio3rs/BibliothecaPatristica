"""Real process workers with a local fake Gateway; run inside Native Lab."""
import json

from test_jev_gateway_pool import server
from test_deepscan_index_jev import material, records
from test_jev_index_results import bundle
from scripts import deepscan_index_jev as deep


def test_parallel_windows_and_resume(server,material,monkeypatch,tmp_path):
    args=material;args.workers=2
    monkeypatch.setattr(deep.jev,'ENDPOINT',server[0])
    before=args.db.read_bytes()
    summary=deep.run(args)
    assert summary['calls']==2 and summary['window_statuses']=={'multiple_positives':1}
    assert summary['pool']['max_in_flight']==2
    assert server[1]['peak']==2 and args.db.read_bytes()==before
    assert all(list(p['questions'])==['topic_present'] for p in server[1]['requests'])
    args.resume_log=[args.log];args.log=tmp_path/'resumed.jsonl';args.limit=0
    summary=deep.run(args)
    assert summary['calls']==0 and summary['cache_hits']==2
    assert summary['complete_windows']==1 and len(server[1]['requests'])==2


def test_parallel_limit_retains_incomplete_window(server,material,monkeypatch):
    args=material;args.workers=2;args.limit=1
    monkeypatch.setattr(deep.jev,'ENDPOINT',server[0])
    summary=deep.run(args)
    assert summary['calls']==1 and summary['window_statuses']=={'incomplete':1}
    assert summary['stop_reason']=='limit'
    window=next(r for r in records(args) if r['record_type']=='window_result')
    assert window['single_positive_file'] is None and not window['promotion_allowed']
