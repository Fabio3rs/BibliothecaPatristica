# Recorte do tomo XXI incorporado em PL020

Data da preparacao: 2026-09-04.

## Fronteira confirmada

O fac-simile confirma a seguinte divisao:

- `PL020:1-611`: tomo XX;
- `PL020:612-1223`: tomo XXI incorporado ao mesmo conjunto digital.

A pagina 611 ainda e a ultima folha do `ORDO RERUM` do tomo XX. A pagina 612
abre uma nova folha de rosto com a declaracao `PATROLOGIAE TOMUS XXI`; as paginas
614 e 615 trazem, respectivamente, a folha de rosto de Rufino e o elenchus do
tomo XXI.

O intervalo a mover possui 612 imagens e 1.000 TXT. Entre 612 e 999 ha duas
versoes TXT por pagina; entre 1000 e 1223 ha apenas uma. O recorte deve selecionar
todos os arquivos pelo numero final do nome, nao apenas pelo prefixo `PL020-`.

## 1. Auditar e mover os arquivos

Dry-run, sem movimentacao:

```bash
.venv/bin/python scripts/quarantine_pl020_embedded_pl021.py \
  --quarantine-dir /homessddata/patristica/quarantine/PL020_embedded_PL021_pages_0612_1223 \
  --report /tmp/pl020_split_files_dry_run.json
```

Opcionalmente, `--hash-files` inclui SHA-256 de cada arquivo no relatorio, com o
custo de reler todas as imagens.

Aplicacao:

```bash
.venv/bin/python scripts/quarantine_pl020_embedded_pl021.py \
  --quarantine-dir /homessddata/patristica/quarantine/PL020_embedded_PL021_pages_0612_1223 \
  --report data/pl020_split_files_apply.json \
  --apply \
  --confirm PL020:612-1223
```

O script:

- recusa lacunas e contagens diferentes das confirmadas;
- nao sobrescreve um destino existente;
- deixa as pastas `teste/PL020/images` e `teste/PL020/text` no lugar;
- move somente paginas 612-1223;
- tenta desfazer os movimentos ja concluídos se ocorrer uma excecao;
- pode ser retomado se o processo for interrompido fora do tratamento normal;
- usa o lock de PL020 do pipeline de resumos.

## 2. Auditar e limpar os bancos derivados

Dry-run, sem qualquer `DELETE`:

```bash
.venv/bin/python scripts/cleanup_pl020_split_derivatives.py \
  --report /tmp/pl020_split_databases_dry_run.json
```

Aplicacao, depois da movimentacao dos arquivos:

```bash
.venv/bin/python scripts/cleanup_pl020_split_derivatives.py \
  --report data/pl020_split_databases_apply.json \
  --apply \
  --confirm PL020:612-1223
```

Por seguranca, a aplicacao recusa prosseguir enquanto ainda encontrar imagens ou
TXT 612-1223 na origem. `--skip-source-guard` existe apenas para uma excecao
consciente.

Cada banco e alterado em sua propria transacao `BEGIN IMMEDIATE`. Antes do
commit, o script verifica que o alvo ficou vazio e executa
`PRAGMA foreign_key_check`.

### Limpeza por pagina

- `patristica_resumos.db`: resumos legado e v2, embeddings, reducoes, clusters,
  review queue e context anchors a partir da pagina 612;
- `patristica_keywords.db`: ocorrencias a partir da pagina 612 e keywords que se
  tornarem orfas exclusivamente por esse recorte;
- `editorial_page_estimator.db`: entradas de cache cujos caminhos pertencem aos
  arquivos movidos;
- `scripture_citations.db`: arquivos movidos e dependencias por cascata; PL020
  fica marcado como `partial` para novo scan;
- `summary_scripture_citations_v3.db`: paginas 612-1223 e dependencias por
  cascata. O banco completo ainda deve ser reconstruido depois.

### Limpeza integral de PL020

Os seguintes bancos misturam os dois tomos em estruturas de volume e devem ter
PL020 removido integralmente para posterior reconstrucao:

- `patristic_indices.db`;
- `alphabetical_indices.db`;
- `alphabetical_analysis.db`.

### Bancos preservados

O script nao abre nem altera:

- `ocr_versions.db`;
- `ocr_eval.db`;
- `tesseract.db`;
- `ocr.db`;
- `ocr (copiar 1).db`;
- backups e bancos historicos.

O historico OCR pode conservar o material excedente. O cache Tesseract e
enderecado pelo hash da imagem, portanto continua potencialmente reutilizavel
mesmo depois da movimentacao.

## 3. Reconstrucoes posteriores

Depois da limpeza:

1. reconstruir os indices geral e alfabetico de PL020 usando somente 1-611;
2. executar novamente o scanner de citacoes de PL020;
3. reconstruir `summary_scripture_citations_v3.db`;
4. regenerar os shards `data/shards/enrichment/PL020*`;
5. regenerar os exports em `web/public` e o build do site;
6. recalcular clustering se os produtos de cluster forem publicados.

Os payloads e exports `PL020*` antigos devem ser tratados como derivados do
volume misto e nao reutilizados na reconstrucao.
