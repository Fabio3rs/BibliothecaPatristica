"""Audit named-index scope and observable pending reasons, without canonical writes.

Classification describes evidence available to the mechanical pipeline, not
proof of an OCR error or of printed-index completeness. Regional assignments
are alternatives; a mixed index is never silently counted as purely onomastic.
"""
from collections import Counter, defaultdict
from contextlib import closing
from functools import lru_cache
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

from tools.corpus_utils import page_number
from tools.indexing.entry_parser_bulk import readonly

VERSION = 'onomastic-pending-audit-v1'
NAMED = {'onomastic_index', 'author_index', 'geographic_index', 'author_and_work_index'}
MIXED = {'subject_and_name_index', 'mixed_index'}
ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1024)
def source_text(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return None


def classify_scope(metadata, assignments):
    reviewed = metadata.get('reviewed_source_section', {})
    alternatives = [dict(a) for a in assignments]
    if reviewed.get('kind'):
        alternatives.append(dict(index_type=reviewed['kind'], evidence_priority=100,
                                 title_raw=reviewed.get('opening_raw', ''), source=metadata.get('source')))
    kind = metadata.get('section_kind') or metadata.get('region_kind')
    if kind:
        alternatives.append(dict(index_type=kind, evidence_priority=60, title_raw='metadata'))
    if not alternatives:
        return 'unclassified', []
    priority = max(a['evidence_priority'] for a in alternatives)
    top = {a['index_type'] for a in alternatives if a['evidence_priority'] == priority}
    named = [a for a in alternatives if a['index_type'] in NAMED]
    if top & NAMED:
        if len(top) > 1 or priority < 60:
            return 'onomastic_possible', alternatives
        return 'onomastic_assigned', alternatives
    if top & MIXED:
        return 'mixed_names_and_other_material', alternatives
    if named:
        return 'onomastic_possible', alternatives
    if top <= {'unknown', 'index_unspecified', 'context', 'unclassified'}:
        return 'unclassified', alternatives
    return 'other_editorial_material', alternatives


def source_checks(item, metadata):
    raw = source_text(item['source'])
    if raw is None:
        return ['source_unreadable'], None
    ref = item['ref_raw'] or ''
    span = metadata.get('reference_span') or metadata.get('source_span')
    if not ref.strip():
        return ['empty_reference'], raw
    if (isinstance(span, list) and len(span) == 2 and all(isinstance(n, int) for n in span)
            and 0 <= span[0] < span[1] <= len(raw) and raw[span[0]:span[1]].strip() == ref.strip()):
        return ['literal_reference_at_span'], raw
    count = raw.count(ref.strip())
    return [('reference_relocatable' if count == 1 else
             'reference_ambiguous_without_span' if count else 'reference_not_literal_in_current_source')], raw


def reason(item, metadata, signals):
    """Return one exclusive operational queue; secondary signals stay separate."""
    if 'source_unreadable' in signals:
        return 'source_unavailable'
    if 'reference_not_literal_in_current_source' in signals or 'reference_ambiguous_without_span' in signals:
        return 'source_or_span_reconciliation'
    ref = item['ref_raw'] or ''
    flags = metadata.get('group_flags', []) + metadata.get('flags', [])
    if any(re.search(r'concaten|ownership_conflict|overlapping', str(f), re.I) for f in flags):
        return 'segmentation_flagged'
    if metadata.get('textual_cross_reference') or re.search(r'\b(?:vide|voir|cf)\b|^\s*v\.', ref, re.I):
        return 'cross_reference_join'
    if re.search(r'\b(?:ibid|ibidem|eodem|idem)\b', ref, re.I):
        return 'editorial_inheritance'
    ns = metadata.get('reference_namespace', '')
    atoms = metadata.get('locators', [])
    if any(a.get('uncertainty') for a in atoms):
        return 'locator_interpretation_uncertain'
    if ns in {'chapter', 'chapter_number', 'book_chapter', 'internal_text_number', 'logion', 'dual_canonical_numbers'}:
        return 'internal_numbering_map'
    if (atoms or metadata.get('page_line_tokens') or metadata.get('page_candidates_numbers')
            or metadata.get('reference_interpretation', {}).get('notation_status') == 'interpreted_page_line'):
        return 'parsed_locator_without_destination'
    if metadata.get('page_line_pending') or metadata.get('reference_interpretation', {}).get('pending'):
        return 'locator_grammar_pending'
    if re.search(r'\d', ref):
        return 'numeric_reference_without_current_diagnostic'
    if re.search(r'\b[IVXLCDM]+\b', ref):
        return 'roman_reference_namespace_needed'
    if not ref.strip():
        return 'no_printed_locator_extracted'
    return 'textual_reference_uninterpreted'


def matched_scope(item, metadata, rules):
    for rule in rules:
        if item['volume'] != rule['volume']:
            continue
        physical = page_number(Path(item['source']))
        if physical is None or not rule['start_physical'] <= physical <= rule['end_physical']:
            continue
        opening = source_text(str(ROOT/rule['opening_source']))
        if opening is None or rule['opening_literal'] not in opening:
            continue
        if physical == rule['start_physical']:
            span = metadata.get('reference_span') or metadata.get('source_span')
            if not span or span[0] < opening.index(rule['opening_literal']):
                continue
        return rule
    return None


def reviewed_scope_conflict(item, metadata, raw, rules):
    if raw is None:
        return None
    evidence = metadata.get('namespace_evidence', {})
    quote = evidence.get('raw', '')
    if re.search(r'TABLE DES MATI[ÈE]RES|ORDO RERUM', quote, re.I) and quote in raw:
        return dict(reason='local_non_onomastic_title', title=quote, source=item['source'])
    rule = matched_scope(item, metadata, rules)
    if rule and rule['actual_kind'] not in NAMED | MIXED:
        return dict(reason='documented_other_section', **rule)
    return None


def run(catalog, out, scope_reviews):
    out.mkdir(parents=True, exist_ok=False)
    review_data = json.loads(scope_reviews.read_text())
    rules = review_data['scopes']
    reading_reviews = {}
    for review_file in review_data.get('reading_reviews', []):
        for review in json.loads((ROOT/review_file).read_text())['exclusions']:
            reading_reviews[review['item_id']] = review
    assignments = defaultdict(list)
    counters = defaultdict(Counter)
    reasons = Counter()
    volumes = defaultdict(Counter)
    rows, conflicts, digest = [], [], hashlib.sha256()
    with closing(readonly(catalog)) as db:
        for row in db.execute('SELECT DISTINCT item_id,index_type,title_raw,source,evidence_priority FROM index_source_section_candidates'):
            assignments[row['item_id']].append(dict(row))
        for row in db.execute('SELECT * FROM items ORDER BY id'):
            item = dict(row); metadata = json.loads(item.pop('metadata_json'))
            evidence = list(assignments.get(item['id'], []))
            rule = matched_scope(item, metadata, rules)
            if rule and rule['actual_kind'] in NAMED:
                evidence.append(dict(index_type=rule['actual_kind'], evidence_priority=100,
                                     title_raw=rule['opening_literal'], source=rule['opening_source']))
            scope, alternatives = classify_scope(metadata, evidence)
            linked = item['state'] == 'with_destinations'
            counters[scope]['items'] += 1
            counters[scope]['with_candidates' if linked else 'without_candidates'] += 1
            if scope not in {'onomastic_assigned', 'onomastic_possible', 'mixed_names_and_other_material'}:
                continue
            signals, raw = source_checks(item, metadata)
            conflict = reviewed_scope_conflict(item, metadata, raw, rules)
            primary = 'already_has_candidate' if linked else reason(item, metadata, signals)
            reading_review = reading_reviews.get(item['id'])
            if (reading_review and reading_review['reference_raw'] == item['ref_raw']
                    and (ROOT/reading_review['source']).resolve() == Path(item['source']).resolve()):
                signals.append('facsimile_reading_differs_from_ocr_reference')
                if not linked:
                    primary = 'reviewed_source_reading_conflict'
            if conflict:
                primary = 'section_scope_conflict'
                conflicts.append(dict(item_id=item['id'], volume=item['volume'], linked=linked, evidence=conflict))
            namespace = metadata.get('reference_namespace', '')
            if len(item['label'] or '') > 400:
                signals.append('long_owner_review_signal_not_error')
            record = dict(item_id=item['id'], volume=item['volume'], scope=scope,
                          has_candidate=linked, reason=primary, namespace=namespace,
                          label=item['label'], reference_raw=item['ref_raw'], source=item['source'],
                          source_signals=signals, section_alternatives=alternatives,
                          conflict=conflict, reading_review=reading_review)
            serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
            digest.update((serialized+'\n').encode())
            rows.append(record)
            if not linked:
                reasons[(scope, primary)] += 1
                volumes[item['volume']]['pending'] += 1
                volumes[item['volume']][primary] += 1
    with (out/'records.jsonl').open('w') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n')
    with (out/'pending.csv').open('w') as f:
        columns=['item_id','volume','scope','reason','namespace','label','reference_raw','source']
        writer=csv.DictWriter(f, fieldnames=columns); writer.writeheader()
        writer.writerows({k:r[k] for k in columns} for r in rows if not r['has_candidate'])
    summary=dict(version=VERSION,catalog=str(catalog.resolve()),by_scope=dict(counters),
                 pending_reasons=[dict(scope=s,reason=r,count=n) for (s,r),n in sorted(reasons.items())],
                 pending_by_volume=dict(sorted(volumes.items())),
                 conflicts=len(conflicts), pending_conflicts=sum(not c['linked'] for c in conflicts),
                 logical_sha256=digest.hexdigest(),
                 source_pages_read=source_text.cache_info().misses,
                 canonical_updates=0,catalog_updates=0,
                 quality='Operational evidence categories, not verified error causes. Missing extraction has no item denominator.')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (out/'scope_conflicts.json').write_text(json.dumps(conflicts,ensure_ascii=False,indent=2)+'\n')
    lines=['# Auditoria de pendências onomásticas','',summary['quality'],'',
           '| Escopo | Registros | Com candidato | Sem candidato |','|---|---:|---:|---:|']
    for scope,c in sorted(counters.items()):
        lines.append(f"| {scope} | {c['items']} | {c['with_candidates']} | {c['without_candidates']} |")
    lines+=['','| Escopo | Fila operacional | Pendentes |','|---|---|---:|']
    for (s,r),n in sorted(reasons.items()):lines.append(f'| {s} | {r} | {n} |')
    lines+=['',f'Conflitos de seção: {len(conflicts)} registros, {summary["pending_conflicts"]} sem destino.',
            'Atribuições regionais são candidatas. Conflitos permanecem no inventário, sem apagamento ou reclassificação canônica.',
            'Ausência de correspondência literal pode indicar spans antigos, normalização, parser ou OCR; não prova falha do OCR.',
            'Índices mistos e regiões não classificadas não podem ser descartados como irrelevantes.',
            'Ter algum destino não implica resolver todas as referências de uma entrada.']
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--scope-reviews', type=Path, default=ROOT/'docs/fixtures/index_rebuild/onomastic_scope_reviews.json')
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.catalog,args.out,args.scope_reviews),ensure_ascii=False,indent=2))
