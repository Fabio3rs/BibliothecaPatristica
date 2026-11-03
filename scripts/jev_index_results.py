"""Replay Jev evaluation logs and apply verified file-level resolutions.

No HTTP requests. Evaluation probabilities are semantic evidence, never stored
as calibrated location scores. Existing targets and negative results are retained.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.alphabetical_index_db import refresh_volume_quality
from scripts.resolve_alphabetical_unresolved import QUERY, connect_readonly


MATCH_FIELDS = ('ref_id', 'entry_key', 'section_key', 'tome', 'entry_sha256',
                'ocr_file', 'ocr_sha256', 'text_sha256', 'payload_sha256',
                'instruction_version', 'instruction_sha256', 'model')


def read_evaluations(paths):
    """Newest valid evaluation wins; a torn final JSONL line is recoverable."""
    results = {}
    for path in paths:
        path = Path(path).resolve()
        with path.open(encoding='utf-8') as stream:
            for number, line in enumerate(stream, 1):
                try:
                    record = json.loads(line)
                except ValueError:
                    if not line.endswith('\n'):
                        break
                    raise ValueError(f'Invalid JSONL record: {path}:{number}') from None
                if record.get('record_type') != 'association' or record.get('status') != 'evaluated':
                    continue
                key = (record.get('tome'), record.get('ref_id'), record.get('payload_sha256'))
                record = dict(record)
                record['reused_from'] = {'log': str(path), 'line': number,
                    'record_sha256': hashlib.sha256(line.encode()).hexdigest()}
                results[key] = record
    return results


def matching_evaluation(old, current):
    return all(old.get(k) is not None and old.get(k) == current.get(k) for k in MATCH_FIELDS)


def canonical_location_evidence(proof):
    """Use the established page-evidence vocabulary consumed by quality/export."""
    kind = {'neighbors': 'neighbor_sequence', 'printed-header': 'direct_editorial_page',
            'internal-marker': 'cited_page_match'}[proof['candidate_source']]
    return {'kind': kind, 'file': proof['ocr_file'], 'source': proof['candidate_source'],
            'detail': 'Physical-file mapping revalidated before semantic resolution.',
            'location_evidence': proof.get('location_evidence'),
            'numbering_scope': proof.get('numbering_scope'), 'visual_check': proof.get('visual_check')}


class ResolutionWriter:
    """Lazy backup and atomic per-reference commits, safe to resume after interruption."""
    def __init__(self, args, prepare):
        self.args = args
        self.prepare = prepare
        self.backup_path = args.log.with_suffix('.before-apply.sqlite').resolve()
        self.backup = None
        self.applied = 0

    def apply(self, result, spec):
        if (result.get('status') != 'evaluated' or result.get('decision') is not True
                or result.get('needs_review') is not False):
            return {'status': 'not_promotable'}
        p = result.get('probability')
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
            return {'status': 'invalid_probability'}
        if p < self.args.threshold or abs(p - self.args.threshold) <= self.args.review_band:
            return {'status': 'threshold_or_review_band'}
        if spec is None:
            return {'status': 'candidate_not_found'}
        db = Path(self.args.db).resolve()
        with closing(sqlite3.connect(db.as_uri()+'?mode=rw', uri=True, timeout=30)) as con:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('BEGIN IMMEDIATE')
            row = con.execute(QUERY.replace('WHERE s.volume_id = ?', 'WHERE r.ref_id = ?'),
                              (result['ref_id'],)).fetchone()
            if not row:
                return {'status': 'reference_missing'}
            row = dict(row)
            targets = [row.get(k) for k in ('target_file', 'resolved_target_ocr_file') if row.get(k)]
            if targets or row.get('locator_status') == 'resolved':
                same = targets and all(Path(t).resolve() == Path(result['ocr_file']).resolve() for t in targets)
                return {'status': 'already_resolved' if same and row['locator_status'] == 'resolved' else 'existing_resolution_preserved'}
            if row != spec['row']:
                return {'status': 'db_changed_since_selection'}
            if row['locator_status'] not in ('unresolved', 'unverified'):
                return {'status': 'ineligible_status'}
            fresh, payload = self.prepare({**spec, 'row': row}, self.args)
            if payload is None or fresh['needs_review'] or not matching_evaluation(result, fresh):
                return {'status': 'stale_or_unverified_evaluation'}
            if self.backup is None:
                self.backup_path.parent.mkdir(parents=True, exist_ok=True)
                with self.backup_path.open('xb'):
                    pass
                # The write reservation prevents a concurrent writer during backup.
                with closing(connect_readonly(db)) as source, closing(sqlite3.connect(self.backup_path)) as backup:
                    source.backup(backup)
                self.backup = str(self.backup_path)
            stamp = datetime.now(timezone.utc).isoformat()
            proof = {k: result.get(k) for k in MATCH_FIELDS}
            proof.update(method='jev_semantic_file_v1', probability=p, decision=True,
                         threshold=self.args.threshold, review_band=self.args.review_band,
                         resolution_level='ocr_file', position_status='not_resolved',
                         location_evidence=fresh.get('location_evidence'),
                         candidate_source=fresh['candidate_source'],
                         numbering_scope=fresh.get('numbering_scope'), visual_check=fresh.get('visual_check'),
                         evaluated_log=result.get('reused_from') or {'log': str(self.args.log.resolve())},
                         applied_at=stamp)
            raw = json.loads(row['raw_json'] or '{}')
            raw['jev_validation'] = proof
            location = canonical_location_evidence(proof)
            raw['compact_locator'] = {'status': 'resolved', 'method': proof['method'],
                                      'resolution_level': 'ocr_file', 'position_status': 'not_resolved',
                                      'evidence': [location, {'kind': 'jev_topic_present', 'probability': p}]}
            con.execute("""UPDATE alphabetical_refs SET target_file=?,resolved_target_ocr_file=?,
                locator_status='resolved',target_file_probability=NULL,
                target_ocr_file_candidate_score=NULL,raw_json=? WHERE ref_id=?""",
                (fresh['ocr_file'], fresh['ocr_file'], json.dumps(raw, ensure_ascii=False), row['ref_id']))
            order = con.execute('SELECT COALESCE(MAX(evidence_order),0)+1 FROM alphabetical_locator_evidence WHERE ref_id=?',
                                (row['ref_id'],)).fetchone()[0]
            con.execute("""INSERT INTO alphabetical_locator_evidence
                (ref_id,evidence_order,evidence_kind,evidence_ocr_file_path,detail,raw_json)
                VALUES (?,?,'jev_topic_present',?,?,?)""", (row['ref_id'], order, fresh['ocr_file'],
                'Semantic YES plus revalidated physical-page mapping; exact passage not resolved.',
                json.dumps(proof, ensure_ascii=False)))
            con.execute("""INSERT INTO alphabetical_locator_evidence
                (ref_id,evidence_order,evidence_kind,evidence_ocr_file_path,detail,raw_json)
                VALUES (?,?,?,?,?,?)""", (row['ref_id'], order+1, location['kind'], fresh['ocr_file'],
                location['detail'], json.dumps(location, ensure_ascii=False)))
            con.execute("""UPDATE alphabetical_entries SET
                target_file_best=COALESCE(target_file_best,?),
                resolved_target_ocr_file=COALESCE(resolved_target_ocr_file,?) WHERE entry_key=?""",
                (fresh['ocr_file'], fresh['ocr_file'], row['entry_key']))
            refresh_volume_quality(con, row['volume_id'])
            con.commit()
        self.applied += 1
        return {'status': 'applied', 'ref_id': row['ref_id'], 'target_file': fresh['ocr_file'],
                'resolution_level': 'ocr_file', 'backup': self.backup}
