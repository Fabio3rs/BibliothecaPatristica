#!/usr/bin/env python3
"""Experimental Jev search across PG040 editorial windows. Never writes SQLite.

--max-references selects windows (20 by default, including up to 4 controls).
--limit caps real HTTP attempts including retries (100 by default).
--no-api prepares ALL selected windows regardless of --limit, without HTTP.
Each file receives one topic_present question over its complete OCR.
No --apply: semantic presence does not establish the cited editorial location.
"""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import argparse
import json
import math
import os
import random
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import audit_pg040_concordance as audit
from scripts import validate_index_jev as jev
from scripts.jev_index_results import read_evaluations, matching_evaluation
from scripts.jev_gateway_pool import run_pool
from scripts.resolve_alphabetical_unresolved import QUERY, connect_readonly, repair_block_reason
from scripts.reviewed_index_evidence import validate_reviewed_editorial

VERSION = 'jev_editorial_window_v1'
CONTROL_IDS = {423605, 423621, 423654, 423656}


class SourceGuard:
    """Rehash on stat changes; never reuse a hash just by pathname."""
    def __init__(self, sources, root):
        self.sources, self.root, self.cache = sources, root.resolve(), {}

    def check(self):
        for proof in self.sources:
            path = Path(proof['file']).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError('source_outside_volume')
            try:
                stat = path.stat()
                stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                old = self.cache.get(str(path))
                actual = old[1] if old and old[0] == stamp else jev.sha(path.read_bytes())
                self.cache[str(path)] = stamp, actual
                if actual != proof['sha256']:
                    raise ValueError('source_changed_since_planning')
            except OSError:
                raise ValueError('source_unavailable_since_planning') from None


def reference_guard(con, window):
    current = con.execute(QUERY.replace('WHERE s.volume_id = ?', 'WHERE r.ref_id = ?'),
                          (window['row']['ref_id'],)).fetchone()
    if not current or dict(current) != window['row']:
        raise ValueError('reference_changed_since_planning')


def sample_windows(windows, seed, mode):
    rng = random.Random(seed)
    if mode == 'random':
        result = list(windows)
        rng.shuffle(result)
        return result
    groups = {}
    for window in windows:
        groups.setdefault(window['category'], []).append(window)
    for values in groups.values():
        rng.shuffle(values)
    result = []
    while any(groups.values()):
        for category in sorted(groups):
            if groups[category]:
                result.append(groups[category].pop())
    return result


def build_plan(args):
    summary, table, markers, intervals, references = audit.investigate(args.db, args.root)
    inputs = {p['file']: p for p in summary['inputs']}
    physical_sources = {jev.page_number(Path(p['file'])): p for p in summary['inputs']}
    by_number = {}
    for marker in markers:
        by_number.setdefault(marker['number'], []).append(marker)
    anchors = {n: values[0] for n, values in by_number.items()
               if len(values) == 1 and values[0]['sequence_support']}
    intervals = {item['antwerp']: item for item in intervals}
    windows, exclusions = [], []
    for record in references:
        row = record['row_snapshot']
        if args.ref_id and row['ref_id'] not in args.ref_id:
            continue
        reason = None
        control = row['ref_id'] in CONTROL_IDS and row['locator_status'] == 'resolved'
        if row['locator_status'] == 'resolved' and not control:
            reason = 'existing_resolution_not_a_reviewed_control'
        elif not control and (row.get('target_file') or row.get('resolved_target_ocr_file')):
            reason = 'existing_target_preserved'
        elif not control and row['locator_status'] not in ('unresolved', 'unverified', 'ambiguous'):
            reason = 'ineligible_reference_status'
        elif repair_block_reason(row):
            reason = repair_block_reason(row)
        elif not row.get('lemma_raw') or len(row.get('entry_raw') or '') > max(2000, 10 * len(row['lemma_raw'])):
            reason = 'editorial_entry_requires_review'
        elif re.search(r'\b(?:forte|loco)\b', row['ref_raw'], re.I):
            reason = 'editorial_qualification_requires_review'
        elif row['ref_kind'] not in jev.EDITORIAL_KINDS or row.get('range_end_raw') or row.get('cited_editorial_page_end_number'):
            reason = 'unsupported_locator_or_editorial_range'
        elif Path(row['section_file_start'] or '').resolve() != Path(summary['scope']['index_notice']['file']).resolve():
            reason = 'section_outside_documented_edition'
        if not reason:
            try:
                validate_reviewed_editorial(row)
            except ValueError as exc:
                reason = str(exc)
        number = record['antwerp']
        interval = intervals.get(number)
        files, bounds, kind = [], [], 'bounded_markers'
        if not reason and interval and interval['status'] == 'bounded_marker_candidate':
            files = interval['files']
            bounds = interval['start_occurrences'] + interval['end_occurrences']
        elif not reason:
            left = max((n for n in anchors if n <= number), default=None) if number is not None else None
            right = min((n for n in anchors if n > number), default=None) if number is not None else None
            if left is None or right is None:
                reason = 'missing_outer_anchor'
            else:
                bounds = [anchors[left], anchors[right]]
                a, b = [bound['physical_page'] for bound in bounds]
                if b < a or any(n not in physical_sources for n in range(a, b+1)):
                    reason = 'missing_file_or_reversed_anchors'
                else:
                    files = [{'physical_page': n, **physical_sources[n]} for n in range(a, b+1)]
                    kind = 'relaxed_outer_anchors'
        if not reason and len(files) > args.max_files:
            reason = 'window_exceeds_cap_not_truncated'
        if reason:
            exclusions.append({'record_type': 'plan_exclusion', 'ref_id': row['ref_id'],
                               'index_entry': row.get('lemma_raw'), 'reason': reason})
            continue
        category = ('control' if control else kind if kind == 'relaxed_outer_anchors' else
                    'bounded_name_hint' if record['whole_file_name_hints'] else 'bounded_no_literal')
        window = {'row': row, 'files': files, 'bounds': bounds, 'kind': kind,
                  'category': category, 'control': control,
                  'expected_control_file': str(Path(row['target_file']).resolve()) if control and row.get('target_file') else None,
                  'edition_scope': summary['scope'], 'needs_review': True, 'promotion_allowed': False}
        window['window_sha256'] = jev.sha(jev.encoded(window))
        windows.append(window)
    controls = sorted((w for w in windows if w['control']), key=lambda w: w['row']['ref_id'])
    if not args.ref_id:
        controls = controls[:min(args.controls, args.max_references)]
    others = sample_windows([w for w in windows if not w['control']], args.seed, args.sample)
    selected = (controls + others)[:args.max_references]
    if args.ref_id:
        present = {r['ref_id'] for r in references}
        exclusions.extend({'record_type': 'plan_exclusion', 'ref_id': n, 'reason': 'reference_not_in_pg040'}
                          for n in sorted(set(args.ref_id) - present))
    return selected, exclusions, {'audit_summary': summary, 'inputs': list(inputs.values()),
                                 'eligible_windows': len(windows), 'selected_windows': len(selected)}


def prepare(window, candidate, args):
    row = window['row']
    result = {'record_type': 'deepscan_association', 'mode': VERSION,
              'tome': row['volume_id'], 'ref_id': row['ref_id'], 'entry_key': row['entry_key'],
              'section_key': row['section_key'], 'index_entry': row['lemma_raw'],
              'index_reference': row['ref_raw'], 'window_sha256': window['window_sha256'],
              'entry_sha256': jev.sha(jev.encoded({k: row.get(k) for k in ('lemma_raw', 'entry_raw', 'ref_raw')})),
              'ocr_file': str(Path(candidate['file']).resolve()), 'physical_page': candidate['physical_page'],
              'candidate_source': window['kind'], 'selection_category': 'internal-marker',
              'model': jev.MODEL, 'instruction_version': jev.INSTRUCTION_VERSION,
              'probability': None, 'decision': None, 'status': 'not_started',
              'needs_review': True, 'promotion_allowed': False,
              'review_reasons': ['exploratory_window_requires_editorial_review'], 'attempts': []}

    def skip(reason):
        result.update(status='ocr_unavailable', skip_reason=reason)
        return result, None

    path = Path(result['ocr_file'])
    if not path.is_relative_to(args.root.resolve()):
        return skip('outside_volume')
    try:
        if path.stat().st_size > args.max_input_bytes:
            return skip('ocr_too_large_no_truncation')
        raw = path.read_bytes()
        text = raw.decode('utf-8')
    except (OSError, UnicodeError):
        return skip('missing_or_invalid_utf8_ocr')
    if jev.sha(raw) != candidate['sha256']:
        return skip('ocr_changed_since_planning')
    parsed = jev.parse_ocr_page_xml(text)
    if sum(c.isalpha() for c in parsed['body_text']) < 20 or text.count('\ufffd') / max(1, len(text)) > .01:
        return skip('insufficient_readable_body')
    payload = jev.build_payload(row, text)
    if len(jev.encoded(payload)) > args.max_input_bytes:
        return skip('payload_too_large_no_truncation')
    result.update(ocr_sha256=jev.sha(raw), text_sha256=jev.sha(text.encode()),
                  payload_sha256=jev.sha(jev.encoded(payload)),
                  instruction_sha256=jev.sha(payload['questions']['topic_present']['instructions'].encode()),
                  ocr_chars=len(text), ocr_excerpt=text[:400], printed_header=parsed['header_text'],
                  scope='complete_physical_ocr_not_exact_editorial_span')
    if args.include_text:
        result['ocr_text'] = text
    if args.include_payload:
        result['payload'] = payload
    return result, payload


def aggregate(window, results, args, invalid_reason=None):
    evaluated = sorted((r for r in results if r['status'] == 'evaluated'),
                       key=lambda r: (-r['probability'], r['physical_page'], r['ocr_file']))
    complete = bool(results) and len(evaluated) == len(window['files']) and not invalid_reason
    positives = [r for r in evaluated if r['decision']]
    status = ('single_positive' if len(positives) == 1 else 'multiple_positives' if positives else 'no_positive') if complete else 'incomplete'
    if args.no_api and all(r['status'] == 'prepared' for r in results) and not invalid_reason:
        status = 'prepared'
    expected = window['expected_control_file']
    expected_result = next((r for r in evaluated if expected and Path(r['ocr_file']).resolve() == Path(expected).resolve()), None)
    return {'record_type': 'window_result', 'ref_id': window['row']['ref_id'],
            'index_entry': window['row']['lemma_raw'], 'index_reference': window['row']['ref_raw'],
            'window_sha256': window['window_sha256'], 'window_kind': window['kind'],
            'status': status, 'complete': complete, 'invalid_reason': invalid_reason,
            'expected_files': len(window['files']), 'evaluated_files': len(evaluated),
            'positive_files': [r['ocr_file'] for r in positives],
            'single_positive_file': positives[0]['ocr_file'] if complete and len(positives) == 1 else None,
            'ranking': [{'file': r['ocr_file'], 'physical_page': r['physical_page'],
                         'probability': r['probability'], 'decision': r['decision'],
                         'cache_hit': bool(r.get('cache_hit'))} for r in evaluated],
            'score_gap': evaluated[0]['probability'] - evaluated[1]['probability'] if len(evaluated) >= 2 else None,
            'association_statuses': dict(Counter(r['status'] for r in results)),
            'needs_review': True, 'promotion_allowed': False,
            'control': window['control'], 'expected_control_file': expected,
            'control_target_in_window': expected in [r['ocr_file'] for r in results] if expected else None,
            'control_positive_detected': expected_result['decision'] if expected_result else None,
            'control_evaluation_reused': bool(expected_result.get('cache_hit')) if expected_result else None}


def run(args):
    deadline = time.monotonic() + args.max_seconds
    key = None if args.no_api else os.environ.get('AI_GATEWAY_API_KEY')
    if not args.no_api and not key:
        raise ValueError('AI_GATEWAY_API_KEY is required (or use --no-api)')
    cached = read_evaluations(args.resume_log, record_types=('association', 'deepscan_association'))
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open('x', encoding='utf-8') as log:
        def emit(record):
            log.write(jev.encoded(record).decode() + '\n')
            log.flush()
            if record.get('status') == 'evaluated':
                os.fsync(log.fileno())

        emit({'record_type': 'run', 'mode': VERSION, 'started_at': datetime.now(timezone.utc).isoformat(),
              'options': {k: str(v) if isinstance(v, Path) else [str(x) for x in v] if isinstance(v, list) else v
                          for k, v in vars(args).items()}, 'model': jev.MODEL,
              'instruction_version': jev.INSTRUCTION_VERSION, 'db_access': 'mode=ro; query_only=ON',
              'promotion_allowed': False, 'module_hashes': {
                  name: jev.sha((ROOT/'scripts'/name).read_bytes()) for name in
                  ('deepscan_index_jev.py','audit_pg040_concordance.py','audit_alphabetical_numbering.py',
                   'validate_index_jev.py','jev_gateway_http.py','jev_gateway_pool.py','jev_index_results.py')}})
        windows, exclusions, planning = build_plan(args)
        emit({'record_type': 'planning', **planning})
        for exclusion in exclusions:
            emit(exclusion)
        source_guard = SourceGuard(planning['inputs'], args.root)
        states, by_ref = [], {}
        records = []
        gateway = jev.Gateway(args, deadline, key)
        stop = 'candidates_exhausted'
        pool_details = None
        last_progress = 0

        def finish(state):
            nonlocal last_progress
            result = state['result']
            if 'attempts' in state:
                jev.record_attempts(result, state['attempts'], args, gateway.stop_reason or state.get('not_sent_reason'))
            result.update(needs_review=True, promotion_allowed=False)
            records.append(result)
            state['emitted'] = True
            emit(result)
            if args.verbose:
                print(f"{result['ref_id']} | {result['index_entry']} | physical={result['physical_page']} "
                      f"status={result['status']} p={result['probability']} cache={bool(result.get('cache_hit'))}", flush=True)
            if args.progress_every and gateway.calls >= last_progress + args.progress_every:
                last_progress = gateway.calls
                print(json.dumps({'progress': True, 'calls': gateway.calls, 'completed_calls': gateway.completed_calls,
                                  'reported_cost_usd': gateway.reported_cost}), flush=True)

        try:
            with closing(connect_readonly(args.db)) as con:
                for window in windows:
                    emit({'record_type': 'window_plan', **window})
                    by_ref[window['row']['ref_id']] = []
                    for candidate in window['files']:
                        result, payload = prepare(window, candidate, args)
                        state = {'window': window, 'result': result, 'payload': payload}
                        states.append(state)
                        by_ref[window['row']['ref_id']].append(result)
                for state in states:
                    if time.monotonic() >= deadline:
                        stop = 'time_budget'
                        break
                    result, payload, window = state['result'], state['payload'], state['window']
                    try:
                        source_guard.check()
                        reference_guard(con, window)
                    except ValueError as exc:
                        result.update(status='input_changed', skip_reason=str(exc))
                        state['payload'] = None
                        finish(state)
                        continue
                    if payload is None:
                        finish(state)
                        continue
                    if args.show_payload:
                        print(json.dumps(payload, ensure_ascii=False, indent=2))
                    old = cached.get((result['tome'], result['ref_id'], result.get('payload_sha256')))
                    reusable = old is not None and matching_evaluation(old, result)
                    if args.no_api:
                        result.update(status='prepared', cache_available=reusable)
                        finish(state)
                    elif reusable:
                        p = jev.parse_answer({'answers': {'topic_present': {'probability': old.get('probability')}}})
                        jev.classify(result, p, args)
                        result.update(cache_hit=True, reused_from=old['reused_from'])
                        finish(state)

                def items():
                    for state in states:
                        if state.get('emitted') or not state['payload']:
                            continue
                        try:
                            source_guard.check()
                            reference_guard(con, state['window'])
                        except ValueError as exc:
                            state['result'].update(status='input_changed', skip_reason=str(exc))
                            finish(state)
                            continue
                        yield state

                if not args.no_api and stop != 'time_budget':
                    if args.workers > 1:
                        pool_details = run_pool(items(), args, gateway, jev.ENDPOINT, finish)
                        stop = pool_details['stop_reason']
                    else:
                        for state in items():
                            if not gateway.available():
                                stop = gateway.stop_reason or ('limit' if gateway.calls >= args.limit else 'time_budget')
                                break
                            state['attempts'] = gateway.evaluate(state['payload'])
                            finish(state)
                stop = gateway.stop_reason or stop
                for state in states:
                    if not state.get('emitted'):
                        state['result'].update(status='not_started', skip_reason=stop)
                        finish(state)
                windows_out = []
                global_error = None
                try:
                    source_guard.check()
                except ValueError as exc:
                    global_error = str(exc)
                for window in windows:
                    invalid = global_error
                    try:
                        reference_guard(con, window)
                    except ValueError as exc:
                        invalid = str(exc)
                    group = aggregate(window, by_ref[window['row']['ref_id']], args, invalid)
                    windows_out.append(group)
                    emit(group)
                    if args.verbose:
                        print(f"Window {group['ref_id']}: {group['status']} "
                              f"({group['evaluated_files']}/{group['expected_files']} evaluated)")
        finally:
            gateway.session.close()
        summary = jev.summarize(records, gateway.calls, args.log, stop)
        summary.update(mode=VERSION, references_selected=len(windows), planned_associations=len(states),
                       window_statuses=dict(Counter(w['status'] for w in windows_out)),
                       complete_windows=sum(w['complete'] for w in windows_out),
                       planning_exclusions=dict(Counter(e['reason'] for e in exclusions)),
                       controls_selected=sum(w['control'] for w in windows_out),
                       controls_positive=sum(w['control_positive_detected'] is True for w in windows_out),
                       controls_reused=sum(w['control_evaluation_reused'] is True for w in windows_out),
                       estimated_uncached_associations=sum(r['status']=='prepared' and not r.get('cache_available') for r in records) if args.no_api else None,
                       db_updates=0, applied=0, promotion_allowed=False, pool=pool_details)
        emit(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, default=ROOT/'data/alphabetical_indices.db')
    p.add_argument('--root', type=Path, default=ROOT/'teste/PG040/text')
    p.add_argument('--tome', choices=['PG040'], default='PG040', help='Initial implementation supports PG040 only')
    p.add_argument('--max-references', type=int, default=20, help='Selected windows, including controls')
    p.add_argument('--controls', type=int, default=4, help='Up to four previously reviewed PG040 controls; 0 excludes them')
    p.add_argument('--ref-id', type=int, action='append', default=[], help='Restrict to explicit reference IDs; repeatable')
    p.add_argument('--max-files', type=int, default=5, help='Whole window cap; oversized windows are never truncated')
    p.add_argument('--limit', type=int, default=100, help='Maximum HTTP attempts, including retries; 0 sends none')
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--max-cost-usd', type=float, default=.05)
    p.add_argument('--max-seconds', type=float, default=1800)
    p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--max-retries', type=int, default=1)
    p.add_argument('--max-input-bytes', type=int, default=60000)
    p.add_argument('--threshold', type=float, default=.5)
    p.add_argument('--review-band', type=float, default=.1)
    p.add_argument('--sample', choices=['mixed', 'random'], default='mixed')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--progress-every', type=int, default=20)
    p.add_argument('--resume-log', type=Path, action='append', default=[])
    p.add_argument('--log', type=Path, default=ROOT/'data/jev_validation'/('deepscan_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')+'.jsonl'))
    for option in ('no-api','verbose','show-payload','include-text','include-payload'):
        p.add_argument('--'+option, action='store_true')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if (args.max_references < 1 or args.max_files < 1 or args.workers < 1 or args.limit < 0
            or not 0 <= args.controls <= 4 or not 0 <= args.max_retries <= 5 or args.progress_every < 0):
        p.error('Invalid limits, workers, controls or retry count')
    if (any(not math.isfinite(v) or v <= 0 for v in (args.max_cost_usd,args.max_seconds,args.timeout,args.max_input_bytes))
            or not 0 <= args.threshold <= 1 or not 0 <= args.review_band <= 1):
        p.error('Budgets must be positive and finite; threshold/review-band must be in [0,1]')
    try:
        run(args)
    except (OSError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
