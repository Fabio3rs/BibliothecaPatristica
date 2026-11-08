#!/usr/bin/env python3
"""Plan/apply source-proven reassignment of pending references in grouped entries.

This first adapter only handles independent name + comma-separated integers in
PG113/PG148. It never creates citations, changes numbers, or resolves targets.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.indexing.multimodal.contract import digest
from tools.indexing.multimodal.literal import SCHEMA, walk
from tools.indexing.multimodal.literal_reconcile import reconcile, tokens, TokenIndex
from tools.indexing.multimodal.sources import check_sources
from tools.indexing.index_target_locator import normalize_for_search
from scripts.alphabetical_index_db import refresh_volume_quality
from scripts.repair_alphabetical_material import insert_row
from scripts.reviewed_index_evidence import identity

METHOD = 'literal_unique_occurrence_reassignment_v1'


def read(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def values(text):
    return [t['value'] for t in tokens(text, join_wraps=True)]


def independent_entries(dto):
    """A single letter groups independent names; an entry parent may supply context."""
    items = {path: (parent, e) for path, parent, e in walk(dto['elements'])}
    result = set()
    for path, (parent, item) in items.items():
        if item['type'] != 'entry':
            continue
        while parent:
            parent, node = items[parent]
            if (node['type'] != 'heading' or node['role'] not in {'group', 'section'}
                    or not re.fullmatch('[A-Z]', node['text'].strip())):
                break
        else:
            result.add(path)
    return result


def load_pages(run, pages):
    result = {}
    for item in read(Path(run)/'manifest.json')['tasks']:
        directory = Path(item['directory'])
        task = read(directory/'task.json')
        page = next(p for p in task['pages'] if p['page_id'] == task['primary_page_id'])
        key = f"{task['volume']}:{page['physical_sequence']}"
        if key not in pages:
            continue
        if task.get('extraction_profile') != 'literal-v1' or key in result:
            raise ValueError('Expected one literal task per selected page')
        check_sources(task)
        raw = (directory/'response.json').read_bytes()
        cp = read(directory/'checkpoint.json')
        if cp['input_fingerprint'] != item['input_fingerprint'] or cp['response_sha256'] != digest(raw):
            raise ValueError('Response checkpoint/hash mismatch')
        response = json.loads(raw)
        if response['status'] != 'received':
            raise ValueError('Incomplete response')
        Draft202012Validator(SCHEMA).validate(response['dto'])
        result[key] = (task, page, response['dto'], directory/'response.json')
    if result.keys() != set(pages):
        raise ValueError('Selected page absent from archived run')
    return result


def build_plan(con, run, corroborate_run, pages):
    if not pages or any(p.split(':')[0] not in {'PG113', 'PG148'} for p in pages):
        raise ValueError('First repair batch supports explicit PG113/PG148 pages only')
    primary, corroborating = load_pages(run, pages), load_pages(corroborate_run, pages)
    changes, skipped = [], Counter()
    for key, (task, page, dto, response_path) in sorted(primary.items()):
        _, other_page, other_dto, other_response_path = corroborating[key]
        if (response_path.resolve() == other_response_path.resolve()
                or page['ocr_sha256'] != other_page['ocr_sha256']
                or [r['sha256'] for r in page['regions']] != [r['sha256'] for r in other_page['regions']]):
            raise ValueError('Need separate responses over the same OCR/images')
        # Structure agreement is checked independently of references_raw formatting.
        independent = independent_entries(dto)
        other_independent = independent_entries(other_dto)
        other_entries = Counter(tuple(values(e['text'])) for path, _, e in walk(other_dto['elements'])
                                if path in other_independent)
        current = reconcile(dto, task)
        old = [dict(r) for r in con.execute('''SELECT e.* FROM alphabetical_entries e
            JOIN alphabetical_sections s USING(section_key) WHERE s.volume_id=?''', (task['volume'],))
               if r['index_entry_source_ocr_file'] and
               (ROOT/Path(r['index_entry_source_ocr_file'])).resolve() == Path(page['ocr_file']).resolve()]
        indexes = {e['entry_key']: TokenIndex(tokens(e['entry_raw'], join_wraps=True)) for e in old}
        sources = [{'file': page['ocr_file'], 'sha256': page['ocr_sha256']},
                   *[{'file': r['path'], 'sha256': r['sha256']} for r in page['regions']],
                   *[{'file': str(p.resolve()), 'sha256': digest(p.read_bytes())}
                     for p in (response_path, other_response_path)]]
        raw_source = Path(page['ocr_file']).read_text()
        for new in current['entries']:
            text_values = values(new['text'])
            refs_raw = new['references_raw'] or ''
            if (new['anchor']['status'] != 'anchored' or new['conflicts'] or new['object'] not in independent
                    or new['continuation'] != 'none' or not new['name']
                    or not re.fullmatch(r'[0-9]+(?:,\s*[0-9]+)*\.?', refs_raw)
                    or text_values != values(new['name'] + ', ' + refs_raw)):
                skipped['not_independent_simple_verified_entry'] += 1
                continue
            if other_entries[tuple(text_values)] != 1:
                skipped['no_unique_segmentation_agreement'] += 1
                continue
            owners = [e for e in old if len(indexes[e['entry_key']].find(text_values)) == 1]
            if len(owners) != 1 or indexes[owners[0]['entry_key']].values == text_values:
                skipped['already_separate_or_legacy_owner_not_unique'] += 1
                continue
            owner = owners[0]
            if owner['entry_kind'] != 'lemma':
                skipped['legacy_kind_requires_review'] += 1
                continue
            start, end = new['anchor']['char_start'], new['anchor']['char_end']
            source_lines = [l for l in page['lines'] if l['char_start'] < end and start < l['char_end']]
            blocks = {l['block_id'] for l in source_lines}
            if len(blocks) != 1 or any(b['type'] != 'texto_principal' for b in page['blocks'] if b['id'] in blocks):
                skipped['source_block_requires_review'] += 1
                continue
            lo, hi = raw_source.count('\n', 0, start)+1, raw_source.count('\n', 0, end)+1
            child = {'lemma': new['name'], 'text': raw_source[start:end],
                     'source_file': page['ocr_file'], 'start': start, 'end': end,
                     'line_start': lo, 'line_end': hi,
                     'text_sha256': digest(''.join(raw_source.splitlines(keepends=True)[lo-1:hi]).encode()),
                     'llm_text': new['text'], 'object': new['object']}
            numbers = re.findall(r'[0-9]+', refs_raw)
            old_refs = [dict(r) for r in con.execute('SELECT * FROM alphabetical_refs WHERE entry_key=?', (owner['entry_key'],))]
            for number in numbers:
                if numbers.count(number) != 1 or indexes[owner['entry_key']].values.count(number) != 1:
                    skipped['repeated_number_ownership_ambiguous'] += 1
                    continue
                candidates = [r for r in old_refs if r['ref_raw'] == number]
                if len(candidates) != 1:
                    skipped['legacy_reference_not_unique'] += 1
                    continue
                ref = candidates[0]
                if (ref['locator_status'] not in {'unresolved', 'unverified', 'ambiguous'}
                        or ref['target_file'] or ref['resolved_target_ocr_file']
                        or ref['scripture_ref_order'] is not None or ref['ref_kind'] != 'editorial_page'
                        or ref['page_ref_int'] != int(number)
                        or ref['cited_editorial_page_start_number'] != int(number)
                        or any(ref[k] for k in ['line_ref_raw', 'range_start_raw', 'range_end_raw',
                                               'page_ref_col', 'cited_editorial_line_raw',
                                               'cited_editorial_line_start_raw', 'cited_editorial_column_raw',
                                               'cited_editorial_line_end_raw', 'cited_editorial_line_start_number',
                                               'cited_editorial_line_end_number', 'cited_editorial_page_end_raw',
                                               'cited_editorial_page_end_number'])
                        or con.execute('SELECT 1 FROM alphabetical_locator_evidence WHERE ref_id=?', (ref['ref_id'],)).fetchone()):
                    skipped['protected_resolution_evidence_or_locator'] += 1
                    continue
                raw = json.loads(ref['raw_json'])
                if raw.get('reviewed_location'):
                    skipped['protected_reviewed_location'] += 1
                    continue
                block = (raw.get('reviewed_editorial') or raw.get('deterministic_repair') or {}).get('block_reason')
                if block in {'source_anchor_not_unique', 'grouped_entry_requires_segmentation'}:
                    block = None
                changes.append({'ref_id': ref['ref_id'], 'volume_id': task['volume'],
                                'old_entry': owner, 'old_ref': ref, 'child': child, 'sources': sources,
                                'block_reason': block,
                                'reason': 'Two saved extractions agree on an independent simple entry; '
                                          'OCR and legacy text contain it uniquely; cited number has '
                                          'exactly one occurrence in the legacy entry.'})
    if len({p['ref_id'] for p in changes}) != len(changes):
        raise ValueError('Conflicting proposals for one reference')
    return {'method': METHOD, 'run': str(Path(run).resolve()),
            'corroborate_run': str(Path(corroborate_run).resolve()), 'pages': sorted(pages),
            'changes': sorted(changes, key=lambda p: p['ref_id']), 'skipped': dict(skipped),
            'counts': {'references_to_reassign': len(changes),
                       'entries_to_create': len({(p['old_entry']['entry_key'], p['child']['start'], p['child']['end']) for p in changes})}}


def table_hash(con, sql):
    result, count = hashlib.sha256(), 0
    for row in con.execute(sql):
        result.update(json.dumps(tuple(row), ensure_ascii=False, separators=(',', ':')).encode())
        result.update(b'\n')
        count += 1
    return {'rows': count, 'sha256': result.hexdigest()}


def protected_state(con):
    return {'resolved_refs': table_hash(con, "SELECT * FROM alphabetical_refs WHERE locator_status='resolved' OR target_file IS NOT NULL OR resolved_target_ocr_file IS NOT NULL ORDER BY ref_id"),
            'locator_evidence': table_hash(con, 'SELECT * FROM alphabetical_locator_evidence ORDER BY rowid')}


def apply_plan(db, plan_path, backup, audit):
    db, plan_path, backup, audit = [Path(p).resolve() for p in (db, plan_path, backup, audit)]
    if len({db, plan_path, backup, audit}) != 4 or backup.exists() or audit.exists():
        raise ValueError('Distinct new backup/audit paths required')
    plan = read(plan_path)
    if plan['method'] != METHOD or not plan['changes']:
        raise ValueError('Unsupported or empty plan')
    plan_hash = digest(plan_path.read_bytes())
    with closing(sqlite3.connect(db.as_uri()+'?mode=rw', uri=True, timeout=30)) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('BEGIN IMMEDIATE')
        try:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='alphabetical_literal_patches'").fetchone():
                if con.execute('SELECT count(*) FROM alphabetical_literal_patches WHERE plan_sha256=?', (plan_hash,)).fetchone()[0]:
                    raise ValueError('Plan already applied; original references are preserved in the audit table')
            fresh = build_plan(con, plan['run'], plan['corroborate_run'], plan['pages'])
            if fresh != plan:
                raise ValueError('Database or source evidence changed; regenerate and review plan')
            protected_before = protected_state(con)
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open('xb'):
                pass
            with closing(sqlite3.connect(db.as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(backup)) as dst:
                src.backup(dst)
            con.execute('''CREATE TABLE IF NOT EXISTS alphabetical_literal_patches (
                patch_key TEXT PRIMARY KEY, plan_sha256 TEXT NOT NULL, ref_id INTEGER NOT NULL,
                original_json TEXT NOT NULL, proposal_json TEXT NOT NULL, applied_at TEXT NOT NULL)''')
            orders = dict(con.execute('SELECT section_key,MAX(entry_order) FROM alphabetical_entries GROUP BY section_key'))
            children, entry_orders = {}, Counter()
            now = datetime.now(timezone.utc).isoformat()
            for p in plan['changes']:
                old, child = p['old_entry'], p['child']
                key = old['entry_key'] + ':literal:' + digest([child['source_file'], child['start'], child['end']])[:16]
                if key not in children:
                    new = dict(old)
                    section = old['section_key']
                    orders[section] += 1
                    new.update(entry_key=key, entry_order=orders[section], entry_kind='lemma',
                               lemma_raw=child['lemma'], lemma_display=child['lemma'],
                               lemma_norm=normalize_for_search(child['lemma']), lemma_sort=normalize_for_search(child['lemma']),
                               entry_raw=child['text'], context_raw=child['text'], parent_node_key=None,
                               heading_letter=None, inferred_printed_page=None, index_editorial_page_estimate=None,
                               target_file_best=None, resolved_target_ocr_file=None, confidence=None,
                               index_entry_source_ocr_file=child['source_file'], editorial_anchor_file=child['source_file'],
                               raw_json=json.dumps({'literal_reextraction': {'method': METHOD, 'sources': p['sources'],
                                   'source_span': child, 'original_entry_key': old['entry_key'], 'plan_sha256': plan_hash}}, ensure_ascii=False))
                    insert_row(con, 'alphabetical_entries', new)
                    con.execute('''INSERT OR IGNORE INTO alphabetical_source_spans
                        (volume_id,ocr_file_path,line_start,line_end,block_type,text_sha256,created_at)
                        VALUES (?,?,?,?,?,?,?)''', (p['volume_id'], child['source_file'], child['line_start'], child['line_end'], 'texto_principal', child['text_sha256'], now))
                    sid = con.execute('''SELECT span_id FROM alphabetical_source_spans WHERE volume_id=?
                        AND ocr_file_path=? AND line_start=? AND line_end=? AND block_type='texto_principal' AND text_sha256=?''',
                        (p['volume_id'], child['source_file'], child['line_start'], child['line_end'], child['text_sha256'])).fetchone()[0]
                    con.execute('INSERT INTO alphabetical_entry_source_spans(entry_key,span_id) VALUES (?,?)', (key, sid))
                    children[key] = new
                entry_orders[key] += 1
                fields = {'entry_key': key, 'ref_order': entry_orders[key], 'confidence': None,
                          'target_file_probability': None, 'target_ocr_file_candidate_score': None}
                raw = json.loads(p['old_ref']['raw_json'])
                raw['reviewed_editorial'] = {'method': METHOD, 'sources': p['sources'], 'reason': p['reason'],
                                            'block_reason': p['block_reason'], 'plan_sha256': plan_hash,
                                            'identity': identity({**children[key], **p['old_ref'], **fields})}
                fields['raw_json'] = json.dumps(raw, ensure_ascii=False)
                con.execute('UPDATE alphabetical_refs SET '+','.join(k+'=?' for k in fields)+' WHERE ref_id=?',
                            [*fields.values(), p['ref_id']])
                con.execute('INSERT INTO alphabetical_literal_patches VALUES (?,?,?,?,?,?)',
                            (f"{plan_hash}:{p['ref_id']}", plan_hash, p['ref_id'],
                             json.dumps({'entry': old, 'ref': p['old_ref']}, ensure_ascii=False), json.dumps(p, ensure_ascii=False), now))
            for volume in {p['volume_id'] for p in plan['changes']}:
                refresh_volume_quality(con, volume)
            if protected_before != protected_state(con):
                raise ValueError('Protected resolutions or locator evidence changed')
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or con.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('SQLite integrity check failed')
            for p in plan['changes']:
                for source in p['sources']:
                    if digest(Path(source['file']).read_bytes()) != source['sha256']:
                        raise ValueError('Source changed during application')
            con.commit()
        except BaseException:
            con.rollback()
            raise
    result = {'method': METHOD, 'db': str(db), 'backup': str(backup), 'plan_sha256': plan_hash,
              'references_reassigned': len(plan['changes']), 'new_entries': len(children),
              'by_volume': dict(Counter(p['volume_id'] for p in plan['changes'])),
              'resolutions_created': 0, 'references_created_or_deleted': 0,
              'protected_state_before_and_after': protected_before, 'integrity': 'ok', 'foreign_keys': 'ok'}
    write_new(audit, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, default=ROOT/'data/alphabetical_indices.db')
    p.add_argument('--run', type=Path)
    p.add_argument('--corroborate-run', type=Path)
    p.add_argument('--page', action='append', default=[])
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--backup', type=Path)
    p.add_argument('--audit', type=Path)
    args = p.parse_args()
    if args.apply:
        if not args.backup or not args.audit:
            p.error('--apply requires --backup and --audit')
        report = apply_plan(args.db, args.plan, args.backup, args.audit)
    else:
        if not args.run or not args.corroborate_run or not args.page:
            p.error('Planning requires --run, --corroborate-run and --page')
        with closing(sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro', uri=True)) as con:
            con.row_factory = sqlite3.Row
            report = build_plan(con, args.run, args.corroborate_run, args.page)
        write_new(args.plan, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'changes'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
