"""Bounded corpus discovery and mechanical work inventory, without inference.

Headings are leads, not certified ownership. Registered sources and references
are read-only snapshots; no absence of an index is inferred from a tail scan.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import re
import uuid

from scripts.resolve_alphabetical_unresolved import (
    QUERY, EDITORIAL_KINDS, cited_start, is_eligible, repair_block_reason,
)
from scripts.scan_editorial_ocr import BLOCK, ATTR, EDITION_NOTE
from tools.corpus_utils import page_files, page_number
from tools.indexing.entry_parser_bulk import readonly, open_experiment
from tools.indexing.parser_target_exports import write_jsonl
from tools.indexing.title_page_experiment import VOLUME

VERSION = 'index-coverage-v1'
BOUNDARY = re.compile(r'\b(?:ORDO\s+RERUM|ERRATA|CORRIGENDA|TABLE\s+DES\s+MATI[EÈ]RES)\b', re.I)
INDEX = re.compile(r'\b(?:INDEX|INDICES|INDICE|ONOMASTIC\w*|TABLE\s+(?:ALPHAB[EÉ]TIQUE|DES\s+NOMS)|LEXICON|GLOSSARIUM)\b', re.I)
FAMILIES = {
    'onomastic': re.compile(r'\b(?:NOMIN\w*|NOMS|PERSON\w*|ONOMASTIC\w*)\b', re.I),
    'places': re.compile(r'\b(?:LOCORUM|LIEUX|GEOGRAPH\w*)\b', re.I),
    'thematic': re.compile(r'\b(?:RERUM|MATI[EÈ]RES|SUBJECT\w*)\b', re.I),
    'biblical': re.compile(r'\b(?:SCRIPTUR\w*|BIBLI\w*|SACR\w*)\b', re.I),
    'alphabetical': re.compile(r'\b(?:ALPHABET\w*|ALPHABÉT\w*)\b', re.I),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def classify_reference(row):
    """Reuse existing eligibility/locator rules, without declaring namespace known."""
    material = (row['pipeline_owner'] == 'alphabetical'
                and row['alphabetical_role'] == 'owned_section'
                and row['material_reference_mode'] != 'source_only'
                and row['scripture_ref_order'] is None and row['ref_kind'] != 'scripture')
    if not material:
        return 'outside_material_scope'
    if row['locator_status'] == 'resolved':
        return 'existing_resolution_not_revalidated'
    if not is_eligible(row, include_unverified=True):
        return 'existing_candidate_or_ambiguity_requires_review'
    try:
        blocked = repair_block_reason(row)
    except (ValueError, TypeError, AttributeError):
        return 'invalid_editorial_metadata'
    if blocked:
        return 'editorial_block:' + str(blocked)
    if row['ref_kind'] not in EDITORIAL_KINDS or cited_start(row) is None:
        return 'notation_requires_interpretation'
    if row['ref_kind'] == 'editorial_range':
        return 'range_requires_full_extent'  # Existing resolver only maps its start.
    if row['ref_kind'] == 'editorial_page_line' or row.get('line_ref_raw') or row.get('cited_editorial_line_raw'):
        return 'page_line_requires_scoped_mapping'
    if re.search(r'\b[IVX]+\s*,|\b(?:Leo|tom\w*|edit\w*)\b', row.get('ref_raw') or '', re.I):
        return 'namespace_requires_scoped_mapping'
    return 'numeric_locator_input_requires_namespace_check'


def read_snapshot(db):
    con = readonly(db)
    try:
        con.execute('BEGIN')
        sections = [dict(r) for r in con.execute('SELECT * FROM alphabetical_sections ORDER BY section_key')]
        origins = [dict(r) for r in con.execute('''SELECT s.volume_id, e.section_key,
            e.entry_key, e.index_entry_source_ocr_file AS source
            FROM alphabetical_entries e JOIN alphabetical_sections s USING(section_key)
            ORDER BY s.volume_id,e.entry_key''')]
        refs = []
        for volume in sorted({s['volume_id'] for s in sections}):
            for row in con.execute(QUERY, (volume,)):
                row = dict(row)
                refs.append({'volume': volume, 'ref_id': row['ref_id'], 'entry_key': row['entry_key'],
                    'section_key': row['section_key'], 'ref_raw': row['ref_raw'],
                    'locator_status': row['locator_status'], 'route': classify_reference(row),
                    'cited_start': cited_start(row), 'lemma_raw': row['lemma_raw'],
                    'target_file': row['resolved_target_ocr_file'] or row['target_file']})
        return sections, origins, refs
    finally:
        con.close()


def heading_signals(raw):
    signals = []
    for block in BLOCK.finditer(raw):
        attrs = dict(ATTR.findall(block['attrs']))
        body = block['body']
        # Inspect short blocks and individual short lines. Do not mistake a
        # paragraph mentioning an index for a measured region of ownership.
        chunks = [(0, len(body))] if len(body.strip()) <= 350 else [
            (m.start(), m.end()) for m in re.finditer(r'[^\n]+', body) if len(m[0].strip()) <= 200]
        for a, b in chunks:
            text = body[a:b]
            normalized = ' '.join(re.sub(r'<[^>]*>', ' ', text).split())
            boundary = bool(BOUNDARY.search(normalized))
            index = bool(INDEX.search(normalized))
            if not boundary and not index:
                continue
            start, end = block.start('body')+a, block.start('body')+b
            signals.append({'kind': 'boundary_hint' if boundary else 'index_heading_hint',
                'families': [key for key, pattern in FAMILIES.items() if pattern.search(normalized)],
                'quote': raw[start:end], 'span': [start, end], 'block_type': attrs.get('tipo'),
                'ownership': 'uncertain', 'verified': False})
        for match in EDITION_NOTE.finditer(body):
            a, b = block.start('body')+match.start(), block.start('body')+match.end()
            signals.append({'kind': 'editorial_note_hint', 'quote': raw[a:b], 'span': [a,b],
                            'ownership': 'uncertain', 'verified': False})
    return signals


def scan(task):
    try:
        data = Path(task['source']).read_bytes()
        return {**task, 'sha256': sha(data), 'signals': heading_signals(data.decode('utf-8')),
                'status': 'read', 'error': None}
    except (OSError, UnicodeError) as exc:
        return {**task, 'sha256': None, 'signals': [], 'status': 'read_error',
                'error': f'{type(exc).__name__}: {exc}'}


def make_inventory(root, volumes, sections, origins, tail_pages):
    tasks, issues, volumes_info = {}, [], {}
    by_section, by_origin = defaultdict(list), defaultdict(list)
    for s in sections:
        by_section[s['volume_id']].append(s)
    for e in origins:
        by_origin[e['volume_id']].append(e)
    for volume in volumes:
        listing = page_files(root/volume/'text', suffixes=['.txt'])
        owned = set()
        def add(path, physical, reason):
            key = str(path.resolve())
            task = tasks.setdefault(key, {'source': key, 'volume': volume, 'physical': physical, 'reasons': []})
            if reason not in task['reasons']:
                task['reasons'].append(reason)
        def select(number, reason):
            paths = listing.get(number, [])
            if len(paths) != 1:
                issues.append({'volume': volume, 'physical': number, 'reason': reason,
                    'issue': 'missing_or_ambiguous_source', 'paths': [str(p) for p in paths]})
            else:
                add(paths[0], number, reason)
        for number in sorted(listing)[-tail_pages:]:
            select(number, 'tail_probe')
        for s in by_section[volume]:
            start, end = s.get('index_ocr_file_start') or s.get('file_start'), s.get('index_ocr_file_end') or s.get('file_end')
            a, b = page_number(Path(start)) if start else None, page_number(Path(end)) if end else None
            if a is None or b is None or not 0 <= b-a <= 2000:
                issues.append({'volume': volume, 'issue': 'unusable_section_bounds', 'section_key': s['section_key']})
                continue
            if s['pipeline_owner'] == 'alphabetical' and s['alphabetical_role'] == 'owned_section':
                owned.update(range(a,b+1))
            for number in range(a,b+1):
                select(number, 'registered_section')
        for entry in by_origin[volume]:
            if not entry['source']:
                issues.append({'volume': volume, 'issue': 'entry_missing_origin', 'entry_key': entry['entry_key']})
                continue
            path = root/volume/'text'/Path(entry['source']).name
            add(path, page_number(path), 'registered_entry_origin')
        volumes_info[volume] = {'volume': volume, 'physical_files': sum(map(len, listing.values())),
            'physical_positions': len(listing), 'registered_sections': len(by_section[volume]),
            'registered_owned_sections': sum(s['pipeline_owner']=='alphabetical' and s['alphabetical_role']=='owned_section' for s in by_section[volume]),
            'registered_entries': len(by_origin[volume]), 'registered_owned_physical_pages': sorted(owned)}
    for task in tasks.values():
        task['inside_registered_owned_range'] = task['physical'] in volumes_info[task['volume']]['registered_owned_physical_pages']
    return sorted(tasks.values(), key=lambda t:(t['volume'],t['physical'] or -1,t['source'])), issues, volumes_info


def run(args):
    if not 1 <= args.tail_pages <= 100 or not 1 <= args.workers <= 64:
        raise ValueError('Require tail-pages 1..100 and workers 1..64')
    if args.out.exists():
        raise ValueError('Output directory must be new')
    disk = {p.name for p in args.root.iterdir() if p.is_dir() and VOLUME.fullmatch(p.name) and (p/'text').is_dir()}
    sections, origins, refs = read_snapshot(args.source_db)
    volumes = sorted(disk | {s['volume_id'] for s in sections}) if args.all_volumes else sorted(set(args.volume or []))
    if not volumes or any(not VOLUME.fullmatch(v) for v in volumes):
        raise ValueError('Select valid volumes or --all-volumes')
    sections = [s for s in sections if s['volume_id'] in volumes]
    origins = [e for e in origins if e['volume_id'] in volumes]
    refs = [r for r in refs if r['volume'] in volumes]
    # Validate the isolated destination before starting corpus I/O.
    con = open_experiment(args.experiment_db, args.source_db)
    try:
        tasks, issues, info = make_inventory(args.root, volumes, sections, origins, args.tail_pages)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pages = list(pool.map(scan, tasks))
        proposals = []
        for page in pages:
            volume = info[page['volume']]
            volume['pages_read'] = volume.get('pages_read',0) + (page['status']=='read')
            volume['read_errors'] = volume.get('read_errors',0) + (page['status']!='read')
            hints = [s for s in page['signals'] if s['kind']=='index_heading_hint']
            if hints:
                volume['index_hint_pages'] = volume.get('index_hint_pages',0)+1
                if not page['inside_registered_owned_range']:
                    volume['hints_outside_owned_ranges'] = volume.get('hints_outside_owned_ranges',0)+1
                    proposals.append({k:page[k] for k in ['volume','physical','source','sha256','signals','reasons']})
        route_counts = defaultdict(Counter)
        for row in refs:
            route_counts[row['volume']][row['route']] += 1
        for volume in info.values():
            v = volume['volume']
            volume['reference_routes'] = dict(route_counts[v])
            volume['discovery_status'] = ('registered_scope_requires_audit' if volume['registered_owned_sections'] else
                'index_hints_require_review' if volume.get('index_hint_pages') else 'no_index_confirmed_in_bounded_scan')
            volume['coverage_complete'] = False
            volume['corpus_directory_present'] = v in disk
        run_id = uuid.uuid4().hex
        config = {'version':VERSION, 'run_id':run_id, 'root':str(args.root.resolve()),
            'source_db':str(args.source_db.resolve()), 'tail_pages':args.tail_pages, 'workers':args.workers,
            'source_snapshot_sha256':sha(json.dumps([sections,origins,refs],sort_keys=True,ensure_ascii=False).encode()),
            'code_sha256':sha(Path(__file__).read_bytes()), 'volumes':volumes,
            'scope':'last N available physical positions plus registered sources and sections; not whole corpus'}
        summary = {'run_id':run_id, 'volumes':len(volumes), 'pages_selected':len(pages),
            'pages_read':sum(p['status']=='read' for p in pages), 'read_errors':sum(p['status']!='read' for p in pages),
            'inventory_issues':len(issues), 'discovery_statuses':dict(Counter(v['discovery_status'] for v in info.values())),
            'index_hint_pages_outside_owned_ranges':len(proposals),
            'reference_routes':dict(Counter(r['route'] for r in refs)),
            'source_db_updates':0, 'inference_calls':0, 'import_ready':False}
        args.out.mkdir(parents=True)
        for name, value in [('config',config),('summary',summary),('volumes',list(info.values())),('issues',issues),('sections',sections)]:
            (args.out/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        write_jsonl(args.out/'pages.jsonl',pages)
        write_jsonl(args.out/'discovery_candidates.jsonl',proposals)
        write_jsonl(args.out/'reference_worklist.jsonl',refs)
        fields=['volume','physical_files','registered_owned_sections','registered_entries','pages_read',
                'read_errors','index_hint_pages','hints_outside_owned_ranges','discovery_status']
        with (args.out/'volumes.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
            writer.writeheader()
            writer.writerows(info.values())
        con.executescript('''CREATE TABLE IF NOT EXISTS index_coverage_runs(
            run_id TEXT PRIMARY KEY,config_json TEXT NOT NULL,summary_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS index_coverage_volumes(
            run_id TEXT, volume TEXT, record_json TEXT NOT NULL, PRIMARY KEY(run_id,volume));''')
        with con:
            con.execute('INSERT INTO index_coverage_runs VALUES(?,?,?)',(run_id,json.dumps(config),json.dumps(summary)))
            con.executemany('INSERT INTO index_coverage_volumes VALUES(?,?,?)',
                [(run_id,v,json.dumps(record,ensure_ascii=False)) for v,record in info.items()])
        lines=['# Cobertura experimental dos índices','',
            'Sinais lexicais não certificam índices ou ausência deles. Nenhum destino foi resolvido.', '',
            '| Volume | Seções próprias cadastradas | Páginas lidas | Sinais fora das seções | Estado |','|---|---:|---:|---:|---|']
        for v in info.values():
            lines.append(f"| {v['volume']} | {v['registered_owned_sections']} | {v.get('pages_read',0)} | {v.get('hints_outside_owned_ranges',0)} | {v['discovery_status']} |")
        (args.out/'report.md').write_text('\n'.join(lines)+'\n')
        return summary
    finally:
        con.close()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    group=ap.add_mutually_exclusive_group(required=True)
    group.add_argument('--all-volumes',action='store_true')
    group.add_argument('--volume',action='append')
    ap.add_argument('--root',type=Path,default=Path('teste'))
    ap.add_argument('--source-db',type=Path,default=Path('data/alphabetical_indices.db'))
    ap.add_argument('--experiment-db',type=Path,default=Path('data/index_entry_parser/experiments.sqlite'))
    ap.add_argument('--tail-pages',type=int,default=24)
    ap.add_argument('--workers',type=int,default=4)
    ap.add_argument('--out',type=Path,required=True)
    print(json.dumps(run(ap.parse_args()),ensure_ascii=False,indent=2))
