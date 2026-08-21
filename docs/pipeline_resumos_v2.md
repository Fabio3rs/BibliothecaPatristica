# Pipeline de resumos v2

O runbook do PoC, as amostras recomendadas e os gates de integração com o site
estão registrados em
[`docs/poc_resumos_v2_operacao_2026-08-24.md`](poc_resumos_v2_operacao_2026-08-24.md).

## Objetivo e regra de migração

O v2 corrige a cadeia serial sem substituir imediatamente os 288 mil resumos
legados. Cada resultado novo nasce em **shadow** (`is_current=0`). A exportação
continua usando `resumos` até que um segmento completo e seguro seja promovido.
Assim, uma execução interrompida, uma resposta inválida ou uma experiência com
outro modelo não remove conteúdo hoje publicado.

O modelo padrão é `gemma4:cloud` via Ollama. A primeira passada produz somente o
resumo canônico em português. Tradução, embedding e clustering são passos
separados, evitando pedir ao modelo tarefas heterogêneas na mesma chamada.

## Cadeia serial e tolerância

As páginas permanecem estritamente seriais dentro de cada volume. Volumes podem
rodar em paralelo (até 20 processos), mas uma página N+1 só usa um contexto N
persistido e validado.

O prompt de N+1 não replica todos os metadados históricos. Ele recebe a
`cumulative_summary` anterior, a `active_work_key`, evidências determinísticas
da página corrente e, sob ambiguidade, um lookahead compacto. Segmentos,
contribuidores, tipos de página e citações das páginas anteriores continuam no
banco, mas não são reenviados em bloco ao modelo. Isso limita ruído e evita que
o crescimento do histórico degrade a tarefa local.

Estados de geração:

- `valid`: saída estrutural e contextual válida;
- `metadata_pending`: resumo utilizável, com divergência não bloqueante de
  metadados (por exemplo, `work_key` não confirmado);
- `context_provisional`: contexto insuficiente ou risco de falso
  “administrativo”; permanece em shadow e pausa a cadeia;
- `stale`: geração promovida invalidada por reparação explícita.

Uma ambiguidade contextual recebe uma segunda tentativa com previews
determinísticos de N+1…N+X. Esses previews só resolvem a fronteira da página N e
não podem ser resumidos como conteúdo de N. Se a dúvida persistir, a geração é
gravada para auditoria, a execução recebe `status=blocked` e a página seguinte
não é processada. Erro de leitura, erro de provider e JSON irrecuperável também
bloqueiam. Ambiguidades apenas bibliográficas não bloqueiam.

`--resume` não é necessário: a retomada é automática pelo manifesto e pelo run.

## Evidências determinísticas e fac-símile

Antes da chamada são calculados:

- cabeçalho original da página;
- qualidade/sinal do OCR e estrutura XML;
- possíveis tipos editoriais (prefácio, dedicatória, índice, bibliografia,
  aparato crítico etc.);
- mistura de scripts e indício conservador de múltiplas colunas;
- candidatos de obra do `patristic_indices.db`, aberto em modo read-only;
- candidatos de citações bíblicas pelo mesmo detector conservador usado nas
  keywords, distinguindo corpo, nota e aparato.

O fac-símile é convertido para JPEG pelo transporte compartilhado e anexado
automaticamente quando o score atinge o limiar. Fronteira de obra, material
editorial denso, OCR fraco, layout complexo e muitas citações ambíguas aumentam
o score. Modos disponíveis: `auto`, `always` e `never`.

O índice fornece `work_key`, autor/título original e tradução já existente em
português. O modelo só pode copiar literalmente um `work_key` permitido; o
catálogo não autoriza invenção e conflitos devem ser registrados.

## Formato canônico

O JSON pedido ao modelo contém somente:

- `page_kinds`;
- `segments` ordenados, cada um com `kind`, `work_key` e `summary`;
- `contributors`, com papéis controlados (`editor`, `translator`, `dedicant`,
  `dedicatee`, `commentator`, `printer`);
- `cumulative_summary` da obra corrente;
- `source_conflicts`;
- `administrative_reason`.

`summary_display_pt`, `search_text_pt` e `embedding_text` são derivados
localmente. Isso reduz variação do modelo e permite mudar a indexação sem refazer
o resumo. Cabeçalhos e keywords podem ser habilitados ou desabilitados no build
do indexador; não fazem parte obrigatória do texto canônico.

O alvo de 1.800 caracteres de `cumulative_summary` é editorial, não um corte de
persistência. O parser conserva a resposta integral e só abre revisão automática
acima de 6.000 caracteres. Se o prompt total ultrapassar a janela real do
modelo, apenas a cópia do contexto anterior enviada na chamada é reduzida,
preservando seu início e seu fim; o valor completo no SQLite permanece intacto.

## Banco, timestamps e invalidação

As tabelas novas ficam no mesmo `patristica_resumos.db`:

- `resumo_runs`;
- `resumo_generations`;
- `resumo_translations`;
- `resumo_review_queue`;
- `resumo_context_anchors`;
- `resumo_cluster_runs` e `resumo_generation_clusters`.

Todas usam `created_at DEFAULT CURRENT_TIMESTAMP` e triggers SQLite específicos
para `updated_at`. A geração guarda `ocr_result_id`, hashes da fonte/saída/cadeia
e a versão do schema/prompt.

O embedding primário fica na mesma linha da geração. Uma alteração de
`embedding_text` dispara um trigger que limpa vetor, dimensão, modelo e hash.
Logo, `tools/generate_resumo_embeddings.py --kind v2` encontra naturalmente o
trabalho pendente. Clusters v2 são versionados por conjunto de embeddings e
parâmetros; as tabelas antigas não são sobrescritas.

O antigo `save_resumo` também deixou de usar `INSERT OR REPLACE`: seu upsert
preserva `id`, keywords e derivados legados.

## Reparação, promoção e alcance

Para refazer uma página e a parte dependente da cadeia:

```bash
python resumo_serial.py \
  --volume-dir teste/PG001 \
  --force-replace-from 175 \
  --force-replace-through next-work
```

`next-work` usa a próxima fronteira única e confiável dos índices e invalida até
a página imediatamente anterior. Também são aceitos `end` ou um número físico.
A geração antiga deixa de ser atual, mas o resumo legado continua publicado. A
opção `--promote` promove transacionalmente apenas segmentos completos com
status `valid|metadata_pending` e sem contaminação contextual:

```bash
python resumo_serial.py --volume-dir teste/PG001 --promote
```

Isso também funciona depois que o run já terminou, sem chamar novamente o LLM.

Runs shadow produzidos antes da remoção do corte de 1.800 caracteres podem ser
restaurados deterministicamente pelo JSON preservado em `raw_response`:

```bash
python tools/repair_resumo_cumulative_from_raw.py --run-id 1
python tools/repair_resumo_cumulative_from_raw.py --run-id 1 --apply
```

O primeiro comando é somente leitura. O segundo adquire o lock do volume, cria
um backup SQLite compacto em `data/repair_backups/`, restaura a síntese,
recalcula toda a cauda de hashes, marca traduções dependentes como `stale` e
reconcilia a fila de revisão em uma única transação. Gerações já promovidas são
recusadas.

## Execução em paralelo

```bash
./resumo_parallel.sh --pattern 'teste/PG*' --jobs 10
```

Defaults: pipeline v2, Ollama, `gemma4:cloud`, 10 volumes em paralelo,
fac-símile automático com score 2 e lookahead de 3 páginas. O lançador rejeita
mais de 20 jobs. Por padrão tudo permanece em shadow; acrescente `--promote`
somente quando desejar publicação automática de segmentos seguros.

No v2 com `gemma4:cloud`, a janela padrão é 131.072 tokens, metade da capacidade
de 256K do modelo. `--num-ctx N` continua sendo um override explícito. Outros
modelos Ollama conservam o default de 16.384 tokens.

Cada volume recebe um lock exclusivo, portanto duas invocações simultâneas
não podem avançar a mesma cadeia. Falhas de um volume não interrompem os
demais por padrão; o lote termina com status diferente de zero e conserva um
`jobs-*.tsv` quando GNU Parallel está disponível. Use `--fail-fast` quando for
preferível parar de agendar novos volumes depois da primeira falha. Uma cadeia
que continuar provisória apó o lookahead também é reportada como problema no
resumo final. `--fill-gaps` e `--page` são rejeitados no v2 porque quebrariam a
semântica da corrente; reparos usam `--force-replace-from`.

Para inspecionar execução e pendências sem escrever no banco:

```bash
python tools/report_resumos_v2.py --documento PG001
python tools/report_resumos_v2.py --json
```

## Segunda passada: internacionalização

EN, FR e IT são traduzidos juntos em uma única chamada por página. Isso mantém
terminologia, estilo e nível de detalhe consistentes entre idiomas sem misturar
a tarefa de tradução com a geração do resumo. A entrada contém somente resumo
canônico da página, síntese acumulada já consolidada, texto de busca e glossário;
nunca OCR, imagem ou contexto serial bruto.

```bash
python tools/translate_resumos_v2.py --locales en,fr,it --jobs 4
```

Por padrão só gera traduções para páginas v2 promovidas. `--run-id N` traduz um
run shadow para avaliação; `--include-shadow` amplia explicitamente o escopo.
Strings de autor/obra dos índices são cruzadas pelo `work_key`; todas as
traduções disponíveis desses termos entram juntas no glossário. A resposta só é
aceita se contiver exatamente todos os idiomas solicitados. As linhas por idioma
são então gravadas na mesma transação SQLite. A passada de produção exige o
conjunto completo `en,fr,it`; experimentos parciais devem continuar no
playground, sem disputar com os overlays publicados. O site usa o overlay do
idioma quando disponível e mantém o português como fallback.

## Embedding, HDBSCAN/KNN e publicação

Ordem recomendada após promoção:

```bash
python tools/generate_resumo_embeddings.py --kind v2 --model qwen3-embedding:8b
python tools/hdbscan_resumo_embeddings.py --kind v2 --model qwen3-embedding:8b
python tools/export_enrichment_shards.py --all --no-text
python tools/render_publication_from_shards.py \
  --index data/shards/enrichment/index.json \
  --out web/public \
  --db data/patristica_keywords.db \
  --keywords-json web/public/dict/keywords_lookup.json \
  --resumos-db data/patristica_resumos.db \
  --related-source v2
node tools/build_search_indexador_from_shards.mjs --all
```

Durante a migração, omita `--related-source v2` para conservar o KNN legado.
Use v2 somente depois de concluir embedding e clustering do conjunto que será
publicado. O índice de busca é o `PatrologiaIndexer` customizado; este fluxo não
reintroduz Pagefind.

O indexador inclui `search_text_pt` e traduções por padrão. Flags de estudo:

- `--no-v2-search`;
- `--no-translations`;
- `--include-original-header` (opt-in);
- `--no-keywords`.

Essas chaves permitem medir separadamente resumo, tradução, cabeçalho e
keywords antes de aumentar o dump definitivo.
