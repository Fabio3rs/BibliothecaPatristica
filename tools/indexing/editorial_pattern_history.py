"""Bounded OCR-history checks for locator patterns; differences are not corrections."""
from collections import Counter
from contextlib import closing
from pathlib import Path
import re

from scripts.audit_po015_names_lexical import history_page_identity
from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.ocr_variant_audit import tokens
from tools.indexing.entry_parser_experiment import digest


def locations(values, needle):
    return [i for i in range(len(values)-len(needle)+1) if values[i:i+len(needle)] == needle]


def anchored_reference(current_tokens, alternative_tokens, span):
    """Find a unique pair of four-token flanks, never a naked numeric match."""
    a,b=span
    selected=[i for i,t in enumerate(current_tokens) if a <= t['span'][0] < t['span'][1] <= b]
    if not selected or selected[0]<4 or selected[-1]+5>len(current_tokens):
        return None
    start,end=selected[0],selected[-1]+1
    left=[t['token'] for t in current_tokens[start-4:start]]
    right=[t['token'] for t in current_tokens[end:end+4]]
    values=[t['token'] for t in current_tokens]
    other=[t['token'] for t in alternative_tokens]
    if len(locations(values,left))!=1 or len(locations(values,right))!=1:
        return None
    lpos,rpos=locations(other,left),locations(other,right)
    if len(lpos)!=1 or len(rpos)!=1 or not lpos[0]+4<=rpos[0]:
        return None
    x,y=lpos[0]+4,rpos[0]
    if y-x>max(40,4*(end-start)):
        return None
    # Include intervening punctuation, which tokenization deliberately excludes.
    left_end=alternative_tokens[x-1]['span'][1]
    right_start=alternative_tokens[y]['span'][0]
    return {'alternative_span':[left_end,right_start],
            'left_anchor':left,'right_anchor':right,
            'current_tokens':[t['token'] for t in current_tokens[start:end]],
            'alternative_tokens':other[x:y]}


def extraction_diagnostics(group, raw):
    if not group:
        return ['group_missing']
    if digest(raw.encode()) != group.get('source_sha256'):
        return ['source_changed_realign_before_attribution']
    a,b=group['char_start'],group['char_end']
    if not 0<=a<b<=len(raw) or raw[a:b]!=group['raw']:
        return ['stored_reference_span_mismatch']
    result=[]
    if re.search(r'<[^>]+>',group['raw']):
        result.append('inline_markup_in_stored_reference')
    owner=group.get('owner_raw','')
    # This shows context split between fields, not that it was lost downstream.
    if re.search(r'\b[IVXLCDM]+\s*[,;.]?\s*$',owner) and re.match(r'\s*\d',group['raw']):
        result.append('roman_context_in_owner_not_reference')
    if len(re.findall(r'\d+[.;]\s*\n\s*[^\W\d_]',owner))>=2:
        result.append('multiple_entry_like_lines_in_owner')
    return result or ['literal_span_matches_no_specific_extraction_error_detected']


def audit_history(patterns, groups, cache, database, root, out, max_pages=8, max_groups=5):
    if max_pages<1 or max_groups<1:
        raise ValueError('History limits must be positive')
    # Round-robin volumes, highest pending impact first, avoids one volume
    # consuming the entire small diagnostic budget.
    queues={}
    for p in patterns:
        if p['source'] not in [x['source'] for x in queues.get(p['volume'],[])]:
            queues.setdefault(p['volume'],[]).append(p)
    chosen=[]
    while len(chosen)<max_pages and any(queues.values()):
        for volume in sorted(queues):
            if queues[volume] and len(chosen)<max_pages:
                chosen.append(queues[volume].pop(0))
    snapshot_dir=out/'history_sources';snapshot_dir.mkdir()
    pages,checks,stats=[],[],Counter()
    with closing(readonly(database)) as db:
        db.execute('BEGIN')
        for pattern in chosen:
            source=pattern['source'];volume=pattern['volume'];physical=pattern['physical']
            current=cache.get(str(Path(source).resolve()))
            if not current:
                pages.append({'source':source,'status':'source_unavailable'});continue
            current_raw=current['raw'];current_tokens=tokens(current_raw)
            current_path=snapshot_dir/f'{volume}-{physical}-current-{current["sha256"][:12]}.txt'
            current_path.write_text(current_raw)
            # Prefer groups with explicit field-boundary issues, retain negatives too.
            group_ids=list(dict.fromkeys(g for p in patterns if p['source']==source for g in p['group_ids']))
            group_ids.sort(key=lambda g:(extraction_diagnostics(groups.get(g),current_raw)==
                ['literal_span_matches_no_specific_extraction_error_detected'],g))
            group_ids=group_ids[:max_groups]
            image=root/volume/'images'/f'{volume}-{physical:03d}.png'
            if not image.exists(): image=root/volume/'images'/f'{volume}-{physical}.png'
            image_hash=digest(image.read_bytes()) if image.exists() else None
            page={'source':source,'volume':volume,'physical':physical,'current_sha256':current['sha256'],
                  'snapshot':str(current_path.resolve()),'current_matching_versions':[],
                  'selected_group_ids':group_ids,'versions':[]}
            for row in db.execute('''SELECT r.id,r.text_content,r.text_hash,r.image_hash,r.is_current,
                    e.engine,e.model FROM ocr_results r JOIN ocr_engine_versions e ON e.id=r.engine_version_id
                    WHERE r.volume_id=? AND r.page_num=? ORDER BY r.id''',(volume,physical)):
                version=dict(row);alternative=version.pop('text_content');checksum=digest(alternative.encode())
                version['computed_sha256']=checksum
                version['page_identity']=history_page_identity(volume,physical,version['image_hash'],image_hash)
                version['text_matches_current']=alternative==current_raw
                version['recorded_text_hash_matches']=version['text_hash']==checksum
                snapshot=snapshot_dir/f'version-{version["id"]}.txt';snapshot.write_text(alternative)
                version['snapshot']=str(snapshot.resolve());page['versions'].append(version)
                stats['versions_examined']+=1
                if version['text_matches_current']:
                    page['current_matching_versions'].append(version['id']);continue
                if version['page_identity'].startswith('excluded_'):
                    stats['structural_history_exclusions']+=1;continue
                alt_tokens=tokens(alternative)
                for ident in group_ids:
                    group=groups.get(ident)
                    diagnostics=extraction_diagnostics(group,current_raw)
                    record={'group_id':ident,'source':source,'version_id':version['id'],
                            'engine':version['engine'],'model':version['model'],
                            'owner_raw':group.get('owner_raw') if group else None,
                            'extraction_diagnostics':diagnostics,'page_identity':version['page_identity'],
                            'status':'not_aligned','automatic_repair':False}
                    if not group or diagnostics[0] in {'stored_reference_span_mismatch','source_changed_realign_before_attribution'}:
                        record['status']='current_group_requires_realignment'
                    else:
                        a,b=group['char_start'],group['char_end']
                        record['current_reference']={'span':[a,b],'quote':current_raw[a:b]}
                        aligned=anchored_reference(current_tokens,alt_tokens,[a,b])
                        if aligned:
                            x,y=aligned['alternative_span'];record.update(aligned)
                            record['alternative_quote']=alternative[x:y]
                            old_numbers=[t for t in aligned['current_tokens'] if t.isdigit()]
                            new_numbers=[t for t in aligned['alternative_tokens'] if t.isdigit()]
                            record['status']=('numeric_reading_disagreement' if old_numbers!=new_numbers else
                                'other_reading_disagreement' if aligned['current_tokens']!=aligned['alternative_tokens'] else
                                'same_reference_tokens')
                    stats[record['status']]+=1;checks.append(record)
            pages.append(page)
    stats['distinct_groups_selected'] = len({g for p in pages for g in p.get('selected_group_ids',[])})
    stats['distinct_groups_with_numeric_disagreement'] = len({r['group_id'] for r in checks if r['status']=='numeric_reading_disagreement'})
    return {'pages':pages,'checks':checks,'counts':dict(stats),
            'selection':'bounded_round_robin_volume_high_impact_pages_not_random_sample',
            'limits':'Disagreement identifies an OCR-version issue, not which reading matches print. Not aligned is not evidence of omission.'}
