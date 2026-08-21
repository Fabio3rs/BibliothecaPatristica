# PoC e operação dos resumos v2

Data do levantamento: 2026-08-24.

## Objetivo

Este documento reúne as observações operacionais obtidas ao comparar o pipeline
legado de resumos com o pipeline v2 e ao seguir seus dados até o projeto do
site. Ele complementa `docs/pipeline_resumos_v2.md`, que continua sendo a
especificação principal.

O objetivo do PoC é validar, antes de uma publicação sobre o corpus completo:

- qualidade e continuidade da cadeia serial;
- reconhecimento de material editorial e transições de obra;
- uso condicionado do fac-símile;
- tradução conjunta para inglês, francês e italiano;
- custo dos embeddings, HDBSCAN/KNN, shards e índice invertido;
- efeito dos novos campos na busca e no viewer.

## Estado observado antes do PoC

Na inspeção somente leitura de 2026-08-24, o banco de produção continha 288.575
linhas na tabela legada `resumos` e ainda não possuía as tabelas do schema v2.
Os shards de enriquecimento e os metadados publicados também continuavam no
formato legado.

Em particular, `PG001:175` ainda estava publicada como `Conteúdo
administrativo`, associada no legado a São Clemente I e à Segunda Epístola aos
Coríntios.

Portanto, implementar os scripts v2 não altera sozinho o site. O conteúdo novo
só se torna publicável depois de:

1. gerar a versão shadow;
2. auditar e promover a geração;
3. traduzir e regenerar os derivados desejados;
4. exportar os shards;
5. renderizar `web/public`;
6. reconstruir o PatrologiaIndexer customizado.

## Amostras recomendadas

### Smoke test do prompt

O benchmark de guardrails utilizou cinco páginas, cada uma exercitando uma
situação editorial diferente:

- `PG001:175`: início das *Duas Epístolas às Virgens*;
- `PG020:27`: início do Livro I da *História Eclesiástica*;
- `PG043:166`: página de título, dedicatória e início de prefácio;
- `PG079:685`: início com candidatos de índice ambíguos;
- `PL046:475`: fim de sermão, epístola e início de prefácio na mesma página.

Essas páginas devem ser usadas como smoke test em `--dry-run` antes de processar
volumes inteiros.

### PoC de volumes completos

O trio recomendado é:

- `PG001`;
- `PL001`;
- `PO002`.

Ele representa PG, PL e PO, já foi usado nos estudos de busca e
internacionalização e coincide com a amostra de desenvolvimento do indexador do
site.

Começar por `PG001` é especialmente útil porque o volume contém a regressão
conhecida da página física 175 e possui fac-símile pareado.

## Evidência específica de PG001:175

A análise determinística atual encontra para a página:

- arquivo OCR `7fa490d2-837f-49cc-b89b-e5df30fac772-175.txt`;
- 2.292 caracteres de OCR limpo;
- cabeçalho `EPISTOLÆ AD VIRGINES. — PROOEMIA.`;
- início exato de `PG001:work:epistolae_duae_ad_virgines`;
- confiança de catálogo `0.70`;
- fac-símile `teste/PG001/images/PG001-175.png`;
- score de fac-símile `2`, suficiente para o limiar automático padrão.

O prompt v2 determina que introduções, proêmios, prefácios, dedicatórias,
páginas de título, bibliografias, índices e aparatos críticos são conteúdo
descritível. Se o modelo ainda devolver `administrative` nessa página, o
validador deve marcar risco de falso administrativo, repetir a chamada com
lookahead e pausar a cadeia se o problema persistir.

## Runbook seguro

### 1. Smoke test sem escrita

```bash
python resumo_serial.py \
  --pipeline v2 \
  --volume-dir teste/PG001 \
  --page 175 \
  --dry-run \
  --provider ollama \
  --model gemma4:cloud \
  --facsimile-mode auto \
  --verbose
```

Repetir o teste, conforme necessário, para as outras quatro páginas da amostra.

### 2. Backup consistente do SQLite

Antes da primeira escrita no banco de produção:

```bash
sqlite3 data/patristica_resumos.db \
  ".backup '/tmp/patristica_resumos_before_v2.db'"
```

Evitar executar simultaneamente outros lotes com escrita intensa no mesmo
SQLite. WAL e `busy_timeout` tornam a concorrência possível, mas não eliminam
contenção nem simplificam a auditoria.

### 3. Gerar um volume em shadow

```bash
python resumo_serial.py \
  --pipeline v2 \
  --volume-dir teste/PG001 \
  --provider ollama \
  --model gemma4:cloud \
  --facsimile-mode auto \
  --facsimile-threshold 2 \
  --lookahead-pages 3
```

Não usar `--promote` na primeira execução. O v2 retoma automaticamente pelo
manifesto e pelo run; em caso de interrupção, repetir o mesmo comando. A flag
`--resume` é mantida por compatibilidade, mas não é necessária no v2.

### 4. Auditar a geração

```bash
python tools/report_resumos_v2.py --documento PG001
python tools/report_resumos_v2.py --documento PG001 --json
```

Para listar as revisões:

```bash
sqlite3 'file:data/patristica_resumos.db?mode=ro' \
  "SELECT documento,pagina_num,issue_kind,severity,status
     FROM resumo_review_queue
    WHERE documento='PG001'
    ORDER BY pagina_num,issue_kind;"
```

Antes da promoção, verificar:

- run concluído;
- nenhuma geração `context_provisional`;
- ausência de bloqueio contextual não resolvido;
- sequência completa para o intervalo que será promovido;
- páginas editoriais relevantes descritas, não reduzidas a administrativas.

### 5. Executar o trio do PoC

O lançador atual recebe um glob, não uma lista de volumes. Para selecionar
somente os três volumes, usar três invocações, que podem ficar em terminais
separados:

```bash
./resumo_parallel.sh --pattern 'teste/PG001' --jobs 1
./resumo_parallel.sh --pattern 'teste/PL001' --jobs 1
./resumo_parallel.sh --pattern 'teste/PO002' --jobs 1
```

### 6. Traduzir um run shadow para avaliação

Obter o `run_id` no relatório e executar:

```bash
python tools/translate_resumos_v2.py \
  --run-id RUN_ID \
  --locales en,fr,it \
  --provider ollama \
  --model gemma4:cloud \
  --jobs 4
```

Os três idiomas são produzidos na mesma chamada por página. Essa tradução de um
run shadow permite avaliar consistência e custo antes da publicação.

### 7. Promover o volume aprovado

Depois que o run completo estiver auditado, repetir a identificação de
provider/modelo e acrescentar `--promote`:

```bash
python resumo_serial.py \
  --pipeline v2 \
  --volume-dir teste/PG001 \
  --provider ollama \
  --model gemma4:cloud \
  --promote
```

Se o run já estiver concluído, esse comando promove os segmentos seguros sem
refazer chamadas. A promoção altera a seleção híbrida no SQLite, mas ainda não
altera os arquivos publicados.

### 8. Gerar traduções e embeddings promovidos

Traduções:

```bash
python tools/translate_resumos_v2.py \
  --locales en,fr,it \
  --jobs 4
```

Embeddings:

```bash
python tools/generate_resumo_embeddings.py \
  --kind v2 \
  --model qwen3-embedding:8b \
  --batch-size 32
```

Depois de promover ao menos o trio do PoC, gerar o espaço reduzido e os grupos:

```bash
python tools/hdbscan_resumo_embeddings.py \
  --kind v2 \
  --model qwen3-embedding:8b
```

Um único volume serve para smoke técnico do embedding, mas não representa bem o
KNN cross-volume.

### 9. Exportar e reconstruir o site

Somente depois dos gates de publicação descritos abaixo:

```bash
python tools/export_enrichment_shards.py --all --no-text

python tools/render_publication_from_shards.py \
  --index data/shards/enrichment/index.json \
  --out web/public \
  --db data/patristica_keywords.db \
  --keywords-json web/public/dict/keywords_lookup.json \
  --resumos-db data/patristica_resumos.db \
  --related-source v2 \
  --related-topk 7

cd web
npm run build:search-indexador
npm run check
npm run build
```

`npm run build` não reconstrói automaticamente o índice de busca. O comando
`build:search-indexador` é uma etapa separada.

## Interação com o site

O caminho de dados é:

```text
OCR + índices + análise determinística + fac-símile
    -> resumo_generations shadow
    -> promoção
    -> tradução / embedding / HDBSCAN
    -> export_enrichment_shards.py
    -> render_publication_from_shards.py
    -> meta/*.json.gz e volumes.json
    -> PatrologiaIndexer customizado
    -> /search, viewer e agent-chat
```

A exportação é híbrida: uma página sem geração v2 promovida continua usando o
resumo legado. O viewer e a página de busca usam o overlay do locale quando ele
existe e retornam ao português quando não existe.

O KNN permanece legado por padrão. `--related-source v2` só deve ser usado após
gerar embeddings e um cluster run v2 representativo do conjunto promovido.

## Gates antes de publicar o PoC

Os itens seguintes foram encontrados na análise estática e devem ser resolvidos
ou explicitamente aceitos antes de substituir os arquivos públicos.

### 1. Política de páginas administrativas

`tools/search_record_policy.mjs` exclui somente o resumo literalmente igual a
`Conteúdo administrativo`. O v2 descreve páginas vazias com outra frase e
publica `page_kinds=["administrative"]`. Sem ajuste, páginas administrativas v2
entrarão no índice.

O filtro deve considerar os metadados estruturados do v2, preservando páginas
editoriais substantivas que apenas mencionem conteúdo administrativo.

### 2. Tamanho dos metadados públicos

O renderer inclui atualmente, quando presentes:

- `search_text_pt`;
- `embedding_text`;
- cabeçalho original;
- `page_kinds`;
- segmentos que repetem partes do resumo;
- resumo, síntese acumulada e texto de busca em três idiomas.

`embedding_text` não é consumido pelo frontend nem pelo builder do índice e não
deve permanecer nos shards públicos. Segmentos e textos exclusivos de build
também devem ser avaliados separadamente dos metadados mínimos de runtime.

Como um bloco de metadados contém 50 páginas, o custo excedente afeta também as
páginas vizinhas quando o viewer ou a busca carrega um único resultado.

### 3. Autor e obra ainda vêm do legado

A geração v2 seleciona `work_key`, mas a exportação continua preenchendo
`author` e `work` com `author_detected` e `work_detected` de `resumos`.
Correções estruturais podem, portanto, chegar ao texto do resumo sem corrigir os
metadados exibidos ou o título do resultado.

Autor e obra publicados devem ser derivados do `work_key` confirmado e do banco
de índices, conservando fallback legado quando nenhum vínculo foi validado.

### 4. Alterações de OCR e evidências não invalidam automaticamente o resume

O manifesto do run contém número físico e nome do arquivo, mas não o hash do
conteúdo. Uma edição no `.txt`, mudança de `ocr_result_id`, reparação dos índices
ou nova versão do detector bíblico pode deixar uma geração antiga marcada como
concluída.

O `source_hash` armazena as evidências quando a página é processada, porém ainda
não é usado como condição prévia da retomada. O resume deve comparar a fonte
atual com a persistida ou incluir versões/hashes suficientes no manifesto.

### 5. Índice único mistura todos os idiomas

O builder acrescenta PT, EN, FR e IT ao mesmo documento. Isso melhora recall
multilíngue, mas pode:

- fazer uma consulta em um idioma aparecer em todas as rotas;
- exibir um snippet em outro idioma sem o termo pesquisado;
- reduzir a importância local dos termos ao aumentar o total de tokens;
- aumentar o dump com sínteses acumuladas e vocabulário repetido.

A tradução conjunta pode ser mantida. O PoC deve comparar índice base em
português, índice por locale e índice multilíngue antes de escolher o formato de
produção.

### 6. Cobertura do KNN v2

O renderer escolhe o cluster run v2 concluído mais recente, mas não confirma que
seu conjunto corresponde exatamente às gerações promovidas atuais. Promoções
posteriores ao clustering podem ficar sem páginas relacionadas.

Registrar e validar a cobertura do conjunto antes de ativar
`--related-source v2`.

### 7. Freshness das traduções

A seleção de overlays concluídos deve confirmar que `source_hash`, versão do
prompt e modelo correspondem à geração corrente. Uma tradução concluída de uma
versão anterior não deve permanecer como fallback silencioso quando a fonte
mudar.

O validador também deve permitir uma síntese acumulada legitimamente vazia em
páginas administrativas no começo do volume, sem relaxar a validação dos outros
campos.

### 8. Páginas existentes somente no v2

A projeção híbrida parte da tabela legada e faz `LEFT JOIN` da geração v2. Uma
página v2 sem linha correspondente em `resumos` não é exportada. Embora o corpus
atual tenha ampla cobertura legada, a exportação deve eventualmente partir da
união das chaves legadas e v2.

## Critérios de saída do PoC

O PoC pode ser considerado pronto para escalar quando:

- as cinco páginas de smoke mantiverem classificação e transições corretas;
- os três volumes concluírem sem saltos de cadeia;
- bloqueios e `metadata_pending` forem contabilizados e amostrados;
- a tradução conjunta demonstrar consistência terminológica;
- o dump por configuração de idioma tiver tamanho medido;
- páginas administrativas reais ficarem fora da busca;
- o viewer não receber campos de embedding ou build desnecessários;
- autor e obra publicados refletirem o `work_key` validado;
- embeddings e KNN cobrirem exatamente as gerações promovidas;
- o legado continuar disponível como fallback durante a migração.

