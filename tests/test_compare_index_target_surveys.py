import json

from scripts.compare_index_target_surveys import compare


def test_compare_uses_printed_provenance_not_reference_ids(tmp_path):
    for label, ident, candidates in [('before','old',['a']), ('after','new',['a','b'])]:
        d = tmp_path/label/'volumes/PG001'; d.mkdir(parents=True)
        rows = [{'id':ident,'source':'s','source_sha256':'h','char_start':2,'char_end':4,
                 'raw':'10','status':'candidate'},
                {'id':'pending','source':'s','source_sha256':'h','char_start':5,'char_end':7,
                 'raw':'20','status':'candidate'}]
        (d/'groups.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        refs = [{'group_id':ident,'files':[{'file':p} for p in candidates]}]
        if label == 'after':
            refs += [{'group_id':'pending','files':[{'path':'c'}]}]
        (d/'occurrences.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in refs))
    result = compare(tmp_path/'before',tmp_path/'after',tmp_path/'out')
    c = result['counts']
    assert c['same_source_groups'] == 2
    assert c['gained_first_candidate'] == 1 and c['lost_all_candidates'] == 0
    assert c['added_group_file_pairs'] == 2 and c['removed_group_file_pairs'] == 0
