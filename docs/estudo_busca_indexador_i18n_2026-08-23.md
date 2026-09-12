# Estudo da busca customizada, keywords e internacionalização

Data do levantamento: 2026-08-23

## 1. Escopo

Este documento registra a investigação da busca de páginas em `web/search`, do
indexador customizado em `.work/PatrologiaIndexer`, do material publicado em
`web/public`, das bases de resumos e keywords e do pipeline HDBSCAN.

A primeira versão proposta é corretiva:

- corrigir dados e relações atualmente errados;
- ampliar a superfície pesquisável com material já existente;
- não introduzir aliases de erros ortográficos ou correção lexical no front;
- preservar a velocidade e o carregamento por shards do indexador customizado.

Embora ainda existam scripts e artefatos Pagefind no repositório, a rota de
busca de páginas usa o `PatrologiaIndexer`. O adaptador do runtime conserva o
nome histórico `indexador-pagefind.js`, mas não executa Pagefind.

## 2. Arquitetura atual da busca de páginas

O builder corrente é:

- `tools/build_search_indexador_from_shards.mjs`

O front é:

- `web/src/components/pages/SearchPage.astro`
- `web/public/indexador/runtime/indexador-pagefind.js`
- `web/public/indexador/runtime/indexador-lib.js`

O índice publicado em `web/public/indexador/search` possui:

| Medida | Valor |
| --- | ---: |
| documentos | 288.570 |
| termos | 429.343 |
| shards de postings | 1.222 |
| shards de documentos | 578 |
| postings + mapa | 120.423.505 bytes |
| documentos | 8.101.977 bytes |
| total | 128.525.482 bytes / 122,57 MiB |

Configuração publicada:

- layout unificado;
- `min_importance=2.01`;
- 5.000 termos por shard;
- 307.200 bytes estimados por shard;
- 500 documentos por shard;
- termos estruturais preservados: `pg`, `pl`, `po`.

O manifesto publicado foi gerado antes da regra atual que elimina páginas cujo
`summary_page` normalizado seja exatamente `Conteúdo administrativo`. Há 6.568
páginas assim na base e elas ainda aparecem no índice publicado. Um rebuild com
o builder corrente deve removê-las.

## 3. Conteúdo atualmente indexado

Cada documento recebe, com deduplicação apenas de campos completos:

- nome enriquecido do resultado;
- volume e coleção (`PG`, `PL`, `PO`);
- `summary_page`;
- `summary_global`;
- autor detectado;
- obra detectada;
- labels canônicos das keywords publicadas;
- nomes de livros derivados das citações bíblicas.

O nome do resultado inclui as três primeiras keywords e também é indexado.
Assim, essas três keywords recebem reforço indireto e, quando o label canônico
está errado, também contaminam o título visível do resultado.

Não são indexados atualmente:

- corpo OCR ou `ocr_clean_search`;
- títulos/cabeçalhos extraídos do OCR;
- variantes brutas das keywords;
- traduções de autores, obras, seções ou entradas editoriais;
- categorias de keyword;
- posições dos termos;
- stemming, correção lexical ou fuzzy matching.

`summary_global` merece revisão. Ele é cumulativo e frequentemente repete temas
de páginas anteriores, criando falsos positivos e postings redundantes. Uma
versão corretiva deveria comparar sua remoção ou redução com o ganho obtido ao
indexar material estrutural mais preciso.

## 4. Semântica de consulta

O indexador normaliza por compatibilidade Unicode, remove marcas e aplica
case-folding. O underscore não é separador, portanto uma expressão como
`primeira_epistola_aos_corintios` vira um único termo indexável.

O índice não guarda posição de termos. No DSL atual, palavras adjacentes são
combinadas implicitamente por OR. O estado `exactPhrase` existe no front, mas
não é usado por `buildIndexadorQuery()`.

Consequências observadas no índice publicado:

| Consulta | Total observado | Observação |
| --- | ---: | --- |
| `trindade` | 10.000 | atingiu o teto configurado |
| `trinitas` | 310 | resultados com labels canônicos incorretos |
| `ressurreição` | 10.000 | atingiu o teto |
| `resurrection` | 812 | ruidoso e sem tradução sistemática |
| `Clemente` | 6.742 | ampla cobertura |
| `Clemens` | 495 | inclui página administrativa |
| `Primeira Epístola aos Coríntios` | 59.022 | termos unidos por OR |
| `First Epistle to the Corinthians` | 646 | dominado por palavras fracas |

Usando AND explicitamente:

- `primeira && epistola && corintios`: 676 resultados;
- incluindo `aos`: 293 resultados;
- a versão inglesa completa com AND: zero resultados;
- `clemens && romanus`: 110 resultados;
- `ressurreicao && carne`: 4 resultados.

Para labels conhecidos, autores, obras e traduções, uma solução possível é
indexar as duas representações:

```text
primeira epistola aos corintios
primeira_epistola_aos_corintios
```

O token unido pode implementar seleção exata em chips e autocomplete, enquanto
uma consulta livre pode combinar os termos informativos com AND. Isso não é
correção lexical e não exige dicionário ortográfico no navegador.

## 5. Volume de keywords e custo do dump

A base `data/patristica_keywords.db` contém:

| Medida | Valor |
| --- | ---: |
| keywords | 737.611 |
| keywords com ocorrência | 737.369 |
| associações página-keyword | 7.012.058 |
| páginas com keyword | 284.504 |
| citações bíblicas | 23.177 |
| keywords temáticas | 714.434 |
| associações de citação | 118.094 |
| associações temáticas | 6.893.964 |
| associações sem grupo HDBSCAN | 1.918.218 |
| caracteres das labels repetidas por página | 94.342.231 |
| ocorrências aproximadas de palavras | 13.069.285 |

Depois do colapso atual por grupo/citação, há aproximadamente 4.546.919
associações publicáveis de página para conceito.

O dump experimental foi produzido com o binário real e a mesma poda do índice
publicado. Foram indexadas todas as labels brutas associadas às páginas, sem
resumos ou outros campos:

| Parte do dump | Tamanho |
| --- | ---: |
| postings | 33.320.014 bytes / 31,78 MiB |
| documentos mínimos | 1.193.401 bytes / 1,14 MiB |
| mapa | 6.003 bytes |
| total | 34.519.418 bytes / 32,92 MiB |

O experimento resultou em 256.553 termos tokenizados. Como o índice principal
já contém os documentos e muitos termos das keywords também aparecem nos
resumos, 31,78 MiB é um teto razoável para o acréscimo dos postings brutos. A
soma conservadora com o índice atual é 154,35 MiB; um rebuild integrado deve
ficar abaixo disso por sobreposição.

## 6. Defeito crítico do pipeline HDBSCAN

O fluxo efetivamente publicado é:

1. `tools/generate_keyword_embeddings.py`;
2. `/homessddata/Projects/pdfocr/hdbscan_embedding.py`;
3. `tools/fill_canon_keywords.py`;
4. `tools/export_keywords_dicts.py`.

Há também `tools/cluster_keywords.py`, mas ele usa as colunas legadas
`keywords.embedding`, `cluster_id` e `cluster_label`. Não é o produtor dos
grupos publicados e seu papel deveria ser documentado ou depreciado para evitar
execução do pipeline errado.

### 6.1 Canônicos desalinhados

`fill_canon_keywords.py` tenta criar `cluster_canonical_names` com `CREATE
TABLE ... AS`, sem substituir atomicamente uma tabela existente. Quando a tabela
já existe, a exceção é capturada e os dados antigos permanecem.

Os IDs de grupo HDBSCAN não são estáveis entre execuções. No estado auditado:

- existem 1.927 grupos HDBSCAN não-ruído;
- `cluster_canonical_names` possui apenas 1.792 linhas;
- somente 6 linhas canônicas ainda apontam para o mesmo grupo atual;
- 1.618 apontam para outro grupo atual;
- 168 apontam para keywords que agora são ruído;
- faltam 135 labels, correspondendo aos 135 labels vazios publicados.

Exemplos concretos:

- `Trinitas`/`Trindade` estão no grupo atual 1085, mas o canônico publicado é
  `Ascetismo Capadócio`, pertencente atualmente ao grupo 935;
- `Cristo` está no grupo atual 1510, mas o label publicado vem de `Dedicação de
  muralha`, atualmente no grupo 1332.

Esse é o principal motivo para títulos e resultados semanticamente absurdos.
Os grupos precisam ter seus canônicos reconstruídos atomicamente depois de toda
execução HDBSCAN, antes de avaliar a qualidade do clustering.

### 6.2 Escolha do canônico

Mesmo depois do alinhamento, o critério atual privilegia:

- probabilidade de associação;
- proximidade ao comprimento médio das strings;
- ordem alfabética.

Ele não usa frequência de ocorrência, centralidade semântica ou preferência por
formas legíveis. O ranking deveria incorporar frequência por página e uma medida
de centralidade/representatividade.

### 6.3 Reprodutibilidade e separação de domínios

O script raiz de HDBSCAN:

- não fixa `random_state` no UMAP;
- aceita `--model` como opcional, embora múltiplos modelos possam coexistir;
- carrega aproximadamente 737 mil vetores de dimensão 4.096, cerca de 12 GiB
  apenas em floats antes do overhead;
- mistura citações bíblicas e temas no mesmo espaço global.

Há 289 grupos mistos, 5 grupos apenas de citações e 1.633 apenas temáticos. Um
grupo possui 4.421 membros, dos quais 4.237 são citações. Como a publicação já
trata citações individualmente, é recomendável separar clustering temático e
tratamento de citações, ou ao menos usar namespaces e execuções distintas.

### 6.4 Embeddings bíblicos ainda antigos

`generate_keyword_embeddings.py` já possui o enriquecimento novo com o texto da
Vulgata e as colunas `input_hash` e `input_profile`. A tabela
`keyword_embedding` auditada ainda não possui essas colunas, provando que o novo
pipeline não foi executado sobre a base atual.

Antes de reagrupar:

1. executar a migração/geração nova dos embeddings de citações;
2. validar hashes e perfis;
3. separar ou reavaliar o clustering de citações e temas;
4. reconstruir os canônicos de forma destrutiva-controlada e atômica;
5. exportar novamente os dicionários;
6. rebuildar o índice de páginas.

## 7. Internacionalização atual

As rotas `/en/search`, `/it/search` e `/fr/search` traduzem a interface, mas
consultam o mesmo conteúdo predominantemente português. O campo de busca dentro
do viewer apenas encaminha `term` e `volume` para essa busca, portanto herda a
mesma limitação.

A home contorna parcialmente o problema com uma lista manual pequena, por
exemplo exibindo `Augustine of Hippo` mas enviando `Agostinho de Hipona` como
termo. Esse método não escala.

O projeto já possui um ativo reutilizável em `data/patristic_indices.db`:

- 200.404 strings editoriais originais;
- 15.901.886 caracteres de origem;
- traduções completas de todas as strings para pt-BR, inglês, italiano e francês;
- entre 17,39 e 18,36 milhões de caracteres por idioma traduzido.

O builder `tools/build_indices_indexador_from_json.mjs` já inclui `original`,
`search` e todas as traduções no índice separado de índices editoriais.

## 8. Cruzamento de traduções com páginas

Recomenda-se cruzar as strings no build, não no browser.

### 8.1 Associação estrutural

- aplicar autor e título traduzidos às páginas compreendidas pelo intervalo da
  obra;
- aplicar heading traduzido ao intervalo da seção;
- aplicar entrada traduzida à página de destino quando o alvo estiver resolvido.

Os 4.772 intervalos de obras cobrem, por união estimada, 259.363 de 288.570
páginas, ou 89,88% do corpus. Há 4.527 obras com início de referência e 3.039
com fim. Mesmo sem correspondência literal, o título da obra é semanticamente
válido dentro de seu intervalo.

### 8.2 Associação por ocorrência textual

Para os casos sem vínculo estrutural ou como confirmação adicional:

1. agrupar os padrões por volume;
2. normalizar Unicode, caixa, ligaturas, pontuação e espaços;
3. exigir comprimento e quantidade mínima de palavras informativas;
4. buscar com matcher multipadrão no autor, obra, resumo, cabeçalho e OCR;
5. acrescentar as traduções quando a origem for encontrada;
6. registrar proveniência e limitar o volume de aliases por página.

Um teste literal, case-insensitive, sem normalização de pontuação e usando apenas
strings multi-palavra com pelo menos 12 caracteres encontrou:

| Volume | padrões | páginas com correspondência | páginas totais |
| --- | ---: | ---: | ---: |
| PG001 | 452 | 238 | 742 |
| PL001 | 147 | 141 | 693 |
| PO002 | 20 | 129 | 706 |

O resultado já cobre entre 18% e 32% das páginas da amostra. Uma normalização
conservadora deve ampliar a cobertura sem fuzzy matching.

## 9. Esforço de multitradução

O banco de resumos possui aproximadamente:

| Material | Caracteres |
| --- | ---: |
| `resumo_pagina` | 292.024.025 |
| `resumo_global` | 385.557.646 |
| ambos | 677.581.671 |
| `pagina_texto` OCR | 1.604.258.966 |
| `ocr_clean_search` | 1.522.348.227 |

Traduzir os dois resumos de 288.575 páginas para inglês, italiano e francês
representaria 865.725 saídas página-idioma, aproximadamente 170 milhões de
tokens de entrada e 508 milhões de tokens de saída quando os três idiomas são
gerados conjuntamente. A saída textual seria da ordem de 2 GiB antes da
indexação.

Traduzir somente `summary_page` ainda representaria aproximadamente 73 milhões
de tokens de entrada, 219 milhões de saída e cerca de 876 MB de texto bruto nas
três traduções.

Não é recomendável traduzir o OCR integral nesta fase. Além do volume, a
tradução propagaria erros OCR e exigiria uma camada significativa de auditoria.

Estratégia incremental recomendada:

1. reutilizar as 200.404 strings já traduzidas;
2. traduzir os cerca de 1.927 temas canônicos depois do reparo HDBSCAN;
3. traduzir somente autores e obras distintas ainda ausentes;
4. executar piloto de `summary_page` em três volumes;
5. medir recall, precisão e tamanho do dump antes de escalar.

Toda tradução nova deve ser cacheada por hash do texto-fonte, idioma, modelo,
prompt e versão do pipeline, com estados explícitos de sucesso e falha.

## 10. Arquitetura multilíngue recomendada

Para evitar quadruplicar o índice principal e misturar idiomas no score:

```text
indice-base
  português + formas originais em latim/grego + estrutura

overlay-en
  aliases traduzidos para inglês

overlay-it
  aliases traduzidos para italiano

overlay-fr
  aliases traduzidos para francês
```

Cada rota consulta o índice-base e o overlay do locale, combina por URL e
deduplica. O browser continua baixando apenas os shards relativos aos termos da
consulta; não recebe um dicionário completo de traduções.

Uma primeira implementação mais simples pode incluir as traduções no mesmo
`content`, mas o overlay oferece score mais controlável, custo de download por
locale e possibilidade de evoluir os idiomas de maneira independente.

Os snippets também precisam ser considerados. Hoje `SearchPage.astro` monta o
trecho visível apenas com `summary_page` e `summary_global` em português. Se um
resultado for encontrado por título ou entrada traduzida, a UI deverá mostrar o
campo correspondente ou pelo menos indicar `correspondência em título/índice
traduzido`, em vez de exibir um trecho sem o termo pesquisado.

## 11. Ordem recomendada de implementação

1. Reexecutar os embeddings enriquecidos das citações bíblicas.
2. Tornar o HDBSCAN reproduzível e separar temas de citações.
3. Recriar atomicamente `cluster_canonical_names` e melhorar o ranking canônico.
4. Reexportar keywords e rebuildar o índice, removendo páginas administrativas.
5. Corrigir consulta multi-palavra e ativar o comportamento de expressão exata.
6. Medir índice com keywords brutas adicionadas ao conteúdo completo.
7. Associar traduções existentes por intervalos de obra e alvos resolvidos.
8. Adicionar matching normalizado por volume para strings editoriais.
9. Criar overlays por idioma e snippets com proveniência da correspondência.
10. Pilotar tradução de `summary_page`; não traduzir `summary_global` ou OCR
    integral sem evidência de custo-benefício.

## 12. Validações realizadas

- consultas SQLite em modo read-only nas bases de resumos, keywords e índices;
- inspeção manual dos scripts `.mjs`, Python e C++ do indexador;
- dump temporário com o binário real para medir todas as keywords brutas;
- execução do runtime WASM no navegador local contra o índice publicado;
- consultas em português, latim e inglês, incluindo formas AND;
- inspeção de requests de shards e do console;
- correspondência literal de strings editoriais em amostras OCR de PG001,
  PL001 e PO002.

O dump temporário e o servidor local de validação foram removidos/encerrados. O
levantamento não alterou os dados nem os índices publicados. A investigação
posterior de PG001 produziu a alteração de código descrita a seguir.

## 13. Investigação da obra sem arquivo em PG001

Item investigado:

> Epistolæ duæ ad virgines (Syriace et Latine), interprete D. Cl. Villecourt,
> S. R. E. cardinali.

O payload declarava corretamente `start_page = 349`, mas deixava `start_file`
e os dois campos finais nulos. A evidência material confirma:

| Limite | Página editorial | Arquivo OCR físico | Evidência |
| --- | ---: | --- | --- |
| início | 349 | `teste/PG001/text/7fa490d2-837f-49cc-b89b-e5df30fac772-175.txt` | folha de rosto com `EPISTOLÆ DUÆ AD VIRGINES` |
| fim | 508 | `teste/PG001/text/d7eec708-d14d-4a19-b94f-f953d90c26b6-254.txt` | última página antes da obra seguinte |
| obra seguinte | 509 | `teste/PG001/text/d7eec708-d14d-4a19-b94f-f953d90c26b6-255.txt` | `CONSTITUTIONES APOSTOLICÆ — PROŒMIA` |

As imagens pareadas confirmam os limites:

- `teste/PG001/images/PG001-175.png`: páginas impressas 349–350 e folha de
  rosto da obra;
- `teste/PG001/images/PG001-254.png`: páginas impressas 507–508, embora o OCR
  tenha lido `807` e `806`;
- `teste/PG001/images/PG001-255.png`: páginas 509–510 e início de
  `Constitutiones apostolicæ`.

O formulário longo do índice aparece literalmente no ELENCHUS frontal, arquivo
físico 004, e volta a aparecer na tabela final, arquivo 739. O título material,
porém, usa o núcleo `EPISTOLÆ DUÆ AD VIRGINES` e formula de modo diferente os
créditos de língua e tradução. Portanto, o arquivo correto não contém a string
bibliográfica inteira do índice.

### 13.1 Causa registrada nos logs

O relatório `work_anchor_reconciliation_report.json` mostra a sequência da
falha:

1. o arquivo 004 recebeu score 12,5 por conter a descrição completa, mas foi
   corretamente marcado como fonte de índice e excluído;
2. o arquivo 175 recebeu score 7,5, página inferida 349 e estimador `[349, 350]`
   com confiança 0,88;
3. como nenhuma das consultas era o núcleo curto do título, o arquivo 175 não
   recebeu uma evidência `*_name_match` ou `*_name_fuzzy`;
4. `_choose_text_candidate` exige evidência nominal depois de remover fontes de
   índice e terminou com `no_non_index_candidate_with_title_evidence`.

O problema não estava na falta de OCR nem na falta de paginação. Era uma
incompatibilidade entre uma descrição bibliográfica de índice e o título
material da obra.

### 13.2 Dimensão sistêmica

Nos 310 relatórios disponíveis há 4.249 itens:

- 531 permanecem `unresolved`;
- 242 foram reparados deterministicamente;
- 198 itens terminaram especificamente em
  `no_non_index_candidate_with_title_evidence`;
- esses 198 casos aparecem em 73 relatórios de volume;
- 116 dos 198 já possuem ao menos um candidato não-índice cuja estimativa de
  página inclui a página declarada com confiança mínima de 0,6.

Os 116 não devem ser corrigidos em lote apenas pela página: alguns títulos
compartilham início, página ou contêiner. Eles formam, entretanto, uma fila
prioritária para rerun com título aproximado e inspeção da imagem.

## 14. Correção implementada no reconciliador

A correção mantém a comparação conservadora:

- preserva a descrição integral;
- gera também o núcleo anterior ao primeiro parêntese quando ele possui pelo
  menos três tokens e doze caracteres normalizados;
- trata descrição completa e núcleo como aliases, usando o melhor match em vez
  de somar scores correlacionados;
- continua exigindo suporte de página editorial e margem de score;
- mantém a normalização já existente de caixa, diacríticos e ligaturas:
  `æ = ae` e `œ = oe`;
- devolve `image_file` para cada candidato quando houver uma imagem PNG irmã
  inequívoca em disco;
- preserva `image_file` no relatório e no hint de rerun entregue ao agente;
- instrui o agente a inspecionar a imagem pareada quando houver ambiguidade de
  OCR, paginação ou limite compartilhado.

No dry-run real de PG001, o arquivo 175 passou a ter score 13,5, probabilidade
0,988204 e status `repaired`, conservando a página 349. O retorno inclui
`teste/PG001/images/PG001-175.png` como hint. O payload e o banco reais não
foram gravados durante esse dry-run; a aplicação controlada posterior está
registrada na seção 15.

Foram executados 28 testes focados com sucesso. Há regressões para título
descritivo versus núcleo, aliases sem soma de scores, normalização `æ/ae` e
`œ/oe`, propagação de `image_file` e instrução de inspeção visual.

### 14.1 Localização do código

Os executáveis versionados `scripts/index_target_locator.py` e
`scripts/reconcile_work_index_anchors.py` ainda são wrappers. Eles importam a
implementação de `tools/indexing/index_target_locator.py` e
`tools/indexing/index_work_anchor_reconciler.py`. O diretório
`patristica_pipeline/` inteiro aparece atualmente como não rastreado pelo Git.

Os helpers reutilizáveis foram retirados de `patristica_pipeline/`: módulos de
índices ficam em `tools/indexing/`, módulos bíblicos em `tools/scripture/` e os
utilitários comuns de corpus/OCR em `tools/`. `patristica_pipeline/` permanece
local e reservado à pipeline de agentes smoltools.

## 15. Auditoria ampliada, reparo do banco e exportação

Foi executado um dry-run determinístico, sem agente, sobre os 73 volumes que
possuíam ao menos um caso de
`no_non_index_candidate_with_title_evidence`. A primeira rodada sugeriu sete
alterações. A inspeção da imagem de PG013 revelou uma proposta incorreta: o
arquivo físico 013 já era o início correto, na página 17, mas o OCR havia lido
17/18 como 47/48. O reconciliador foi então endurecido para rejeitar também
candidatos classificados como `index_page_candidate`; o novo teste de
regressão elevou a suíte focada para 28 testes. Um novo dry-run de PG013 não
propôs alteração.

Depois dessa guarda, foram aplicados seis reparos seguros em cinco volumes:

| Volume | Obra | Causa anterior | Início corrigido | Arquivo físico |
| --- | --- | --- | ---: | ---: |
| PG001 | `Epistolæ duæ ad virgines` | `no_non_index_candidate_with_title_evidence` | 349 | 175 |
| PG111 | `Vita S. Clementis Bulgarorum episcopi` | `no_non_index_candidate_with_title_evidence` | 479 | 246 |
| PL005 | `Instructiones adversus Gentium deos` | `no_non_index_candidate_with_title_evidence` | 201 | 105 |
| PG156 | `De fulmine Agareno (Bajazete)` | `locator_candidate_probability_gap` | 581 | 389 |
| PL005 | `Disputationum adversus gentes libri septem` | `locator_candidate_probability_gap` | 365 | 187 |
| PL072 | `Marius Aventicensis episc.` | `locator_candidate_probability_gap` | 791 | 401 |

Portanto, além de PG001, PG111 e PL005 sofreram exatamente a mesma falha de
título bibliográfico longo versus título material. A auditoria encontrou ainda
três âncoras reparáveis de uma classe mais ampla. Isso não significa que os 198
casos sistêmicos tenham sido resolvidos: somente candidatos que passaram pelas
guardas de página, score, probabilidade e revisão foram gravados.

Em PG001, a inspeção manual das imagens completou também o fim da obra na
página 508, arquivo físico 254; a obra seguinte começa na página 509, arquivo
255. Os payloads dos cinco volumes foram importados para
`data/patristic_indices.db`. `PRAGMA integrity_check` retornou `ok`, e a
auditoria de materialização encontrou zero divergências entre obras/seções e
seus donos.

Antes das gravações foi criada uma cópia recuperável em
`/tmp/pdfocr-anchor-backup-c62fd3`, contendo o banco, os cinco payloads e os
dois diretórios públicos anteriores. O backup ocupa aproximadamente 1,2 GiB.

### 15.1 Exportação publicada em disco

O export solicitado foi executado diretamente:

```text
python tools/export_indices_from_db.py \
  --db data/patristic_indices.db \
  --out web/public/indices
```

Resultado: 407 volumes exportados, manifesto schema 3 gerado em
`2026-08-24T00:05:35+00:00`, ocupando aproximadamente 27 MiB. Os cinco shards
afetados passaram em `gzip -t`. No formato compacto, `start_file` e `end_file`
são convertidos em `reference_start_page` e `reference_end_page`; PG001 foi
publicado como páginas 349–508 e arquivos físicos 175–254.

O índice customizado, que não usa Pagefind, foi reconstruído em seguida. O
manifesto final contém 170.671 documentos, 259.736 termos, 262 shards de termos
e 342 shards de documentos, com cerca de 32 MiB. Dois smoke tests confirmaram
que tanto `Duas Epístolas às Virgens` quanto `Epistolae duae ad virgines`
incluem a URL da obra `PG001:work:epistolae_duae_ad_virgines`.

`npm run check` terminou com zero erros. `npm run build` compilou os entrypoints
e o cliente, mas a geração de rotas foi interrompida por um derivado alheio a
esta exportação que não está presente no checkout:
`web/public/alpha/scripture/manifest.json`.

## 16. Auditoria de âncoras nulas e de apontamentos para páginas de índice

Uma segunda auditoria somente de leitura percorreu os 407 payloads e as 4.772
obras, sem executar agente e sem aplicar alterações. O dry-run foi salvo
temporariamente em `/tmp/work-anchor-null-index-audit-20260823.json`.

### 16.1 Âncoras nulas

No SQLite há 237 obras, distribuídas por 49 volumes, sem `start_file`:

- 171 possuem `start_page`, mas não têm arquivo navegável;
- 66 não possuem nem página nem arquivo inicial;
- outras 133 possuem arquivo, mas não `start_page`, o que não impede a
  navegação física;
- 1.732 não possuem `end_file`; em 175 delas o `end_page` é conhecido, mas o
  arquivo final não. Esse segundo grupo afeta delimitação, não o link inicial.

O export público confirma o mesmo total: 237 das 4.772 obras não possuem
`reference_start_page`, e 171 delas ainda exibem uma página editorial inicial.

Há uma diferença importante entre banco e payloads. Nos JSONs atuais existem
227 `start_file` realmente nulos, em 45 volumes. Os dez casos restantes são
`null` obsoletos apenas no SQLite: o payload já contém uma âncora. Eles estão em:

| Volume | Quantidade |
| --- | ---: |
| PG046 | 2 |
| PG083 | 1 |
| PG140 | 1 |
| PL020 | 1 |
| PL031 | 1 |
| PL106 | 2 |
| PL114 | 2 |

Como o export parte do SQLite, esses dez registros já resolvidos no payload
foram publicados novamente sem referência física. Eles podem ser tratados por
uma ressincronização controlada dos sete volumes, após conferir o diff completo
de cada importação; não exigem agente nem nova localização no OCR.

O dry-run propôs preencher quatro dos 227 `null` reais. A inspeção das imagens
mostrou por que eles não devem ser aplicados em lote:

- PL180, arquivo físico 831: confirmação visual forte para `Relatio pro
  monasterio Rotensi`, página 1649;
- PG030, arquivo 436: o título `Eustathii in Hexaemeron S. Basilii` está
  materialmente presente, mas começa na página impressa 869 enquanto o índice
  declara 870;
- PG002: o algoritmo propôs o arquivo 587, páginas 1165–1166; o início material
  de `Epistola ad Diognetum`, página 1167, está no arquivo 588;
- PL055, arquivo 674: falso positivo; a imagem contém `Index rerum`, não `De
  vocatione omnium gentium libri duo`.

Portanto, entre esses quatro, PL180 é diretamente aplicável; PG030 requer uma
decisão sobre corrigir também a página editorial; PG002 deve usar o arquivo
vizinho 588; e PL055 deve ser rejeitado. Os outros 223 permanecem sem solução
determinística segura.

### 16.2 Obras marcadas como apontando para o próprio índice

A regra atual encontrou 54 obras, em 31 volumes, cujo `start_file` cai dentro
de uma faixa classificada como `volume_front`, `volume_end`, `volume_table`,
`fascicle_inventory` ou `retrospective_table`. Destas, 40 têm títulos
explicitamente indexais ou editoriais, como `Index`, `Ordo`, `Elenchus`,
`Indiculus`, `Opera omnia` ou `Tomus`. Nesses casos, apontar para a própria peça
editorial pode ser correto.

Os 14 títulos restantes também não podem ser classificados como erros apenas
pela faixa física. A inspeção das imagens mostrou páginas mistas:

- PG120/655 contém o `Fragmentum ex Vita S. Eusebiæ` na parte superior e o
  começo do `Ordo rerum` abaixo;
- PL093/569 contém a `Expositio` de Beda acima e o `Ordo rerum` abaixo;
- PL181/873 contém a `Charta de bonis` acima e o índice final abaixo;
- PL173/012 contém materialmente `Gesta abbatum Trudonensium`, embora a seção
  frontal tenha sido estendida até o mesmo arquivo.

PG124/011, PL040/010 e PL081/009 também contêm materialmente o título ou o
frontispício da obra apontada. Logo, `start_file_points_to_index_source` é um
sinal útil, mas a granularidade por arquivo gera falsos positivos quando uma
imagem compartilha obra e índice. Uma correção futura deve representar faixa
ou região da página, ou ao menos marcar `mixed_signal`, antes de excluir o
arquivo inteiro.

### 16.3 Seções de índice sem arquivo

Há quatro seções no banco sem `file_start` e `file_end`:

- PL015, `Concordantiæ S. Ambrosii et Josephi`: arquivos físicos 517–518;
- PL015, `Elenchus manuscriptorum`: arquivos 519–521, compartilhando o arquivo
  521 com o início da seção seguinte;
- PL015, `Index rerum et sententiarum`: arquivos 521–559;
- PL154, `Ordo rerum`: arquivo 732, que contém as páginas 1455–1456 e o início
  do índice antes das folhas de guarda.

O formato público inclui somente três dessas seções sem
`reference_page_start`; a concordância de PL015, classificada como
`work_front`, não é materializada no shard compacto. As quatro âncoras podem ser
preenchidas diretamente a partir do OCR e das imagens, sem agente.

## 17. Consolidação da auditoria manual de âncoras

A auditoria ampliada foi executada sobre os 407 payloads com 20 workers. Antes
da revisão havia 237 obras sem `start_file`; após sincronizar payloads já
corrigidos, revisar os candidatos determinísticos e investigar amostras nulas
em paralelo, o total caiu para 212. As 1.946 seções passaram a possuir
`file_start` e `file_end`.

Foram corrigidas âncoras confirmadas em PG002, PG005, PG026, PG028, PG030,
PG046, PG062, PG109, PG136, PL017, PL020, PL032, PL087, PL155, PL163, PL166,
PL180 e PL204, além da sincronização de volumes cujo payload estava à frente do
SQLite. PL020 revelou cinco páginas truncadas no inventário (`29→295`,
`33→335`, `38→387`, `46→463` e `54→541`), todas confirmadas individualmente no
OCR e na imagem.

Remissões externas ou locais sem corpo próprio foram preservadas sem arquivo e
marcadas como `manual_anchor_review.status = resolved_unlocated`, entre elas
itens em PG089, PL055, PL129 e PL155. O reconciliador agora reconhece decisões
manuais `resolved` e `resolved_unlocated`, não as envia novamente ao locator e
não tenta sobrescrevê-las em auditorias futuras.

O procedimento reprodutível, incluindo busca focal no OCR, resolução da imagem
pareada, schema de obras e seções, importação, exportação e protocolo de
subagentes, está documentado em
`docs/reparacao_indices_ocr_ancoras.md`.

## 18. Relações de strings, índices SQLite e feedback do export

A base já cruza traduções idênticas globalmente: `index_strings.source_text`
possui índice `UNIQUE`, e `index_translations` garante uma única relação por
`(string_id, language)`. No estado exportado há 200.404 strings e 801.616
relações, exatamente quatro idiomas por string. O plano de consulta usa
`idx_index_strings_source_text` e
`idx_index_translations_string_language`; não foi encontrada falta de índice
que explique a duração do export.

O reaproveitamento, porém, é literal depois de colapsar espaços. Caixa,
pontuação, diacríticos e ligaturas continuam sendo identidades distintas na
base de tradução. Não é seguro trocar o `UNIQUE` atual por uma forma agressiva
normalizada, pois strings editorialmente diferentes podem colidir. Uma evolução
mais segura é preservar a string original e criar uma relação explícita de
equivalência/ocorrência com papel (`work_title`, `section_heading`,
`entry_target`, `ocr_heading`) e documento. Isso permitiria cruzar cabeçalhos
OCR com strings já traduzidas sem perder proveniência.

No exportador, a ordenação temporária das linhas de tradução foi removida
porque o resultado é consumido como mapa e não depende de ordem. A consulta já
estava coberta pelos índices corretos; o maior custo observado é serialização e
gzip nível 9 por volume.

O script `tools/export_indices_from_db.py` agora:

- mostra pré-auditoria, número de strings/relações, progresso e tempos;
- grava shard e manifesto atomicamente;
- oferece `--progress-every` e `--compresslevel`;
- permite `--volume ... --merge-manifest` para atualizar shards selecionados
  sem reduzir o manifesto completo aos volumes filtrados.

A exportação focal dos 30 volumes tocados levou 40,5 segundos, conservou os 407
volumes no manifesto e foi seguida pelo rebuild do índice customizado.

### 18.1 Exportação paralela por volume

O export é paralelizável porque cada shard de volume é independente. Foi
adicionado `--workers N` com `ProcessPoolExecutor`; cada processo abre sua
própria conexão SQLite em modo somente leitura, processa e compacta um volume
por vez e grava o shard atomicamente. A conexão usada pela pré-auditoria é
fechada antes de criar o pool, evitando herdar handles SQLite pelo `fork`.
O padrão respeita a afinidade de CPU e fica limitado a 20 processos; o modo
serial permanece disponível com `--workers 1`.

O processo principal reúne os resultados fora de ordem, ordena os itens por
`volume_id` e só então substitui o manifesto. Se um worker falhar, o manifesto
anterior permanece disponível e nenhum shard individual fica truncado.

Validação com os mesmos 30 volumes e gzip nível 9:

| Workers | Tempo | Relação |
| ---: | ---: | ---: |
| 1 | 40,5 s | 1,0× |
| 20 | 7,3 s | 5,5× |

Os 30 JSONs descompactados e seus itens de manifesto foram comparados com a
saída serial e eram idênticos. O ganho não é linear porque leitura SQLite,
serialização e contenção de armazenamento continuam compartilhadas.

## 19. Revisão do extrator de keywords e citações bíblicas

O `keywords_serial.py` já possuía limpeza estrutural e normalização de citações
depois da resposta do modelo, mas o agente não recebia evidência bíblica
determinística extraída do OCR. Além disso, o `--source` padrão era
`resumo_pagina`: uma referência podia ser produzida a partir do resumo sem o
modelo ver o texto primário. A normalização posterior corrigia a forma da
string, mas não provava que livro, capítulo e versículo estavam na página.

O normalizador antigo também não é adequado para varrer OCR completo sem uma
camada mais conservadora. Em amostras reais ele interpretou `num. 6` como
`Números 6` e `col. 895` como `Colossenses 895`. No fac-símile PG001/017,
`num. 6` é materialmente o número 6 de uma referência bibliográfica a Orígenes;
no fac-símile PG001/074, `col. 695` é uma coluna da edição, não uma citação.

### 19.1 Camada de evidência anterior ao agente

O fluxo de keywords passou a reutilizar, por página, o detector moderno de
`tools/scripture/citation_index.py`. Esse detector já oferece:

- normalização OCR com offsets preservados;
- aliases PT/latim/francês/inglês e convenções históricas da Vulgata;
- limite máximo de capítulos por livro;
- capítulo/versículo, intervalos e listas;
- classificação de contexto em corpo, nota, cabeçalho, rodapé e aparato
  crítico quando o OCR possui blocos XML;
- distinção entre números de chamada de nota e a referência subsequente.

Até doze grupos compactos são colocados no prompt dentro de
`evidencia_citacoes_regex`, antes do conteúdo potencialmente longo. O contrato
explica que são candidatos a conferir, não itens obrigatórios. Citações no
corpo, notas e aparato são pesquisáveis; números editoriais, de coluna, de nota
ou de capítulos da obra não devem ser promovidos a referências bíblicas.

Para evitar os dois falsos positivos observados, formas isoladas ambíguas de
capítulo como `num. VI` e `col. 3` não entram na evidência entregue ao agente.
Essa escolha é deliberadamente favorável à precisão; uma referência real com
forma tão ambígua deve obter outra evidência contextual ou revisão visual.

Essas formas também não sobrevivem à sanitização final de keywords ou
categorias. Valores isolados como `num. VI`, `nm. 6`, `col. 3`, `coluna IV` e
`columna 8` são removidos com o diagnóstico
`removed_editorial_locator`. A regra exige que o valor inteiro seja apenas o
localizador: uma expressão substantiva como `Comentário da coluna 3` não é
apagada mecanicamente. Formas por extenso como `Número 666` são preservadas,
pois podem representar um conceito pesquisável e não apenas numeração
editorial. No SQLite normalizado atual, a regra estrita alcança dez labels e
dez ocorrências; a versão ampla alcançaria 70 labels/161 ocorrências e foi
rejeitada justamente por incluir termos potencialmente semânticos.

### 19.2 Fac-símile opcional

Foi adicionado `--facsimile`. Quando a imagem irmã existe e tem no máximo 20
MiB, o script a anexa no formato multimodal nativo:

- `images` em Ollama;
- `image_url` com data URL no endpoint OpenAI compatível.

O pareamento usa `pagina_file` para resolver o `.txt` físico e a função
`resolve_paired_page_image`; o número terminal do arquivo nunca é tratado como
página editorial. A imagem é opt-in para não ampliar custo, tráfego e latência
de todas as páginas por padrão. O prompt define o fac-símile como evidência
para resolver OCR, layout e pontuação, não como licença para inferir conteúdo.

O cliente OpenAI também deixou de gravar todo o request em `debug.json` e de
imprimir toda a resposta no stdout. O request poderia conter OCR e imagem em
base64, tornando esse comportamento inadequado para uma execução de corpus.

### 19.3 Auditoria sem exclusão automática

O novo modo:

```bash
python keywords_serial.py \
  --doc PG001 --page 17 \
  --verify --verify-scripture-warrant \
  --verify-report /tmp/keywords-scripture-audit.jsonl \
  --no-integrity-check
```

compara citações explícitas das keywords com os pares detectados no OCR. Uma
divergência é registrada como
`scripture_without_ocr_warrant[...]`. Ela não é apagada automaticamente,
inclusive com `--verify-fix`, porque ausência de match ainda pode ser falha de
OCR, numeração bíblica alternativa ou padrão não coberto.

A inspeção focal mostrou três classes diferentes:

- PG001/017: o fac-símile confirma `Psal. CXVII, 20`; a keyword contém
  `Salmos 118,20`, forma compatível com outra numeração do Saltério. Isso deve
  ser modelado como relação de numeração, não tratado como simples erro;
- PG001/074: a keyword `Atos dos Apóstolos 10,41` não aparece no OCR nem no
  fac-símile revisado e é uma boa candidata a alucinação herdada do resumo;
- PG001/120: o detector recupera referências no rodapé e em `VARIORUM NOTÆ`,
  incluindo `Psal. XI, 5`, `Psal. XXI, 19` e `Psal. LXXVII, 36-37`, mostrando
  que o aparato crítico amplia materialmente a superfície pesquisável.

### 19.4 Cache e custo

`extract_citations_from_value_cached` possuía um `lru_cache` declarado dentro
da própria função pública. O cache era recriado a cada chamada e nunca tinha
hits. Ele foi movido para escopo de módulo; a chave usa valor limpo, tipo de
fonte e modo de suporte, enquanto `source_path` é reaplicado em uma cópia do
resultado para não vazar proveniência entre campos. Em um smoke de 10.000
chamadas idênticas houve 9.999 hits e uma única execução do parser.

### 19.5 Limites e próximo passo

Esta revisão melhora a entrada e a auditabilidade, mas ainda não deve iniciar
um backfill global. Primeiro é recomendável gerar um relatório estratificado
em subconjuntos PG/PL/PO e revisar falsos positivos/negativos por contexto. Em
especial, a relação entre numeração hebraica, Septuaginta/Vulgata e formas
alternativas de capítulos/versículos precisa de uma representação explícita;
converter apenas por diferença aritmética seria incorreto em vários Salmos.

Um smoke read-only foi feito com as primeiras 15 páginas que possuíam keyword
marcada como citação em cada coleção. A amostra é deliberadamente pequena,
ordenada e não serve como estimativa de prevalência:

| Coleção | páginas | com imagem | com evidência regex | grupos | páginas com divergência |
| --- | ---: | ---: | ---: | ---: | ---: |
| PG | 15 | 15 | 11 | 38 | 15 |
| PL | 15 | 15 | 10 | 13 | 8 |
| PO | 15 | 0 | 4 | 8 | 14 |

Nos 38 grupos PG, os contextos foram 14 no corpo, 13 no aparato crítico, sete
no rodapé e quatro em notas. O resultado confirma duas decisões: aparato e
notas não podem ser descartados, e divergência automática não pode ser
sinônimo de exclusão. A ausência de imagens na pequena amostra PO também mostra
que o pipeline precisa continuar funcional apenas com OCR.
