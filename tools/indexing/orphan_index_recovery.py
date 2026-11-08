"""Bounded recovery of orphan index regions, into an isolated experimental store."""
import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import uuid

from tools.corpus_utils import page_files, page_number
from tools.indexing import editorial_regions_experiment as detector
from tools.indexing.entry_parser_bulk import open_experiment, readonly
from tools.indexing.parser_region_audit import assignment

VERSION = 'orphan-index-recovery-v2'
BODY_TYPES = {'texto_principal', 'outro'}
BOUNDARIES = {'section_start', 'section_end', 'work_opening'}

# Scoped, visually audited alternating titles. These identify sections, not locators.
# Evidence: data/index_entry_parser/coverage_visual_audit_001/fragmentation.json.
AUDITED_HEADER_FAMILIES = {
    'PL218': {'INDEX PATRISTICO THEOLOGICUS': 'XXV',
              'INDICES PATROLOGIAE INDEX XXV': 'XXV'},
    'PL219': {'INDEX DISCIPLINARIS': 'CXIX',
              'INDICES PATROLOGIAE SPECIALES INDEX CXIX': 'CXIX',
              'INDEX DE CENSURIS ECCLESIASTICIS': 'CXXII',
              'INDICES PATROLOGIAE SPECIALES INDEX CXXII': 'CXXII',
              'INDICES PATROLOGIE SPECIALES INDEX CXXII': 'CXXII'},
}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def is_index(kind):
    return bool(kind and (kind.endswith('_index') or kind == 'index_unspecified'))


def title_identity(text):
    text = re.sub(r'\d+', ' ', detector.fold(text))
    words = re.findall(r'[^\W\d_]+', text)
    # Attested running-title inflections, not general stemming of personal names.
    aliases = {'PETRI': 'PETR', 'PETRUM': 'PETR',
               'CANTORIS': 'CANTOR', 'CANTOREM': 'CANTOR'}
    return ' '.join(aliases.get(w, w) for w in words)


def compatible(a, b):
    family_a, family_b = a.get('audited_header_family'), b.get('audited_header_family')
    if family_a and family_b:
        return family_a == family_b
    titles_a = {a['identity'], *(title_identity(e['quote']) for e in a.get('continuity_titles', []))}
    titles_b = {b['identity'], *(title_identity(e['quote']) for e in b.get('continuity_titles', []))}
    if (titles_a & titles_b) - {None}:
        return True
    generic = {'INDEX', 'INDICES', 'ΠΙΝΑΞ'}
    return (a['identity'] in generic or b['identity'] in generic) and (
        a['kind'] == b['kind'] or 'index_unspecified' in {a['kind'], b['kind']})


def reference_support(rows):
    """Evidence of list structure only; numbers retain their literal interpretation."""
    evidence = []
    for row in rows:
        text = detector.fold(row['quote'])
        terminal = bool(re.search(r'\d[\d\s,.;:–—()\[\]A-D-]*(?:ET SS[.]?|S[Q.]*)?$', text))
        inline = len(re.findall(r'[,;]\s*\d+(?:[ ,–-]+\d+)*[.;]', text))
        calendar = len(re.findall(r'\b(?:[1-9]|[12]\d|3[01])\s+(?:JAN|FEBR?|MART?|APR|MAII|JUN|JUL|AUG|SEPT?|OCT|NOV|DEC)[.;\s]', text + ' '))
        if terminal or inline >= 2 or calendar:
            evidence.append({'span': row['span'], 'quote': row['quote'],
                             'terminal_locator_shape': terminal, 'inline_count': inline,
                             'calendar_date_count': calendar})
    strong = bool(evidence) and (len(evidence) >= 2 or len(rows) <= 4 or
                                any(e['inline_count'] >= 3 for e in evidence))
    return {'supported': strong, 'matching_rows': len(evidence), 'body_rows': len(rows),
            'examples': evidence[:5]}


def inspect_page(raw, source, volume, physical):
    facts = detector.local_facts(raw)
    events, rows = list(facts['events']), facts['rows']
    for row in rows:
        if row['block_type'] == 'cabecalho' and re.fullmatch(
                r'SERIES (?:PRIMA|SECUNDA|TERTIA)[. ]*', detector.fold(row['quote'])):
            events.append({**row, 'kind': 'editorial_series', 'role': 'section_end',
                           'reason': 'explicit_editorial_series_transition'})
        if detector.is_typographic_title(row['quote']) and re.fullmatch(
                r'TABLE ANALYTIQUE DES MATIERES[. ]*', detector.fold(row['quote'])):
            if not any(e['span'] == row['span'] for e in events):
                events.append({**row, 'kind': 'contents_catalog', 'role': 'section_start',
                               'reason': 'explicit_analytical_contents_title'})
    events.sort(key=lambda e: e['span'])
    for event in events:
        if event['role'] != 'running_header' or not is_index(event['kind']):
            continue
        preceding = [e for e in events if e['span'][0] < event['span'][0] and is_index(e['kind'])
                     and e['role'] == 'running_header' and title_identity(e['quote']) != title_identity(event['quote'])]
        following = next((r for r in rows if r['span'][0] > event['span'][0]), None)
        if preceding and not re.search(r'\d', event['quote']) and following and (
                detector.is_typographic_title(following['quote']) and len(detector.fold(following['quote'])) > 1
                and not re.search(r'\d', following['quote'])):
            event.update(role='section_start', reason='distinct_display_title_with_subtitle_after_running_header')
    # MONITUM may introduce the index itself. Require the explicit in-hoc-indice wording;
    # other prefaces and work openings retain their boundary role.
    for event in events:
        if event['role'] != 'work_opening' or detector.fold(event['quote']).strip('.') != 'MONITUM':
            continue
        prior = [e for e in events if e['span'][0] < event['span'][0] and e['role'] in BOUNDARIES]
        after = [r for r in rows if r['span'][0] > event['span'][0]][:3]
        if prior and is_index(prior[-1]['kind']) and any(re.search(
                r'^IN HOC INDICE\b|\bHUNC GENERALEM IN OPERA .* INDICEM\b|\bINDEX ISTE\b',
                detector.fold(r['quote'])) for r in after):
            event.update(role='index_preface', reason='explicit_notice_about_this_index')
    cuts = sorted({0, len(raw), *(e['span'][0] for e in events if e['role'] in BOUNDARIES)})
    pieces = []
    for start, end in zip(cuts, cuts[1:]):
        local = [e for e in events if start <= e['span'][0] < end]
        boundary = next((e for e in local if e['span'][0] == start and e['role'] in BOUNDARIES), None)
        if boundary and not is_index(boundary['kind']):
            continue
        body = [r for r in rows if start <= r['span'][0] < end and r['block_type'] in BODY_TYPES
                and not any(e['span'] == r['span'] for e in local if e['role'] in BOUNDARIES)]
        support = reference_support(body)
        headers = [e for e in local if is_index(e['kind']) and e['role'] in {'running_header', 'section_start'}]
        # Repeated column headers are fine; conflicting named headers need review.
        identities = {title_identity(e['quote']) for e in headers}
        if len(identities) > 1:
            named = identities - {'INDEX', 'INDICES', 'ΠΙΝΑΞ'}
            if len(named) > 1:
                continue
        has_index_notice = any(e['role'] == 'index_preface' for e in local)
        if not support['supported'] and not (boundary and is_index(boundary['kind']) and has_index_notice):
            continue
        header = max(headers, key=lambda e: len(title_identity(e['quote'])), default=None)
        # A mixed page may announce the NEXT index in its running header. It cannot
        # name the preceding fragment. Keep that prefix unnamed for bounded linking.
        later_index = any(e['role'] == 'section_start' and is_index(e['kind'])
                          and e['span'][0] >= end for e in events)
        if not boundary and later_index:
            header = None
        continuity_titles = []
        if boundary and is_index(boundary['kind']):
            continuity_titles = [e for e in events if e['span'][0] < start and e['role'] == 'running_header'
                                 and is_index(e['kind'])]
        notices = []
        for row in rows:
            if not start <= row['span'][0] < end:
                continue
            text = detector.fold(row['quote'])
            if re.match(r'^(?:REVOCATUR|REVOCETUR|NUMER|PAGIN)', text):
                hint = detector.numbering_notice(row['quote'])
                if hint:
                    if 'EDITIONIS BENEDICTINAE' in text:
                        hint = 'Benedictina'
                    notices.append({**row, 'namespace_hint': hint})
            elif re.match(r'^PRIOR NOTA ARITHMETICA\b', text) and 'INDICAT TOMUM' in text and 'COLUMNAM' in text:
                notices.append({**row, 'namespace_hint': 'edition_unspecified',
                                'locator_components': ['tome', 'column', 'letter_subdivision']})
        identity = title_identity(header['quote']) if header else None
        family = AUDITED_HEADER_FAMILIES.get(volume, {}).get(identity)
        pieces.append({'piece': len(pieces), 'span': [start, end], 'rows': body,
                       'audited_header_family': f'{volume}:{family}' if family else None,
                       'header_family_basis': 'scoped_visual_audit' if family else None,
                       'identity': title_identity(header['quote']) if header else None,
                       'kind': header['kind'] if header else 'index_unspecified',
                       'continuity_titles': continuity_titles,
                       'header_evidence': headers, 'structure': support, 'declarations': notices,
                       'explicit_opening': bool(boundary and is_index(boundary['kind'])),
                       'touches_start': start == 0, 'touches_end': end == len(raw),
                       'following_boundary': next((e for e in events if e['span'][0] == end), None)})
    return {'source': str(source), 'source_sha256': detector.sha(raw.encode()), 'volume': volume,
            'physical': physical, 'pieces': pieces, 'signals': events,
            'empty_reason': facts.get('empty_reason'), 'verified': False}


def adjacent_link(left, right):
    if not left['touches_end'] or not right['touches_start'] or right['explicit_opening']:
        return False
    # One unlabelled page may be a continuation; two cannot propagate ownership.
    if not left['identity'] or not right['identity']:
        return bool(left['identity'] or right['identity'])
    return compatible(left, right)


def recover_volume(volume, seeds, entries_by_source, root, max_expansion):
    files = page_files(root / volume / 'text', suffixes=['.txt'])
    loaded, raw_texts, problems = {}, {}, []
    seed_numbers = {s['physical'] for s in seeds}

    def load(number):
        if number in loaded:
            return loaded[number]
        paths = files.get(number, [])
        if len(paths) != 1:
            loaded[number] = {'physical': number, 'pieces': [],
                              'failure': 'missing_source' if not paths else 'ambiguous_source',
                              'candidates': [str(p.resolve()) for p in paths]}
            return loaded[number]
        path = paths[0].resolve()
        try:
            raw = path.read_bytes().decode('utf-8')
            loaded[number] = inspect_page(raw, path, volume, number)
            raw_texts[number] = raw
        except (OSError, UnicodeError) as exc:
            loaded[number] = {'physical': number, 'pieces': [], 'failure': 'read_error', 'error': str(exc)}
        return loaded[number]

    eligible, invalid = [], {}
    for seed in seeds:
        page = load(seed['physical'])
        reason = page.get('failure')
        if not reason and (page['source'] != seed['source'] or page['source_sha256'] != seed['source_sha256']):
            reason = 'seed_source_changed'
        parsed = entries_by_source.get(seed['source'])
        if not reason and (not parsed or parsed['sha256'] != seed['source_sha256']):
            reason = 'parser_source_changed_or_missing'
        if not reason and set(seed['candidate_ids']) - {e['id'] for e in parsed['entries']}:
            reason = 'parser_candidates_missing'
        if reason:
            invalid[seed['source']] = reason
            problems.append({'source': seed['source'], 'physical': seed['physical'], 'reason': reason})
        else:
            eligible.append(seed)

    seed_nodes = set()
    for seed in eligible:
        entries = entries_by_source[seed['source']]['entries']
        wanted = set(seed['candidate_ids'])
        for piece in load(seed['physical'])['pieces']:
            if piece['identity'] and any(e['id'] in wanted and e['char_start'] < piece['span'][1]
                                        and e['char_end'] > piece['span'][0] for e in entries):
                seed_nodes.add((seed['physical'], piece['piece']))

    nodes, links, stops = set(seed_nodes), set(), {}
    pending = deque(sorted(seed_nodes))
    while pending:
        node = pending.popleft()
        number, ix = node
        piece = load(number)['pieces'][ix]
        for step in (-1, 1):
            key = (node, step)
            if not piece['touches_start' if step == -1 else 'touches_end']:
                stops[key] = 'internal_boundary'
                continue
            if step == -1 and piece['explicit_opening']:
                stops[key] = 'explicit_opening_candidate'
                continue
            other = number + step
            if min(abs(other - seed) for seed in seed_numbers) > max_expansion:
                stops[key] = 'expansion_cap_unknown_boundary'
                continue
            neighbor = load(other)
            candidates = neighbor['pieces']
            matches = []
            for candidate in candidates:
                a, b = (candidate, piece) if step == -1 else (piece, candidate)
                if adjacent_link(a, b):
                    if not candidate['identity']:
                        # A numeric prose page is not evidence of index continuation.
                        # Require a matching header beyond it, or an internal closing boundary.
                        mixed_end = (step == 1 and candidate['following_boundary'] is not None)
                        beyond = other + step
                        bridge = False
                        if not mixed_end and min(abs(beyond - seed) for seed in seed_numbers) <= max_expansion:
                            witness = load(beyond)
                            bridge = any(c['identity'] and compatible(piece, c) and (
                                adjacent_link(c, candidate) if step == -1 else adjacent_link(candidate, c))
                                for c in witness['pieces'])
                        if not mixed_end and not bridge:
                            continue
                    matches.append(candidate)
            if len(matches) != 1:
                stops[key] = neighbor.get('failure') or ('ambiguous_continuity' if matches else 'unsupported_or_incompatible_neighbor')
                continue
            dest = (other, matches[0]['piece'])
            links.add(tuple(sorted([node, dest])))
            if dest not in nodes:
                nodes.add(dest)
                pending.append(dest)

    # Union with a component-wide identity guard: a generic header must not join A to B.
    parent = {n: n for n in nodes}
    identities = {n: [load(n[0])['pieces'][n[1]]] if load(n[0])['pieces'][n[1]]['identity'] else [] for n in nodes}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    rejected_links = []
    for a, b in sorted(links):
        x, y = find(a), find(b)
        if x == y:
            continue
        if not all(compatible(i, j) for i in identities[x] for j in identities[y]):
            rejected_links.append({'left': a, 'right': b, 'reason': 'conflicting_component_titles'})
            continue
        parent[y] = x
        identities[x].extend(identities[y])
    groups = defaultdict(list)
    for node in sorted(nodes):
        groups[find(node)].append(node)
    regions_by_page, sections = defaultdict(list), []
    for group in sorted(groups.values()):
        if not set(group) & seed_nodes:
            continue
        first, last = group[0], group[-1]
        opening = load(first[0])['pieces'][first[1]]
        opening_status = ('internal_title_candidate' if opening['explicit_opening'] else
                          'title_with_numbering_notice_candidate' if opening['identity'] and opening['declarations'] else
                          'unknown_first_observed')
        kind = next((load(n)['pieces'][i]['kind'] for n, i in group if load(n)['pieces'][i]['kind'] != 'index_unspecified'), 'index_unspecified')
        anchors = [{'source': load(n)['source'], 'source_sha256': load(n)['source_sha256'],
                    'span': load(n)['pieces'][i]['span']} for n, i in group]
        sid = detector.sha(encoded([VERSION, anchors]).encode())[:24]
        spans, headers, declarations = [], [], []
        for number, ix in group:
            page, piece = load(number), load(number)['pieces'][ix]
            evidence_source = {k: page[k] for k in ['source', 'source_sha256', 'physical']}
            for row in piece['rows']:
                spans.append({**evidence_source, **row})
                regions_by_page[number].append({**row, 'section_id': sid, 'kind': kind})
            headers.extend({**evidence_source, **h} for h in piece['header_evidence'])
            headers.extend({**evidence_source, **h, 'use': 'continuity_alias_only'} for h in piece['continuity_titles'])
            declarations.extend({**evidence_source, **h} for h in piece['declarations'])
        sections.append({'section_id': sid, 'volume': volume, 'kind': kind,
                         'physical_start': first[0], 'physical_end': last[0], 'files': len(group),
                         'opening_status': opening_status, 'section_opening_verified': False,
                         'left_stop': stops.get((first, -1), 'conflicting_component_titles'),
                         'right_stop': stops.get((last, 1), 'conflicting_component_titles'),
                         'spans': spans, 'headers': headers, 'declarations': declarations,
                         'verified': False, 'import_ready': False})
    by_id = {s['section_id']: s for s in sections}
    associations = []
    for seed in seeds:
        parsed = entries_by_source.get(seed['source']) or {'entries': []}
        lookup = {e['id']: e for e in parsed['entries']}
        regions = sorted(regions_by_page[seed['physical']], key=lambda r: r['span'])
        for cid in seed['candidate_ids']:
            entry, region = lookup.get(cid), None
            if seed['source'] in invalid:
                status = invalid[seed['source']]
            else:
                status, region = assignment(raw_texts[seed['physical']], entry, regions)
            associations.append({'volume': volume, 'physical': seed['physical'], 'source': seed['source'],
                                 'source_sha256': seed['source_sha256'], 'candidate_id': cid,
                                 'span': [entry['char_start'], entry['char_end']] if entry else None,
                                 'snippet': entry['entry_raw'][:300] if entry else None,
                                 'status': status, 'region': region,
                                 'opening_status': by_id[region['section_id']]['opening_status'] if region else None,
                                 'verified': False, 'import_ready': False})
    return {'volume': volume, 'pages': [loaded[n] for n in sorted(loaded)], 'sections': sections,
            'associations': associations, 'issues': problems, 'rejected_links': rejected_links}


def load_inputs(args):
    seeds = [json.loads(line) for line in args.orphan_manifest.read_text().splitlines() if line.strip()]
    if args.volume:
        seeds = [s for s in seeds if s['volume'] in args.volume]
    seen = set()
    for seed in seeds:
        path = Path(seed['source'])
        expected = args.root.resolve() / seed['volume'] / 'text' / path.name
        if path.resolve() != expected.resolve() or expected.parent != args.root.resolve() / seed['volume'] / 'text':
            raise ValueError('Seed source outside configured volume/text directory')
        if not re.fullmatch(r'P[GLO][0-9]{3}(?:[._-][A-Za-z0-9]+)*', seed['volume']):
            raise ValueError('Invalid volume identifier')
        if page_number(path) != seed['physical']:
            raise ValueError('Physical identity mismatch')
        if seed['source'] in seen or len(set(seed['candidate_ids'])) != len(seed['candidate_ids']):
            raise ValueError('Duplicate seed or candidate ID')
        if len(seed['candidate_ids']) != seed['candidate_count']:
            raise ValueError('Candidate count mismatch')
        seen.add(seed['source'])
    con = readonly(args.parser_db)
    entries = {}
    try:
        con.execute('BEGIN')
        if not con.execute('SELECT 1 FROM runs WHERE run_id=?', (args.parser_run,)).fetchone():
            raise ValueError('Unknown parser run')
        for seed in seeds:
            row = con.execute('''SELECT t.result_json FROM run_pages r JOIN tasks t USING(fingerprint)
                WHERE r.run_id=? AND r.source=?''', (args.parser_run, seed['source'])).fetchone()
            if row:
                entries[seed['source']] = json.loads(row[0]).get('page')
    finally:
        con.close()
    return seeds, entries


def run(args):
    if args.workers < 1 or args.max_expansion < 0:
        raise ValueError('workers must be positive and max-expansion nonnegative')
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError('Use a new output directory')
    seeds, entries = load_inputs(args)
    for protected in [args.parser_db, args.source_db]:
        if args.experiment_db.resolve() == protected.resolve() or (
            args.experiment_db.exists() and protected.exists() and args.experiment_db.samefile(protected)):
            raise ValueError('Recovery database must differ from input databases')
    con = open_experiment(args.experiment_db, args.source_db)
    run_id = uuid.uuid4().hex
    config = {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}
    config.update(version=VERSION, manifest_sha256=detector.sha(args.orphan_manifest.read_bytes()),
                  code_sha256=detector.sha(Path(__file__).read_bytes()),
                  detector_local_fingerprint=detector.local_fingerprint())
    con.executescript('''
        CREATE TABLE IF NOT EXISTS orphan_recovery_runs(run_id TEXT PRIMARY KEY,created_at TEXT,
            config_json TEXT NOT NULL,status TEXT NOT NULL,summary_json TEXT);
        CREATE TABLE IF NOT EXISTS orphan_recovery_volumes(run_id TEXT,volume TEXT,result_json TEXT NOT NULL,
            PRIMARY KEY(run_id,volume));
    ''')
    con.execute('INSERT INTO orphan_recovery_runs VALUES(?,?,?,?,NULL)',
                (run_id, datetime.now(timezone.utc).isoformat(), encoded(config), 'running'))
    con.commit()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'manifest.json').write_text(encoded({'run_id': run_id, **config}) + '\n')
    groups = defaultdict(list)
    for seed in seeds:
        groups[seed['volume']].append(seed)
    counts, per_volume, opening_counts = Counter(), {}, Counter()
    sample, rng = [], random.Random(args.seed)
    streams = {k: (args.out / (k + '.jsonl')).open('w') for k in ['pages', 'sections', 'associations', 'issues', 'rejected_links']}

    def work(volume):
        return recover_volume(volume, groups[volume], entries, args.root, args.max_expansion)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for result in pool.map(work, sorted(groups)):
                volume = result['volume']
                stats = Counter(a['status'] for a in result['associations'])
                stats.update({'seed_pages': len(groups[volume]), 'examined_files': sum('source' in p for p in result['pages']),
                              'sections': len(result['sections']), 'issues': len(result['issues'])})
                counts.update(stats)
                per_volume[volume] = dict(stats)
                opening_counts.update(s['opening_status'] for s in result['sections'])
                for key, stream in streams.items():
                    for row in result[key]:
                        stream.write(encoded(row) + '\n')
                    stream.flush()
                if result['associations']:
                    sample.append(rng.choice(result['associations']))
                with con:
                    con.execute('INSERT INTO orphan_recovery_volumes VALUES(?,?,?)', (run_id, volume, encoded(result)))
                if args.verbose:
                    print(encoded({'volume': volume, **stats}), flush=True)
        summary = {'run_id': run_id, 'version': VERSION, 'status': 'completed', 'volumes': len(groups),
                   'candidates': sum(s['candidate_count'] for s in seeds), 'counts': dict(counts),
                   'opening_statuses': dict(opening_counts), 'by_volume': per_volume,
                   'production_db_updates': 0, 'inference_calls': 0, 'quality_verified': False,
                   'import_ready': False, 'scope': 'Source region recovery only; no destination resolution.',
                   'limitations': ['Headers and numeric structure are heuristic, not verified ownership.',
                                   'Unknown boundaries and headerless bridges or mixed-page endings require review.',
                                   'Existing candidates are reassociated, never re-extracted or silently corrected.']}
        (args.out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        (args.out / 'review_sample.json').write_text(json.dumps(sample, ensure_ascii=False, indent=2) + '\n')
        report = ['# Recuperação experimental de regiões de índices', '',
                  'Associações geométricas propostas; não verificadas nem prontas para importação.',
                  'Não resolve páginas de destino. OCR e bancos de origem permanecem somente leitura.', '',
                  f"{len(seeds)} páginas de entrada, {len(groups)} volumes, {summary['candidates']} candidatos existentes.", '',
                  '| Volume | Páginas de entrada | Arquivos lidos | Regiões | Associados | Restantes |',
                  '|---|---:|---:|---:|---:|---:|']
        for volume, stat in sorted(per_volume.items()):
            assigned = stat.get('inside_index_candidate', 0)
            total = sum(s['candidate_count'] for s in groups[volume])
            report.append(f"| {volume} | {stat['seed_pages']} | {stat['examined_files']} | {stat['sections']} | {assigned} | {total-assigned} |")
        report.extend(['', '## Aberturas candidatas', '', *[f'- {k}: {v}' for k, v in sorted(opening_counts.items())], '',
                       'A amostra por volume tem seleção reproduzível; não é uma avaliação visual ou cega de qualidade.',
                       'Spans e citações estão em sections.jsonl; limites não comprovados permanecem explícitos.'])
        (args.out / 'report.md').write_text('\n'.join(report) + '\n')
        with con:
            con.execute('UPDATE orphan_recovery_runs SET status=?,summary_json=? WHERE run_id=?',
                        ('completed', encoded(summary), run_id))
        return summary
    except BaseException:
        with con:
            con.execute('UPDATE orphan_recovery_runs SET status=? WHERE run_id=?', ('failed', run_id))
        raise
    finally:
        for stream in streams.values():
            stream.close()
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--orphan-manifest', type=Path, required=True)
    parser.add_argument('--parser-run', required=True)
    parser.add_argument('--parser-db', type=Path, default=Path('data/index_entry_parser/experiments.sqlite'))
    parser.add_argument('--source-db', type=Path, default=Path('data/alphabetical_indices.db'))
    parser.add_argument('--experiment-db', type=Path, default=Path('data/index_entry_parser/orphan_recovery.sqlite'))
    parser.add_argument('--root', type=Path, default=Path('teste'))
    parser.add_argument('--volume', action='append')
    parser.add_argument('--workers', type=int, default=20)
    parser.add_argument('--max-expansion', type=int, default=64, help='Maximum distance from nearest seed; a search budget, not a section limit')
    parser.add_argument('--seed', type=int, default=73)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--out', type=Path, required=True)
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
