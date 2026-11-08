#!/usr/bin/env python3
"""Compare read-only global surveys and report scoped experiments without adding overlaps."""
import argparse
from collections import Counter
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import sqlite3


def load(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def readonly(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    con.execute('PRAGMA query_only=ON')
    return con


def compare_surveys(before, after):
    """Compare exact identities/evidence and file edges, not just equal totals."""
    with closing(readonly(after / 'survey.sqlite')) as con:
        con.execute('ATTACH DATABASE ? AS previous',
                    ((before / 'survey.sqlite').resolve().as_uri() + '?mode=ro',))
        result = {}
        for table in ('groups', 'occurrences'):
            result[table] = {
                'added': con.execute(f'SELECT count(*) FROM {table} n WHERE NOT EXISTS '
                                    f'(SELECT 1 FROM previous.{table} p WHERE p.id=n.id)').fetchone()[0],
                'removed': con.execute(f'SELECT count(*) FROM previous.{table} p WHERE NOT EXISTS '
                                      f'(SELECT 1 FROM {table} n WHERE n.id=p.id)').fetchone()[0],
                'status_changed': con.execute(f'SELECT count(*) FROM {table} n JOIN previous.{table} p '
                                             'USING(id) WHERE n.status != p.status').fetchone()[0],
                'evidence_changed': con.execute(f'SELECT count(*) FROM {table} n JOIN previous.{table} p '
                                               'USING(id) WHERE n.evidence_json != p.evidence_json').fetchone()[0],
            }
        for name, left, right in [('added', 'main', 'previous'), ('removed', 'previous', 'main')]:
            result['candidate_edges_' + name] = con.execute(
                f'SELECT count(*) FROM (SELECT occurrence_id,path,physical FROM {left}.candidates '
                f'EXCEPT SELECT occurrence_id,path,physical FROM {right}.candidates)').fetchone()[0]
        return result


def run(before, survey, audit, campaign, out):
    out.mkdir(parents=True, exist_ok=False)
    summary = load(survey / 'summary.json')
    audit_config = load(audit / 'config.json')
    if Path(audit_config['survey']).resolve() != survey.resolve():
        raise ValueError('Audit does not belong to this survey')
    audit_summary = load(audit / 'summary.json')
    comparison = compare_surveys(before, survey)
    rows, pending, without_extraction, without_groups = [], [], [], []
    collections = {}
    for directory in sorted((survey / 'volumes').iterdir()):
        path = directory / 'summary.json'
        if not path.exists():
            rows.append({'volume': directory.name, 'status': 'error'})
            pending.append(load(directory / 'error.json'))
            continue
        s = load(path)
        c, g = s['counts'], s['group_statuses']
        row = {'volume': s['volume'], 'status': s['status'],
               **{k: c.get(k, 0) for k in ('physical_files', 'selected_source_pages', 'units',
                  'documented_profile_with_files', 'unmapped_namespace_screen_with_files')},
               'groups': sum(g.values()),
               'segmentation_or_notation_review': g.get('segmentation_or_notation_review', 0),
               'source_or_span_invalid': g.get('source_or_span_invalid', 0),
               'no_header_candidate': s['occurrence_statuses'].get('no_supported_header_coincidence', 0)}
        rows.append(row)
        collection = collections.setdefault(s['volume'][:2], Counter())
        collection.update({k: v for k, v in row.items() if isinstance(v, int)})
        collection['volumes'] += 1
        if not row['selected_source_pages']:
            without_extraction.append(s['volume'])
        if not row['groups']:
            without_groups.append(s['volume'])
        for line in (directory / 'sources.jsonl').open():
            source = json.loads(line)
            if source['state'] != 'current':
                pending.append({'volume': s['volume'], **source})
    with (out / 'by_volume.csv').open('w') as handle:
        fields = sorted(set().union(*(r.keys() for r in rows)) - {'volume', 'status'})
        writer = csv.DictWriter(handle, fieldnames=['volume', 'status', *fields])
        writer.writeheader()
        writer.writerows(rows)
    # These experiments overlap the survey. Preserve their own denominators and quality states.
    pilots = {name: load(campaign / name / 'summary.json') for name in
              ('pg033', 'pg109', 'pg148', 'gennadius', 'orosius', 'victor', 'po', 'supplement')}
    checks = {}
    with closing(readonly(survey / 'survey.sqlite')) as con:
        checks['survey_quick_check'] = con.execute('PRAGMA quick_check').fetchone()[0]
        checks['survey_fk_errors'] = len(con.execute('PRAGMA foreign_key_check').fetchall())
    with closing(readonly(audit / 'audit.sqlite')) as con:
        checks['audit_quick_check'] = con.execute('PRAGMA quick_check').fetchone()[0]
    result = {'survey': summary, 'audit': audit_summary, 'comparison': comparison,
              'collections': collections, 'scoped_experiments_not_additive': pilots,
              'volumes_without_selected_extraction': without_extraction,
              'volumes_without_reference_groups': without_groups,
              'source_pending_count': len(pending), 'integrity': checks,
              'warning': 'Candidates are not verified links; scoped experiments overlap. '
                         'No global recall denominator exists for all printed entries.'}
    write(out / 'summary.json', result)
    write(out / 'source_pending.json', pending)
    write(out / 'comparison.json', comparison)
    write(out / 'coverage_gaps.json', {'without_selected_extraction': without_extraction,
                                      'without_reference_groups': without_groups})
    inputs = [before / 'config.json', before / 'summary.json', survey / 'config.json',
              survey / 'summary.json', audit / 'config.json', audit / 'summary.json']
    inputs += [campaign / name / 'summary.json' for name in pilots]
    inputs.append(Path(__file__).resolve())
    write(out / 'manifest.json', {'version': 'global-index-progress-v1',
          'inputs': {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
          'production_updates': 0, 'inference_calls': 0})
    lines = ['# Progresso global da localização dos índices', '',
             f"Varredura: {summary['completed_volumes']}/{summary['volumes_in_corpus']} volumes/partes; "
             f"{summary['counts']['physical_files']} OCRs; {summary['worker_processes_used']} processos.", '',
             f"Seleção global: {summary['counts']['selected_source_pages']} fontes, "
             f"{summary['counts']['units']} unidades e {sum(summary['group_statuses'].values())} grupos. "
             f"{summary['volumes_with_reference_groups']} volumes têm grupos; "
             f"{len(without_extraction)} não têm extração selecionada. Ausência na seleção não prova ausência de índice.", '',
             '## Comparação com a execução anterior', '', '```json',
             json.dumps(comparison, ensure_ascii=False, indent=2), '```', '',
             '## Camadas de localização', '',
             '| Camada | Ocorrências |', '|---|---:|',
             f"| Perfis documentados, com arquivos candidatos | {summary['counts']['documented_profile_with_files']} |",
             f"| Coincidências numéricas com arquivos, namespace inicialmente não confirmado | {summary['counts']['unmapped_namespace_screen_with_files']} |",
             f"| Tokens simples sem coincidência elegível | {summary['occurrence_statuses']['no_supported_header_coincidence']} |",
             '', 'A auditoria abaixo reclassifica a peneira numérica; não deve ser somada a ela.', '',
             '| Estado na auditoria | Ocorrências |', '|---|---:|']
    lines += [f'| {k} | {v} |' for k, v in audit_summary['totals'].items()]
    lines += ['', '## Mapas especializados reconstruídos', '',
              'Populações sobrepostas ao survey, sem soma entre linhas. Limites conferidos não certificam a associação semântica.', '',
              '| Piloto | Ocorrências | Com janelas/candidatos | Observação |', '|---|---:|---:|---|']
    for name in ('pg033', 'pg109', 'gennadius', 'orosius'):
        p = pilots[name]
        n = p.get('source_occurrences', p.get('occurrences'))
        found = p['occurrence_statuses'].get('bounded_window_candidate', 0)
        note = f"{p['reviewed_window_occurrences']} com limites visuais" if 'reviewed_window_occurrences' in p else 'mapa limitado ao piloto'
        lines.append(f'| {name} | {n} | {found} | {note} |')
    p = pilots['victor']
    lines.append(f"| Victor | {p['source_occurrences']} | {p['occurrences_with_any_candidate_window']} | Numeração duplicada 55–64 permanece ambígua |")
    for p in pilots['po']['sections']:
        lines.append(f"| {p['volume']} | {p['material_occurrences']} | {p['occurrence_bounded_fascicle_candidate']} | {p['namespace_status']} |")
    lines += ['', 'PG148: teste dirigido do número142; não equivale a um mapa completo do volume.', '',
              '## Recuperação suplementar PL058', '',
              f"{pilots['supplement']['units']} unidades e {pilots['supplement']['reference_groups']} grupos em duas fontes, "
              'reconciliados separadamente. Não incluídos nos totais da seleção global acima.', '',
              '```json', json.dumps(pilots['supplement']['occurrence_statuses'], indent=2), '```', '',
              '## Volumes com mais grupos selecionados', '',
              '| Volume | Unidades | Grupos | Candidatos por perfil | Peneira numérica com candidato |', '|---|---:|---:|---:|---:|']
    for r in sorted(rows, key=lambda r: r.get('groups', 0), reverse=True)[:20]:
        lines.append(f"| {r['volume']} | {r['units']} | {r['groups']} | {r['documented_profile_with_files']} | {r['unmapped_namespace_screen_with_files']} |")
    lines += ['', '## Bloqueios e leitura dos resultados', '',
              f"- {len(pending)} fontes pendentes; lista com caminho e motivo em source_pending.json.",
              '- coverage_gaps.json distingue falta de extração selecionada de falta de grupos.',
              '- by_volume.csv cobre todos os volumes, inclusive aqueles sem resultados.',
              '- As populações são unidades experimentais de várias famílias editoriais; não medem completude do impresso.',
              '- Nenhum OCR ou banco de origem foi alterado; nenhuma inferência ou promoção automática.',
              '- Mapas especializados usam o OCR atual, mas extrações antigas com hash divergente continuam bloqueadas.', '']
    (out / 'report.md').write_text('\n'.join(lines))
    return {'out': str(out), 'comparison': comparison, 'integrity': checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'survey', 'audit', 'campaign', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
