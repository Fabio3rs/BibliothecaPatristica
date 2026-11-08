"""Literal, section-scoped author remissions; no fuzzy identity decisions or OCR edits."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from tools.indexing.entry_parser_bulk import readonly
from tools.indexing.reviewed_index_targets import current_occurrence, load_entries

VERSION = 'onomastic-cross-references-v1'
SECTION = 'PL218:index-iii-alphabetic-authors'
MAX_HOPS = 8
STOP = {'s', 'ss', 'de', 'sive', 'dictus', 'et'}


def tokens(text):
    text = text.casefold().replace('æ', 'ae').replace('œ', 'oe')
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    return re.findall(r'[^\W\d_]+|\d+', text)


def parse_remission(raw):
    """Accept complete name lists, optional supra and explicit year qualification."""
    match = re.fullmatch(r'\s*vide\s+(?:(supra)\s+)?(.+?)\s*', raw, re.I | re.S)
    if not match:
        return {'reason': 'unsupported_remission_syntax', 'targets': []}
    body = match[2].strip().rstrip('.')
    body = re.sub(r'(?<=[^\W\d_])-\s*\n\s*(?=[a-zà-ÿæœ])', '', body)
    body = re.sub(r'\s+', ' ', body)
    if re.search(r'\b[IVXLCDM]+\s*[,.;]\s*\d', body):
        return {'reason': 'numeric_vide_not_name_remission', 'targets': []}
    year_match = re.search(r'(?:,\s*|\s+ad\s+)an\.\s*(\d{3,4})$', body, re.I)
    year = year_match[1] if year_match else None
    if year_match:
        body = body[:year_match.start()]
    if body.endswith('-') or re.search(r'\b(?:ad|an|anno)$', body, re.I) or '\n' in body or any(c.isdigit() for c in body):
        return {'reason': 'truncated_or_unsupported_target', 'targets': []}
    names = [part.strip() for part in body.split(',')]
    if not all(tokens(name) and re.fullmatch(r'[\w\s.()æœÆŒ-]+', name) for name in names):
        return {'reason': 'unsupported_target_list', 'targets': []}
    return {'reason': 'parsed_name_remission', 'targets': names, 'year': year, 'supra': bool(match[1])}


def name_match_kind(query, label):
    """Match literal prefixes, documented Joan. abbreviation and parenthetical alias."""
    q = tokens(query)
    expanded = re.sub(r'\bJoan(?:n)?[.]', 'Joannes', label, flags=re.I)
    variants = [[t for t in tokens(value) if t not in {'s', 'ss'}]
                for value in (label, expanded, re.sub(r'\([^()]*\)', ' ', expanded))]
    if q and any(q == value for value in variants):
        return 'normalized_full_name'
    if q and any(value[:len(q)] == q for value in variants):
        return 'normalized_name_prefix'
    return None


def name_matches(query, label):
    return name_match_kind(query, label) is not None


def segmentation_issue(entry):
    owner = entry['owner_raw']
    if re.search(r'\b[IVXLCDM]{2,}(?:[.,][IVXLCDM]+)*[.,]?\s*\n\s*[A-Z][a-z]', owner):
        return 'owner_contains_numbering_and_second_name'
    if re.search(r'\bvidesis[.]\s*\n\s*[A-Z]', owner, re.I):
        return 'owner_contains_prior_remission_and_second_name'
    return None


def build_edges(entries):
    edges, diagnostics = {}, {}
    for ident, entry in sorted(entries.items()):
        cross = entry.get('textual_cross_reference')
        if not cross:
            continue
        parsed = parse_remission(cross['raw'])
        if segmentation_issue(entry):
            parsed = {'reason': 'source_owner_segmentation_suspect', 'targets': []}
        found, rejected_targets = [], []
        for query in parsed['targets']:
            for target_id, target in sorted(entries.items()):
                if target_id == ident or target.get('section_id') != entry.get('section_id'):
                    continue
                if not name_matches(query, target['owner_raw']):
                    continue
                if segmentation_issue(target):
                    rejected_targets.append({'item_id': target_id, 'reason': segmentation_issue(target)})
                    continue
                if parsed.get('supra') and (target.get('physical', 0), target['owner_span'][0]) >= (entry.get('physical', 0), entry['owner_span'][0]):
                    continue
                if parsed.get('year') and parsed['year'] not in tokens(target.get('biographical_date_evidence', {}).get('raw', '')):
                    continue
                shared = sorted((set(tokens(entry['owner_raw'])) & set(tokens(target['owner_raw']))) - set(tokens(query)) - STOP)
                found.append({'target_item_id': target_id, 'query_name': query,
                    'target_name': target['owner_raw'], 'shared_owner_tokens': shared,
                    'match': name_match_kind(query, target['owner_raw']), 'remission': cross,
                    'target_owner': {'source': target['source'], 'span': target['owner_span'], 'raw': target['owner_raw']}})
        for edge in found:
            edge['name_alternatives'] = sum(other['query_name'] == edge['query_name'] for other in found)
        edges[ident] = found
        diagnostics[ident] = {**parsed, 'rejected_target_segmentations': rejected_targets, 'matched_entries': len(found), 'reason': parsed['reason'] if not parsed['targets'] else ('matched_names' if found else ('target_owner_segmentation_suspect' if rejected_targets else 'target_name_not_found'))}
    return edges, diagnostics


def resolve(ident, edges, bases, max_hops=MAX_HOPS):
    """Enumerate simple paths. Homonyms stay alternatives; loops never imply identity."""
    candidates, issues = {}, set()
    def walk(current, path, visited):
        if current in visited:
            issues.add('remission_cycle')
            return
        if len(path) >= max_hops:
            issues.add('remission_hop_limit')
            return
        for edge in edges.get(current, []):
            target = edge['target_item_id']
            chain = path + [edge]
            if target in visited | {current}:
                issues.add('remission_cycle')
                continue
            for base in bases.get(target, []):
                if base.get('method') == VERSION:
                    continue
                priority = .82 if edge['match'] == 'normalized_full_name' else (.74 if edge['name_alternatives'] == 1 else (.67 if edge['shared_owner_tokens'] else .45))
                score = min(float(base.get('score', 0)), priority) * (.9 ** (len(chain)-1))
                file = base['file']
                record = candidates.setdefault(file, {'file': file, 'score': 0, 'method': VERSION,
                    'navigable': True, 'certified': False, 'passage_verified': False,
                    'localization_level': 'ocr_page_file', 'evidence': [],
                    'uncertainties': ['remission_name_match_not_identity_certification', 'inherited_target_candidates_not_new_page_verification']})
                record['score'] = max(record['score'], score)
                record['evidence'].append({'kind': 'section_name_remission_chain', 'chain': chain,
                    'terminal_item_id': target, 'inherited_candidate': base})
            if target in edges:
                walk(target, chain, visited | {current})
    walk(ident, [], set())
    for candidate in candidates.values():
        if len(edges.get(ident, [])) > 1:
            candidate['uncertainties'].append('multiple_name_matches_preserved')
    return sorted(candidates.values(), key=lambda c: (-c['score'], c['file'])), sorted(issues)


def run(extraction, catalog, out, section=SECTION):
    rows = load_entries(Path(extraction), Path(catalog))
    rows = [row for row in rows if json.loads(row['metadata_json']).get('section_id') == section]
    entries, sources, rejected = {}, {}, {}
    for row in rows:
        old = json.loads(row['metadata_json'])
        path = row['source']
        if path not in sources:
            sources[path] = Path(path).read_text() if Path(path).is_file() else None
        current, state = current_occurrence(old, sources[path]) if sources[path] is not None else (None, 'source_missing')
        if current is None:
            rejected[row['id']] = state
            continue
        if current.get('textual_cross_reference'):
            cross = dict(current['textual_cross_reference'])
            shift = current['reference_span'][0] - old['reference_span'][0]
            cross['span'] = [n+shift for n in cross['span']]
            if sources[path][slice(*cross['span'])] != cross['raw']:
                rejected[row['id']] = 'remission_span_changed'
                continue
            # The original parser stops its vide field at a newline. Preserve
            # the complete literal reference tail so soft wraps remain usable.
            cross['span'][1] = current['reference_span'][1]
            cross['raw'] = sources[path][slice(*cross['span'])]
            current['textual_cross_reference'] = cross
        entries[row['id']] = current
    edges, diagnostics = build_edges(entries)
    bases, old_links = {}, set()
    with closing(readonly(catalog)) as db:
        for ident in entries:
            bases[ident] = []
            for candidate in db.execute('SELECT * FROM candidates WHERE item_id=? AND navigable=1 ORDER BY file', (ident,)):
                old_links.add((ident, candidate['file']))
                payload = json.loads(candidate['candidate_json'])
                if Path(candidate['file']).is_file():
                    bases[ident].append({**payload, 'file': candidate['file'], 'score': candidate['score']})
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    counts = Counter(section_entries=len(rows), literal_entries_checked=len(entries), source_occurrences_rejected=len(rejected), remissions=len(edges))
    results, logical = [], hashlib.sha256()
    by_id = {r['id']: r for r in rows}
    with closing(sqlite3.connect(out/'relations.sqlite')) as db:
        db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE candidates(item_id TEXT,file TEXT,rank INTEGER,score REAL,navigable INTEGER,candidate_json TEXT,PRIMARY KEY(item_id,file));
            CREATE TABLE relations(item_id TEXT PRIMARY KEY,group_id TEXT,volume TEXT,status TEXT,evidence_json TEXT);
            CREATE TABLE metadata(key TEXT PRIMARY KEY,json TEXT);''')
        for ident, diagnostic in sorted(diagnostics.items()):
            candidates, issues = resolve(ident, edges, bases)
            new = [c for c in candidates if (ident, c['file']) not in old_links]
            located = bool(candidates)
            counts['with_candidates' if located else 'without_candidates'] += 1
            counts['new_links'] += len(new)
            counts['newly_located_items'] += bool(new) and not any(k[0] == ident for k in old_links)
            counts['ambiguous_name_matches'] += diagnostic['matched_entries'] > 1
            if new and not any(k[0] == ident for k in old_links):
                category = 'full_name' if any(e['match'] == 'normalized_full_name' for e in edges[ident]) else ('unique_prefix' if diagnostic['matched_entries'] == 1 else 'ambiguous_prefix')
                counts['newly_located_' + category] += 1
            result = {'item_id': ident, 'label': by_id[ident]['label'], 'reference': by_id[ident]['ref_raw'], 'diagnostic': diagnostic,
                'issues': issues, 'matched_entries': edges[ident], 'candidates': candidates, 'new_links': len(new)}
            results.append(result)
            raw = json.dumps(result, ensure_ascii=False, sort_keys=True)
            logical.update((raw+'\n').encode())
            db.execute('INSERT INTO relations VALUES(?,?,?,?,?)', (ident, by_id[ident]['group_id'], by_id[ident]['volume'], 'candidate_cross_reference' if located else 'pending_cross_reference', raw))
            for rank, candidate in enumerate(new, 1):
                db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)', (ident, candidate['file'], rank, candidate['score'], 1, json.dumps(candidate, ensure_ascii=False, sort_keys=True)))
        reasons = Counter(r['diagnostic']['reason'] if not r['diagnostic']['matched_entries'] else 'target_entries_have_no_physical_candidates' for r in results if not r['candidates'])
        summary = dict(version=VERSION, catalog=str(Path(catalog).resolve()), extraction=str(Path(extraction).resolve()), section=section,
            counts=dict(counts), pending_reasons=dict(reasons), rejected_occurrences=rejected, max_hops=MAX_HOPS,
            logical_sha256=logical.hexdigest(), canonical_updates=0, catalog_updates=0, inference_calls=0,
            quality='Name-remission candidates, not certified identities or destinations; all homonyms retained.')
        db.execute('INSERT INTO metadata VALUES(?,?)', ('summary', json.dumps(summary, ensure_ascii=False, sort_keys=True)))
        db.commit()
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (out/'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n' for r in results))
    lines = ['# Remissões onomásticas mecânicas', '', 'Nenhuma escrita no catálogo ou nas fontes. Candidatos preservam homônimos e evidência herdada.', '', '| Verbete | Remissão | Nomes candidatos | Arquivos | Novos vínculos |', '|---|---|---:|---:|---:|']
    for r in results:
        lines.append('| '+ ' | '.join(str(v).replace('|', '\\|').replace('\n', ' ') for v in (r['label'],r['reference'],r['diagnostic']['matched_entries'],len(r['candidates']),r['new_links']))+' |')
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    return summary
