#!/usr/bin/env python3
"""Read-only inventory of alphabetical material references and source provenance.

No OCR inference, API calls, or SQLite writes. Log reasons describe historical
observations with matching entry identity, not a rerun of the locator.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
ONOMASTIC = {'author_index', 'onomastic_mixed', 'onomastic_person'}


def page(path):
    match = re.search(r'-(\d+)\.[^.]+$', str(path or ''))
    return int(match[1]) if match else None


def identity(entry, ref):
    value = dict(lemma_raw=entry['lemma_raw'], entry_raw=entry['entry_raw'], ref_raw=ref['ref_raw'])
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def material(ref, section):
    return (section['pipeline_owner'] == 'alphabetical'
            and section['alphabetical_role'] == 'owned_section'
            and section['material_reference_mode'] != 'source_only'
            and ref['scripture_ref_order'] is None and ref['ref_kind'] != 'scripture')


def source(entry):
    if entry['index_entry_source_ocr_file']:
        return entry['index_entry_source_ocr_file'], 'entry_source_recorded_not_visually_verified'
    raw = json.loads(entry['raw_json'] or '{}')
    if raw.get('source_file'):
        return raw['source_file'], 'raw_json_source_recorded_not_visually_verified'
    return '', 'unknown_entry_page_section_range_only'


def log_records(paths):
    latest, deep, provenance = {}, {}, []
    for path in paths:
        blob = path.read_bytes()
        provenance.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(blob).hexdigest()})
        for number, line in enumerate(blob.decode().splitlines(), 1):
            record = json.loads(line)
            if record.get('record_type') == 'association':
                record['_log'] = f'{path}:{number}'
                latest[record['ref_id']] = record
            elif record.get('record_type') == 'window_result':
                deep[record['ref_id']] = record
    return latest, deep, provenance


def write_csv(path, rows):
    if not rows:
        path.write_text('', encoding='utf-8')
        return
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True)
                             if isinstance(v, (dict, list)) else v for k, v in row.items()})


def summarize(rows):
    pending = [r for r in rows if r['pending']]
    return {'references_total': len(rows), 'pending_references': len(pending),
            'resolved_references': len(rows) - len(pending),
            'entries_with_references': len({r['entry_key'] for r in rows}),
            'entries_with_pending': len({r['entry_key'] for r in pending}),
            'pending_percent': round(100 * len(pending) / len(rows), 2) if rows else 0,
            'pending_statuses': dict(Counter(r['locator_status'] for r in pending)),
            'pending_diagnoses': dict(Counter(r['diagnosis'] for r in pending))}


def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    con = sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    try:
        sections = {r['section_key']: dict(r) for r in con.execute('SELECT * FROM alphabetical_sections')}
        entries = {r['entry_key']: dict(r) for r in con.execute('SELECT * FROM alphabetical_entries')}
        refs = [dict(r) for r in con.execute('SELECT * FROM alphabetical_refs')]
    finally:
        con.close()
    latest, deep, provenance = log_records(args.log)
    rows, by_section, by_volume, by_source = [], defaultdict(list), defaultdict(list), defaultdict(list)
    for ref in refs:
        entry = entries[ref['entry_key']]
        section = sections[entry['section_key']]
        if not material(ref, section):
            continue
        pending = ref['locator_status'] != 'resolved'
        raw = json.loads(ref['raw_json'] or '{}')
        review = raw.get('reviewed_editorial') or raw.get('deterministic_repair') or {}
        log = latest.get(ref['ref_id'], {})
        log_matches = (log.get('entry_sha256') == identity(entry, ref)
                       and log.get('entry_key') == entry['entry_key']
                       and log.get('section_key') == entry['section_key'])
        diagnosis = review.get('block_reason')
        origin = 'current_db' if diagnosis else ''
        if not diagnosis and log_matches:
            diagnosis = log.get('skip_reason')
            if log.get('status') == 'evaluated':
                diagnosis = 'jev_positive_not_promoted' if log.get('decision') else 'jev_negative_on_candidate'
            diagnosis = diagnosis or log.get('status')
            origin = 'historical_log_matching_entry_only'
        diagnosis = diagnosis or ('no_matching_diagnosis' if pending else 'resolved')
        src, quality = source(entry)
        start = section['index_ocr_file_start'] or section['file_start'] or ''
        end = section['index_ocr_file_end'] or section['file_end'] or ''
        physical = page(src)
        within = (page(start) <= physical <= page(end)
                  if None not in (page(start), page(end), physical) else None)
        row = {'volume': section['volume_id'], 'section_key': section['section_key'],
               'section_kind': section['section_kind'], 'onomastic_taxonomy': section['section_kind'] in ONOMASTIC,
               'entry_key': entry['entry_key'], 'ref_id': ref['ref_id'],
               'lemma': entry['lemma_raw'], 'reference': ref['ref_raw'],
               'locator_status': ref['locator_status'], 'pending': pending,
               'target_present': bool(ref['target_file'] or ref['resolved_target_ocr_file']),
               'source_file': src, 'source_physical_page': physical,
               'source_provenance': quality, 'source_within_section_range': within,
               'entry_chars': len(entry['entry_raw'] or ''),
               'diagnosis': diagnosis, 'diagnosis_origin': origin,
               'diagnosis_log': log.get('_log') if origin.startswith('historical') else '',
               'log_identity_matches': log_matches,
               'section_start_file': start, 'section_end_file': end}
        rows.append(row)
        by_section[section['section_key']].append(row)
        by_volume[section['volume_id']].append(row)
        if pending:
            by_source[(section['section_key'], src)].append(row)
    totals = summarize(rows)
    volumes, section_rows, pages = [], [], []
    for volume, group in by_volume.items():
        stats = summarize(group)
        volumes.append({'volume': volume, **stats,
                        'share_of_pending_percent': round(stats['pending_references'] * 100 / max(1, totals['pending_references']), 2),
                        'onomastic_pending': sum(r['pending'] and r['onomastic_taxonomy'] for r in group)})
    for key, group in by_section.items():
        s = sections[key]
        start, end = group[0]['section_start_file'], group[0]['section_end_file']
        nums = range(page(start), page(end)+1) if None not in (page(start), page(end)) and page(end) >= page(start) else []
        image_root = ROOT / 'teste' / s['volume_id'] / 'images'
        images = {page(p): str(p) for p in image_root.glob('*') if p.is_file() and p.suffix.lower() in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp'}} if image_root.exists() else {}
        observed = sorted({r['source_physical_page'] for r in group
                           if r['pending'] and r['source_physical_page'] is not None})
        section_rows.append({'volume': s['volume_id'], 'section_key': key,
            'section_kind': s['section_kind'], 'heading': s['heading_raw'], **summarize(group),
            'physical_start': page(start), 'physical_end': page(end), 'physical_span_files': len(nums),
            'printed_start_db_unverified': s['index_editorial_page_start'],
            'printed_end_db_unverified': s['index_editorial_page_end'],
            'source_start_file': start, 'source_end_file': end,
            'image_files_available_in_span': sum(n in images for n in nums),
            'recorded_pending_source_pages_unverified': observed,
            'pending_source_unknown': sum(r['pending'] and not r['source_file'] for r in group),
            'pending_source_outside_section': sum(r['pending'] and r['source_within_section_range'] is False for r in group)})
        for (section_key, src), subset in by_source.items():
            if section_key != key:
                continue
            pages.append({'volume': s['volume_id'], 'section_key': key, 'section_kind': s['section_kind'],
                'physical_page': page(src), 'source_file': src,
                'source_exists': Path(src).is_file() if src else False,
                'image_file': images.get(page(src), ''),
                'attribution': subset[0]['source_provenance'], **summarize(subset),
                'max_entry_chars': max(r['entry_chars'] for r in subset),
                'sample_ref_ids': [r['ref_id'] for r in subset[:5]]})
    for collection in (volumes, section_rows, pages):
        collection.sort(key=lambda r: (-r['pending_references'], r['volume']))
    used = {r['entry_key'] for r in rows}
    unreferenced = [e for e in entries.values() if e['entry_key'] not in used
                    and sections[e['section_key']]['pipeline_owner'] == 'alphabetical'
                    and sections[e['section_key']]['alphabetical_role'] == 'owned_section']
    summary = {'created_at': datetime.now(timezone.utc).isoformat(), 'db': str(args.db.resolve()),
        'scope': 'Owned alphabetical material references; excludes scripture and source_only. Pending = status != resolved.',
        'source_caveat': 'Recorded entry source is not visually verified; section start is never substituted for an unknown entry page.',
        'log_caveat': 'Diagnosis matched by entry identity only, not revalidated location/OCR. Current DB blockers take precedence.',
        'totals': totals, 'all_db_reference_statuses': dict(Counter(r['locator_status'] for r in refs)),
        'excluded_references': len(refs) - len(rows),
        'onomastic_taxonomy': summarize([r for r in rows if r['onomastic_taxonomy']]),
        'pending_volumes': sum(v['pending_references'] > 0 for v in volumes),
        'entries_without_selected_material_refs': len(unreferenced),
        'entries_without_selected_material_refs_by_volume': dict(Counter(sections[e['section_key']]['volume_id'] for e in unreferenced)),
        'pending_with_existing_target': sum(r['pending'] and r['target_present'] for r in rows),
        'resolved_without_target': sum(not r['pending'] and not r['target_present'] for r in rows),
        'top4_pending_share_percent': round(sum(v['pending_references'] for v in volumes[:4])*100/max(1,totals['pending_references']),2),
        'log_inputs': provenance,
        'deepscan_window_results_historical': dict(Counter(r['status'] for r in deep.values()))}
    (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    write_csv(args.out / 'volumes.csv', volumes)
    write_csv(args.out / 'sections.csv', section_rows)
    write_csv(args.out / 'source_pages.csv', pages)
    write_csv(args.out / 'pending_references.csv', [r for r in rows if r['pending']])
    write_csv(args.out / 'entries_without_material_references.csv', [
        {'volume': sections[e['section_key']]['volume_id'], 'section_key': e['section_key'],
         'entry_key': e['entry_key'], 'entry_kind': e['entry_kind'], 'lemma': e['lemma_raw'],
         'source_file': source(e)[0], 'note': 'Not automatically an omission: may be cross-reference, scripture, or source-only entry.'}
        for e in unreferenced])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'data/alphabetical_indices.db')
    parser.add_argument('--log', type=Path, action='append', default=[], help='Chronological order, newest last')
    parser.add_argument('--out', type=Path, required=True, help='New output directory')
    run(parser.parse_args())
