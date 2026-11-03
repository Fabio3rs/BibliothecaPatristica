"""Report distinctions that must survive mixed v2/v3 experiments."""
from tools.knowledge_graph.playground.experiment_runner import plan_experiment, execute_experiment
from tools.knowledge_graph.playground.reporting import report, markdown
from tools.knowledge_graph.playground.snapshot_v2 import demo_snapshot
from tools.knowledge_graph.playground.storage import Store


def test_mixed_contract_report_separates_projection_and_unresolved_positions(tmp_path):
    snapshot = demo_snapshot()
    block = snapshot['ocr']['blocks'][0]
    block['text'] += '\n' + block['text']
    store = Store(tmp_path / 'lab.db')
    case = store.case(snapshot)
    experiment = plan_experiment(store, [case], [{'provider': 'fixture', 'model': 'demo'}],
        variants=['concepts-v2.7-compact-confidence', 'concepts-v3-statements-confidence'])
    execute_experiment(store, experiment)
    data = report(store, experiment)
    rows = {r['variant']: r for r in data['runs']}
    old = rows['concepts-v2.7-compact-confidence']
    new = rows['concepts-v3-statements-confidence']
    assert old['relations'] > 0 and old['projected_relations'] == 0
    assert new['relations'] == 0 and new['projected_relations'] == 3
    assert new['evidence_valid'] and new['ambiguous_quotes'] > 0 and not new['exact_evidence_valid']
    assert data['summary']['ambiguous_quotes'] > 0
    assert data['precision_recall'] is None
    text = markdown(data)
    lines = [line for line in text.splitlines() if line.startswith('|')]
    headers = [s.strip() for s in lines[0].split('|')[1:-1]]
    records = [dict(zip(headers, [s.strip() for s in line.split('|')[1:-1]])) for line in lines[2:]]
    displayed = next(row for row in records if row['Run'] == str(new['run_id']))
    assert displayed['Model relations'] == '0' and displayed['Projected relations'] == '3'
    assert displayed['Exact / approximate / ambiguous / segmented quotes'].split(' / ')[2] == str(new['ambiguous_quotes'])
    assert displayed['Exact / approximate / ambiguous / segmented quotes'].split(' / ')[3] == str(new['segmented_quotes'])
    assert all(len(line.split('|')) == len(lines[0].split('|')) for line in lines)
