"""Consolidate recovery candidates with explicit page overrides, without source mutation."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from tools.indexing.entry_parser_experiment import ROOT, digest, source_span
from tools.indexing.structured_index_experiment import (
    SELECTION_VERSION, check_span, consolidate, encoded, key, read_selection, readonly, resolve,
)
from tools.indexing.index_extraction_recovery import LOGICAL_SCHEMA, assemble
from tools.indexing.index_conservation_audit import coverage
from tools.indexing.residual_recovery_campaign import residual_pieces

VERSION = 'recovered-index-consolidation-v1'


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def adapt_entry(entry, raw, source, physical):
    """Adapt the old literal contract; an unproven owner stays unassigned."""
    item = deepcopy(entry)
    a, b = check_span(raw, item, item['entry_raw'])
    item['original_extraction'] = deepcopy(entry)
    item.update(source=source, physical_page=physical)
    item.setdefault('section_candidate', item.get('section_id'))
    item.setdefault('flags', item.get('parser_flags', []))
    if 'reference_groups' not in item:
        item['reference_groups'] = []
        span, literal = item.get('reference_span'), item.get('references_raw')
        if span and literal:
            x, y = check_span(raw, span, literal, (a, b))
            owner = item.get('text_raw', '')
            if owner and raw[a:a+len(owner)] == owner and a+len(owner) <= x:
                item['reference_groups'] = [{**span, 'raw': literal,
                    'owner_text_raw': owner, 'owner_span': source_span(raw, a, a+len(owner)),
                    'namespace_status': 'literal_uninterpreted', 'inheritance_status': 'not_inferred'}]
            else:
                item['pending_reference_groups'] = [{**span, 'raw': literal,
                    'reason': 'legacy_owner_span_not_proven'}]
        elif span or literal:
            raise ValueError('Incomplete legacy reference evidence')
    return item


def merge_page(raw, source, physical, baseline, additions):
    entries = [adapt_entry(e, raw, source, physical) for e in (baseline or {}).get('entries', [])]
    original = len(entries)
    seen = {(e['char_start'], e['char_end']): e for e in entries}
    for addition in additions:
        if addition['source_sha256'] != digest(raw.encode()):
            raise ValueError('Stale recovery source')
        item = adapt_entry(addition, raw, source, physical)
        span = item['char_start'], item['char_end']
        # Even exact overlaps need an explicit version selection: never vote by count.
        if span in seen or any(span[0] < y and x < span[1] for x, y in seen):
            raise ValueError('Recovery overlaps an existing occurrence')
        seen[span] = item
        entries.append(item)
    entries.sort(key=lambda e: (e['char_start'], e['char_end']))
    for left, right in zip(entries, entries[1:]):
        if left['char_end'] > right['char_start']:
            raise ValueError('Baseline contains overlapping occurrences')
    # Keep old residual evidence, subtracting new claims, rather than treating all XML as text.
    residuals = []
    for residual in (baseline or {}).get('residuals', []):
        a, b = check_span(raw, residual, residual['raw'])
        pieces = [(a, b)]
        for e in entries:
            x, y = e['char_start'], e['char_end']
            pieces = [(l, r) for start, end in pieces
                      for l, r in ([(start, end)] if y <= start or x >= end else
                                   [(start, max(start, x)), (min(end, y), end)]) if l < r]
        for l, r in pieces:
            if raw[l:r].strip():
                residuals.append({**residual, **source_span(raw, l, r), 'raw': raw[l:r]})
    page = {**(baseline or {}), 'source': source, 'physical_page': physical,
            'sha256': digest(raw.encode()), 'entries': entries, 'residuals': residuals,
            'events': (baseline or {}).get('events', [])}
    # A missing baseline does not provide complete residual accounting. Campaign accounting
    # remains separately persisted, never inferred from the absence of residual records here.
    page['residual_accounting'] = 'baseline_residuals_minus_claims; see campaign accounting'
    return page, {'baseline_units': original, 'added_units': len(additions)}


def run(campaign, out, overrides=None, assembly=None, root=ROOT):
    root, campaign, out = Path(root).resolve(), Path(campaign).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Use a new output directory')
    config = json.loads((campaign/'manifest.json').read_text())
    audit = resolve(config['audit'], root)
    audit_manifest = json.loads((audit/'manifest.json').read_text())
    if config['parser_run'] != audit_manifest['parser_run']:
        raise ValueError('Campaign and audit refer to different parser runs')
    comparisons = rows(campaign/'page_comparison.jsonl')
    additions = defaultdict(list)
    for row in rows(campaign/'proposals.jsonl'):
        additions[str(resolve(row['source'], root))].append(row)
    override_manifest, override_records = read_selection(Path(overrides), root) if overrides else ({}, [])
    selected = {str(r['path']): r for r in override_records}
    if len({str(resolve(m['source'], root)) for m in comparisons}) != len(comparisons):
        raise ValueError('Duplicate campaign page')
    input_paths = [campaign/'manifest.json', campaign/'proposals.jsonl', campaign/'page_comparison.jsonl',
                   audit/'manifest.json'] + ([Path(overrides)] if overrides else []) + ([Path(assembly)] if assembly else [])
    provenance = {str(p.resolve()): digest(p.read_bytes()) for p in input_paths}
    outputs, decisions, exclusions = [], [], []
    with closing(readonly(resolve(audit_manifest['parser_db'], root))) as con:
        con.execute('BEGIN')
        for meta in comparisons:
            source = str(resolve(meta['source'], root))
            if source in selected:
                decisions.append({'source': source, 'decision': 'explicit_page_override',
                    'superseded_baseline_units': meta.get('old_entries'),
                    'superseded_recovery_units': len(additions.pop(source, [])),
                    'artifact': selected[source]['origin']})
                continue
            extra = additions.pop(source, [])
            if meta['status'] != 'evaluated':
                exclusions.append({'source': source, 'reason': meta['status'], 'proposals': len(extra)})
                continue
            raw = Path(source).read_text()
            if digest(raw.encode()) != meta['source_sha256']:
                raise ValueError(f'Stale source: {source}')
            found = con.execute("""SELECT t.result_json FROM tasks t JOIN run_pages p USING(fingerprint)
                WHERE p.run_id=? AND p.source=?""", (config['parser_run'], meta['source'])).fetchall()
            if len(found) > 1:
                raise ValueError('Ambiguous baseline page')
            baseline = json.loads(found[0][0]).get('page') if found else None
            old_entries = baseline['entries'] if baseline else []
            if digest(encoded(old_entries).encode()) != meta['old_entries_sha256']:
                raise ValueError('Baseline entries differ from campaign evidence')
            if baseline and baseline['sha256'] != meta['source_sha256']:
                raise ValueError('Baseline source hash mismatch')
            page, counts = merge_page(raw, source, meta['physical'], baseline, extra)
            scopes = [{'span': s['span']} for s in meta.get('section_stats', [])]
            if scopes:
                accounting = coverage(raw, scopes, page['entries'])
                if accounting['characters'] != meta['after'] or accounting['invalid_candidate_ids']:
                    raise ValueError('Consolidation changed regional character accounting')
                page['residuals'] = [{'raw': p['text'], **source_span(raw, *p['span']),
                    'reason': 'regional_residual_after_recovery'}
                    for p in residual_pieces(raw, accounting['runs'])]
                page['residual_accounting'] = 'recomputed_with_campaign_region_scopes'
            page['campaign_accounting'] = meta
            page['source_quality'] = meta.get('source_quality', {})
            for entry in page['entries']:
                entry.setdefault('quality', {})['registered_source_issues'] = page['source_quality']
                containing = [s for s in scopes if s['span'][0] <= entry['char_start']
                              < entry['char_end'] <= s['span'][1]]
                intersects = any(entry['char_start'] < s['span'][1]
                                 and s['span'][0] < entry['char_end'] for s in scopes)
                entry['quality']['regional_ownership'] = (
                    'candidate_scope' if len(containing) == 1 else
                    'overlapping_candidate_scopes' if containing else
                    'crosses_candidate_boundary' if intersects else
                    'outside_audited_regions' if scopes else 'not_assessed')
            outputs.append((page, {'campaign': str(campaign), 'baseline_run': config['parser_run']}))
            decisions.append({'source': source, 'decision': 'merged_nonoverlapping_occurrences', **counts})
    if additions:
        raise ValueError('Proposals have no campaign page')
    considered = {d['source'] for d in decisions}
    for record in override_records:
        if str(record['path']) not in considered:
            decisions.append({'source': str(record['path']), 'decision': 'explicit_page_outside_comparable_campaign',
                              'artifact': record['origin']})
        outputs.append((record['page'], record['origin']))
    out.mkdir(parents=True); (out/'pages').mkdir()
    sources = []
    for page, origin in outputs:
        artifact = out/'pages'/f"{key(page['source'])[:24]}.json"
        artifact.write_text(encoded(page)+'\n')
        sources.append({'source': page['source'], 'sha256': page['sha256'],
            'physical': page['physical_page'], 'artifact': str(artifact),
            'units': len(page['entries']), 'upstream': origin})
    manifest = {'version': SELECTION_VERSION, 'sources': sources,
                'continuation_artifacts': override_manifest.get('continuation_artifacts', [])}
    selection = out/'selection.json'; selection.write_text(encoded(manifest)+'\n')
    counts = consolidate(selection, out/'structured', root, 20, 73)
    _, records = read_selection(selection, root)
    logical = []
    with closing(sqlite3.connect(out/'structured/index.sqlite')) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.executescript(LOGICAL_SCHEMA)
        con.execute("""CREATE TABLE pending_reference_groups(
            pending_id TEXT PRIMARY KEY, unit_id TEXT NOT NULL REFERENCES units,
            evidence_json TEXT NOT NULL)""")
        with con:
            for record in records:
                sid = key('source', str(record['path']), record['page']['sha256'])
                for e in record['page']['entries']:
                    uid = key('unit', sid, e['char_start'], e['char_end'])
                    for group in e.get('pending_reference_groups', []):
                        con.execute('INSERT INTO pending_reference_groups VALUES(?,?,?)',
                                    (key(uid, group), uid, encoded(group)))
            if assembly:
                review = json.loads(Path(assembly).read_text())
                if review.get('version') != 'logical-continuity-review-v1':
                    raise ValueError('Unknown continuity review format')
                logical, _ = assemble(con, records, review['reviews'], root)
            counts['logical_entries'] = len(logical)
            counts['effective_reference_groups'] = con.execute('SELECT count(*) FROM effective_reference_groups').fetchone()[0]
            counts['pending_reference_groups'] = con.execute('SELECT count(*) FROM pending_reference_groups').fetchone()[0]
            if con.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Invalid relations')
            con.execute('INSERT INTO metadata VALUES(?,?)', ('recovery_consolidation', encoded(provenance)))
    counts.update(version=VERSION, decisions=dict(Counter(d['decision'] for d in decisions)),
                  excluded_pages=exclusions, all_pages_visually_verified=False)
    counts['regional_ownership'] = dict(Counter(e.get('quality', {}).get('regional_ownership',
        'explicit_page_version') for page, _ in outputs for e in page['entries']))
    counts['retained_baseline_units'] = sum(d.get('baseline_units', 0) for d in decisions)
    counts['retained_campaign_units'] = sum(d.get('added_units', 0) for d in decisions)
    counts['override_units'] = sum(len(r['page']['entries']) for r in override_records)
    counts['superseded_campaign_units'] = sum(d.get('superseded_recovery_units', 0) for d in decisions)
    (out/'summary.json').write_text(json.dumps(counts, ensure_ascii=False, indent=2)+'\n')
    (out/'decisions.jsonl').write_text(''.join(encoded(d)+'\n' for d in decisions))
    (out/'logical_entries.jsonl').write_text(''.join(encoded(d)+'\n' for d in logical))
    (out/'manifest.json').write_text(encoded({'version': VERSION, 'inputs': provenance,
        'code_sha256': digest(Path(__file__).read_bytes())})+'\n')
    (out/'report.md').write_text('# Consolidação das recuperações\n\n'
        f"{counts['sources']} páginas; {counts['units']} unidades; {counts['reference_groups']} grupos literais. "
        f"{len(logical)} entradas lógicas preservadas.\n\n"
        'Uma versão explícita por página revisada; demais páginas combinam ocorrências sem sobreposição. '
        'Grupos sem proprietário comprovado ficam em pending_reference_groups. '
        'Consultar decisions.jsonl para substituições, summary.json para exclusões. '
        'As contagens não medem qualidade editorial nem completude do impresso. '
        'Os resíduos regionais são recalculados e conferidos contra a campanha; '
        'páginas explicitamente revisadas preservam os resíduos de sua versão selecionada. '
        'A contabilização regional permanece em campaign_accounting dos artefatos de página. '
        'Nenhum OCR ou banco original foi modificado.\n')
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--override-selection', type=Path)
    parser.add_argument('--assembly-reviews', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.campaign, args.out, args.override_selection, args.assembly_reviews),
                     ensure_ascii=False, indent=2))
