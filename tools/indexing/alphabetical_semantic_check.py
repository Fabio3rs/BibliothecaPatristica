"""Optional Jev substage: reviewed editorial maps + semantic file verification.

No agent calls. The coordinator owns audit/checkpoints and staging writes; HTTP
workers use the existing bounded Jev pool. The canonical database is never opened.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
from uuid import uuid4

from scripts.jev_gateway_pool import run_pool
from scripts.validate_index_jev import (
    ENDPOINT, INSTRUCTION_VERSION, MODEL, Gateway, build_payload, record_attempts, summarize,
    classify, parse_answer,
)
from tools.indexing.alphabetical_stage_lock import volume_lock as semantic_lock
from tools.indexing.alphabetical_analysis_db import (
    connect_analysis_db, mark_downstream_stale, stable_fingerprint,
)
from tools.indexing.alphabetical_compact_pipeline import _target_is_inside_index_section
from tools.indexing.index_target_locator import parse_ocr_page_xml

CONTRACT = "staging-jev-v2"
MAP_VERSION = "reviewed-editorial-map-v1"
QUERY = """SELECT o.*, e.lemma_raw, e.entry_raw, e.source_file, e.section_key,
 e.semantic_json AS entry_json, s.semantic_json AS section_json, s.section_kind
 FROM analysis_occurrences o JOIN analysis_entries e USING(entry_key)
 JOIN analysis_sections s ON s.section_key=e.section_key
 WHERE o.volume_id=? AND o.locator_item_json IS NOT NULL
 ORDER BY o.entry_key, o.ref_order"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_semantic_runs (
 run_id TEXT PRIMARY KEY, volume_id TEXT NOT NULL, status TEXT NOT NULL,
 input_fingerprint TEXT NOT NULL, log_path TEXT NOT NULL, summary_json TEXT);
CREATE TABLE IF NOT EXISTS analysis_semantic_evaluations (
 fingerprint TEXT PRIMARY KEY, volume_id TEXT NOT NULL, occurrence_key TEXT NOT NULL,
 record_json TEXT NOT NULL);
"""


@dataclass(frozen=True)
class JevOptions:
    workers: int = 4
    limit: int = 20
    max_cost_usd: float = 0.05
    max_seconds: float = 1800
    timeout: float = 30
    max_retries: int = 2
    threshold: float = 0.5
    review_band: float = 0.1
    max_input_bytes: int = 200_000
    no_api: bool = False
    apply: bool = False
    verbose: bool = False
    include_payload: bool = False

    def validate(self):
        for name in ('workers', 'limit', 'max_input_bytes'):
            if getattr(self, name) < 1:
                raise ValueError(f'--jev-{name.replace("_", "-")} must be positive')
        for name in ('max_cost_usd', 'max_seconds', 'timeout'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'--jev-{name.replace("_", "-")} must be finite and positive')
        if self.max_retries < 0 or not 0 < self.threshold < 1 or not 0 <= self.review_band < .5:
            raise ValueError('invalid Jev retries, threshold or review band')
        if self.no_api and self.apply:
            raise ValueError('--jev-no-api cannot be combined with --jev-apply')


class JevBudget:
    """One command-wide budget, shared across volumes, separate from Codex."""
    def __init__(self, options):
        options.validate()
        self.options = options
        self.gateway = None

    def get_gateway(self):
        if self.gateway is None:
            key = os.environ.get('AI_GATEWAY_API_KEY', '')
            if not key:
                raise ValueError('AI_GATEWAY_API_KEY is required for Jev inference')
            args = SimpleNamespace(**asdict(self.options))
            self.gateway = Gateway(args, time.monotonic() + args.max_seconds, key)
        return self.gateway

    def close(self):
        if self.gateway:
            self.gateway.session.close()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def normalized(text):
    return ' '.join(text.split())


def row_identity(row):
    values = {k: row[k] for k in (
        'volume_id', 'occurrence_key', 'entry_json', 'ref_json', 'section_json',
        'source_file', 'locator_item_json', 'lemma_raw', 'entry_raw', 'ref_raw')}
    for key in ('entry_json', 'ref_json', 'section_json', 'locator_item_json'):
        values[key] = json.loads(values[key])
    return stable_fingerprint(values)


def load_rows(con, volume_id, occurrence_key=None):
    query, params = QUERY, [volume_id]
    if occurrence_key is not None:
        query = query.replace('ORDER BY', 'AND o.occurrence_key=? ORDER BY')
        params.append(occurrence_key)
    return [dict(r) for r in con.execute(query, params)]


def read_source(path, root, expected=None, quote=None):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('source_outside_volume')
    raw = path.read_bytes()
    if expected is not None and digest(raw) != expected:
        raise ValueError('source_hash_changed')
    text = raw.decode('utf-8')
    if quote is not None and (len(normalized(quote)) < 8 or normalized(text).count(normalized(quote)) != 1):
        raise ValueError('evidence_quote_not_unique')
    return text, {'file': str(path), 'sha256': digest(raw)}


def reviewed_evidence(evidence, root):
    if (not isinstance(evidence, dict)
            or not isinstance(evidence.get('file'), str) or not evidence['file'].strip()
            or not isinstance(evidence.get('sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', evidence['sha256'])
            or not isinstance(evidence.get('quote'), str) or not evidence['quote'].strip()):
        raise ValueError('invalid_reviewed_evidence')
    return read_source(evidence['file'], root, evidence['sha256'], evidence['quote'])[1]


def simple_coordinate(item, row):
    """This map version cannot disambiguate tomes, series, lines or open ranges."""
    match = re.fullmatch(r'\s*(?:(?:p(?:ag)?|col)\.?\s*)?(\d+)\s*([A-Da-d])?\.?\s*', row['ref_raw'])
    return bool(match and item.get('cited_pages') == [int(match[1])]
                and not item.get('line_ref_raw'))


def mapping_draft(rows):
    """Proposals only. Explicit review is needed for both scope and each mapping."""
    sections = {}
    for row in rows:
        item = json.loads(row['locator_item_json'])
        section = sections.setdefault(row['section_key'], {
            'volume_id': row['volume_id'], 'section_key': row['section_key'],
            'section_sha256': stable_fingerprint(json.loads(row['section_json'])),
            'reviewed': False, 'work': '', 'edition': '', 'reviewer': '',
            'evidence': [], 'mappings': []})
        if item.get('ref_kind') not in {'editorial_page', 'editorial_column'}:
            continue
        pages = item.get('cited_pages') or []
        if len(pages) != 1:
            continue
        for c in item.get('candidates') or []:
            if not c.get('helper_is_best'):
                continue
            key = str(pages[0])
            if any(m['coordinate'] == key and m['file'] == c['file'] for m in section['mappings']):
                continue
            path = Path(c['file'])
            section['mappings'].append({'coordinate': key, 'file': str(path),
                'target_sha256': digest(path.read_bytes()) if path.is_file() else None,
                'verified': False, 'evidence': [],
                'proposal_score': c.get('probability')})
    return {'schema_version': MAP_VERSION, 'sections': list(sections.values())}


def load_map(path):
    if path is None:
        return {'schema_version': MAP_VERSION, 'sections': []}
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema_version') != MAP_VERSION or not isinstance(value.get('sections'), list):
        raise ValueError('invalid editorial map schema')
    for scope in value['sections']:
        if (not isinstance(scope, dict) or not isinstance(scope.get('mappings'), list)
                or not isinstance(scope.get('evidence'), list)
                or any(not isinstance(m, dict) or not isinstance(m.get('evidence'), list) for m in scope['mappings'])):
            raise ValueError('invalid editorial map sections or mappings')
    return value


def current_map_error(record):
    """Revoking the relevant mapping must also prevent cached assembly."""
    try:
        path = record.get('location_map_path')
        if not isinstance(path, str) or not path:
            return 'missing Jev editorial map path'
        value = load_map(path)
        proof = record['location_evidence']
        scopes = [s for s in value['sections'] if s.get('volume_id') == record['tome']
                  and s.get('section_key') == record['section_key']]
        if len(scopes) != 1:
            return 'Jev editorial map scope changed'
        scope = scopes[0]
        mappings = [m for m in scope['mappings'] if m.get('coordinate') == proof['mapping']['coordinate']]
        if ({k: v for k, v in scope.items() if k != 'mappings'} != proof['section']
                or mappings != [proof['mapping']]):
            return 'Jev editorial map changed or revoked'
    except (OSError, ValueError, TypeError, KeyError):
        return 'invalid or missing Jev editorial map'
    return None


def prepare(row, location_map, root, options):
    item = json.loads(row['locator_item_json'])
    record = {'record_type': 'association', 'tome': row['volume_id'],
        'occurrence_key': row['occurrence_key'], 'entry_key': row['entry_key'],
        'ref_order': row['ref_order'], 'section_key': row['section_key'],
        'index_entry': row['lemma_raw'], 'index_reference': row['ref_raw'],
        'model': MODEL, 'instruction_version': INSTRUCTION_VERSION, 'contract': CONTRACT,
        'selection_category': 'reviewed-editorial-map', 'probability': None,
        'decision': None, 'needs_review': True, 'review_reasons': [],
        'identity_sha256': row_identity(row), 'applied': False}

    def skip(reason, status='candidate_ambiguous'):
        record.update(status=status, skip_reason=reason, review_reasons=[reason])
        return record, None

    if row['locator_status'] not in {'pending', 'ambiguous'} or row['target_file']:
        return skip('existing_resolution_preserved', 'already_resolved')
    previous = json.loads(row.get('decision_json') or '{}')
    if (previous.get('raw_json') or {}).get('requires_semantic_review'):
        return skip('existing_editorial_conflict_requires_repair')
    if (item.get('scripture_ref') or item.get('ref_kind') not in {'editorial_page', 'editorial_column'}
            or item.get('range_end_raw') or len(item.get('cited_pages') or []) != 1):
        return skip('unsupported_or_non_single_editorial_coordinate')
    if not simple_coordinate(item, row):
        return skip('qualified_coordinate_requires_mapping')
    if (not row['lemma_raw'] or len(row['entry_raw']) > max(2000, 10 * len(row['lemma_raw']))
            or not row['source_file']):
        return skip('editorial_entry_requires_review')
    try:
        index_text, source = read_source(row['source_file'], root)
        if (not normalized(row['entry_raw']) or normalized(index_text).count(normalized(row['entry_raw'])) != 1
                or not re.search(r'(?<!\w)' + re.escape(normalized(row['ref_raw'])) + r'(?!\w)', normalized(row['entry_raw']))):
            return skip('source_anchor_not_unique')
        scopes = [s for s in location_map['sections'] if s.get('volume_id') == row['volume_id']
                  and s.get('section_key') == row['section_key']]
        if len(scopes) != 1:
            return skip('editorial_scope_requires_review')
        scope = scopes[0]
        if (scope.get('reviewed') is not True
                or not all(isinstance(scope.get(k), str) and scope[k].strip() for k in ('work', 'edition', 'reviewer'))
                or not scope.get('evidence')
                or scope.get('section_sha256') != stable_fingerprint(json.loads(row['section_json']))):
            return skip('editorial_scope_requires_review')
        matches = [m for m in scope.get('mappings', []) if m.get('coordinate') == str(item['cited_pages'][0])]
        if not matches:
            return skip('no_supported_physical_page', 'candidate_not_found')
        if len(matches) != 1:
            return skip('multiple_physical_pages')
        mapping = matches[0]
        if mapping.get('verified') is not True or not mapping.get('evidence') or not mapping.get('target_sha256'):
            return skip('editorial_mapping_requires_review')
        sources = [source]
        for evidence in [*scope['evidence'], *mapping['evidence']]:
            sources.append(reviewed_evidence(evidence, root))
        path = Path(mapping['file']).resolve()
        if _target_is_inside_index_section(str(path), item):
            return skip('candidate_is_index_page')
        if path.stat().st_size > options.max_input_bytes:
            return skip('ocr_too_large_no_truncation', 'ocr_unavailable')
        text, target = read_source(path, root, mapping['target_sha256'])
        sources.append(target)
        if sum(c.isalpha() for c in parse_ocr_page_xml(text)['body_text']) < 20:
            return skip('insufficient_readable_body', 'ocr_unavailable')
        if text.count('\ufffd') / max(1, len(text)) > .01:
            return skip('invalid_ocr', 'ocr_unavailable')
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'missing_or_invalid_evidence'
        return skip(reason)
    payload = build_payload(row, text)
    if len(json.dumps(payload, ensure_ascii=False).encode()) > options.max_input_bytes:
        return skip('payload_too_large_no_truncation', 'ocr_unavailable')
    proof = {'section': {k: v for k, v in scope.items() if k != 'mappings'},
             'mapping': mapping, 'sources': sources,
             'index_source': source, 'target': target}
    record.update(status='prepared', needs_review=False, ocr_file=str(path),
        ocr_sha256=target['sha256'], ocr_chars=len(text), ocr_excerpt=text[:400],
        payload_sha256=stable_fingerprint(payload), location_evidence=proof,
        threshold=options.threshold, review_band=options.review_band,
        selection_reason='Reviewed work/edition map and unique index-source anchor; full-page thematic evaluation')
    record['fingerprint'] = stable_fingerprint({
        'contract': CONTRACT, 'identity': record['identity_sha256'], 'proof': proof,
        'payload': payload, 'instruction_version': INSTRUCTION_VERSION,
        'threshold': options.threshold, 'review_band': options.review_band})
    if options.include_payload:
        record['payload'] = payload
    return record, payload


def check_idle(con, volume_id):
    if con.execute("SELECT 1 FROM analysis_volume_stages WHERE volume_id=? AND status='running'", (volume_id,)).fetchone():
        raise ValueError('another analysis stage is running for this volume; finish it before semantic-check')


def semantic_decision(record):
    """The semantic probability is deliberately NOT a location confidence."""
    return {'entry_key': record['entry_key'], 'ref_order': record['ref_order'],
        'status': 'resolved', 'target_file': record['ocr_file'], 'confidence': None,
        'reason': 'Reviewed editorial mapping and positive Jev file-content check',
        'semantic_validation': record,
        'evidence': [
            {'kind': 'cited_page_match', 'ocr_file_path': record['ocr_file'],
             'detail': 'Hash-checked, reviewed work/edition mapping; see semantic_validation.location_evidence'},
            {'kind': 'jev_topic_present', 'detail': 'File-level thematic presence, not location probability',
             'topic_probability': record['probability'], 'model': MODEL,
             'evaluation_fingerprint': record['fingerprint']}]}


def validate_decision(result, item):
    """Called by the locator validator; stale evidence blocks verify/assemble."""
    record = result.get('semantic_validation') or {}
    try:
        p = record['probability']
        proof = record['location_evidence']
        scope, mapping = proof['section'], proof['mapping']
        map_error = current_map_error(record)
        if map_error:
            return map_error
        if (record['contract'] != CONTRACT or record['status'] != 'evaluated'
                or record['decision'] is not True or record['needs_review'] is not False
                or isinstance(p, bool) or not isinstance(p, (float, int)) or not 0 <= p <= 1
                or p < record['threshold'] or abs(p - record['threshold']) <= record['review_band']
                or record['entry_key'] != item['entry_key'] or record['ref_order'] != item['ref_order']
                or record['index_reference'] != item['ref_raw']
                or record['index_entry'] != item['lemma_raw']
                or scope['section_key'] != item['section_key'] or scope['reviewed'] is not True
                or not all(scope.get(k) for k in ('work', 'edition', 'reviewer', 'evidence'))
                or mapping['verified'] is not True or not mapping['evidence']
                or item.get('cited_pages') != [int(mapping['coordinate'])]
                or Path(mapping['file']).resolve() != Path(record['ocr_file']).resolve()
                or proof['target']['sha256'] != record['ocr_sha256']
                or result.get('confidence') is not None
                or Path(result['target_file']).resolve() != Path(record['ocr_file']).resolve()):
            return 'invalid Jev semantic decision'
        for source in record['location_evidence']['sources']:
            if digest(Path(source['file']).read_bytes()) != source['sha256']:
                return 'Jev evidence source hash changed'
        root = Path(record['ocr_file']).resolve().parent
        required = [proof['target'], proof['index_source']]
        for evidence in [*scope['evidence'], *mapping['evidence']]:
            required.append(reviewed_evidence(evidence, root))
        if (any(source not in proof['sources'] for source in required)
                or Path(proof['target']['file']).resolve() != Path(record['ocr_file']).resolve()
                or mapping['target_sha256'] != record['ocr_sha256']):
            return 'missing or inconsistent Jev source evidence'
    except (KeyError, TypeError, OSError, ValueError):
        return 'invalid or missing Jev source evidence'
    return None


def check_stored_decisions(con, volume_id):
    """Run even when verify/assemble would reuse their checkpoints."""
    for row in load_rows(con, volume_id):
        result = json.loads(row['decision_json'] or '{}')
        record = result.get('semantic_validation')
        if record:
            if record.get('identity_sha256') != row_identity(row):
                raise ValueError('Jev staging identity changed; rerun locate and semantic-check')
            error = validate_decision(result, json.loads(row['locator_item_json']))
            if error:
                raise ValueError(error)


def semantic_hints(con, volume_id):
    """Give verify fresh YES/NO observations without treating them as proof."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_semantic_evaluations'").fetchone():
        return {}
    current = {r['occurrence_key']: r for r in load_rows(con, volume_id)}
    hints = {}
    for saved in con.execute('SELECT record_json FROM analysis_semantic_evaluations WHERE volume_id=? ORDER BY rowid', (volume_id,)):
        record = json.loads(saved['record_json'])
        row = current.get(record.get('occurrence_key'))
        if not row or row_identity(row) != record.get('identity_sha256') or record.get('status') != 'evaluated':
            continue
        if current_map_error(record):
            continue
        try:
            if any(digest(Path(s['file']).read_bytes()) != s['sha256']
                   for s in record['location_evidence']['sources']):
                continue
        except (KeyError, OSError):
            continue
        hints.setdefault(row['occurrence_key'], []).append({
            'file': record['ocr_file'], 'topic_probability': record['probability'],
            'decision': record['decision'], 'needs_review': record['needs_review'],
            'model': record['model'], 'evaluation_fingerprint': record['fingerprint'],
            'instruction': 'Semantic presence only, not pagination proof. A negative does not prove absence; inspect editorial or OCR conflicts independently.'})
    return hints


def apply_record(con, row, record, location_map, root, options):
    con.execute('BEGIN IMMEDIATE')
    try:
        check_idle(con, row['volume_id'])
        current = next(iter(load_rows(con, row['volume_id'], row['occurrence_key'])), None)
        if current is None:
            raise ValueError('staging_occurrence_removed')
        checked, _ = prepare(current, location_map, root, options)
        if checked.get('fingerprint') != record['fingerprint']:
            raise ValueError('staging_or_sources_changed_before_apply')
        decision = semantic_decision({**record, 'applied': True})
        from tools.indexing.alphabetical_compact_pipeline import validate_locator_results
        report = validate_locator_results([json.loads(current['locator_item_json'])], [decision], source_root=root)
        if report['status'] != 'ok':
            raise ValueError('semantic_decision_failed_locator_validation')
        con.execute("""UPDATE analysis_occurrences SET locator_status='resolved', target_file=?,
            target_probability=NULL, decision_json=? WHERE occurrence_key=? AND volume_id=?
            AND locator_status IN ('pending','ambiguous') AND target_file IS NULL""",
            (record['ocr_file'], json.dumps(decision, ensure_ascii=False), row['occurrence_key'], row['volume_id']))
        mark_downstream_stale(con, volume_id=row['volume_id'], after_stage='locate')
        con.commit()
        record['applied'] = True
    except Exception:
        con.rollback()
        raise


def semantic_check_stage(**kwargs):
    with semantic_lock(kwargs['analysis_db'], kwargs['volume_id'], kwargs['options'].no_api):
        return _semantic_check_stage(**kwargs)


def _semantic_check_stage(*, analysis_db, volume_id, source_root, intermediate_dir,
                         options, budget=None, location_map_path=None):
    options.validate()
    own_budget = budget is None
    budget = budget or JevBudget(options)
    location_map = load_map(location_map_path)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
    out = Path(intermediate_dir) / 'semantic-check' / run_id
    out.mkdir(parents=True, exist_ok=False)
    log_path = out / 'evaluations.jsonl'
    rows = []
    with closing(connect_analysis_db(analysis_db, read_only=True)) as con:
        if not options.no_api:
            check_idle(con, volume_id)
        rows = load_rows(con, volume_id)
        cache_exists = con.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_semantic_evaluations'").fetchone()
        cached = {r['fingerprint']: json.loads(r['record_json']) for r in con.execute(
            'SELECT fingerprint, record_json FROM analysis_semantic_evaluations WHERE volume_id=?', (volume_id,))} if cache_exists else {}
    write_json(out / 'location_map_draft.json', mapping_draft(rows))
    write_json(out / 'manifest.json', {'contract': CONTRACT, 'volume_id': volume_id,
        'options': asdict(options), 'location_map': location_map,
        'input_fingerprint': stable_fingerprint([row_identity(r) for r in rows])})
    records = []
    writer = None
    if not options.no_api:
        writer = connect_analysis_db(analysis_db)
        try:
            writer.executescript(SCHEMA)
            check_idle(writer, volume_id)
            writer.execute('INSERT INTO analysis_semantic_runs VALUES (?,?,?,?,?,NULL)',
                (run_id, volume_id, 'running', stable_fingerprint([row_identity(r) for r in rows]), str(log_path)))
            writer.commit()
        except BaseException:
            writer.close()
            raise
    applied = 0
    backup = None
    pool_report = {'stop_reason': 'inspection' if options.no_api else 'candidates_exhausted'}
    calls_before = budget.gateway.calls if budget.gateway else 0
    prepared = 0
    try:
        with log_path.open('x', encoding='utf-8') as log:
            def save(row, record):
                nonlocal applied, backup
                if record['status'] == 'evaluated' and writer:
                    writer.execute('INSERT OR IGNORE INTO analysis_semantic_evaluations VALUES (?,?,?,?)',
                        (record['fingerprint'], volume_id, row['occurrence_key'], json.dumps(record, ensure_ascii=False)))
                    writer.commit()
                    if options.apply and record['decision'] and not record['needs_review']:
                        try:
                            try:
                                fresh_map = load_map(location_map_path)
                            except OSError:
                                raise ValueError('editorial_map_unavailable_before_apply') from None
                            if backup is None:
                                backup = out / 'before-apply.sqlite'
                                with sqlite3.connect(backup) as destination:
                                    writer.backup(destination)
                            apply_record(writer, row, record, fresh_map, source_root, options)
                            applied += 1
                        except ValueError as exc:
                            record.update(application_error=str(exc), needs_review=True)
                records.append(record)
                log.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
                log.flush()
                if options.verbose:
                    print(json.dumps({k: record.get(k) for k in ('occurrence_key', 'status', 'probability', 'cache_hit', 'applied', 'skip_reason')}, ensure_ascii=False), flush=True)

            def states():
                nonlocal prepared
                for row in rows:
                    record, payload = prepare(row, location_map, source_root, options)
                    record['location_map_path'] = str(Path(location_map_path).resolve()) if location_map_path else None
                    if payload is None:
                        save(row, record)
                        continue
                    if options.no_api:
                        if prepared >= options.limit:
                            break
                        prepared += 1
                        if options.include_payload:
                            write_json(out / f'payload-{prepared:04d}.json', payload)
                        save(row, record)
                        continue
                    hit = cached.get(record['fingerprint'])
                    if hit and hit.get('status') == 'evaluated':
                        try:
                            probability = parse_answer({'answers': {'topic_present': {'probability': hit.get('probability')}}})
                        except ValueError:
                            pass
                        else:
                            classify(record, probability, SimpleNamespace(**asdict(options)))
                            record.update(cache_hit=True, reused_fingerprint=hit['fingerprint'], attempts=[])
                            save(row, record)
                            continue
                    yield {'row': row, 'record': record, 'payload': payload}

            iterator = states()
            first = next(iterator, None)
            if first is not None:
                gateway = budget.get_gateway()
                def finish(state):
                    record_attempts(state['record'], state['attempts'], SimpleNamespace(**asdict(options)), gateway.stop_reason)
                    save(state['row'], state['record'])
                from itertools import chain
                pool_report = run_pool(chain([first], iterator), SimpleNamespace(**asdict(options)), gateway, ENDPOINT, finish)
        calls = (budget.gateway.calls if budget.gateway else 0) - calls_before
        summary = summarize(records, calls, log_path, pool_report['stop_reason'])
        summary.update(stage='semantic-check', volume_id=volume_id, applied=applied,
            canonical_db_updates=0, pool=pool_report, candidates_total=len(rows),
            budget_scope='command-wide across volumes, separate from Codex',
            command_calls=budget.gateway.calls if budget.gateway else 0,
            command_reported_cost_usd=budget.gateway.reported_cost if budget.gateway else 0,
            draft_map=str(out / 'location_map_draft.json'), no_api=options.no_api,
            backup=str(backup) if backup else None)
        write_json(out / 'summary.json', summary)
        if writer:
            writer.execute('UPDATE analysis_semantic_runs SET status=?, summary_json=? WHERE run_id=?',
                ('complete', json.dumps(summary, ensure_ascii=False), run_id))
            writer.commit()
        return summary
    except BaseException:
        if writer:
            writer.rollback()
            writer.execute("UPDATE analysis_semantic_runs SET status='failed' WHERE run_id=?", (run_id,))
            writer.commit()
        raise
    finally:
        if writer:
            writer.close()
        if own_budget:
            budget.close()
