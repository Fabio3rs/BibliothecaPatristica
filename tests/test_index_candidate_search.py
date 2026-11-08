import json
import sqlite3

import pytest

from tools.indexing.candidate_search import build, diagnose, evaluate, rank_candidates, ranking_metrics, search


def record(targets=()):
    return {'id': 'r1', 'group_id': 'g1', 'volume': 'PG001', 'query': 'Ἀαρών',
            'ref_raw': '123', 'source': 'index.txt', 'tier': 'unmapped_namespace_screen',
            'status': 'no_lexical_match', 'verified': False, 'import_ready': False,
            'targets': list(targets)}


def test_numeric_only_ambiguous_and_stale_candidates_remain_navigable():
    r = record([{'file': 'a.txt', 'error': 'changed_target'}, {'file': 'b.txt'}])
    candidates = rank_candidates(r, lambda p: True)
    assert [c['file'] for c in candidates] == ['a.txt', 'b.txt']
    assert all(c['navigable'] and not c['certified'] for c in candidates)
    assert 'changed_target' in candidates[0]['uncertainties']
    assert 'editorial_namespace_unconfirmed' in candidates[0]['uncertainties']


def test_unsupported_marker_is_lower_ranked_without_being_excluded():
    r = record([{'file':'weak','candidate_basis':'header_without_sequence'}, {'file':'supported'}])
    r['reference_interpretation'] = {'uncertainties':['line_uncertain']}
    ranked = rank_candidates(r, lambda _: True)
    assert [c['file'] for c in ranked] == ['supported','weak']
    assert ranked[0]['score'] == 10 and ranked[1]['score'] == 5
    assert all('line_uncertain' in c['uncertainties'] for c in ranked)


def test_empty_lemma_does_not_drop_numeric_file_candidates(tmp_path):
    target = tmp_path/'target.txt'; target.write_text('text')
    audit, survey = tmp_path/'audit.sqlite', tmp_path/'survey.sqlite'
    r = record(); r.update(query='', status='empty_query')
    with sqlite3.connect(audit) as con:
        con.execute('CREATE TABLE evidence(volume,id,evidence_json)')
        con.execute('INSERT INTO evidence VALUES(?,?,?)', ('PG001','r1',json.dumps(r)))
    with sqlite3.connect(survey) as con:
        con.executescript('CREATE TABLE groups(volume,id,evidence_json); CREATE TABLE occurrences(id,evidence_json);')
        con.execute('INSERT INTO occurrences VALUES(?,?)', ('r1',json.dumps({
            'group_id':'g1','files':[{'path':str(target),'physical':2}]})))
    result = build(audit,survey,tmp_path/'out')
    assert result['counts']['empty_query_numeric_candidates_retained'] == 1
    assert result['counts']['with_destinations'] == 1
    with sqlite3.connect(tmp_path/'out/search.sqlite') as con:
        c = json.loads(con.execute('SELECT candidate_json FROM candidates').fetchone()[0])
    assert 'lexical_query_missing' in c['uncertainties'] and c['navigable']


def test_full_fuzzy_outweighs_first_word_exact_and_does_not_discard_other_files():
    candidates = rank_candidates(record([
        {'file': 'weak', 'current': {'method': 'exact', 'query_scope': 'first_token_only'}},
        {'file': 'strong', 'alternatives': [{'method': 'fuzzy', 'query_scope': 'full_label',
                                           'version_id': 7, 'page_identity': 'volume_page_registration'}]},
        {'file': 'number_only'}, {'file': 'missing'}]), lambda p: p != 'missing')
    assert [c['file'] for c in candidates] == ['strong', 'weak', 'number_only', 'missing']
    assert candidates[0]['evidence']['alternatives'][0]['version_id'] == 7
    assert candidates[0]['score_kind'] == 'heuristic_not_probability'


def test_multiple_gold_missing_queries_and_incomplete_judgments():
    m = ranking_metrics(['wrong', 'correct', 'correct'], ['correct', 'other'], ['wrong'])
    assert m['reciprocal_rank'] == .5
    assert m['Recall@5'] == .5
    assert m['Precision@5'] == .2
    assert m['candidate_coverage'] == .5
    assert ranking_metrics([], ['correct'])['Recall@1'] == 0
    assert ranking_metrics(['unknown', 'correct'], ['correct'])['Precision@5'] is None
    assert ranking_metrics(['unknown', 'correct'], ['correct'], gold_complete=True)['Precision@5'] == .2
    with pytest.raises(ValueError):
        ranking_metrics([], ['a'], ['a'])


def test_build_preserves_pending_groups_searches_greek_and_does_not_write_inputs(tmp_path):
    target = tmp_path / 'target.txt'
    target.write_text('OCR sem o nome')
    audit, survey = tmp_path / 'audit.sqlite', tmp_path / 'survey.sqlite'
    with sqlite3.connect(audit) as con:
        con.execute('CREATE TABLE evidence(volume,id,evidence_json)')
        r = record([{'file': str(target), 'physical': 1}])
        con.execute('INSERT INTO evidence VALUES(?,?,?)', ('PG001', r['id'], json.dumps(r)))
    with sqlite3.connect(survey) as con:
        con.execute('CREATE TABLE groups(volume,id,evidence_json)')
        for ident in ['g1', 'pending']:
            g = {'id': ident, 'volume': 'PG001', 'owner_raw': 'Ἀαρών', 'raw': '124',
                 'source': 'index.txt', 'char_start': 0, 'char_end': 3,
                 'source_sha256': 'old', 'status': 'segmentation_or_notation_review'}
            con.execute('INSERT INTO groups VALUES(?,?,?)', ('PG001', ident, json.dumps(g)))
    before = audit.read_bytes(), survey.read_bytes(), target.read_bytes()
    out = tmp_path / 'out'
    summary = build(audit, survey, out)
    assert summary['counts']['searchable_items'] == 2
    assert summary['counts']['with_destinations'] == 1
    assert summary['counts']['source_only'] == 1
    assert summary['counts']['groups_without_audited_occurrences'] == 1
    rows = search(out / 'search.sqlite', 'Ααρων')
    assert len(rows) == 2
    assert all(r['metadata']['canonical_write_allowed'] is False for r in rows)
    pending = next(r for r in rows if r['id'] == 'group:pending')
    assert pending['state'] == 'source_only' and not pending['candidates']
    assert search(out / 'search.sqlite', '" OR *') == []
    assert before == (audit.read_bytes(), survey.read_bytes(), target.read_bytes())
    review = tmp_path / 'reviews.json'
    review.write_text(json.dumps({'samples': [
        {'id': 'r1', 'reviewed': True, 'positive_files': [str(target)]},
        {'id': 'not_exported', 'reviewed': True, 'positive_files': ['other']},
        {'id': 'unreviewed', 'reviewed': False}]}))
    report = evaluate(out / 'search.sqlite', review)
    assert report['reviewed_queries'] == 2
    assert report['metrics']['MRR'] == .5
    assert report['metrics']['Recall@20'] == .5
    assert report['pending_queries'] == ['unreviewed']
    assert summary['canonical_updates'] == 0
    diagnostic = diagnose(out / 'search.sqlite')
    assert diagnostic['source_only'] == 1
    assert diagnostic['statuses'] == {'segmentation_or_notation_review': 1}
    assert diagnostic['simple_numeric_lists_by_status'] == {'segmentation_or_notation_review': 1}
    assert diagnostic['with_destinations_by_audit_status'] == {'no_lexical_match': 1}
