"""Validate visually reviewed alternative readings and stage them without editing OCR."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from tools.indexing.entry_parser_experiment import digest
from tools.indexing.entry_parser_literal_rows import attributed_reference_groups
from tools.indexing.structured_index_experiment import check_span, encoded, key

VERSION = 'source-repair-staging-v1'
SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE occurrences(
 occurrence_id TEXT PRIMARY KEY, proposal_id TEXT UNIQUE NOT NULL, volume TEXT NOT NULL,
 physical INTEGER NOT NULL, source TEXT NOT NULL, source_sha256 TEXT NOT NULL,
 image TEXT NOT NULL, image_sha256 TEXT NOT NULL, lemma_raw TEXT NOT NULL,
 visual_literal TEXT NOT NULL, quality_json TEXT NOT NULL, evidence_json TEXT NOT NULL);
CREATE TABLE readings(
 reading_id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL REFERENCES occurrences,
 version_id INTEGER NOT NULL, snapshot TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
 char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, literal TEXT NOT NULL,
 agrees_with_visual INTEGER NOT NULL, evidence_json TEXT NOT NULL,
 UNIQUE(snapshot,snapshot_sha256,char_start,char_end));
CREATE TABLE reference_groups(
 reference_id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL REFERENCES occurrences,
 owner_raw TEXT NOT NULL, reference_raw TEXT NOT NULL,
 coordinate_space TEXT NOT NULL CHECK(coordinate_space='visual_transcription'),
 owner_start INTEGER NOT NULL, owner_end INTEGER NOT NULL,
 ref_start INTEGER NOT NULL, ref_end INTEGER NOT NULL, evidence_json TEXT NOT NULL);
CREATE VIRTUAL TABLE recovery_search USING fts5(occurrence_id UNINDEXED, visual_literal);
'''


def verified_text(path, expected):
    data = Path(path).read_bytes()
    if digest(data) != expected:
        raise ValueError(f'Stale evidence: {path}')
    return data.decode('utf-8')


def validate(review):
    if review.get('schema') != 'source-repair-visual-review-v1':
        raise ValueError('Unsupported visual review format')
    pages, result, excluded, identities = {}, [], [], set()
    for page in review['page_provenance']:
        identity = page['volume'], page['physical']
        if identity in pages:
            raise ValueError('Duplicate physical source')
        verified_text(page['source'], page['source_sha256'])
        if digest(Path(page['image']).read_bytes()) != page['image_sha256']:
            raise ValueError('Stale facsimile')
        inventories = json.loads(verified_text(page['inventory'], page['inventory_sha256']))
        matching = [p for p in inventories if p['source'] == page['source']
                    and p['source_sha256'] == page['source_sha256']
                    and p['image_sha256'] == page['image_sha256']]
        if len(matching) != 1:
            raise ValueError('Inventory identity mismatch')
        pages[identity] = page, {v['id']: v for v in matching[0]['versions']}
    for proposal in review['proposals']:
        if proposal['id'] in identities:
            raise ValueError('Duplicate proposal identity')
        identities.add(proposal['id'])
        page, versions = pages[(proposal['volume'], proposal['physical'])]
        for name in ('source', 'source_sha256'):
            if proposal['current_'+name] != page[name]:
                raise ValueError('Proposal source identity mismatch')
        if proposal['image'] != page['image'] or proposal['image_sha256'] != page['image_sha256']:
            raise ValueError('Proposal image identity mismatch')
        if proposal.get('current_span') is not None or proposal.get('applied') is not False:
            raise ValueError('This stage only accepts unapplied missing-text proposals')
        if proposal.get('status') != 'proposal_only_visual_reviewed':
            raise ValueError('Explicit visual review required')
        visual = proposal['visual_reading']
        if not visual.get('literal') or not visual.get('location') or not visual.get('method'):
            raise ValueError('Missing visual evidence')
        accepted = []
        for span in proposal['alternative_spans']:
            version = versions[span['version_id']]
            # Cross-check against the original inventory, not the proposal's eligibility flag.
            if version['image_hash'] != page['image_sha256']:
                excluded.append({'proposal_id': proposal['id'], 'version_id': version['id'],
                                 'reason': 'different_image_hash'})
                continue
            if (span['snapshot'] != version['snapshot'] or
                    span['snapshot_sha256'] != version['text_hash'] or
                    version.get('comparison_status') != 'compared'):
                raise ValueError('Alternative snapshot identity mismatch')
            raw = verified_text(span['snapshot'], span['snapshot_sha256'])
            check_span(raw, span, span['literal'])
            accepted.append(span)
        if not accepted:
            raise ValueError('No verified reading of the same image')
        groups = attributed_reference_groups(visual['literal'], 0, len(visual['literal']),
                                              'analytic-tome-rows-v1')
        result.append({'proposal': proposal, 'readings': accepted, 'groups': groups})
    return result, excluded


def run(review_path, out):
    review_path, out = Path(review_path).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Use a new output directory')
    review = json.loads(review_path.read_text())
    records, excluded = validate(review)
    out.mkdir(parents=True)
    documents = []
    with closing(sqlite3.connect(out/'repairs.sqlite')) as con:
        con.execute('PRAGMA journal_mode=WAL'); con.executescript(SCHEMA)
        with con:
            con.execute('INSERT INTO metadata VALUES(?,?)', ('configuration', encoded({
                'version': VERSION, 'review': str(review_path),
                'review_sha256': digest(review_path.read_bytes()),
                'code_sha256': digest(Path(__file__).read_bytes())})))
            for record in records:
                p = record['proposal']
                oid = key(p['image_sha256'], p['id'])
                quality = {'source_fidelity': 'assistant_visual_review_of_short_fragment',
                    'human_reviewed': False, 'segmentation': 'directed_visual_review',
                    'reference_attribution': 'mechanical_on_visual_transcription',
                    'edition_mapping': 'not_inferred', 'destination': 'not_attempted',
                    'source_repaired': False, 'publication': 'review_required'}
                con.execute('INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (oid, p['id'], p['volume'], p['physical'], p['current_source'],
                     p['current_source_sha256'], p['image'], p['image_sha256'], p['lemma'],
                     p['visual_reading']['literal'], encoded(quality), encoded(p)))
                for span in record['readings']:
                    con.execute('INSERT INTO readings VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (key(span['snapshot'], span['snapshot_sha256'], span['char_start'], span['char_end']), oid,
                         span['version_id'], span['snapshot'], span['snapshot_sha256'], span['char_start'],
                         span['char_end'], span['literal'], int(span['literal'] == p['visual_reading']['literal']), encoded(span)))
                for g in record['groups']:
                    con.execute('INSERT INTO reference_groups VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (key(oid, g), oid, g['owner_text_raw'], g['raw'], 'visual_transcription',
                         g['owner_span']['char_start'], g['owner_span']['char_end'],
                         g['char_start'], g['char_end'], encoded(g)))
                con.execute('INSERT INTO recovery_search VALUES(?,?)', (oid, p['visual_reading']['literal']))
                documents.append({'occurrence_id': oid, 'proposal_id': p['id'], 'volume': p['volume'],
                    'physical': p['physical'], 'source': p['current_source'], 'current_span': None,
                    'visual_transcription': p['visual_reading'], 'reference_groups': record['groups'],
                    'reference_coordinate_space': 'visual_transcription', 'alternative_readings': record['readings'],
                    'quality': quality, 'conflicts': p['conflicts']})
            if con.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Invalid recovery relations')
            summary = {t: con.execute(f'SELECT count(*) FROM {t}').fetchone()[0]
                       for t in ('occurrences', 'readings', 'reference_groups')}
            summary.update(version=VERSION, excluded_readings=excluded, source_updates=0,
                           production_db_updates=0, api_calls=0, import_ready=False)
            con.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
    (out/'documents.jsonl').write_text(''.join(encoded(d)+'\n' for d in documents))
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (out/'review.json').write_text(encoded(review)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.review, args.out), ensure_ascii=False, indent=2))
