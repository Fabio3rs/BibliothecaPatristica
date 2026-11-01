"""Read-only v2 snapshots, explicit shadow selection and frozen benchmarks."""
from __future__ import annotations

import json
import re
from pathlib import Path

from resumo_v2 import sha256_text
from tools.corpus_utils import page_number, resolve_page_file
from tools.indexing.index_target_locator import resolve_paired_page_image
from .common import ROOT, KIT, digest, read_json, readonly, safe_child
from .ocr_adapter import parse_blocks

REQUIRED = {'id', 'run_id', 'documento', 'pagina_num', 'pagina_file', 'is_current',
            'status', 'tainted_by_page', 'segments_json', 'output_hash', 'source_hash',
            'summary_display_pt', 'cumulative_summary', 'static_analysis_json'}

SOURCE_DRIFT_ERROR = 'Current OCR differs from generation OCR; use explicit allow_source_drift to study this mismatch'


def parse_page(spec):
    match = re.fullmatch(r'([A-Za-z]+\d+(?:\.\d+)?):([1-9]\d*)', spec)
    if not match:
        raise ValueError('Use a physical page such as PG026:71')
    return match[1].upper(), int(match[2])


def check_schema(con):
    missing = REQUIRED - {r[1] for r in con.execute('PRAGMA table_info(resumo_generations)')}
    if missing:
        raise ValueError(f'Missing v2 columns: {sorted(missing)}. No legacy fallback.')


def list_generations(db, spec):
    doc, page = parse_page(spec)
    with readonly(db) as con:
        check_schema(con)
        return [dict(r) for r in con.execute(
            'SELECT id,run_id,is_current,status,tainted_by_page,output_hash '
            'FROM resumo_generations WHERE documento=? AND pagina_num=? ORDER BY id DESC', (doc, page))]


def resolve_ocr(corpus_root, row):
    doc, name = row['documento'], row['pagina_file']
    if not re.fullmatch(r'[A-Za-z]+\d+(?:\.\d+)?', doc) or Path(name).name != name:
        raise ValueError('Unsafe source locator')
    directory = safe_child(corpus_root, doc + '/text')
    path = safe_child(directory, name)
    if path.is_file():
        if page_number(path) != row['pagina_num']:
            raise ValueError('Stored filename does not match physical page')
        return path, 'stored_filename'
    path, ambiguous = resolve_page_file(directory, volume_id=doc, page_num=row['pagina_num'], suffixes=('.txt',))
    if ambiguous:
        raise ValueError('Ambiguous OCR files for physical page')
    if path is None:
        raise FileNotFoundError('OCR file unavailable for selected v2 generation')
    return safe_child(directory, path.name), 'numeric_fallback'


def image_info(ocr_path, corpus_root):
    paired = resolve_paired_page_image(str(ocr_path))
    if not paired:
        return {'available': False, 'attached': False, 'warning': 'Facsimile missing or ambiguous'}
    path = Path(paired).resolve()
    if not path.is_relative_to(Path(corpus_root).resolve()):
        raise ValueError('Image path escapes corpus root')
    if path.stat().st_size > 40_000_000:
        return {'available': False, 'attached': False, 'warning': 'Facsimile exceeds 40 MB limit'}
    from PIL import Image
    try:
        with Image.open(path) as image:
            size = list(image.size)
            image.verify()
    except (OSError, ValueError):
        return {'available': False, 'attached': False, 'warning': 'Facsimile cannot be decoded'}
    return {'available': True, 'attached': False, 'path': str(path),
            'sha256': digest(path.read_bytes()), 'dimensions': size}


def assemble(row, raw, path, corpus_root, indices_db=None):
    segments = json.loads(row['segments_json'])
    if not isinstance(segments, list) or any(not isinstance(s, dict) or not isinstance(s.get('summary'), str) for s in segments):
        raise ValueError('Malformed v2 segments; cannot prepare page')
    output_hash = sha256_text(row['summary_display_pt'], row['cumulative_summary'],
                              json.dumps(segments, ensure_ascii=False, sort_keys=True))
    if output_hash != row['output_hash']:
        raise ValueError('V2 output_hash does not match stored summary and segments')
    analysis = json.loads(row['static_analysis_json'] or '{}')
    works = []
    if indices_db and Path(indices_db).is_file():
        with readonly(indices_db) as con:
            if {'work_key', 'volume_id'} <= {r[1] for r in con.execute('PRAGMA table_info(works)')}:
                for key in sorted({s.get('work_key') for s in segments if s.get('work_key')}):
                    item = con.execute('SELECT * FROM works WHERE work_key=? AND volume_id=?', (key, row['documento'])).fetchone()
                    if item:
                        works.append(dict(item))
    snap = {
        'source': {'documento': row['documento'], 'physical_page': row['pagina_num'],
                   'pagina_file': row['pagina_file'], 'v2_generation_id': row['id'],
                   'run_id': row['run_id'], 'scope': 'promoted' if row['is_current'] else 'shadow',
                   'status': row['status'], 'is_current': row['is_current'],
                   'v2_output_hash': row['output_hash'], 'v2_source_hash': row['source_hash'],
                   'source_hash_verification': 'opaque composite preserved, not recomputed',
                   'previous_generation_id': row.get('previous_generation_id'),
                   'ocr_path': str(path), 'ocr_sha256': digest(raw), 'synthetic': False},
        'v2': {'segments': [dict(s, id=f's{i}') for i, s in enumerate(segments, 1)],
               'page_kinds': json.loads(row.get('page_kinds_json') or '[]'),
               'summary_display_pt': row['summary_display_pt'],
               'metadata_context': {'static_analysis': analysis, 'index_works': works,
                                    'contributors': json.loads(row.get('contributors_json') or '[]'),
                                    'source_conflicts': json.loads(row.get('source_conflicts_json') or '[]')},
               'context_only': {'cumulative_summary': row['cumulative_summary'],
                                'role': 'disambiguation_only_not_current_page_evidence'}},
        'ocr': parse_blocks(raw), 'ocr_raw': raw,
        'facsimile': image_info(path, corpus_root),
        'generation_snapshot': {k: v for k, v in row.items() if not k.startswith('embedding')},
    }
    return snap


def load_page(db, corpus_root, spec, *, generation_id=None, run_id=None,
              indices_db=None, ocr_versions_db=None, allow_source_drift=False):
    doc, page = parse_page(spec)
    if generation_id is not None and run_id is not None:
        raise ValueError('Choose generation_id OR run_id')
    with readonly(db) as con:
        check_schema(con)
        query = 'SELECT * FROM resumo_generations WHERE documento=? AND pagina_num=?'
        params = [doc, page]
        if generation_id is not None:
            query += ' AND id=?'; params.append(int(generation_id))
        elif run_id is not None:
            query += ' AND run_id=?'; params.append(int(run_id))
        else:
            query += " AND is_current=1 AND status IN ('valid','metadata_pending') AND tainted_by_page IS NULL"
        rows = con.execute(query, params).fetchmany(2)
        fallback = False
        if not rows and generation_id is None and run_id is None:
            rows = con.execute(
                "SELECT * FROM resumo_generations WHERE documento=? AND pagina_num=? "
                "AND is_current=0 AND status IN ('valid','metadata_pending') "
                "AND tainted_by_page IS NULL ORDER BY id DESC LIMIT 1", (doc, page)).fetchall()
            fallback = bool(rows)
        if len(rows) != 1:
            raise ValueError('No unique eligible v2 generation. Explicitly choose a shadow generation ID or run ID.')
        row = dict(rows[0])
    if row['status'] not in {'valid', 'metadata_pending'} or row['tainted_by_page'] is not None:
        raise ValueError('Ineligible or tainted v2 generation')
    path, locator = resolve_ocr(corpus_root, row)
    raw = path.read_bytes().decode('utf-8')
    historical, historical_ocr = None, None
    if ocr_versions_db and Path(ocr_versions_db).is_file() and row.get('ocr_result_id'):
        with readonly(ocr_versions_db) as con:
            if {'id','volume_id','page_num','text_content'} <= {r[1] for r in con.execute('PRAGMA table_info(ocr_results)')}:
                old = con.execute('SELECT volume_id,page_num,text_content FROM ocr_results WHERE id=?', (row['ocr_result_id'],)).fetchone()
                if old:
                    if old['volume_id'] != doc or old['page_num'] != page:
                        raise ValueError('Historical OCR reference points to a different physical page')
                    historical = old['text_content'] == raw
                    historical_ocr = {'id': row['ocr_result_id'], 'sha256': digest(old['text_content'])}
    if locator == 'numeric_fallback' and historical is not True:
        raise ValueError('Numerical fallback requires a matching historical OCR snapshot')
    if historical is False and not allow_source_drift:
        raise ValueError(SOURCE_DRIFT_ERROR)
    snap = assemble(row, raw, path, corpus_root, indices_db)
    snap['source'].update(locator_status=locator, historical_ocr_matches=historical,
                          source_drift_allowed=allow_source_drift,
                          selection_policy='latest_eligible_shadow_fallback' if fallback else 'explicit_or_current')
    if historical_ocr is not None:
        snap['source']['historical_ocr'] = historical_ocr
    if historical is False:
        snap['ocr']['warnings'].append('SOURCE_CONFLICT: current OCR differs from summary generation OCR')
    elif historical is None:
        snap['ocr']['warnings'].append('historical_ocr_unavailable: current OCR link not independently verified')
    return snap


def benchmark_records(directory):
    path = Path(directory) / 'selected_pages.jsonl'
    if not path.is_file():
        return []
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def benchmark_catalog(directory, evaluation=False):
    records = []
    for r in benchmark_records(directory):
        if r['split'] == 'blind_holdout' and not evaluation:
            continue
        s = r['source']
        records.append({'benchmark_id': r['benchmark_id'], 'split': r['split'],
                        'page': f"{s['documento']}:{s['pagina_num']}", 'collection': s['documento'][:2],
                        'generation_id': s['generation_id'], 'scope': s['scope'],
                        'page_kind': s['primary_page_kind'], 'work_key': s.get('work_key') or '',
                        'categories': r['selection']['categories']})
    return records


def guard_holdout(directory, spec, evaluation=False):
    if evaluation:
        return
    doc, _ = parse_page(spec)
    if any(r['source']['documento'] == doc and r['split'] == 'blind_holdout' for r in benchmark_records(directory)):
        raise ValueError('Volume reserved for blind holdout. Enable explicit evaluation mode.')


def load_benchmark(directory, identifier, corpus_root, evaluation=False):
    r = next((r for r in benchmark_records(directory) if r['benchmark_id'] == identifier), None)
    if r is None:
        raise ValueError('Benchmark ID not found')
    if r['split'] == 'blind_holdout' and not evaluation:
        raise ValueError('Blind holdout requires explicit evaluation mode')
    source = r['source']
    row_path = safe_child(directory, source['generation_snapshot'])
    raw_path = safe_child(directory, source['ocr_snapshot'])
    if digest(row_path.read_bytes()) != source['generation_snapshot_sha256'] or digest(raw_path.read_bytes()) != source['ocr_sha256']:
        raise ValueError('Benchmark frozen source hash mismatch')
    row = read_json(row_path)
    if row['id'] != source['generation_id'] or row['documento'] != source['documento'] or row['pagina_num'] != source['pagina_num']:
        raise ValueError('Benchmark generation identity mismatch')
    path = safe_child(corpus_root, row['documento'] + '/text/' + row['pagina_file'])
    snap = assemble(row, raw_path.read_bytes().decode('utf-8'), path, corpus_root)
    snap['source'].update(benchmark_id=identifier, split=r['split'],
                          frozen_benchmark=True, benchmark_record_hash=digest(r))
    return snap


def demo_snapshot():
    snap = read_json(KIT / 'page-input.demo.json')
    snap['source']['synthetic'] = True
    snap['source']['scope'] = 'synthetic'
    snap['source']['ocr_sha256'] = digest(snap['ocr']['blocks'])
    snap['ocr']['warnings'] = ['Synthetic example, not an authenticated historical quotation']
    snap['facsimile']['available'] = False
    return snap
