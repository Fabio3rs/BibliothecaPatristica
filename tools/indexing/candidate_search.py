"""Searchable historical index candidates; canonical certification is separate."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing, ExitStack
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from scripts.audit_po015_names_lexical import normalize, tokens
from scripts.catalog_po015_names import sha
from tools.indexing.entry_parser_bulk import readonly

VERSION = 'index-candidate-search-v2'
CUTOFFS = (1, 5, 10, 20)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def rank_candidates(record, exists=lambda p: Path(p).is_file()):
    """Heuristic score, not a probability; absence of lexical evidence is allowed."""
    unique = {}
    for target in record.get('targets', []):
        path = target['file']
        numeric = 40 if record.get('tier') in {'documented_profile', 'po015_catalog'} else 10
        reasons = ['documented_pagination' if numeric == 40 else 'numeric_candidate']
        warnings = list(record.get('group_flags', []))
        if numeric == 10:
            warnings.append('editorial_namespace_unconfirmed')
        interpretation = record.get('reference_interpretation', {})
        warnings.extend(interpretation.get('uncertainties', []))
        warnings.extend(interpretation.get('flags', []))
        if ('without_sequence' in target.get('candidate_basis', '') or
                target.get('evidence_strength') == 'literal_marker_without_sequence'):
            numeric //= 2
            reasons.append('literal_marker_without_sequence')
            warnings.append('pagination_sequence_unconfirmed')
        if interpretation.get('candidate_role', '').startswith('alternative_'):
            numeric //= 2
            warnings.append(interpretation['candidate_role'])
        if target.get('error'):
            warnings.append(target['error'])
        readings = []
        if target.get('current'):
            readings.append({**target['current'], 'source': 'current'})
        readings.extend({**a, 'source': 'history'} for a in target.get('alternatives', []))
        lexical, best = 0, None
        for reading in readings:
            method = reading.get('method')
            if method not in {'exact', 'normalized', 'fuzzy'}:
                continue
            full = reading.get('query_scope') == 'full_label'
            score = ({'exact': 60, 'normalized': 55, 'fuzzy': 40} if full else
                     {'exact': 10, 'normalized': 8, 'fuzzy': 4})[method]
            if reading.get('page_identity') == 'replacement_source_needs_review':
                score //= 2
                warnings.append('replacement_source_needs_review')
            if score > lexical:
                lexical, best = score, reading
        if best:
            reasons.append(best['query_scope'] + '_' + best['method'])
            if best['query_scope'] != 'full_label':
                warnings.append('partial_label_match')
        else:
            warnings.append('no_lexical_support')
        accessible = exists(path)
        if not accessible:
            warnings.append('file_unavailable')
        item = {'file': path, 'physical': target.get('physical'),
                'score': numeric + lexical, 'score_kind': 'heuristic_not_probability',
                'reasons': reasons, 'uncertainties': sorted(set(warnings)),
                'navigable': accessible, 'certified': False,
                'snippet': next(iter((best or {}).get('matches', [])), {}).get('snippet', ''),
                # Full evidence remains in the audit, keyed by occurrence ID and file.
                'evidence': target}
        if path not in unique or item['score'] > unique[path]['score']:
            unique[path] = item
    ranked = sorted(unique.values(), key=lambda c: (not c['navigable'], -c['score'], c['file']))
    for rank, item in enumerate(ranked, 1):
        item['rank'] = rank
    return ranked


def build(audit, survey, out):
    out.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    db = out / 'search.sqlite'
    with ExitStack() as resources, closing(readonly(survey)) as numeric_source, sqlite3.connect(db) as dest:
        source=resources.enter_context(closing(readonly(audit))) if audit else None
        dest.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE items(id TEXT PRIMARY KEY, group_id TEXT, volume TEXT, label TEXT,
                ref_raw TEXT, source TEXT, state TEXT, metadata_json TEXT);
            CREATE TABLE candidates(item_id TEXT, file TEXT, rank INTEGER, score REAL,
                navigable INTEGER, candidate_json TEXT, PRIMARY KEY(item_id,file));
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);
            CREATE VIRTUAL TABLE items_fts USING fts5(label,ref_raw,volume,
                tokenize='unicode61 remove_diacritics 2');''')
        exists = lru_cache(maxsize=None)(lambda p: Path(p).is_file())
        represented, replaced_sources = set(), set()

        def insert(record):
            ranked = rank_candidates(record, exists)
            searchable = bool((record.get('query') or record.get('ref_raw') or '').strip())
            state = 'with_destinations' if any(c['navigable'] for c in ranked) else 'source_only'
            metadata = {k: v for k, v in record.items() if k != 'targets'}
            metadata.update(searchable=searchable, canonical_status='unreviewed',
                            canonical_write_allowed=False, candidate_count=len(ranked),
                            audit_file=str(audit.resolve()) if audit else None,
                            audit_id=record.get('audit_id', record['id']) if audit else None)
            cursor = dest.execute('INSERT INTO items VALUES(?,?,?,?,?,?,?,?)',
                (record['id'], record['group_id'], record['volume'], record.get('query', ''),
                 record.get('ref_raw', ''), record['source'], state, encoded(metadata)))
            dest.execute('INSERT INTO items_fts(rowid,label,ref_raw,volume) VALUES(?,?,?,?)',
                (cursor.lastrowid, normalize(record.get('query', '')),
                 normalize(record.get('ref_raw', '')), record['volume']))
            for c in ranked:
                dest.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',
                    (record['id'], c['file'], c['rank'], c['score'], c['navigable'], encoded(c)))
            counts['search_items'] += 1
            counts['searchable_items'] += searchable
            counts[state] += 1
            counts['candidate_links'] += len(ranked)
            counts['navigable_candidate_links'] += sum(c['navigable'] for c in ranked)
            counts['items_with_multiple_candidates'] += len(ranked) > 1
            counts['items_with_destinations_without_lexical_support'] += bool(ranked) and all(
                'no_lexical_support' in c['uncertainties'] for c in ranked)

        records=(json.loads(row[0]) for row in source.execute('SELECT evidence_json FROM evidence ORDER BY volume,id')) if source else numeric_records(numeric_source,survey)
        for record in records:
            # Older lexical audits omit targets when there is no usable lemma.
            # Their numeric evidence remains useful for reference/source browsing.
            if record.get('status') == 'empty_query' and not record.get('targets'):
                numeric_row = numeric_source.execute(
                    'SELECT evidence_json FROM occurrences WHERE id=?', (record['id'],)).fetchone()
                if numeric_row:
                    numeric = json.loads(numeric_row[0])
                    if numeric['group_id'] != record['group_id']:
                        raise ValueError('Numeric occurrence and lexical audit disagree on source group')
                    record['targets'] = [{**f, 'file': f.get('file', f.get('path')),
                        'error': 'lexical_query_missing', 'current': None, 'alternatives': []}
                        for f in numeric.get('files', [])]
                    record['numeric_evidence_source'] = str(survey.resolve())
                    counts['empty_query_numeric_candidates_retained'] += bool(record['targets'])
            represented.add(record['group_id'])
            if record.get('selection') == 'po015_catalog_override':
                replaced_sources.add(record['source'])
            insert(record)
        counts['audited_occurrences'] = counts['search_items'] if audit else 0
        if not audit:
            counts['numeric_only_occurrences']=counts['search_items']
        # Uninterpreted groups are still searchable, linking back to the index.
        with closing(readonly(survey)) as original:
            for row in original.execute('SELECT evidence_json FROM groups ORDER BY volume,id'):
                g = json.loads(row[0])
                if g['id'] in represented or g['source'] in replaced_sources:
                    continue
                insert({'id': 'group:' + g['id'], 'group_id': g['id'], 'volume': g['volume'],
                    'query': g['owner_raw'], 'ref_raw': g['raw'], 'source': g['source'],
                    'source_span': [g['char_start'], g['char_end']], 'source_sha256': g['source_sha256'],
                    'status': g['status'], 'group_flags': g.get('flags', []), 'targets': [],
                    'audit_id': None, 'survey_file': str(survey.resolve()), 'survey_group_id': g['id']})
                counts['groups_without_audited_occurrences'] += 1
        dest.executescript('CREATE INDEX candidates_rank ON candidates(item_id,rank);'
                           'CREATE INDEX items_group ON items(group_id);')
        summary = {'version': VERSION, 'counts': dict(counts),
            'distinct_reference_groups': dest.execute('SELECT count(DISTINCT group_id) FROM items').fetchone()[0],
            'volumes_with_items': dest.execute('SELECT count(DISTINCT volume) FROM items').fetchone()[0],
            'destination_coverage': counts['with_destinations'] / max(1, counts['search_items']),
            'audited_destination_coverage': counts['with_destinations'] / max(1, counts['audited_occurrences']) if audit else None,
            'canonical_updates': 0, 'input_audit': str(audit.resolve()) if audit else None, 'input_survey': str(survey.resolve()),
            'lexical_audit_performed':bool(audit),
            'policy': 'Searchable candidates do not require individual certification. No canonical writes.',
            'ranking': 'Pagination 40 / numeric 10; halve for unsupported sequence and again for alternative interpretation; full exact 60, normalized 55, fuzzy 40; partial 10/8/4; no minimum cutoff.',
            'code_sha256': sha(Path(__file__))}
        dest.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
        dest.commit()
        assert dest.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return summary


def numeric_records(db, survey):
    """Keep each printed occurrence and alternatives without inventing lexical evidence."""
    for row in db.execute('''SELECT o.evidence_json AS occurrence,g.evidence_json AS owner
            FROM occurrences o JOIN groups g ON g.id=o.group_id ORDER BY g.volume,o.id'''):
        ref,group=json.loads(row['occurrence']),json.loads(row['owner'])
        span=ref.get('source_span') or [ref.get('char_start',group['char_start']),
                                      ref.get('char_end',group['char_end'])]
        yield {'id':ref['id'],'group_id':group['id'],'volume':group['volume'],
            'query':ref.get('owner_raw',group['owner_raw']),
            'ref_raw':ref.get('ref_raw',ref.get('raw',group['raw'])),
            'source':group['source'],'source_span':span,'source_sha256':group['source_sha256'],
            'group_flags':group.get('flags',[]),'status':'lexical_audit_not_run',
            'tier':ref['tier'],'reference_interpretation':ref,
            'survey_file':str(survey.resolve()),'survey_group_id':group['id'],
            'targets':[{**f,'file':f.get('file',f.get('path'))} for f in ref.get('files',[])],
            'verified':False,'import_ready':False}


def search(db, query, limit=20, candidate_limit=20, relations_db=None, exclude_ids=()):
    words = [t['norm'] for t in tokens(query)]
    if not words:
        return []
    expression = ' AND '.join('"' + w.replace('"', '""') + '"' for w in words)
    paths = list(relations_db) if isinstance(relations_db, (list, tuple)) else [relations_db] if relations_db else []
    with closing(readonly(db)) as con, ExitStack() as stack:
        overlays = [(path,stack.enter_context(closing(readonly(path)))) for path in paths]
        for path,relation_db in overlays:
            summary = json.loads(relation_db.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()[0])
            if Path(summary['catalog']).resolve() != Path(db).resolve():
                raise ValueError('Editorial relations belong to a different catalog')
        rows = con.execute('''SELECT i.* FROM items_fts JOIN items i ON i.rowid=items_fts.rowid
            WHERE items_fts MATCH ? AND i.id NOT IN (SELECT value FROM json_each(?))
            ORDER BY bm25(items_fts),i.id LIMIT ?''', (expression, encoded(sorted(exclude_ids)), limit))
        result = []
        for row in rows:
            item = dict(row)
            item['metadata'] = json.loads(item.pop('metadata_json'))
            candidates = {c['file']:c for c in (json.loads(r[0]) for r in con.execute(
                'SELECT candidate_json FROM candidates WHERE item_id=? ORDER BY rank', (row['id'],)))}
            if overlays:
                for path,relation_db in overlays:
                    for r in relation_db.execute('SELECT candidate_json FROM candidates WHERE item_id=?', (row['id'],)):
                        c = json.loads(r[0])
                        c['recovery_database'] = str(Path(path).resolve())
                        old = candidates.get(c['file'])
                        if old is None:
                            candidates[c['file']] = c
                        else:
                            winner,other = (c,old) if (c['navigable'],c['score']) > (old['navigable'],old['score']) else (old,c)
                            winner.setdefault('additional_support',[]).append(other)
                            candidates[c['file']] = winner
                if any(c['navigable'] for c in candidates.values()) and item['state']=='source_only':
                    item['baseline_state'] = item['state']
                    item['state'] = 'with_destinations'
                    item['metadata']['editorial_relations_dbs'] = [str(Path(path).resolve()) for path in paths]
                    if len(paths)==1:
                        item['metadata']['editorial_relations_db'] = str(Path(paths[0]).resolve())
            item['metadata']['candidate_count'] = len(candidates)
            ranked = sorted(candidates.values(), key=lambda c:(not c['navigable'],-c['score'],c['file']))
            item['candidates'] = [{**c,'rank':n} for n,c in enumerate(ranked[:candidate_limit],1)]
            result.append(item)
        return result


def search_with_supplements(db, query, limit=20, candidate_limit=20, relations_db=None, supplements=()):
    """Federate supplemental entries; leave canonical catalogs and IDs intact."""
    sources = [(db, relations_db)]
    paths = list(dict.fromkeys(Path(p).resolve() for p in supplements))
    replacements = {}
    for path in paths:
        with closing(readonly(path)) as con:
            row = con.execute("SELECT json FROM metadata WHERE key='summary'").fetchone()
            if not row or Path(json.loads(row[0])['catalog']).resolve() != Path(db).resolve():
                raise ValueError('Search supplement belongs to a different catalog')
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='item_replacements'").fetchone():
                with closing(readonly(db)) as base:
                    for replacement in con.execute('SELECT * FROM item_replacements'):
                        ident = replacement['item_id']
                        old = base.execute('SELECT label,ref_raw,source FROM items WHERE id=?',(ident,)).fetchone()
                        expected = (replacement['original_label'],replacement['original_ref'],replacement['source'])
                        if old is None or tuple(old) != expected:
                            raise ValueError('Search correction no longer matches its catalog record')
                        if ident in replacements:
                            raise ValueError('Competing search corrections for the same item')
                        if not con.execute('SELECT 1 FROM items WHERE id=?',(ident,)).fetchone():
                            raise ValueError('Search correction is missing its replacement item')
                        replacements[ident] = path
        sources.append((path, None))
    merged, scores = {}, Counter()
    for path, relations in sources:
        excluded = [ident for ident, replacement_path in replacements.items()
                    if Path(path).resolve() != replacement_path]
        for rank, item in enumerate(search(path, query, limit, candidate_limit, relations, excluded), 1):
            scores[item['id']] += 1 / (60+rank)
            if item['id'] not in merged:
                merged[item['id']] = item
                item['metadata']['search_database'] = str(Path(path).resolve())
            else:
                merged[item['id']]['metadata'].setdefault('additional_search_records', []).append(
                    {'database': str(Path(path).resolve()), 'record': item})
    for path in paths:
        with closing(readonly(path)) as con:
            for ident, item in merged.items():
                annotations = [json.loads(r[0]) for r in con.execute(
                    'SELECT evidence_json FROM item_annotations WHERE item_id=?', (ident,))]
                if annotations:
                    item['metadata'].setdefault('supplement_annotations', []).extend(annotations)
    result = sorted(merged.values(), key=lambda i: (-scores[i['id']], i['id']))[:limit]
    for item in result:
        item['metadata']['federated_rank_basis'] = 'reciprocal_rank_fusion_k60_not_probability'
    return result


def ranking_metrics(paths, positives, negatives=(), gold_complete=False):
    """Incomplete judgments are explicit, not silently treated as negatives."""
    paths = list(dict.fromkeys(paths))
    gold, negative = set(positives), set(negatives)
    if not gold or gold & negative:
        raise ValueError('Reviewed sample needs nonempty, nonconflicting positive labels')
    ranks = [i for i, path in enumerate(paths, 1) if path in gold]
    result = {'reciprocal_rank': 1 / min(ranks) if ranks else 0,
              'candidate_coverage': len(gold.intersection(paths)) / len(gold)}
    for k in CUTOFFS:
        top = paths[:k]
        relevant = len(gold.intersection(top))
        judged = len(top) if gold_complete else sum(p in gold or p in negative for p in top)
        result[f'Recall@{k}'] = relevant / len(gold)
        result[f'Precision@{k}'] = relevant / k if judged == len(top) else None
        result[f'JudgmentCoverage@{k}'] = judged / len(top) if top else 1.0
    return result


def load_reviews(path):
    data = json.loads(path.read_text())
    if data.get('version') == 'po015-lexical-visual-v1':
        accepted = {'positive_supported', 'positive_supported_with_ocr_warning',
                    'lexical_false_negative_ocr', 'lexical_false_negative_morphology'}
        return [{'id': 'po015:' + r['ref_id'], 'reviewed': True,
                 'positive_files': [f['file'] for f in r['target_files']],
                 'negative_files': [], 'gold_complete': False,
                 'reviewer': data['reviewer'], 'human_reviewed': r['human_reviewed'],
                 'sampling': data['selection'], 'source_review': r}
                for r in data['reviews'] if r['outcome'] in accepted]
    return data['samples']


def evaluate(db, reviews):
    detailed, pending = [], []
    with closing(readonly(db)) as con:
        for r in load_reviews(reviews):
            if not r.get('reviewed'):
                pending.append(r['id'])
                continue
            if not con.execute('SELECT 1 FROM items WHERE id=?', (r['id'],)).fetchone():
                # Absent queries count as retrieval failures, not exclusions.
                paths = []
            else:
                paths = [p[0] for p in con.execute(
                    'SELECT file FROM candidates WHERE item_id=? AND navigable=1 ORDER BY rank', (r['id'],))]
            detailed.append({'id': r['id'], 'review': r, 'ranking': paths,
                'metrics': ranking_metrics(paths, r['positive_files'], r.get('negative_files', []),
                                           r.get('gold_complete', False))})
    metrics, denominators = {}, {}
    names = ['reciprocal_rank', 'candidate_coverage'] + [f'{m}@{k}' for k in CUTOFFS
            for m in ['Recall', 'Precision', 'JudgmentCoverage']]
    for name in names:
        values = [r['metrics'][name] for r in detailed if r['metrics'][name] is not None]
        key = 'MRR' if name == 'reciprocal_rank' else name
        metrics[key] = sum(values) / len(values) if values else None
        denominators[key] = len(values)
    return {'version': VERSION, 'review_file': str(reviews.resolve()), 'review_sha256': sha(reviews),
        'reviewed_queries': len(detailed), 'pending_queries': pending, 'metrics': metrics,
        'metric_denominators': denominators, 'results': detailed,
        'scope': 'Destination ranking for reviewed index references, not end-to-end user-query relevance.',
        'precision_definition': 'Relevant / K; missing slots count as zero; unjudged returned files make Precision@K null.',
        'recall_definition': 'Recall of known reviewed positives; gold completeness recorded per query.'}


def diagnose(db):
    """Explain missing destinations using saved provenance, without new inference."""
    from tools.indexing.global_target_survey import SIMPLE_LIST
    statuses, origins, flags, volumes, simple, destination_states = (Counter() for _ in range(6))
    samples = defaultdict(list)
    with closing(readonly(db)) as con:
        for row in con.execute('SELECT * FROM items ORDER BY volume,id'):
            meta = json.loads(row['metadata_json'])
            status = meta['status']
            if row['state'] != 'source_only':
                destination_states[status] += 1
                continue
            statuses[status] += 1
            reason = meta.get('original_status', status)
            origins[reason] += 1
            volumes[row['volume']] += 1
            if SIMPLE_LIST.fullmatch(row['ref_raw'] or ''):
                simple[status] += 1
            if status == 'segmentation_or_notation_review':
                flags.update(set(meta.get('group_flags', [])))
            key = status + ':' + reason
            if len(samples[key]) < 3:
                samples[key].append({'id': row['id'], 'volume': row['volume'],
                    'label': row['label'], 'ref_raw': row['ref_raw'], 'source': row['source'],
                    'source_span': meta.get('source_span'), 'flags': meta.get('group_flags', [])})
    return {'version': VERSION, 'input': str(db.resolve()), 'source_only': sum(statuses.values()),
        'statuses': dict(statuses), 'original_causes': dict(origins),
        'flag_occurrences_in_unattempted_groups': dict(flags.most_common()),
        'simple_numeric_lists_by_status': dict(simple),
        'top_source_only_volumes': dict(volumes.most_common(20)),
        'with_destinations_by_audit_status': dict(destination_states), 'samples': dict(samples),
        'interpretation': 'Source-only means no navigable file supplied by the preceding stages, not lexical mismatch. Flag counts overlap. Simple numeric syntax is potential for candidate retrieval, not proof of page semantics.',
        'canonical_updates': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('build')
    audit_input=p.add_mutually_exclusive_group()
    audit_input.add_argument('--audit', type=Path)
    audit_input.add_argument('--numeric-only',action='store_true',help='Build from survey occurrences without a lexical audit DB')
    p.add_argument('--survey', type=Path, default=Path('data/index_entry_parser/global_targets_20261106_002/survey.sqlite'))
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('diagnose')
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('search')
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--query', required=True)
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--candidate-limit', type=int, default=20)
    p.add_argument('--relations-db', type=Path, action='append', help='Read-only recovery overlay; repeat to combine campaigns')
    p.add_argument('--supplement-db', type=Path, action='append', default=[], help='Reconciled source entries; repeat to combine supplements')
    p = sub.add_parser('evaluate')
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--reviews', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'build':
        audit=None if args.numeric_only else args.audit or Path('data/index_catalog/global_lexical_versions_002/audit.sqlite')
        result = build(audit, args.survey, args.out)
    elif args.command == 'search':
        if args.limit < 1 or args.candidate_limit < 1:
            parser.error('Limits must be positive')
        result = (search_with_supplements(args.db,args.query,args.limit,args.candidate_limit,
                                         args.relations_db,args.supplement_db) if args.supplement_db else
                  search(args.db,args.query,args.limit,args.candidate_limit,args.relations_db))
    else:
        result = diagnose(args.db) if args.command == 'diagnose' else evaluate(args.db, args.reviews)
        with args.out.open('x') as f:
            f.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
