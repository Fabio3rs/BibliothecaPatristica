#!/usr/bin/env python3
"""Inventory data/ using filesystem metadata and literal code/doc references.

Reads data files only for the current catalog pointer and its verification report.
Never opens databases, follows directory symlinks, deletes or moves artifacts.
Writes README.md and inventory JSON/CSV under the requested output directory.
"""
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

PURPOSES = {
    'index_entry_parser': ('experimentos mecânicos', 'Extrações, regiões editoriais, mapas numéricos, auditorias de cobertura e campanhas locais; mistura resultados usados e tentativas anteriores.'),
    'index_catalog': ('catálogo experimental e antecedentes', 'Catálogo pesquisável consolidado, suplementos, localização e auditorias. O resultado selecionado é indicado por current_search.json.'),
    'index_payload_audits': ('auditorias e backups', 'Relatórios de reparo de alvos estruturais, partes de campanhas e snapshots históricos de bancos.'),
    'index_payloads': ('resultados de extração', 'JSONs de índices estruturais por volume, prescans, prompts e mensagens finais; fonte editorial usada em reparos e importação.'),
    'alphabetical_index_payloads': ('resultados de extração', 'JSONs de índices alfabéticos/onomásticos/temáticos e artefatos da extração original; não presumir que tudo foi incorporado ao catálogo.'),
    'index_intermediate_payloads': ('intermediários', 'Planos, chunks e fragmentos montados da extração estrutural; alguns reparadores ainda leem estes arquivos.'),
    'intermediate_payloads': ('intermediários', 'Etapas e fragmentos da pipeline de extração; podem conter leituras produzidas por inferência.'),
    'index_reextraction': ('experimentos multimodais', 'Pedidos, respostas, transcrições estruturadas e validações das campanhas via API. Inclui resultados ainda não promovidos.'),
    'index_retrieval': ('experimentos vetoriais', 'Amostras, candidatos, rankings e cache de embeddings; manter o cache evita repetir inferência.'),
    'playgrounds': ('experimentos de modelos', 'Bancos e artefatos de testes de resumos e extração de grafo; podem conter resultados de inferência não reproduzíveis sem custo.'),
    'shards': ('exportações para o site', 'Dados divididos em arquivos para consumo pelo site; derivados dos bancos e pipelines de exportação.'),
    'jev_validation': ('evidências de inferência', 'Logs, avaliações JEV e revisões de candidatos; parte das evidências é reutilizada por reparadores.'),
    'cache': ('cache', 'Caches de ferramentas; a possibilidade de recriação depende do produtor de cada arquivo.'),
    'backups': ('backups históricos', 'Snapshots anteriores a alterações; não são os bancos correntes.'),
    'db_backups': ('backups históricos', 'Cópias de bancos antes de reparos específicos.'),
    'repair_backups': ('backups históricos', 'Cópias preservadas por rotinas de reparo.'),
    'alphabetical_resets': ('histórico de reinicializações', 'Artefatos preservados ao reiniciar extrações alfabéticas.'),
    'ocr_duplicate_quarantine': ('quarentena de OCR', 'Material retirado do fluxo por suspeita de duplicação; não é um cache genérico.'),
    'alphabetical_prefiltered': ('pré-processamento', 'Seleção preliminar de material para extração alfabética.'),
    'audits': ('inventários e rastreabilidade', 'Relatórios de manutenção, incluindo arquivos removidos e este inventário.'),
    'rerun_manifests': ('manifestos de reexecução', 'Listas e configurações para reprocessamento dirigido.'),
    'rerun_reports': ('relatórios de reexecução', 'Resultados e diagnósticos de reprocessamentos.'),
    '.resumo_locks': ('coordenação operacional', 'Arquivos de lock das rotinas de resumos; não remover durante execução.'),
    '.structural-target-repair': ('área de trabalho', 'Estado/intermediários do reparo de alvos estruturais.'),
    'index_repairs': ('reparos dirigidos', 'Artefatos de reparos de índices.'),
    'index_scan_audits': ('auditorias', 'Relatórios de varredura de índices.'),
    'resumo_page_file_repairs': ('reparos dirigidos', 'Auditorias/reparos da associação entre resumos e arquivos de páginas.'),
    'volume_similarity_audit': ('auditorias', 'Comparação de volumes para diagnosticar correspondências e duplicações.'),
}
DB_PURPOSES = {
    'ocr_versions.db': 'Histórico de versões OCR: fonte para recuperar leituras divergentes ou omitidas.',
    'tesseract.db': 'Leituras Tesseract: fonte alternativa de OCR.',
    'patristica_resumos.db': 'Resumos produzidos pelo projeto; contém resultados de inferência.',
    'patristica_keywords.db': 'Palavras-chave e enriquecimento; contém resultados de inferência.',
    'patristic_indices.db': 'Banco de índices estruturais, obras e seções da pipeline original.',
    'alphabetical_indices.db': 'Banco da pipeline de índices alfabéticos/onomásticos/temáticos.',
    'alphabetical_analysis.db': 'Análises da pipeline alfabética.',
    'scripture_citations.db': 'Citações e referências bíblicas extraídas.',
    'editorial_page_estimator.db': 'Mapas/estimativas de paginação editorial.',
}


def classify(name, is_dir):
    if not is_dir:
        if name in DB_PURPOSES:
            return 'banco na raiz', DB_PURPOSES[name]
        if name.endswith(('-wal', '-shm', '-journal', '.lock')):
            return 'auxiliar operacional', 'Arquivo auxiliar de SQLite ou lock; pertence ao banco/processo associado.'
        if name.endswith(('.db', '.sqlite')):
            return 'banco a identificar', 'Consultar as referências no código/documentação; o nome não prova uso atual.'
        return 'arquivo na raiz', 'Consultar referências no código e documentação.'
    if name in PURPOSES:
        return PURPOSES[name]
    if name.endswith('_logs') or name == 'index_logs':
        return 'logs de execução', 'Saída e rastreamento de extrações; pode incluir evidência original de inferência.'
    if name.startswith('alphabetical_locator'):
        return 'experimentos de localização', 'Candidatos físicos, resultados e/ou cache de localizadores alfabéticos.'
    if name.startswith('alphabetical_cli_missing'):
        return 'campanha de extração', 'Extração de volumes ausentes pela pipeline CLI, com intermediários e resultados.'
    if name.startswith(('alphabetical_', 'page_group_audit', 'pg040_', 'jev_deepscan')):
        return 'campanha especializada', 'Auditoria, validação ou reparo delimitado. Ver referências e relatórios internos para o escopo específico.'
    return 'não classificado', 'Finalidade ainda não identificada; não inferir descartabilidade.'


def scan(data, excluded):
    records = defaultdict(lambda: {'allocated_bytes': 0, 'apparent_bytes': 0, 'files': 0})
    largest = []
    symlinks = []
    errors = []
    def error(exc):
        errors.append(str(exc))
    for directory, dirs, files in os.walk(data, followlinks=False, onerror=error):
        parent = Path(directory)
        relative = parent.relative_to(data)
        for depth in range(1, min(2, len(relative.parts))+1):
            records['/'.join(relative.parts[:depth])]
        for name in list(dirs):
            p = parent/name
            if p.is_symlink() or p.resolve() in excluded:
                dirs.remove(name)
                if p.is_symlink(): symlinks.append(str(p))
        for name in files:
            p = parent/name
            if p.is_symlink():
                symlinks.append(str(p)); continue
            if p.resolve() in excluded: continue
            try: s = p.stat()
            except OSError as exc:
                errors.append(str(exc)); continue
            rel = p.relative_to(data)
            for depth in range(1, min(2, len(rel.parts))+1):
                key = '/'.join(rel.parts[:depth])
                row = records[key]
                row['allocated_bytes'] += s.st_blocks * 512
                row['apparent_bytes'] += s.st_size
                row['files'] += 1
            if s.st_size >= 100_000_000:
                largest.append({'path':str(p), 'allocated_bytes':s.st_blocks*512, 'apparent_bytes':s.st_size})
    return records, sorted(largest,key=lambda r:r['allocated_bytes'],reverse=True), symlinks, errors


def references(repo, names):
    command = ['rg','--files','scripts','tools','docs','patristica_pipeline','-g','*.py','-g','*.md','-g','*.sh']
    result = subprocess.run(command,cwd=repo,capture_output=True,text=True)
    paths = [repo/p for p in result.stdout.splitlines()]
    paths += [p for p in repo.iterdir() if p.suffix in {'.py','.sh'}]
    refs = defaultdict(list)
    expression = re.compile(r'data/([^/\s\x22\x27`<>),;]+)')
    for p in sorted(set(paths)):
        try: text = p.read_text(errors='replace')
        except OSError: continue
        if p.name == 'inventory_data_storage.py': continue
        for line_no,line in enumerate(text.splitlines(),1):
            for name in set(expression.findall(line)) & names:
                refs[name].append({'file':str(p.relative_to(repo)), 'line':line_no})
    return refs


def selected_paths(repo, data):
    pointer = data/'index_catalog/current_search.json'
    if not pointer.exists(): return []
    config = json.loads(pointer.read_text())
    rows = [{'path':v,'role':k} for k,v in config.items() if isinstance(v,str) and v.startswith('data/')]
    verification = config.get('verification')
    if verification and (repo/verification).is_file():
        checked = json.loads((repo/verification).read_text()).get('checked_sources',[])
        for path in checked:
            p=Path(path)
            if p.is_absolute() and p.is_relative_to(repo): path=str(p.relative_to(repo))
            rows.append({'path':path,'role':'source_in_latest_catalog_verification'})
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('data'))
    parser.add_argument('--out',type=Path,default=Path('data/audits/storage_inventory'))
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    data=args.data.resolve();out=args.out.resolve()
    if out==data: parser.error('--out must not overwrite the data root README')
    records,largest,symlinks,errors=scan(data,{out,data/'README.md'})
    refs=references(repo,{key for key in records if '/' not in key})
    rows=[]
    for name,stat in records.items():
        category,purpose=classify(name.split('/')[0],(data/name.split('/')[0]).is_dir())
        rows.append({'path':str((data/name).relative_to(repo)) if (data/name).is_relative_to(repo) else str(data/name),
                     'depth':name.count('/')+1,'category':category,'purpose':purpose,
                     **stat,'references':refs.get(name.split('/')[0],[]) if '/' not in name else []})
    rows.sort(key=lambda r:r['allocated_bytes'],reverse=True)
    selected=selected_paths(repo,data)
    links=[{'path':p,'target':str(Path(p).resolve()),'exists':Path(p).exists()} for p in symlinks]
    payload={'generated_at':datetime.now(timezone.utc).isoformat(),'data':str(data),
             'total_allocated_bytes':sum(r['allocated_bytes'] for r in rows if r['depth']==1),
             'limitations':['Inventory uses stat only, not a content equivalence or deletion audit.',
                            'Literal code/doc mentions do not establish active runtime use; dynamic references may be absent.',
                            'Hard-linked paths are counted separately; symlinks and inventory output excluded.',
                            'Recorded catalog sources are not an exhaustive dependency graph.'],
             'selected_paths':selected,'rows':rows,'largest_files':largest[:50],
             'symlinks_skipped':symlinks,'symlink_dependencies':links,'errors':errors}
    out.mkdir(parents=True,exist_ok=True)
    (out/'inventory.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    fields=['path','depth','category','purpose','allocated_bytes','apparent_bytes','files']
    with (out/'inventory.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    lines=['# Inventário de data/', '', f"Gerado em {payload['generated_at']}. Total: **{payload['total_allocated_bytes']/2**30:.2f} GiB**.",
           '', 'Leitura de metadados; nenhum banco aberto, arquivo movido ou removido. Referências são menções literais, não prova de uso atual.',
           'Sufixos de execução não determinam qual versão é válida. Este relatório não autoriza exclusões.',
           '', '## Pastas e arquivos na raiz', '', '| Caminho | GiB | Arquivos | Categoria / conteúdo | Referências encontradas |', '|---|---:|---:|---|---|']
    for row in rows:
        if row['depth']!=1: continue
        mentions=', '.join(f"`{r['file']}:{r['line']}`" for r in row['references'][:3]) or 'Não localizadas pela busca literal'
        lines.append(f"| `{row['path']}` | {row['allocated_bytes']/2**30:.3f} | {row['files']} | {row['category']}: {row['purpose']} | {mentions} |")
    lines += ['', '## Caminhos selecionados ou usados pela última verificação do catálogo', '']
    lines += [f"- `{r['path']}` — {r['role']}" for r in selected]
    lines += ['', '## Dependências por links simbólicos', '',
              'Os caminhos abaixo compartilham dados. Apagar o destino pode quebrar uma execução posterior.', '',
              '| Link | Destino | Existe |', '|---|---|---|']
    lines += [f"| `{r['path']}` | `{r['target']}` | {r['exists']} |" for r in links]
    lines += ['', '## Maiores subdivisões (totais sobrepostos aos diretórios acima)', '', '| Caminho | GiB | Arquivos |', '|---|---:|---:|']
    lines += [f"| `{r['path']}` | {r['allocated_bytes']/2**30:.3f} | {r['files']} |" for r in rows if r['depth']==2 and r['allocated_bytes']>=100_000_000]
    lines += ['', '## Maiores arquivos', '', '| Arquivo | GiB |', '|---|---:|']
    lines += [f"| `{r['path']}` | {r['allocated_bytes']/2**30:.3f} |" for r in largest[:30]]
    lines += ['', '## Limitações', '', *['- '+s for s in payload['limitations']], '', f'Erros de leitura: {len(errors)}. Links simbólicos ignorados: {len(symlinks)}.', '']
    (out/'README.md').write_text('\n'.join(lines))
    print(json.dumps({'out':str(out),'gib':payload['total_allocated_bytes']/2**30,'root_entries':sum(r['depth']==1 for r in rows),'errors':errors},ensure_ascii=False))


if __name__=='__main__':
    main()
