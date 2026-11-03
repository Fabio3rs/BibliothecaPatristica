"""Offline, hash-checked evidence for editorial repairs and reviewed file mappings."""
import hashlib
import json
from pathlib import Path


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(row):
    return hashlib.sha256(json.dumps({k: row.get(k) for k in
        ('ref_id', 'entry_key', 'lemma_raw', 'entry_raw', 'ref_raw')},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_reviewed_location(row):
    proof = json.loads(row.get('raw_json') or '{}').get('reviewed_location')
    if not proof:
        return None
    if (proof.get('method') != 'facsimile_review_v1' or proof.get('identity') != identity(row)
            or proof.get('candidate_source') not in ('internal-marker', 'printed-header')
            or not proof.get('sources') or proof.get('cited_number') != row.get('page_ref_int')):
        raise ValueError('reviewed_location_identity_changed')
    try:
        if checksum(proof['file']) != proof['target_sha256'] or any(
                checksum(s['file']) != s['sha256'] for s in proof['sources']):
            raise ValueError('reviewed_location_source_changed')
    except (OSError, KeyError) as exc:
        raise ValueError('reviewed_location_source_missing') from exc
    return proof


def validate_reviewed_editorial(row):
    proof = json.loads(row.get('raw_json') or '{}').get('reviewed_editorial')
    if not proof:
        return
    if proof.get('identity') != identity(row) or not proof.get('sources'):
        raise ValueError('reviewed_editorial_identity_changed')
    try:
        if any(checksum(s['file']) != s['sha256'] for s in proof['sources']):
            raise ValueError('reviewed_editorial_source_changed')
    except (OSError, KeyError) as exc:
        raise ValueError('reviewed_editorial_source_missing') from exc
