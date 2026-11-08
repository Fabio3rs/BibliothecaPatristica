"""Read-only comparison of printed-index scripture refs and persisted OCR citations."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import sqlite3

from tools.ocr_xml_utils import normalize_visible_text, parse_ocr_xml_page

VERSION = 'scripture-index-comparison-v1'


def readonly(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    con.execute('BEGIN')
    return con


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def relation(ref, occurrence):
    """Starting-point matches do not prove an entire range or pericope."""
    if ref['book_key'] != occurrence['book_key'] or ref['chapter_start'] != occurrence['chapter_start']:
        return 'different'
    if ref['verse_start'] is None:
        return 'chapter_only_candidate'
    if ref['verse_start'] != occurrence['verse_start']:
        return 'different'
    ends = lambda r: (r['chapter_end'] or r['chapter_start'], r['verse_end'] or r['verse_start'])
    if occurrence['open_ended'] or ends(ref) != ends(occurrence):
        return 'starting_point_only'
    return 'same_normalized_reference'


class Sources:
    def __init__(self):
        self.files = {}
        self.blocks = {}

    def load(self, path):
        if path not in self.files:
            try:
                data = Path(path).read_bytes()
                self.files[path] = (hashlib.sha256(data).hexdigest(), data.decode('utf-8', errors='replace'))
            except OSError:
                self.files[path] = (None, None)
        return self.files[path]

    def check(self, row):
        checksum, raw = self.load(row['file_path'])
        if checksum is None:
            return 'missing_file'
        if checksum != row['file_sha256']:
            return 'changed_file'
        if row['file_path'] not in self.blocks:
            self.blocks[row['file_path']] = [normalize_visible_text(b.content_raw)
                for b in parse_ocr_xml_page(raw).blocks]
        blocks = self.blocks[row['file_path']]
        i, a, b = row['block_index'], row['source_start'], row['source_end']
        if not 0 <= i < len(blocks) or not 0 <= a < b <= len(blocks[i]):
            return 'invalid_block_span'
        return 'current_exact_span' if blocks[i][a:b] == row['citation_raw'] else 'span_text_mismatch'


OCCURRENCES = '''SELECT o.*, g.block_index,g.block_type,g.source_start,g.source_end,
    g.citation_raw,g.snippet_raw,g.context_kind,g.detector_rule,g.evidence_json,
    f.file_id,f.file_path,f.file_sha256,f.physical_file_seq,f.is_index_source,f.detector_version
    FROM citation_occurrences o JOIN citation_groups g USING(group_id)
    JOIN citation_files f USING(file_id)
    WHERE f.volume_id=? AND o.book_key=? AND o.chapter_start=?'''


def audit_stored_links(citations, sources, index_paths, out):
    """Recheck historical edges; never treat their score as a probability of truth."""
    seeds = {r['seed_key']:dict(r) for r in citations.execute('SELECT * FROM citation_index_seeds')}
    seed_states = defaultdict(set); counts = Counter(); seed_counts = Counter()
    if seeds:
        query = '''SELECT l.seed_key,l.match_kind,l.score,o.*,g.block_index,g.source_start,
            g.source_end,g.citation_raw,g.snippet_raw,f.file_path,f.file_sha256,
            f.physical_file_seq,f.volume_id,f.is_index_source
            FROM citation_seed_links l JOIN citation_occurrences o USING(occurrence_key)
            JOIN citation_groups g USING(group_id) JOIN citation_files f USING(file_id)'''
        with (out/'historical_links.jsonl').open('w') as handle:
            for row in citations.execute(query):
                o = dict(row); seed = seeds[o['seed_key']]
                state = sources.check(o)
                kind = relation(seed,o)
                payload_current = sources.load(seed['source_payload'])[0] == seed['source_fingerprint']
                status = ('payload_stale' if not payload_current else
                    'index_source_excluded' if o['is_index_source'] or o['file_path'] in index_paths else
                    'invalid_seed_or_occurrence' if seed['seed_status']!='valid' or o['normalization_status']!='normalized' else
                    'different_volume_or_reference' if seed['volume_id']!=o['volume_id'] or kind=='different' else
                    'current_ocr_candidate' if state=='current_exact_span' else 'stale_or_invalid_ocr')
                record = {'seed':seed,'stored_link_and_occurrence':o,'status':status,
                    'evidence_state':state,'reference_relation':kind,
                    'legacy_page_link_not_revalidated':True,'automatic_resolution':False}
                handle.write(encode(record)+'\n'); counts[status]+=1
                seed_states[seed['seed_key']].add(status)
    for key in seeds:
        states = seed_states[key]
        seed_counts['with_current_ocr_candidate' if 'current_ocr_candidate' in states else
                    'only_rejected_or_stale_links' if states else 'without_stored_link'] += 1
    return {'links':dict(counts),'seeds':dict(seed_counts)}


def run(index_db, citation_db, out, volumes=(), inspect_pages=()):
    out = Path(out)
    if out.exists():
        raise ValueError('Use a new output directory')
    sources = Sources()
    counts = Counter(); by_volume = defaultdict(Counter); freshness = Counter()
    with closing(readonly(index_db)) as index, closing(readonly(citation_db)) as citations:
        refs = [dict(r) for r in index.execute('''SELECT sr.*, s.volume_id,e.lemma_raw,
            e.entry_raw,e.index_entry_source_ocr_file FROM alphabetical_scripture_refs sr
            JOIN alphabetical_entries e USING(entry_key)
            JOIN alphabetical_sections s USING(section_key) ORDER BY s.volume_id,sr.scripture_ref_id''')
            if not volumes or r['volume_id'] in volumes]
        index_paths = {r[0] for r in index.execute('''SELECT DISTINCT index_entry_source_ocr_file
            FROM alphabetical_entries WHERE index_entry_source_ocr_file IS NOT NULL''')}
        material = defaultdict(list)
        for r in index.execute('SELECT * FROM alphabetical_refs WHERE scripture_ref_order IS NOT NULL'):
            material[(r['entry_key'], r['scripture_ref_order'])].append(dict(r))
        out.mkdir(parents=True)
        with sqlite3.connect(out/'comparison.sqlite') as db, (out/'comparisons.jsonl').open('w') as details:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE comparisons(scripture_ref_id INTEGER PRIMARY KEY,
                volume TEXT,status TEXT,evidence_json TEXT);
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT);''')
            query_cache = {}; page_cache = {}
            for ref in refs:
                linked = material[(ref['entry_key'],ref['ref_order'])]
                cited_pages = {r['page_ref_int'] for r in linked if r['page_ref_int'] is not None}
                known_targets = {r['resolved_target_ocr_file'] for r in linked if r['resolved_target_ocr_file']}
                key = (ref['volume_id'],ref['book_key'],ref['chapter_start'])
                eligible = bool(ref['book_key'] and ref['chapter_start'])
                if eligible and key not in query_cache:
                    query_cache[key] = [dict(r) for r in citations.execute(OCCURRENCES,key)]
                matches = []; excluded = Counter()
                for o in query_cache.get(key,[]) if eligible else []:
                    kind = relation(ref,o)
                    if kind == 'different':
                        continue
                    if o['is_index_source'] or o['file_path'] in index_paths:
                        excluded['index_source'] += 1; continue
                    if o['normalization_status'] != 'normalized':
                        excluded['not_normalized'] += 1; continue
                    state = sources.check(o); freshness[state] += 1
                    if o['file_id'] not in page_cache:
                        page_cache[o['file_id']] = [dict(r) for r in citations.execute(
                            'SELECT * FROM citation_file_pages WHERE file_id=?',(o['file_id'],))]
                    pages = page_cache[o['file_id']]
                    matches.append({**o,'reference_relation':kind,'evidence_state':state,
                        'editorial_page_hypotheses':pages,
                        'numeric_page_agreement':any(p['editorial_page'] in cited_pages for p in pages),
                        'namespace_verified':False,'matches_existing_target':o['file_path'] in known_targets})
                current = [m for m in matches if m['evidence_state']=='current_exact_span']
                status = ('normalization_required' if not eligible else
                    'current_ocr_candidates' if current else 'stale_or_invalid_evidence_only' if matches else
                    'no_candidate_in_stored_scan')
                record = {'index_reference':ref,'linked_material_references':linked,'status':status,
                    'candidates':matches,'excluded':dict(excluded),'automatic_resolution':False,
                    'index_source_current_sha256':sources.load(ref['index_entry_source_ocr_file'])[0]
                        if ref['index_entry_source_ocr_file'] else None,
                    'index_extraction_freshness':'not_certified_no_source_hash_in_selected_fields',
                    'current_candidate_files':sorted({m['file_path'] for m in current})}
                counts[status] += 1; by_volume[ref['volume_id']][status] += 1
                if any(m['reference_relation']=='same_normalized_reference' for m in current):
                    counts['references_with_current_exact_normalized_match'] += 1
                if any(m['numeric_page_agreement'] for m in current):
                    counts['references_with_current_numeric_page_agreement'] += 1
                if not linked: counts['without_explicit_material_link'] += 1
                db.execute('INSERT INTO comparisons VALUES(?,?,?,?)',
                    (ref['scripture_ref_id'],ref['volume_id'],status,encode(record)))
                details.write(encode(record)+'\n')
            legacy = Counter()
            for r in citations.execute('SELECT * FROM citation_index_seeds'):
                legacy['seeds'] += 1
                checksum,_ = sources.load(r['source_payload'])
                legacy['payload_current' if checksum==r['source_fingerprint'] else
                       'payload_missing' if checksum is None else 'payload_changed'] += 1
            legacy['stored_links_by_kind'] = {r[0]:r[1] for r in citations.execute(
                'SELECT match_kind,COUNT(*) FROM citation_seed_links GROUP BY match_kind')}
            legacy_recheck = audit_stored_links(citations,sources,index_paths,out)
            inspected = []
            for spec in inspect_pages:
                volume,physical = spec.split(':')
                rows = citations.execute('''SELECT g.*,f.file_path,f.file_sha256,f.physical_file_seq
                    FROM citation_files f JOIN citation_groups g USING(file_id)
                    WHERE f.volume_id=? AND f.physical_file_seq=? ORDER BY g.block_index,g.source_start''',
                    (volume,int(physical)))
                for row in rows:
                    r = dict(row); r['evidence_state'] = sources.check(r)
                    r['volume'] = volume; inspected.append(r)
            summary = {'version':VERSION,'current_index_references':len(refs),'volumes':len(by_volume),
                'counts':dict(counts),'candidate_checks_including_reuse':dict(freshness),
                'historical_seed_inventory':dict(legacy),
                'historical_link_recheck':legacy_recheck,
                'stored_citation_occurrences':citations.execute('SELECT COUNT(*) FROM citation_occurrences').fetchone()[0],
                'inspected_citation_groups':len(inspected),'input_db_updates':0,'inference_calls':0,
                'limitations':['Start-anchored reference matching; enclosing ranges and implicit citations may be missed.',
                    'Numeric editorial agreement does not establish edition/namespace.',
                    'No match in stored scan is not evidence of absence in the printed page.',
                    'Index-page exclusion uses stored classification plus current entry source paths; incomplete inventories remain possible.']}
            config = {'index_db':str(Path(index_db).resolve()),'citation_db':str(Path(citation_db).resolve()),
                'volumes':list(volumes),'inspect_pages':list(inspect_pages),
                'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'citation_meta':[dict(r) for r in citations.execute('SELECT * FROM citation_meta')]}
            db.executemany('INSERT INTO metadata VALUES(?,?)', [('summary',encode(summary)),('config',encode(config))])
        for name,obj in [('summary',summary),('config',config),('inspected_pages',inspected)]:
            (out/f'{name}.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
        columns = sorted({k for v in by_volume.values() for k in v})
        with (out/'by_volume.csv').open('w') as f:
            writer=csv.DictWriter(f,fieldnames=['volume',*columns]);writer.writeheader()
            for volume,c in sorted(by_volume.items()):writer.writerow({'volume':volume,**c})
        (out/'report.md').write_text('# Comparação bíblica: índice impresso e OCR\n\n'
            'Candidatos auxiliares, sem promoção de destinos. Fontes abertas somente para leitura.\n\n'
            '```json\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n```\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index-db',type=Path,default=Path('data/alphabetical_indices.db'))
    parser.add_argument('--citation-db',type=Path,default=Path('data/scripture_citations.db'))
    parser.add_argument('--volume',action='append',default=[])
    parser.add_argument('--inspect-page',action='append',default=[],metavar='VOLUME:PHYSICAL')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.index_db,args.citation_db,args.out,args.volume,args.inspect_page),ensure_ascii=False,indent=2))
