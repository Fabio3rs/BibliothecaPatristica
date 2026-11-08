"""Readonly-source consolidation and searchable relational staging; no promotion."""
import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import random
import sqlite3

from tools.indexing.entry_parser_experiment import ROOT, digest, display

VERSION = 'structured-index-experiment-v1'
SELECTION_VERSION = 'structured-index-selection-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def key(*parts):
    return digest(encoded(parts).encode())


def resolve(path, root):
    path = Path(path)
    return (path if path.is_absolute() else root / path).resolve()


def readonly(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


def check_span(raw, item, literal, bounds=None):
    a, b = item['char_start'], item['char_end']
    if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(raw):
        raise ValueError('Invalid source span')
    if bounds and not bounds[0] <= a < b <= bounds[1]:
        raise ValueError('Span outside owner')
    if raw[a:b] != literal:
        raise ValueError('Literal does not match source span')
    if item.get('text_sha256') and digest(literal.encode()) != item['text_sha256']:
        raise ValueError('Stale span hash')
    return a, b


def read_selection(manifest_path, root):
    """One explicit whole-page version per source; never union overlapping runs."""
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('version') != SELECTION_VERSION or not manifest.get('sources'):
        raise ValueError('Expected a nonempty structured-index-selection-v1 manifest')
    pages, seen = [], set()
    for selection in manifest['sources']:
        path = resolve(selection['source'], root)
        if path in seen:
            raise ValueError(f'Duplicate selected source: {path}')
        seen.add(path)
        if bool(selection.get('artifact')) == bool(selection.get('db')):
            raise ValueError('Select exactly one artifact or database run for each source')
        if selection.get('artifact'):
            artifact = resolve(selection['artifact'], root)
            data = artifact.read_bytes()
            page = json.loads(data)
            origin = {**selection, 'artifact_sha256': digest(data)}
        else:
            db = resolve(selection['db'], root)
            with closing(readonly(db)) as con:
                run = con.execute('SELECT status FROM runs WHERE run_id=?',
                                  (selection['run_id'],)).fetchone()
                if not run or run['status'] != 'completed':
                    raise ValueError('Selected database run is absent or incomplete')
                rows = con.execute('''SELECT t.result_json FROM run_pages p
                    JOIN tasks t USING(fingerprint) WHERE p.run_id=? AND p.source=?''',
                    (selection['run_id'], selection['source'])).fetchall()
            if len(rows) != 1:
                raise ValueError('Selected page is not unique in the source run')
            data = rows[0]['result_json']
            page = json.loads(data).get('page')
            origin = {**selection, 'result_sha256': digest(data.encode())}
        if not page or resolve(page['source'], root) != path:
            raise ValueError('Selected source identity mismatch')
        raw_bytes = path.read_bytes()
        if digest(raw_bytes) != selection['sha256'] or page['sha256'] != selection['sha256']:
            raise ValueError(f'Stale OCR source: {path}')
        if page['physical_page'] != selection['physical']:
            raise ValueError('Physical identity mismatch')
        if 'units' in selection and len(page['entries']) != selection['units']:
            raise ValueError('Selected unit count changed')
        if path.parent.name != 'text':
            raise ValueError('Expected a corpus text directory')
        pages.append({'page': page, 'origin': origin, 'path': path, 'root': root,
                      'volume': path.parent.parent.name, 'raw': raw_bytes.decode('utf-8')})
    return manifest, pages


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE sources(
 source_id TEXT PRIMARY KEY, volume TEXT NOT NULL, physical INTEGER NOT NULL,
 path TEXT UNIQUE NOT NULL, sha256 TEXT NOT NULL, origin_json TEXT NOT NULL,
 page_json TEXT NOT NULL);
CREATE TABLE sections(
 section_id TEXT PRIMARY KEY, volume TEXT NOT NULL, original_key TEXT NOT NULL,
 title TEXT, status TEXT NOT NULL);
CREATE TABLE units(
 unit_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources,
 section_id TEXT REFERENCES sections, original_id TEXT NOT NULL,
 ordinal INTEGER NOT NULL, char_start INTEGER NOT NULL, char_end INTEGER NOT NULL,
 unit_kind TEXT NOT NULL, entry_raw TEXT NOT NULL, display_text TEXT NOT NULL,
 search_text TEXT NOT NULL, context_original_id TEXT, parent_candidate_id TEXT REFERENCES units,
 context_status TEXT NOT NULL, flags_json TEXT NOT NULL, quality_json TEXT NOT NULL,
 publication_status TEXT NOT NULL, original_json TEXT NOT NULL,
 UNIQUE(source_id,char_start,char_end));
CREATE TABLE unit_fragments(
 unit_id TEXT NOT NULL REFERENCES units, ordinal INTEGER NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources, char_start INTEGER NOT NULL,
 char_end INTEGER NOT NULL, text_sha256 TEXT NOT NULL, PRIMARY KEY(unit_id,ordinal));
CREATE TABLE clauses(
 clause_id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units, ordinal INTEGER NOT NULL,
 owner_raw TEXT NOT NULL, display_text TEXT NOT NULL,
 char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, attribution_status TEXT NOT NULL);
CREATE TABLE reference_groups(
 reference_id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units,
 clause_id TEXT NOT NULL REFERENCES clauses, ordinal INTEGER NOT NULL,
 literal TEXT NOT NULL, char_start INTEGER NOT NULL, char_end INTEGER NOT NULL,
 namespace_status TEXT NOT NULL, inheritance_status TEXT NOT NULL, original_json TEXT NOT NULL);
CREATE TABLE residuals(
 residual_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources,
 char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, literal TEXT NOT NULL,
 reason TEXT NOT NULL, original_json TEXT NOT NULL);
CREATE TABLE unit_unassigned_text(
 fragment_id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units,
 char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, literal TEXT NOT NULL);
CREATE TABLE continuations(
 continuation_id TEXT PRIMARY KEY, status TEXT NOT NULL, applied INTEGER NOT NULL CHECK(applied=0),
 evidence_json TEXT NOT NULL);
CREATE TABLE continuation_fragments(
 continuation_id TEXT NOT NULL REFERENCES continuations, ordinal INTEGER NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources, char_start INTEGER NOT NULL,
 char_end INTEGER NOT NULL, literal TEXT NOT NULL, PRIMARY KEY(continuation_id,ordinal));
CREATE VIRTUAL TABLE unit_search USING fts5(unit_id UNINDEXED, search_text,
 tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE clause_search USING fts5(clause_id UNINDEXED, search_text,
 tokenize='unicode61 remove_diacritics 2');
CREATE INDEX units_source ON units(source_id,ordinal);
CREATE INDEX clauses_unit ON clauses(unit_id,ordinal);
CREATE INDEX references_unit ON reference_groups(unit_id,ordinal);
CREATE INDEX references_clause ON reference_groups(clause_id,ordinal);
CREATE INDEX unassigned_unit ON unit_unassigned_text(unit_id,char_start);
CREATE VIEW index_rows AS SELECT u.unit_id, u.ordinal, s.volume, s.physical, s.path AS source_path,
 u.section_id, u.entry_raw, u.display_text, u.search_text, u.context_status,
 u.publication_status, u.quality_json,
 (SELECT count(*) FROM clauses c WHERE c.unit_id=u.unit_id) AS clauses,
 (SELECT count(*) FROM reference_groups r WHERE r.unit_id=u.unit_id) AS reference_groups
 FROM units u JOIN sources s USING(source_id);
CREATE VIEW clause_reference_rows AS SELECT s.volume,s.physical,u.unit_id,c.clause_id,
 c.ordinal AS clause_order,c.owner_raw,c.display_text,r.reference_id,r.ordinal AS reference_order,
 r.literal AS reference_literal,r.namespace_status,r.inheritance_status,s.path AS source_path
 FROM clauses c JOIN units u USING(unit_id) JOIN sources s USING(source_id)
 JOIN reference_groups r USING(clause_id);
'''


def insert_page(con, record):
    page, raw, path = record['page'], record['raw'], record['path']
    sid = key('source', str(path), page['sha256'])
    con.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?)',
                (sid, record['volume'], page['physical_page'], str(path), page['sha256'],
                 encoded(record['origin']), encoded(page)))
    seen_ids, consumed, group_spans = set(), [], set()
    for order, entry in enumerate(page['entries']):
        a, b = check_span(raw, entry, entry['entry_raw'])
        if resolve(entry['source'], record['root']) != path or entry['physical_page'] != page['physical_page']:
            raise ValueError('Entry source identity mismatch')
        if entry['id'] in seen_ids:
            raise ValueError('Duplicate original entry ID in source')
        seen_ids.add(entry['id'])
        consumed.append((a, b))
        uid = key('unit', sid, a, b)
        section = entry.get('section_candidate')
        section_id = key('section', record['volume'], section) if section else None
        if section:
            con.execute('INSERT OR IGNORE INTO sections VALUES(?,?,?,?,?)',
                        (section_id, record['volume'], section, None, 'candidate_unverified'))
        context = entry.get('context_candidate_id')
        full_display = display(entry['entry_raw'])
        con.execute('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (uid, sid, section_id, entry['id'], order, a, b,
                     entry.get('unit_kind', 'unspecified'), entry['entry_raw'], full_display,
                     full_display, context, None, 'not_assessed', encoded(entry.get('flags', [])),
                     encoded(entry.get('quality', {})), 'review_required', encoded(entry)))
        con.execute('INSERT INTO unit_fragments VALUES(?,?,?,?,?,?)',
                    (uid, 0, sid, a, b, digest(entry['entry_raw'].encode())))
        con.execute('INSERT INTO unit_search VALUES(?,?)', (uid, full_display))
        clauses, previous_group_end, assigned = {}, a, []
        if 'reference_groups' not in entry:
            raise ValueError('Unsupported entry contract: expected explicit reference_groups')
        for n, group in enumerate(entry['reference_groups']):
            x, y = check_span(raw, group, group['raw'], (a, b))
            oa, ob = check_span(raw, group['owner_span'], group['owner_text_raw'], (a, b))
            if (x, y) in group_spans or x < previous_group_end or ob > x:
                raise ValueError('Duplicate, unordered or overlapping reference attribution')
            group_spans.add((x, y))
            previous_group_end = y
            assigned.extend([(oa, ob), (x, y)])
            owner_key = (oa, ob)
            if owner_key not in clauses:
                cid = key('clause', uid, oa, ob)
                clauses[owner_key] = cid
                con.execute('INSERT INTO clauses VALUES(?,?,?,?,?,?,?,?)',
                            (cid, uid, len(clauses)-1, group['owner_text_raw'],
                             display(group['owner_text_raw']), oa, ob,
                             entry.get('quality', {}).get('reference_attribution', 'not_assessed')))
                con.execute('INSERT INTO clause_search VALUES(?,?)',
                            (cid, display(group['owner_text_raw'])))
            con.execute('INSERT INTO reference_groups VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (key('reference', sid, x, y), uid, clauses[owner_key], n, group['raw'], x, y,
                         group.get('namespace_status', 'not_assessed'),
                         group.get('inheritance_status', 'not_assessed'), encoded(group)))
        # Full group text stays searchable even when its internal extraction is partial.
        cursor = a
        for x, y in sorted(assigned) + [(b, b)]:
            if x > cursor and any(c.isalnum() for c in raw[cursor:x]):
                gap_a = cursor + len(raw[cursor:x]) - len(raw[cursor:x].lstrip())
                gap_b = cursor + len(raw[cursor:x].rstrip())
                con.execute('INSERT INTO unit_unassigned_text VALUES(?,?,?,?,?)',
                            (key('unassigned', uid, gap_a, gap_b), uid,
                             gap_a, gap_b, raw[gap_a:gap_b]))
            cursor = max(cursor, y)
    for left, right in zip(sorted(consumed), sorted(consumed)[1:]):
        if left[1] > right[0]:
            raise ValueError('Overlapping unit source spans')
    for item in page['residuals']:
        a, b = check_span(raw, item, item['raw'])
        if any(a < y and x < b for x, y in consumed):
            raise ValueError('Residual overlaps structured material')
        con.execute('INSERT INTO residuals VALUES(?,?,?,?,?,?,?)',
                    (key('residual', sid, a, b), sid, a, b, item['raw'],
                     item['reason'], encoded(item)))


def link_context(con):
    """Preserve explicit candidate relations only; do not infer a lemma from order."""
    rows = con.execute('SELECT u.*,s.volume FROM units u JOIN sources s USING(source_id)').fetchall()
    lookup = defaultdict(list)
    for row in rows:
        lookup[(row['volume'], row['original_id'])].append(row['unit_id'])
    for row in rows:
        parent = row['context_original_id']
        if parent is None:
            continue
        matches = lookup[(row['volume'], parent)]
        if len(matches) == 1 and matches[0] != row['unit_id']:
            status, target = 'candidate_relation_unverified', matches[0]
        else:
            status, target = 'unresolved_candidate_relation', None
        con.execute('UPDATE units SET parent_candidate_id=?,context_status=? WHERE unit_id=?',
                    (target, status, row['unit_id']))


def insert_continuations(con, pages, manifest, root):
    records = []
    by_path = {record['path']: record for record in pages}
    for record in pages:
        for link in record['page'].get('fragment_links', []):
            records.append((link, {'kind': 'page_fragment_links', 'source': str(record['path'])}))
    for filename in manifest.get('continuation_artifacts', []):
        path = resolve(filename, root)
        data = path.read_bytes()
        for link in json.loads(data):
            records.append((link, {'artifact': str(path), 'sha256': digest(data)}))
    evidence, identities = defaultdict(list), {}
    for link, origin in records:
        if link.get('applied'):
            raise ValueError('Continuation assembly is outside this consolidator')
        fragments = link.get('fragments')
        if fragments is None:
            fragments = [{**link[side], 'source': link['source'],
                          'source_sha256': link['source_sha256']} for side in ('left', 'right')]
        parts = []
        for part in fragments:
            path = resolve(part['source'], root)
            record = by_path.get(path)
            if not record or part['source_sha256'] != record['page']['sha256']:
                raise ValueError('Continuation uses an unselected or stale source')
            a, b = check_span(record['raw'], part, part['raw'])
            parts.append((key('source', str(path), part['source_sha256']), a, b, part['raw']))
        cid = key('continuation', parts)
        identities[cid] = parts
        evidence[cid].append({'origin': origin, 'proposal': link})
    for cid, parts in identities.items():
        con.execute('INSERT INTO continuations VALUES(?,?,?,?)',
                    (cid, 'relation_only_not_assembled', 0, encoded(evidence[cid])))
        for order, (sid, a, b, literal) in enumerate(parts):
            con.execute('INSERT INTO continuation_fragments VALUES(?,?,?,?,?,?)',
                        (cid, order, sid, a, b, literal))


def document(con, uid, root):
    row = con.execute('''SELECT u.*,s.volume,s.physical,s.path,s.sha256 FROM units u
        JOIN sources s USING(source_id) WHERE unit_id=?''', (uid,)).fetchone()
    path = Path(row['path'])
    source = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    clauses = []
    for c in con.execute('SELECT * FROM clauses WHERE unit_id=? ORDER BY ordinal', (uid,)):
        refs = [dict(r) for r in con.execute('''SELECT reference_id,literal,char_start,char_end,
            namespace_status,inheritance_status FROM reference_groups WHERE clause_id=?
            ORDER BY ordinal''', (c['clause_id'],))]
        clauses.append({'clause_id': c['clause_id'], 'text_raw': c['owner_raw'],
                        'display_text': c['display_text'], 'char_start': c['char_start'],
                        'char_end': c['char_end'], 'references': refs})
    return {'unit_id': uid, 'volume': row['volume'], 'physical': row['physical'],
            'section_candidate_id': row['section_id'], 'display_text': row['display_text'],
            'entry_raw': row['entry_raw'], 'search_text': row['search_text'], 'clauses': clauses,
            'source': {'ocr_path': source, 'sha256': row['sha256'],
                       'char_start': row['char_start'], 'char_end': row['char_end']},
            'context_status': row['context_status'], 'parent_candidate_id': row['parent_candidate_id'],
            'unassigned_text': [dict(r) for r in con.execute('''SELECT char_start,char_end,literal
                FROM unit_unassigned_text WHERE unit_id=? ORDER BY char_start''', (uid,))],
            'flags': json.loads(row['flags_json']), 'quality': json.loads(row['quality_json']),
            'publication_status': row['publication_status'],
            'destination_links': [], 'destination_export_status': 'not_imported'}


def export(con, out, root, sample_size, seed):
    strata = defaultdict(list)
    with (out / 'search_documents.jsonl').open('w') as stream:
        for row in con.execute('SELECT * FROM index_rows ORDER BY volume,physical,ordinal'):
            doc = document(con, row['unit_id'], root)
            stream.write(encoded(doc) + '\n')
            fragment = any('continuation' in flag for flag in doc['flags'])
            strata[(row['volume'], row['clauses'] > 1, fragment)].append(row['unit_id'])
    rng = random.Random(seed)
    for members in strata.values():
        rng.shuffle(members)
    chosen = []
    while len(chosen) < sample_size and any(strata.values()):
        for group in sorted(strata):
            if strata[group] and len(chosen) < sample_size:
                chosen.append(strata[group].pop())
    docs = [document(con, uid, root) for uid in chosen]
    (out / 'sample.json').write_text(encoded({'seed': seed, 'documents': docs}) + '\n')
    lines = ['# Amostra de consulta experimental', '',
             'Seleção reprodutível por volume, multiplicidade de cláusulas e alerta de continuidade.',
             'Transcrição candidata; nenhuma aprovação editorial ou de destino é inferida.', '']
    for doc in docs:
        lines.extend([f"## {doc['volume']} / arquivo físico {doc['physical']}", '',
                      f"Unidade: `{doc['unit_id']}`. Contexto: `{doc['context_status']}`.", '',
                      'Texto integral de apresentação e busca:', '',
                      '> ' + doc['display_text'].replace('\n', '\n> '), '',
                      f"Origem: `{doc['source']['ocr_path']}`; caracteres "
                      f"{doc['source']['char_start']}–{doc['source']['char_end']}.", '',
                      '| Cláusula candidata | Referência literal |', '|---|---|'])
        for c in doc['clauses']:
            text = c['display_text'].replace('|', '\\|')
            refs = ' / '.join(r['literal'] for r in c['references']).replace('|', '\\|').replace('\n', ' ')
            lines.append(f'| {text} | {refs} |')
        if doc['unassigned_text']:
            lines.extend(['', 'Texto preservado no grupo, ainda sem cláusula/referência estruturada:', ''])
            lines.extend('> ' + display(item['literal']) for item in doc['unassigned_text'])
        lines.extend(['', 'Alertas preservados: ' + ', '.join(doc['flags']), ''])
    (out / 'sample.md').write_text('\n'.join(lines))


def consolidate(manifest_path, out, root=ROOT, sample_size=12, seed=42):
    root, out, manifest_path = Path(root).resolve(), Path(out).resolve(), Path(manifest_path).resolve()
    if sample_size < 0:
        raise ValueError('sample_size must be nonnegative')
    manifest, pages = read_selection(manifest_path, root)
    # A fresh directory prevents overwriting any historical or production database.
    out.mkdir(parents=True, exist_ok=False)
    db = out / 'index.sqlite'
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript(SCHEMA)
        with con:
            config = {'version': VERSION, 'selection_sha256': digest(manifest_path.read_bytes()),
                      'code_sha256': digest(Path(__file__).read_bytes()), 'seed': seed,
                      'sample_size': sample_size, 'root': str(root)}
            con.execute('INSERT INTO metadata VALUES(?,?)', ('configuration', encoded(config)))
            con.execute('INSERT INTO metadata VALUES(?,?)', ('selection', encoded(manifest)))
            for page in pages:
                insert_page(con, page)
            link_context(con)
            insert_continuations(con, pages, manifest, root)
            if con.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Foreign key validation failed')
            counts = {table: con.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                      for table in ('sources', 'sections', 'units', 'clauses', 'reference_groups',
                                    'unit_fragments', 'residuals', 'unit_unassigned_text',
                                    'continuations', 'continuation_fragments')}
            counts['units_with_unassigned_text'] = con.execute(
                'SELECT count(DISTINCT unit_id) FROM unit_unassigned_text').fetchone()[0]
            counts['by_volume'] = [dict(row) for row in con.execute('''SELECT volume,
                count(*) AS units,sum(clauses) AS clauses,sum(reference_groups) AS reference_groups
                FROM index_rows GROUP BY volume''')]
            counts.update(version=VERSION, publication_ready_count=None, import_ready=False,
                          api_calls=0, production_db_updates=0,
                          status='consolidated_experiment', sample_size=min(sample_size, counts['units']))
            con.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(counts)))
        export(con, out, root, sample_size, seed)
        (out / 'selection.json').write_text(encoded(manifest) + '\n')
        (out / 'summary.json').write_text(json.dumps(counts, ensure_ascii=False, indent=2) + '\n')
        (out / 'report.md').write_text(
            '# Consolidação experimental\n\n'
            f"{counts['sources']} fontes, {counts['units']} unidades, {counts['clauses']} cláusulas, "
            f"{counts['reference_groups']} grupos de referências e {counts['residuals']} resíduos.\n\n"
            'Textos e spans conferidos contra as fontes selecionadas; isso não certifica leitura visual, '
            'segmentação ou contexto. Quantidade apta à publicação: não medida.\n\n'
            'Consultar `index_rows`, `clause_reference_rows`, `unit_search` e `clause_search`. '
            'Busca e apresentação incluem o grupo integral. As cláusulas preservam suas próprias '
            'referências. Nenhum título, pai, número herdado ou destino foi inventado.\n\n'
            'Os JSON originais e as pendências permanecem no banco; continuações são relações '
            'não montadas. `sample.md` e `sample.json` mostram uma amostra; '
            '`search_documents.jsonl` contém todas as unidades. '
            '`unit_unassigned_text` expõe trechos alfanuméricos internos ainda fora das cláusulas '
            'e referências; estes continuam pesquisáveis no texto integral. Nenhuma integração ao site.\n')
        return counts
    except BaseException:
        # The transaction rolls back data. A failed output directory is never reusable silently.
        (out / 'FAILED.txt').write_text('Consolidation or export failed; do not consume this directory.\n')
        with con:
            con.execute("DELETE FROM metadata WHERE key='summary'")
        raise
    finally:
        con.close()


def search(db, query, volume=None, limit=20, scope='clauses'):
    if limit < 1 or scope not in {'clauses', 'units'}:
        raise ValueError('Invalid search limit or scope')
    with closing(readonly(db)) as con:
        config = con.execute("SELECT value_json FROM metadata WHERE key='configuration'").fetchone()
        if not config or json.loads(config[0])['version'] != VERSION:
            raise ValueError('Not a structured index experiment')
        if not con.execute("SELECT 1 FROM metadata WHERE key='summary'").fetchone():
            raise ValueError('Incomplete consolidation')
        table = 'clause_search' if scope == 'clauses' else 'unit_search'
        joins = ('JOIN clauses c ON c.clause_id=clause_search.clause_id '
                 'JOIN units u USING(unit_id)' if scope == 'clauses'
                 else 'JOIN units u ON u.unit_id=unit_search.unit_id')
        fields = ('c.clause_id,c.display_text AS matched_text' if scope == 'clauses'
                  else 'NULL AS clause_id,u.display_text AS matched_text')
        rows = con.execute(f'''SELECT u.unit_id,{fields},s.volume,s.physical,s.path AS source_path,
            u.display_text AS group_text,u.context_status,u.publication_status,
            bm25({table}) AS score FROM {table} {joins} JOIN sources s USING(source_id)
            WHERE {table} MATCH ? AND (? IS NULL OR s.volume=?) ORDER BY score,u.unit_id LIMIT ?''',
            (query, volume, volume, limit)).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            if item['clause_id']:
                refs = con.execute('SELECT literal FROM reference_groups WHERE clause_id=? ORDER BY ordinal',
                                   (item['clause_id'],))
            else:
                refs = con.execute('SELECT literal FROM reference_groups WHERE unit_id=? ORDER BY ordinal',
                                   (item['unit_id'],))
            item['reference_groups'] = [r[0] for r in refs]
            item['score_kind'] = 'fts5_bm25_not_confidence'
            results.append(item)
        return results


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--manifest', type=Path, required=True)
    cli.add_argument('--out', type=Path, required=True)
    cli.add_argument('--root', type=Path, default=ROOT)
    cli.add_argument('--sample-size', type=int, default=12)
    cli.add_argument('--seed', type=int, default=42)
    args = cli.parse_args()
    print(json.dumps(consolidate(args.manifest, args.out, args.root, args.sample_size, args.seed),
                     ensure_ascii=False, indent=2))


def search_main():
    cli = argparse.ArgumentParser(description='Readonly FTS5 search over experimental index text')
    cli.add_argument('--db', type=Path, required=True)
    cli.add_argument('--query', required=True, help='SQLite FTS5 MATCH expression')
    cli.add_argument('--volume')
    cli.add_argument('--limit', type=int, default=20)
    cli.add_argument('--scope', choices=['clauses', 'units'], default='clauses')
    args = cli.parse_args()
    print(json.dumps(search(args.db, args.query, args.volume, args.limit, args.scope),
                     ensure_ascii=False, indent=2))
