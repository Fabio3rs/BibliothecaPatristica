"""Build experimental file candidates using the conventions of reviewed sections.

Extraction and catalog inputs are readonly. This command writes only a new
overlay; the existing catalog merger can apply it separately. Numeric identity
is evidence for ranking, never a claim that a passage was semantically verified.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
import csv
import hashlib
import importlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from tools.indexing.entry_parser_bulk import readonly

VERSION = 'reviewed-index-targets-v1'
MODULES = {
    'pg114': 'tools.indexing.reviewed_pg114_targets',
    'po_pages': 'tools.indexing.reviewed_po_page_targets',
    'po_named': 'tools.indexing.reviewed_po_named_targets',
    'gudian_folio': 'tools.indexing.gudian_folio_targets',
    'arnobius_book': 'tools.indexing.arnobius_book_targets',
    'cedrenum_internal': 'tools.indexing.cedrenum_internal_targets',
    'pl003_tertullian': 'tools.indexing.pl003_tertullian_targets',
    'pl098_cenni': 'tools.indexing.pl098_priority_targets',
    'pg058_bracket': 'tools.indexing.pg058_priority_targets',
    'pl_corpus_authors': 'tools.indexing.pl_corpus_author_targets',
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def locator_coverage(records):
    """Separate one page hit from coverage of every explicit locator in a list.

    Older locator modules need not implement atom diagnostics. Their entries
    remain counted as unmeasured, never as fully covered by a single page hit.
    These counters measure candidate availability, not correctness or recall.
    """
    counts = Counter()
    for record in records:
        diagnostic = record.get('diagnostic', {})
        total = diagnostic.get('locator_count')
        located = diagnostic.get('located_locator_count')
        if (type(total) is not int or type(located) is not int
                or total <= 0 or not 0 <= located <= total):
            counts['entries_without_locator_measurement'] += 1
            continue
        counts['entries_measured'] += 1
        counts['locators'] += total
        counts['locators_with_candidates'] += located
        counts['locators_without_candidates'] += total - located
        counts['entries_all_locators_with_candidates'] += located == total
        counts['entries_some_locators_with_candidates'] += 0 < located < total
        counts['entries_no_locators_with_candidates'] += located == 0
    return dict(counts)


def route(entry):
    """Use declared sections, never the incidental numbers of an unknown index."""
    for name in ('pl003_tertullian', 'pl098_cenni', 'pg058_bracket'):
        if importlib.import_module(MODULES[name]).eligible(entry):
            return name
    section = entry.get('section_id', '')
    if section == 'PL218:index-iii-alphabetic-authors':
        return 'pl_corpus_authors'
    if section == 'PG091:ambigua-gudian-lexical':
        return 'gudian_folio'
    if section in {'PL005:arnobius-authors-book-chapter',
                   'PL005:arnobius-subjects-book-chapter',
                   'PL005:arnobius-vocabulary-book-chapter'}:
        return 'arnobius_book'
    if section == 'PG122:index-in-georgium-cedrenum':
        return 'cedrenum_internal'
    if section.startswith('PG114:allatian-'):
        return 'pg114'
    if section.startswith(('po008-apostles-', 'po018-nubian-')):
        return 'po_named'
    if section.startswith(('PO002:martyrs-', 'PO007:martyrs-', 'PO011:',
                           'PO013:', 'po008-plerophories-')):
        return 'po_pages'
    return None


def current_occurrence(entry, raw):
    """Accept unrelated OCR edits; reanchor an unchanged unique owner/ref pair.

    The original occurrence ID is retained. An ambiguous or changed literal
    belongs in a fresh extraction, not a guessed reassignment to another owner.
    """
    pairs = [('owner_raw', 'owner_span'), ('reference_raw', 'reference_span')]
    if any(not isinstance(entry.get(k), str) or not entry[k]
           or not isinstance(entry.get(s), list) or len(entry[s]) != 2
           for k, s in pairs):
        return None, 'missing_literal_evidence'

    def matches(delta):
        for key, span_key in pairs:
            a, b = entry[span_key]
            if not isinstance(a, int) or not isinstance(b, int):
                return False
            if not 0 <= a + delta < b + delta <= len(raw):
                return False
            if raw[a + delta:b + delta] != entry[key]:
                return False
        return True

    delta = 0
    if not matches(0):
        owner = entry['owner_raw']
        if raw.count(owner) != 1:
            return None, 'literal_changed_or_ambiguous_reanchor'
        delta = raw.index(owner) - entry['owner_span'][0]
        if not matches(delta):
            return None, 'reference_changed_requires_reextraction'
    result = dict(entry)
    for key in ('entry_span', 'owner_span', 'reference_span', 'source_span'):
        if isinstance(entry.get(key), list):
            result[key] = [v + delta for v in entry[key]]
    return result, 'unique_literal_reanchor' if delta else 'literal_spans_current'


def combine_candidates(candidates, root):
    """Keep every file alternative and each distinct piece of supporting evidence."""
    grouped = {}
    corpus = (root / 'teste').resolve()
    for source in candidates:
        candidate = dict(source)
        path = Path(candidate['file']).resolve()
        if not path.is_relative_to(corpus) or not path.is_file() or path.suffix != '.txt':
            raise ValueError(f'Candidate is not an existing corpus OCR file: {path}')
        candidate['file'] = str(path)
        candidate['score'] = float(candidate.get('score', 0))
        proof = candidate.get('evidence', [])
        candidate['evidence'] = proof if isinstance(proof, list) else [proof]
        candidate['uncertainties'] = sorted(set(candidate.get('uncertainties', []) + candidate.get('uncertainty', [])))
        candidate.update(navigable=True, certified=False,
                         localization_level='ocr_page_file', passage_verified=False)
        previous = grouped.get(str(path))
        if previous is None:
            grouped[str(path)] = candidate
            continue
        all_proofs = previous['evidence'] + candidate['evidence']
        warnings = sorted(set(previous.get('uncertainties', []) + candidate.get('uncertainties', [])))
        if candidate['score'] > previous['score']:
            grouped[str(path)] = candidate
        kept = grouped[str(path)]
        kept['evidence'] = [json.loads(k) for k in sorted({encoded(e) for e in all_proofs})]
        kept['uncertainties'] = warnings
    result = sorted(grouped.values(), key=lambda c: (-c['score'], c['file']))
    for rank, candidate in enumerate(result, 1):
        candidate['rank'] = rank
    return result


def build_one(job):
    name, root, entries = job
    module = importlib.import_module(MODULES[name])
    return name, module.build_map(root, entries) if name == 'pl_corpus_authors' else module.build_map(root)


def cross_reference_candidates(records, root):
    """Follow explicit Voir/Vide aliases in the same section, preserving hops.

    This is a literal editorial relationship, not fuzzy identification of people.
    A cycle without a grounded page candidate cannot create a destination.
    """
    def key(text):
        text = unicodedata.normalize('NFKD', text.casefold().replace('œ', 'oe').replace('æ', 'ae'))
        return ' '.join(re.sub(r'[^\w]+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).split())

    owners = defaultdict(list)
    for result in records:
        owners[(result['section_id'], key(result['label']))].append(result)
    aliases = [r for r in records if r['reference_namespace'] == 'cross_reference'
               and r['occurrence_status'] in {'literal_spans_current', 'unique_literal_reanchor'}]
    for _ in range(len(aliases) + 1):
        changed = False
        for result in aliases:
            if result['candidates']:
                continue
            match = re.fullmatch(r'\s*(?:voir|vide)\s*:?\s*(.+?)\s*[.]?\s*', result['reference_raw'], re.I | re.S)
            if not match:
                continue
            text = match[1]
            full = (result['section_id'], key(text))
            terms = [text] if full in owners else re.split(r'[,;]', text)
            candidates, missing, referenced = [], [], []
            for term in terms:
                targets = owners.get((result['section_id'], key(term)), [])
                grounded = [r for r in targets if r['item_id'] != result['item_id'] and r['candidates']]
                if not grounded:
                    missing.append(term.strip())
                for other in grounded:
                    referenced.append(other['item_id'])
                    for original in other['candidates']:
                        candidate = dict(original)
                        candidate['score'] = original['score'] * .9
                        candidate['evidence'] = list(original['evidence']) + [dict(
                            kind='same_section_explicit_cross_reference',
                            source=result['source'], span=result['reference_span'], raw=result['reference_raw'],
                            target_item_id=other['item_id'], target_label=other['label'],
                            target_source=other['source'], target_reference_span=other['reference_span'])]
                        candidate['uncertainties'] = sorted(set(original.get('uncertainties', []) + ['editorial_cross_reference_candidate']))
                        candidates.append(candidate)
            result['diagnostic']['cross_reference'] = dict(unmatched_targets=missing, target_item_ids=sorted(set(referenced)))
            if candidates:
                result['candidates'] = combine_candidates(candidates, root)
                result['status'] = 'with_cross_reference_candidates'
                changed = True
        if not changed:
            break
    return records


def load_entries(extraction, catalog=None):
    with closing(readonly(extraction)) as src:
        rows = [dict(r) for r in src.execute('SELECT * FROM items ORDER BY id')]
    if catalog:
        with closing(readonly(catalog)) as db:
            for row in rows:
                old = db.execute('SELECT * FROM items WHERE id=?', (row['id'],)).fetchone()
                if not old:
                    raise ValueError('Extracted item must be incorporated before locating against catalog: ' + row['id'])
                if any(old[k] != row[k] for k in ('group_id', 'volume', 'label', 'ref_raw', 'source', 'metadata_json')):
                    raise ValueError('Catalog occurrence differs from extraction: ' + row['id'])
    return rows


def run(extraction, catalog, root, out, workers=3, supplied_maps=None):
    rows = load_entries(extraction, catalog)
    entries = [(r, json.loads(r['metadata_json'])) for r in rows]
    routes = sorted({route(e) for _, e in entries} - {None})
    out.mkdir(parents=True, exist_ok=False)
    if supplied_maps is None:
        with ProcessPoolExecutor(max_workers=min(workers, max(1, len(routes)))) as pool:
            maps = dict(pool.map(build_one, [(name, root, [e for _, e in entries if route(e) == name]
                                              if name == 'pl_corpus_authors' else None) for name in routes]))
    else:
        maps = supplied_maps
    for name, mapping in maps.items():
        write_json(out / (name + '_map.json'), mapping)
    modules = {name: importlib.import_module(MODULES[name]) for name in routes}
    counts, sections, volumes, sources = Counter(), defaultdict(Counter), defaultdict(Counter), {}
    logical = hashlib.sha256()
    with closing(sqlite3.connect(out / 'relations.sqlite')) as db, (out / 'results.jsonl').open('w') as output:
        db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,
                navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE relations(item_id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        for row, original in entries:
            name = route(original)
            path = Path(row['source'])
            if str(path) not in sources:
                sources[str(path)] = path.read_text() if path.is_file() else None
            raw = sources[str(path)]
            if raw is None:
                current, occurrence_status = None, 'source_missing'
            elif name == 'pl003_tertullian' and not original.get('owner_span'):
                current, occurrence_status = modules[name].current_reference(original, raw)
            else:
                current, occurrence_status = current_occurrence(original, raw)
            candidates, diagnostic = [], {}
            if current is not None and current.get('source_role') == 'editorial_notation_notice':
                status = 'editorial_notice_not_index_reference'
                diagnostic = {'reason': status}
            elif current is not None and name:
                module = modules[name]
                candidates = combine_candidates(module.locate(current, maps[name]), root)
                if hasattr(module, 'diagnose'):
                    diagnostic = module.diagnose(current, maps[name])
                    if not isinstance(diagnostic, dict):
                        diagnostic = {'reason': diagnostic}
                status = 'with_candidates' if candidates else 'no_candidate_in_section_map'
            else:
                status = occurrence_status if current is None else 'unsupported_section'
            result = dict(item_id=row['id'], group_id=row['group_id'], volume=row['volume'],
                section_id=(current or original).get('section_id') or 'legacy_unclassified', route=name, label=row['label'],
                reference_raw=row['ref_raw'], reference_namespace=original.get('reference_namespace'),
                source_role=original.get('source_role', 'index_candidate'),
                source=row['source'], source_sha256=hashlib.sha256(raw.encode()).hexdigest() if raw is not None else None,
                occurrence_status=occurrence_status, owner_span=current.get('owner_span') if current else None,
                reference_span=current.get('reference_span') if current else None,
                status=status, diagnostic=diagnostic, candidates=candidates)
            serialized = encoded(result)
            logical.update(serialized.encode())
            output.write(serialized + '\n')
            db.execute('INSERT INTO relations VALUES(?,?,?,?,?)',
                       (row['id'], row['group_id'], row['volume'], status, serialized))
            for candidate in candidates:
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',
                    (row['id'], candidate['file'], candidate['rank'], candidate['score'], 1, encoded(candidate)))
        records = cross_reference_candidates([json.loads(r[0]) for r in db.execute(
            'SELECT evidence_json FROM relations ORDER BY item_id')], root)
        target_hashes = {file: hashlib.sha256(Path(file).read_bytes()).hexdigest()
                         for file in sorted({c['file'] for r in records for c in r['candidates']})}
        write_json(out / 'target_sources.json', target_hashes)
        logical = hashlib.sha256()
        output.seek(0)
        output.truncate()
        for result in records:
            for candidate in result['candidates']:
                candidate['target_sha256'] = target_hashes[candidate['file']]
            serialized = encoded(result)
            logical.update(serialized.encode())
            output.write(serialized + '\n')
            db.execute('UPDATE relations SET status=?,evidence_json=? WHERE item_id=?',
                       (result['status'], serialized, result['item_id']))
            for candidate in result['candidates']:
                db.execute('INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?)',
                    (result['item_id'], candidate['file'], candidate['rank'], candidate['score'], 1, encoded(candidate)))
            for counter in (counts, sections[result['section_id']], volumes[result['volume']]):
                counter['items'] += 1
                counter['with_candidates'] += bool(result['candidates'])
                counter['without_candidates'] += not result['candidates']
                counter['candidate_links'] += len(result['candidates'])
                counter['via_cross_reference'] += result['status'] == 'with_cross_reference_candidates'
        summary = dict(version=VERSION, extraction=str(extraction.resolve()),
            catalog=str(catalog.resolve()) if catalog else None, workers=workers,
            counts=dict(counts), by_volume=dict(volumes), by_section=dict(sections),
            locator_coverage=locator_coverage(records),
            locator_coverage_by_volume={v: locator_coverage([r for r in records if r['volume'] == v])
                                        for v in sorted(volumes)},
            logical_sha256=logical.hexdigest(), canonical_updates=0, inference_calls=0,
            code_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                         [Path(__file__), *[Path(m.__file__) for m in modules.values()]]},
            quality='File candidates from editorial evidence; not a semantic accuracy or complete-reference recall measurement.')
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', encoded(summary)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    write_json(out / 'summary.json', summary)
    report = ['# Arquivos candidatos das seções revisadas', '',
              'Resultados mecânicos; sem certificação de passagem ou alteração canônica.', '',
              '| Volume | Registros | Com candidato | Sem candidato | Links |',
              '| --- | ---: | ---: | ---: | ---: |']
    for volume, count in sorted(volumes.items()):
        report.append(f"| {volume} | {count['items']} | {count['with_candidates']} | {count['without_candidates']} | {count['candidate_links']} |")
    report += ['', 'Cada referência pode ter vários números e alternativas. Ter um arquivo candidato',
               'não significa que todos os números foram localizados. Evidências e incertezas por',
               'registro estão em results.jsonl; todos os arquivos existem no corpus.',
               '', f"Fingerprint lógico: `{summary['logical_sha256']}`."]
    (out / 'report.md').write_text('\n'.join(report) + '\n')
    with (out / 'results.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['item_id', 'volume', 'section_id', 'label', 'reference_raw', 'status', 'candidate_files'])
        for line in (out / 'results.jsonl').open():
            result = json.loads(line)
            writer.writerow([result[k] for k in ('item_id', 'volume', 'section_id', 'label', 'reference_raw', 'status')]
                            + [encoded([c['file'] for c in result['candidates']])])
    with (out / 'pending.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['item_id', 'volume', 'section_id', 'label', 'reference_raw',
                         'status', 'source', 'has_candidates', 'diagnostic'])
        for result in records:
            diagnostic = result.get('diagnostic', {})
            total, located = diagnostic.get('locator_count'), diagnostic.get('located_locator_count')
            partial = type(total) is int and type(located) is int and located < total
            if not result['candidates'] or partial:
                writer.writerow([result[k] for k in ('item_id', 'volume', 'section_id', 'label',
                                                     'reference_raw', 'status', 'source')]
                                + [bool(result['candidates']), encoded(diagnostic)])
    print(encoded(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extraction', type=Path, required=True)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    run(args.extraction, args.catalog, args.root.resolve(), args.out, args.workers)
