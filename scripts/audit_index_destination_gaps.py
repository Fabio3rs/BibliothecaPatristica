#!/usr/bin/env python3
"""Explain source-only search records using saved surveys; no inference or DB writes."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import hashlib
from itertools import groupby
import json
from pathlib import Path
import random
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.indexing.entry_parser_bulk import readonly

VERSION = 'destination-gap-audit-v2'


def notation_shape(raw):
    value = ' '.join((raw or '').split()).strip(' .;,')
    if re.fullmatch(r'ibid(?:em)?', value, re.I):
        return 'ibid_only'
    if re.search(r'\bibid', value, re.I):
        return 'ibid_mixed'
    if re.search(r'\bsuppl', value, re.I):
        return 'supplement_locator'
    if re.search(r'\bregistr|neg\.\s*imp', value, re.I):
        return 'register_locator'
    if re.match(r'[IVXLCDM]+\s*[,.:]?\s+[0-9]|[IVXLCDM]+\s*,\s*[0-9]', value):
        return 'roman_and_arabic'
    if re.search(r'\b(?:et\s+)?(?:seq[q]?|sqq?|sq)\b', value, re.I):
        return 'following_pages_qualifier'
    if re.search(r'\b(?:n|not|adnot|nota)\.?\s*[0-9]', value, re.I):
        return 'note_locator'
    if re.search(r'[0-9]\s*[-–—]\s*[0-9]', value):
        return 'numeric_range'
    if re.search(r'[₀-₉]|[0-9]_[0-9]', value):
        return 'subscript_detail'
    if re.fullmatch(r'[0-9]+(?:\s*[,;]\s*[0-9]+)*', value):
        if any(n.startswith('0') for n in re.findall(r'[0-9]+', value)):
            return 'zero_or_zero_prefixed_numeric'
        return 'simple_numeric'
    if re.search(r'[0-9]\s*[, ]?\s*[A-Da-d](?:\b|[.,])', value):
        return 'letter_subdivision'
    return 'other_notation'


def header_evidence(directory, wanted):
    sources_path = directory/'sources.jsonl'
    selected = {json.loads(line)['source'] for line in sources_path.open()} if sources_path.exists() else set()
    markers = directory/'header_markers.jsonl'
    if not markers.exists():
        return {}, selected, False
    found = defaultdict(set)
    for line in markers.open():
        m = json.loads(line)
        if m['number'] in wanted:
            found[m['number']].add(m.get('path', m.get('file')))
    return found, selected, True


def overlay_state(db, paths):
    """Resolve item availability separately from group-level recovery evidence."""
    recovered, by_item, by_group, inputs = set(), defaultdict(list), defaultdict(list), []
    for path in paths:
        with closing(readonly(path)) as con:
            con.execute('BEGIN')
            summary = json.loads(con.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
            if Path(summary['catalog']).resolve() != db.resolve():
                raise ValueError(f'Overlay belongs to another catalog: {path}')
            recovered.update(r[0] for r in con.execute('SELECT DISTINCT item_id FROM candidates WHERE navigable=1'))
            columns = {r[1] for r in con.execute('PRAGMA table_info(relations)')}
            scope = 'item' if 'item_id' in columns else 'group'
            key = 'item_id' if scope == 'item' else 'group_id'
            for ident, status, raw in con.execute(f'SELECT {key},status,evidence_json FROM relations'):
                evidence = json.loads(raw)
                trace = {'database':str(path.resolve()), 'scope':scope, 'status':status,
                         'version':evidence.get('version'), 'raw':evidence.get('raw', evidence.get('ref_raw')),
                         'relations':evidence.get('relations', []), 'parsed':evidence.get('parsed'),
                         'source_state':evidence.get('source_state')}
                (by_item if scope == 'item' else by_group)[ident].append(trace)
            inputs.append({'path':str(path.resolve()), 'version':summary.get('version')})
    return recovered, by_item, by_group, inputs


def recovery_cause(traces, fallback):
    """Refine a failure only when the saved localizer explains it explicitly."""
    for trace in reversed(traces):
        status = trace['status']
        if status == 'locator_without_opening':
            return 'epistle_locator_without_recognized_opening'
        if status == 'year_without_mapped_table':
            return 'chronological_year_without_mapped_table'
        if status == 'source_span_changed':
            return 'recovery_source_span_changed'
        if status == 'unsupported_reference_list':
            return 'recovery_reference_list_unsupported'
        if status == 'relation_without_destination':
            reasons = sorted({r['reason'] for r in trace['relations'] if r.get('reason')})
            if len(reasons) == 1:
                return 'editorial_relation:' + reasons[0]
            if reasons:
                return 'editorial_relation:multiple_pending_reasons'
            if any(r.get('kind') == 'explicit_numeric_anchor' for r in trace['relations']):
                return 'qualified_numeric_anchor_without_destination'
    return fallback


def processing_stage(reason, traces):
    statuses = {t['status'] for t in traces}
    if statuses & {'locator_without_opening', 'year_without_mapped_table'}:
        return 'specialized_recovery_executed_without_destination'
    if statuses & {'relation_without_destination', 'unsupported_reference_list', 'source_span_changed'}:
        return 'editorial_recovery_executed_without_destination'
    if reason in {'segmentation_or_notation_review', 'notation_pending', 'profile_notation_pending'}:
        return 'reference_grammar_pending_no_applicable_saved_recovery'
    if reason == 'namespace_not_mapped':
        return 'editorial_series_map_not_available'
    if reason == 'source_or_span_invalid':
        return 'source_alignment_refresh_required'
    if reason == 'no_header_coincidence':
        return 'generic_numeric_lookup_executed_without_destination'
    return 'specialized_map_or_context_pending'


def audit(db, survey, out, seed=20261106, sample_size=4, relations_db=()):
    if sample_size < 1:
        raise ValueError('sample_size must be positive')
    out.mkdir(parents=True, exist_ok=False)
    recovered, item_traces, group_traces, overlay_inputs = overlay_state(db, relations_db)
    totals, reasons, shapes, causes = (Counter() for _ in range(4))
    by_volume, cross = defaultdict(Counter), Counter()
    samples, seen = defaultdict(list), Counter()
    randomizer = random.Random(seed)
    source_checks = {}
    stages, collections, tiers, group_flags, original_causes = (Counter() for _ in range(5))
    with closing(readonly(db)) as con, (out/'items.jsonl').open('w') as handle:
        con.execute('BEGIN')
        linked_groups = set()
        baseline_counts = Counter()
        recovered_in_base = set()
        for ident, group, state in con.execute('SELECT id,group_id,state FROM items'):
            baseline_counts[state] += 1
            if state == 'with_destinations' or ident in recovered:
                linked_groups.add(group)
            if state == 'source_only' and ident in recovered:
                recovered_in_base.add(ident)
        pending_groups = set()
        cursor = con.execute("SELECT * FROM items WHERE state='source_only' ORDER BY volume,id")
        for volume, volume_rows in groupby(cursor, key=lambda r:r['volume']):
            rows = [row for row in volume_rows if row['id'] not in recovered]
            if not rows:
                continue
            wanted = {int(r['ref_raw']) for r in rows if re.fullmatch(r'[0-9]+',r['ref_raw'] or '')}
            markers, selected, available = header_evidence(survey/'volumes'/volume, wanted)
            for row in rows:
                meta = json.loads(row['metadata_json'])
                reason = meta.get('original_status', meta.get('status','unknown'))
                shape = notation_shape(row['ref_raw'])
                cause = reason
                details = {}
                if reason == 'no_header_coincidence':
                    number = int(row['ref_raw']) if re.fullmatch(r'[0-9]+',row['ref_raw'] or '') else None
                    found = markers.get(number, set())
                    if not available:
                        cause = 'header_inventory_unavailable'
                    elif not found:
                        cause = 'no_literal_header_marker_in_survey'
                    elif found <= selected:
                        cause = 'header_marker_only_in_selected_index_sources'
                    else:
                        cause = 'header_marker_present_but_not_emitted'
                    details = {'number':number,'header_files':sorted(found),
                               'meaning':'Marker presence is not proof of the cited numbering system.'}
                elif reason == 'source_or_span_invalid':
                    key = (row['source'], meta.get('source_sha256'))
                    if key not in source_checks:
                        path = Path(row['source'])
                        if not path.is_file():
                            source_checks[key] = {'state':'source_missing','actual_sha256':None}
                        else:
                            actual = hashlib.sha256(path.read_bytes()).hexdigest()
                            source_checks[key] = {'state':'source_text_changed' if actual != key[1] else 'source_current_span_or_entry_invalid',
                                                  'actual_sha256':actual}
                    details = source_checks[key]
                    cause = details['state']
                elif reason in {'segmentation_or_notation_review','notation_pending','profile_notation_pending'}:
                    cause = 'notation:' + shape
                elif reason == 'namespace_not_mapped':
                    cause = 'unmapped_namespace:' + meta.get('reference_interpretation',{}).get('series_key','unknown')
                original_causes[cause] += 1
                # A group trace applies to this printed reference only if its raw
                # notation agrees; another interpretation's success is not ours.
                all_traces = group_traces.get(row['group_id'], []) + item_traces.get(row['id'], [])
                traces = [t for t in all_traces if t['scope']=='item' or
                          ' '.join((t.get('raw') or '').split()) == ' '.join((row['ref_raw'] or '').split())]
                cause = recovery_cause(traces, cause)
                stage = processing_stage(reason, traces)
                record = {'id':row['id'],'group_id':row['group_id'],'volume':volume,
                          'label':row['label'],'ref_raw':row['ref_raw'],'source':row['source'],
                          'source_span':meta.get('source_span'),'source_sha256':meta.get('source_sha256'),
                          'status':meta.get('status'),'original_reason':reason,'cause':cause,'notation_shape':shape,
                          'processing_stage':stage,'recovery_traces':traces,
                          'other_group_recovery_traces':len(all_traces)-len(traces),
                          'group_has_other_destination':row['group_id'] in linked_groups,
                          'reference_interpretation':meta.get('reference_interpretation',{}),
                          'flags':meta.get('group_flags',[]),'evidence':details}
                handle.write(json.dumps(record,ensure_ascii=False)+'\n')
                totals['source_only_items'] += 1
                totals['items_with_other_destination_in_same_group'] += record['group_has_other_destination']
                reasons[reason] += 1; shapes[shape] += 1; causes[cause] += 1
                by_volume[volume][cause] += 1; cross[reason,shape] += 1
                stages[stage] += 1; collections[volume[:2]] += 1; tiers[meta.get('tier','unspecified')] += 1
                group_flags.update(set(record['flags']))
                pending_groups.add(row['group_id'])
                # Fixed-seed reservoir avoids examples biased to the first volume.
                seen[cause] += 1
                if len(samples[cause]) < sample_size:
                    samples[cause].append(record)
                else:
                    choice = randomizer.randrange(seen[cause])
                    if choice < sample_size:
                        samples[cause][choice] = record
        totals['distinct_source_only_groups'] = len(pending_groups)
        totals['groups_also_having_destinations'] = len(pending_groups & linked_groups)
        totals['groups_without_any_destination'] = len(pending_groups - linked_groups)
        totals['volumes_with_pending_items'] = len(by_volume)
        totals['baseline_source_only_items'] = baseline_counts['source_only']
        totals['recovered_by_supplied_overlays'] = len(recovered_in_base)
        assert totals['source_only_items'] == baseline_counts['source_only'] - len(recovered_in_base)
    with (out/'by_volume_cause.csv').open('w') as f:
        writer = csv.writer(f); writer.writerow(['volume','cause','items'])
        for volume, counts in sorted(by_volume.items()):
            writer.writerows((volume,cause,n) for cause,n in counts.most_common())
    summary = {'version':VERSION,'input_db':str(db.resolve()),'survey':str(survey.resolve()),
               'overlays':overlay_inputs,'processing_stages':dict(stages.most_common()),
               'collections':dict(collections),'source_tiers':dict(tiers.most_common()),
               'group_flags_nonexclusive':dict(group_flags.most_common()),
               'baseline_causes_for_remaining_items':dict(original_causes.most_common()),
               'seed':seed,'sample_size_per_cause':sample_size,'totals':dict(totals),
               'original_reasons':dict(reasons.most_common()),'causes':dict(causes.most_common()),
               'notation_shapes':dict(shapes.most_common()),
               'reason_by_shape':[{'reason':r,'shape':s,'items':n} for (r,s),n in cross.most_common()],
               'top_volumes':dict(Counter({v:sum(c.values()) for v,c in by_volume.items()}).most_common(20)),
               'source_checks':[{'source':p,'expected_sha256':h,**check} for (p,h),check in source_checks.items()],
               'limits':'Counts are search interpretations, not printed entries. Syntax classes do not establish editorial meaning. No missing OCR or physical page inferred from absence in the header inventory.',
               'canonical_updates':0,'inference_calls':0}
    overview = {
        'Numeração sem candidato no mapa genérico':stages['generic_numeric_lookup_executed_without_destination'],
        'Heranças e qualificadores tentados, ainda sem destino':stages['editorial_recovery_executed_without_destination']-causes['recovery_source_span_changed'],
        'Notações sem recuperação aplicável registrada':stages['reference_grammar_pending_no_applicable_saved_recovery'],
        'Mapas especializados, limites e contexto pendentes':stages['specialized_map_or_context_pending']+stages['specialized_recovery_executed_without_destination'],
        'Fonte ou spans desatualizados':stages['source_alignment_refresh_required']+causes['recovery_source_span_changed'],
        'Séries editoriais sem mapa':stages['editorial_series_map_not_available'],
    }
    assert sum(overview.values()) == totals['source_only_items']
    summary['overview'] = overview
    for name, data in [('summary.json',summary),('samples.json',samples)]:
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    report = ['# Itens sem destino: auditoria mecânica', '', summary['limits'], '',
              f"Itens pendentes na combinação consultada: **{totals['source_only_items']:,}**.", '',
              '| Grupo de diagnóstico | Itens |','| --- | ---: |']
    report += [f'| {k} | {v} |' for k,v in overview.items()]
    report += ['', '## Detalhamento das causas registradas', '',
              '| Causa observada | Itens |','| --- | ---: |']
    report += [f'| {k} | {v} |' for k,v in causes.most_common()]
    report += ['', '## Volumes com mais pendências', '', '| Volume | Itens |', '| --- | ---: |']
    report += [f'| {k} | {v} |' for k,v in summary['top_volumes'].items()]
    report += ['', 'Detalhes: items.jsonl; amostra reproduzível: samples.json; distribuição: by_volume_cause.csv.',
               '', 'Nenhuma alteração nos bancos consultados ou atribuição automática de destino.']
    (out/'report.md').write_text('\n'.join(report)+'\n')
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=Path('data/index_catalog/search_candidates_002/search.sqlite'))
    p.add_argument('--survey',type=Path,default=Path('data/index_entry_parser/global_targets_20261106_004'))
    p.add_argument('--seed',type=int,default=20261106)
    p.add_argument('--sample-size',type=int,default=4)
    p.add_argument('--relations-db',type=Path,action='append',default=[],
                   help='Recovery overlay; repeat to audit the combined search state')
    p.add_argument('--out',type=Path,required=True)
    a = p.parse_args()
    print(json.dumps(audit(a.db,a.survey,a.out,a.seed,a.sample_size,a.relations_db),ensure_ascii=False,indent=2))
