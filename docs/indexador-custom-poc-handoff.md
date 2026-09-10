# PatrologiaIndexer: estado da integração e achados do POC

> Documento de continuidade para novas sessões de trabalho.
>
> Última atualização: 2026-08-10.
>
> Estado: POC local funcional e validado; ainda não integrado ao build/deploy de produção.

## Resumo executivo

O `PatrologiaIndexer` já é uma alternativa tecnicamente viável ao Pagefind para
os casos em que o frontend precisa apenas localizar documentos e não precisa
armazenar excertos no índice. O POC implementado cobre:

- indexação nativa por RPC JSONL em `stdin`/`stdout`, sem abrir portas ou sockets;
- servidor HTTP opcional por TCP ou Unix domain socket;
- geração de shards compactos contendo postings, nome e URL;
- runtime de consulta em WASM para o navegador;
- busca booleana e busca opcional por prefixo (`Cle` encontra `Clementis`);
- integração experimental na página `/indices`;
- benchmark offline e reproduzível contra Pagefind.

Na amostra estratificada de 25 volumes, o índice custom foi aproximadamente
`5,43x` mais rápido para indexar, `6,31x` menor em tamanho lógico e gerou
`165,62x` menos arquivos. A principal economia vem de não serializar um
fragmento/excerto para cada registro.

O corte para produção **ainda não foi feito**. O `prebuild` do site e o workflow
de GitHub Pages continuam construindo Pagefind. Além disso, os scripts do POC
apontam para `.work/PatrologiaIndexer`, caminho local que não existe
automaticamente no GitHub Actions.

## Escopo e caminhos importantes

Há dois repositórios Git independentes no ambiente de desenvolvimento:

| Item | Caminho / estado |
| --- | --- |
| Site e dados | `/homessddata/Projects/pdfocr` |
| Fonte do indexador | `/homessddata/Projects/pdfocr/.work/PatrologiaIndexer` |
| Binário nativo usado no POC | `.work/PatrologiaIndexer/build-codex-clang19/indexador` |
| Build WASM usado no POC | `.work/PatrologiaIndexer/build-codex-wasm/` |
| Emscripten SDK | `/mnt/projects/Projects/emsdk/` |
| Binário pronto mais antigo | `/mnt/projects/Projects/indexador/build/indexador` |
| Asset de pré-release | `.work/indexador-linux-x86_64-static-v0.1.tar.gz` |
| SHA-256 do asset | `.work/indexador-linux-x86_64-static-v0.1.tar.gz.sha256` |

Os commits relevantes no repositório do indexador são:

- `b20a8b3`: RPC JSONL por `stdin`/`stdout`, Unix socket, configuração e
  mensagens de erro explícitas;
- `b26f6e0`: busca por prefixo, correções de vetores Embind e exportador de
  assets WASM independente do diretório corrente;
- `5a3306f`: ajustes de `lifetimebound` para Clang/Emscripten.

O repositório principal está bastante sujo e `.work/` aparece como conteúdo não
rastreado nele. Não se deve fazer limpeza, reset ou commit amplo sem selecionar
os arquivos intencionalmente.

## Problema que motivou o POC

### Bloat do Pagefind

O Pagefind 1.4.0 serializa um fragmento por registro para poder produzir
excertos. Não foi encontrada uma opção pública estável para manter somente
postings e URLs. Para este projeto isso é caro porque:

- o site não precisa necessariamente do excerto pré-armazenado;
- o texto mostrado atualmente é derivado principalmente de resumos
  enriquecidos, keywords e outros dados derivados, e **não do OCR integral**;
- quando necessário, um trecho pode ser carregado do material real ou do JSON
  enriquecido somente depois que o usuário selecionar/abrir um resultado;
- centenas de milhares de arquivos pequenos custam muito espaço alocado e são
  inconvenientes para GitHub Pages.

### Chunking e latência

O Pagefind dá pouco controle útil sobre o particionamento interno para este
caso. A produção distribui o corpus em vários índices/lotes, inclusive por
coleções/volumes, para reduzir o custo de cada consulta. Mesmo assim, consultas
podem levar vários segundos e baixar chunks diferentes conforme os filtros.

O indexador custom expõe limites diretos de shard e grava muito menos arquivos.
Ainda é necessário medir o corpus completo antes de escolher entre um índice
único e índices separados por PG/PL/PO.

### Qualidade de busca

O Pagefind usa stemming em português. O indexador custom atualmente faz
normalização de caixa e acentos e busca tokens, mas não stemming. Portanto ele
pode ter recall levemente pior em flexões e variações morfológicas. Esse
trade-off foi considerado aceitável preliminarmente em troca da redução de
tamanho e latência.

A busca por prefixo reduz um problema diferente: consultas parciais durante a
digitação, como `Cle`, passam a encontrar `Clementis`. Ela não substitui
stemming.

## Arquitetura implementada

O fluxo do POC é:

```text
web/public/indices/manifest.json + PG/PL/PO*.json.gz
                  |
                  v
tools/build_indices_indexador_from_json.mjs
                  |
          JSONL por stdin/stdout
                  |
                  v
       binário PatrologiaIndexer
                  |
                  v
web/public/indexador/indices/{index.map, shards...}
                  |
                  v
indexador-pagefind.js + indexador_wasm.js/.wasm
                  |
                  v
web/src/components/pages/IndicesPage.astro
```

O adaptador JS oferece ao frontend uma interface semelhante à parte da API do
Pagefind usada pela página, reduzindo o tamanho da alteração no componente.

## Trabalho realizado no PatrologiaIndexer

### Configuração e CLI

Os defaults atuais são:

| Parâmetro | Default |
| --- | ---: |
| `max_terms_per_shard` | `5000` |
| `max_bytes_per_shard` | `307200` (300 KiB) |
| `documents_per_shard` | `5000` |
| `min_importance` | `2.01` |
| bind HTTP | `localhost` |
| porta HTTP | `8080` |
| diretório de saída | `.` |

A precedência é: defaults, arquivo JSON de configuração, variáveis
`INDEXADOR_*` e, por fim, CLI.

Falhas de configuração, criação do servidor e `listen` agora são escritas em
`stderr`. Isso corrige o comportamento observado inicialmente, no qual o
processo podia encerrar sem uma explicação visível.

### Unix domain socket

Foi adicionada a opção:

```sh
indexador --unix-socket /tmp/indexador.sock
```

Ela serve a API HTTP do `cpp-httplib` por socket Unix. O suporte é útil fora do
sandbox, mas não resolveu sozinho o desenvolvimento neste ambiente: o sandbox
pode negar tanto `bind()` TCP quanto Unix com `EPERM`.

### RPC JSONL por pipe

O modo recomendado para automação offline é:

```sh
indexador --stdio --output-dir ./search-data
```

Cada linha de entrada é uma requisição JSON e cada linha de saída é uma
resposta correlacionada por `message_id`. `stdout` fica reservado ao protocolo;
diagnósticos vão para `stderr`.

Operações implementadas:

- `Ping`;
- `AddBatch`, com validação atômica do lote;
- `Search`;
- `Stats`;
- `DumpIndex`;
- `Shutdown`.

Erros de requisição retornam um payload `Error` correlacionado sem derrubar o
processo. O protocolo está documentado em
`.work/PatrologiaIndexer/docs/stdio-rpc.md`.

O builder Node captura e mostra o `stderr` do filho e também informa código e
sinal se o binário encerrar antes de responder. Dessa forma, uma falha não fica
silenciosa.

### Busca por prefixo

O `index.map` já localizava o shard de um termo exato com `upper_bound(term)` e
o item anterior, isto é, o maior início de shard menor ou igual ao termo. O POC
reaproveitou essa ordenação para prefixos:

1. encontra o primeiro shard que pode conter o prefixo;
2. inclui shards seguintes enquanto seu primeiro termo começar com o prefixo;
3. em cada shard, começa em `lower_bound(prefix)`;
4. percorre somente termos com `starts_with(prefix)`;
5. ordena expansões por importância global e aplica limites configuráveis.

APIs adicionadas incluem `InvertedIndex::get_prefix_shard_files` no C++ e
`query_shard_docs_prefix_raw_limited` no binding WASM.

No runtime JS a função é opt-in:

| Opção | Default do runtime | POC de `/indices` |
| --- | ---: | ---: |
| `prefixMatch` | `false` | `true` |
| `prefixMinLength` | `3` | `3` |
| `prefixMaxTerms` | `32` | `32` |
| `prefixMaxPerTerm` | limite normal por token | `20` |

O default desligado preserva a semântica antiga para consumidores existentes.
Aliases em maiúsculas também são aceitos pelo adaptador.

Resultados observados no POC:

- `Cle`, com prefixo habilitado, retornou 9 resultados incluindo `Clementis`;
- a mesma consulta com prefixo desligado retornou 0;
- `Cl` não expande por causa do mínimo de 3 caracteres, embora possa encontrar
  ocorrências exatas da abreviação `Cl.`;
- `Cle AND Corinthios` funcionou com a DSL booleana.

### WASM e Embind

O runtime é compilado com Emscripten e exportado pelo script:

```text
.work/PatrologiaIndexer/scripts/export-wasm-assets.mjs
```

O exportador foi corrigido para resolver `wasmsrc` a partir do diretório do
próprio projeto, e não do diretório de onde o comando foi chamado. Isso permite
executá-lo por um script do `web/package.json`.

Foi encontrado e corrigido um erro de integração Embind:

```text
BindingError: Cannot pass "corinthios" as a VectorString
```

O JS transformava o retorno de `tokenize()` em `Array`, mas depois entregava o
array a métodos C++ que exigiam um `VectorString` Embind. A correção cria o vetor
Embind explicitamente e libera os handles retornados, evitando tanto o erro de
tipo quanto vazamentos triviais de objetos vinculados.

## Integração experimental no site

A rota fina `web/src/pages/indices.astro` apenas importa o componente real. A
integração foi feita em:

```text
web/src/components/pages/IndicesPage.astro
```

O componente carrega dinamicamente:

```text
/BibliothecaPatristica/indexador/runtime/indexador-pagefind.js
```

e busca mapas/shards em:

```text
/BibliothecaPatristica/indexador/indices
```

O POC não armazena nem renderiza excerpt. Cada documento do índice contém
somente:

- `name`, usado para exibir o resultado;
- `url`, usada para abrir/restaurar o contexto;
- tokens/postings do `content` combinado.

O envelope mínimo de navegação na URL é:

- `volume` sempre;
- `work`, `section` e `entry` quando aplicáveis.

A consulta `q` é mantida no estado da URL da página pelo frontend. O formato
dos parâmetros não tem validação forte contra um schema, mas foi mantido
deliberadamente pequeno para não transformar a URL/document shard em um novo
blob de metadados.

### Conteúdo indexado na página de índices

O builder combina em um único campo textual apenas os valores úteis para
encontrar volumes, obras, seções e entradas, como:

- volume e coleção;
- título e autor da obra;
- tipo/cabeçalho da seção;
- alvo, forma normalizada, texto e nota da entrada.

Não há distinção estrutural entre esses campos durante a busca. Os filtros podem
continuar sendo expressos como termos da consulta. Um campo de metadata poderia
ser adicionado ao formato futuramente, mas não foi necessário para o POC e
aumentaria os shards de documentos.

### Builder de desenvolvimento

O arquivo `tools/build_indices_indexador_from_json.mjs`:

- lê o manifest e os JSONs `.gz` já exportados;
- gera registros mínimos;
- inicia o indexador em `--stdio`;
- envia batches de 100 registros por default;
- solicita estatísticas e `DumpIndex`;
- mostra erros do processo filho;
- exige `--all` explícito para evitar indexar o corpus completo por acidente.

Sem argumentos de seleção, usa apenas `PG001,PL001,PO002`.

Resultado da amostra usada no POC:

- 462 documentos de 3 volumes;
- 1.697 termos;
- aproximadamente 0,04 s;
- 1 shard de postings e 1 shard de documentos;
- aproximadamente 32,1 KiB de índice de busca.

O runtime WASM/JS adicionou cerca de 611 KiB, dos quais o `.wasm` tinha
aproximadamente 541 KiB. Esse é um overhead reutilizável, não um custo por
documento.

### Estado dos scripts NPM

Foram adicionados ao `web/package.json`:

```json
{
  "build:indexador-runtime": "node ../.work/PatrologiaIndexer/scripts/export-wasm-assets.mjs --build-dir ../.work/PatrologiaIndexer/build-codex-wasm --out-dir ./public/indexador/runtime",
  "build:indices-indexador:dev": "node ../tools/build_indices_indexador_from_json.mjs --source-manifest ./public/indices/manifest.json --out ./public/indexador/indices --binary ../.work/PatrologiaIndexer/build-codex-clang19/indexador --base /BibliothecaPatristica --volumes PG001,PL001,PO002"
}
```

`web/public/indexador/` está ignorado no Git porque é artefato gerado.

## Benchmark Pagefind versus custom

O relatório detalhado e o comando de reprodução estão em
[`benchmark_pagefind_vs_indexador.md`](./benchmark_pagefind_vs_indexador.md).

Configuração da rodada de 2026-08-09:

- 25 volumes estratificados: 9 PG, 8 PL e 8 PO;
- 17.514 páginas;
- 48.425.919 bytes de conteúdo pesquisável;
- Pagefind 1.4.0;
- mesmos textos enriquecidos fornecidos aos dois indexadores;
- custom com os defaults de shard e `min_importance=2.01`;
- ingestão custom em batches de 250 pelo RPC JSONL.

| Métrica | Pagefind | PatrologiaIndexer | Razão Pagefind/custom |
| --- | ---: | ---: | ---: |
| Indexação e escrita | 33,85 s | 6,23 s | 5,43x |
| Tamanho lógico | 53.469.101 B | 8.476.042 B | 6,31x |
| Espaço alocado | 98.062.336 B | 8.704.000 B | 11,27x |
| Arquivos | 18.053 | 109 | 165,62x |

O custom gerou 104 shards de postings, 4 shards de documentos e 105.479 termos
distintos. O teto de aproximadamente 300 KiB foi atingido antes do limite de
5.000 termos por shard; nessa amostra, portanto, `max_bytes_per_shard` dominou o
particionamento.

No Pagefind, 17.514 arquivos de fragmentos representaram:

- 29.744.104 bytes lógicos, ou 55,6% do índice;
- 73.183.232 bytes alocados, ou 74,6% do espaço alocado.

Uma extrapolação linear simples para aproximadamente 288.570 páginas sugeriu
~881 MB para Pagefind e ~140 MB para o custom. Isso é apenas ordem de grandeza:
frequência global, `min_importance` e distribuição de termos mudam no corpus
inteiro. Como verificação externa, os índices Pagefind de produção medidos
ocupavam aproximadamente 860 MB lógicos e 1,5 GB alocados.

O benchmark mede tempo de indexação/escrita, tamanho e quantidade de arquivos.
Ele **não** mede relevância, recall, latência de consulta no navegador nem bytes
baixados por consulta.

## Validações já realizadas

### Indexador nativo

O build Clang 19 foi validado novamente em 2026-08-10:

```text
39/39 testes passaram
```

Os testes cobrem tokenização, serialização, normalização Unicode, HTTP,
configuração, RPC por stdio, lookup de shards, busca por prefixo e a DSL JS.
GCC 14 e Clang 19 foram usados com sucesso durante o trabalho; Clang 19 é o
build nativo de referência do POC.

### Frontend

Durante o POC foram verificados:

- carregamento HTTP 200 do runtime, `.wasm`, `index.map` e shards;
- buscas simples e booleanas;
- prefixo habilitado e desligado em instâncias separadas;
- ausência de erros no console;
- layout desktop;
- layout mobile em 375 px, sem overflow horizontal;
- `astro check` sem erros;
- build Astro concluído.

As screenshots ficaram em `/tmp` e não são artefatos duráveis do repositório.

## Comandos de reprodução

### Inicializar dependências vendorizadas

No repositório do indexador:

```sh
git submodule update --init --recursive
```

Submódulos usados no POC:

- `vendor/cpp-httplib` em `33bc1df930363e73fb1fd97f8d475e4272b1f221`;
- `vendor/json` em `3946872265598aed5a7aea68cad4d9d1f168bd4b`.

### Build nativo com Clang 19

```sh
cd /homessddata/Projects/pdfocr/.work/PatrologiaIndexer
cmake -S . -B build-codex-clang19 \
  -DCMAKE_C_COMPILER=clang-19 \
  -DCMAKE_CXX_COMPILER=clang++-19 \
  -DENABLE_TESTS=ON
cmake --build build-codex-clang19 -j2
ctest --test-dir build-codex-clang19 --output-on-failure
```

### Build WASM

O cache default do emsdk fica sob `/mnt/projects/Projects/emsdk/cache`, que pode
ser somente leitura. Sem `EM_CACHE` gravável foi observado:

```text
OSError: [Errno 30] Read-only file system: .../cache/symbol_lists/...json.lock
```

Use:

```sh
cd /homessddata/Projects/pdfocr/.work/PatrologiaIndexer
source /mnt/projects/Projects/emsdk/emsdk_env.sh
EM_CACHE=/tmp/codex-indexador-em-cache emcmake cmake \
  -S . -B build-codex-wasm -DENABLE_TESTS=OFF
EM_CACHE=/tmp/codex-indexador-em-cache \
  cmake --build build-codex-wasm -j2
```

Alterações apenas em `wasmsrc/indexador-lib.js` não exigem recompilar o `.wasm`;
alterações nos bindings ou no C++ exigem.

### Exportar runtime e criar a amostra

```sh
cd /homessddata/Projects/pdfocr/web
npm run build:indexador-runtime
npm run build:indices-indexador:dev
npm run dev
```

Para indexar todos os volumes do manifest, execute o builder diretamente com
`--all`; não remova essa confirmação explícita:

```sh
cd /homessddata/Projects/pdfocr
node tools/build_indices_indexador_from_json.mjs \
  --source-manifest web/public/indices/manifest.json \
  --out web/public/indexador/indices \
  --binary .work/PatrologiaIndexer/build-codex-clang19/indexador \
  --base /BibliothecaPatristica \
  --all
```

### Repetir o benchmark pequeno

```sh
cd /homessddata/Projects/pdfocr
node tools/benchmark_pagefind_vs_indexador.mjs \
  --volumes PG001,PL001,PO002
```

O benchmark cria um diretório temporário, imprime seu caminho e salva nele um
`report.json`.

## Estado real da pipeline de produção

Esta é a distinção mais importante para a próxima sessão:

### O que funciona localmente

- builder Node -> RPC stdio -> índice custom;
- exportação do runtime WASM;
- consumo do índice custom na página `/indices`;
- busca por prefixo configurável;
- benchmarks offline.

### O que continua em Pagefind

- `web/package.json` ainda define
  `"prebuild": "npm run build:indices-search"`;
- `build:indices-search` ainda chama
  `tools/build_indices_pagefind_from_json.mjs`;
- `.github/workflows/pages.yml` ainda cria os lotes Pagefind, grava seu manifest
  e só então executa `npm run build`;
- a busca geral do corpus também permanece na arquitetura Pagefind;
- não há aquisição/build do binário do indexador nem do WASM no Actions;
- `web/public/indexador/` é ignorado e, sem um passo explícito de geração/cópia,
  não entra no deploy.

O workflow atual publica em pushes da branch `codex` e por
`workflow_dispatch`. Ele constrói lotes Pagefind de 25 volumes com concorrência
baseada em `nproc`.

Consequência: a versão experimental de `IndicesPage.astro` depende de assets
que só existem após os comandos locais do POC. Não se deve considerar a
migração concluída nem remover Pagefind antes de tornar esses assets
reproduzíveis no CI.

Também existe uma alteração experimental e incompleta em
`tools/build_indices_pagefind_from_json.mjs`, originada de testes com `curl` e
HTTP. O caminho consolidado para o custom é o novo builder por stdio; não se
deve promover aquela experiência a pipeline sem antes revisar o diff.

## Decisões provisórias

### Não adicionar metadata estruturada agora

Para a página de índices, nome + URL + postings são suficientes. Os filtros
atuais podem ser termos comuns da busca. Metadata estruturada somente deve ser
adicionada se surgir um requisito que não possa ser expresso por tokens, como
filtragem exata de alta confiabilidade sem contaminar ranking.

Se metadata for adicionada, medir separadamente:

- crescimento dos document shards;
- bytes adicionais por resultado;
- impacto de campos repetidos em 288 mil registros;
- compatibilidade de versão entre runtime e índice.

### Não armazenar excerpt no índice

Essa é a principal proposta de valor do custom. Se a UI voltar a precisar de
trechos, preferir carregamento sob demanda do JSON enriquecido ou do texto da
página. Isso também evita perpetuar a impressão de que os snippets Pagefind
atuais são OCR integral: eles vêm do conteúdo enriquecido fornecido ao
indexador.

### Manter os defaults como ponto de partida

Para o corpus grande, iniciar com:

```text
max_terms_per_shard = 5000
max_bytes_per_shard = 307200
documents_per_shard = 5000
min_importance = 2.01
```

`min_importance` atua como poda prática de termos pouco informativos/stop words.
Não aumentar ou reduzir agressivamente sem comparar recall e shards baixados.
Na amostra de 25 volumes, o limite de bytes foi mais restritivo que o limite de
termos.

### Índice único versus PG/PL/PO separados

Hipótese favorável ao índice único:

- elimina manifests/runtimes repetidos;
- simplifica o frontend e consultas cruzadas;
- o lookup ordenado carrega somente shards relacionados aos termos;
- o custom produz poucos arquivos em comparação ao Pagefind.

Riscos a medir:

- crescimento do `index.map`;
- quantidade e tamanho dos shards necessários por consulta;
- custo de termos muito comuns em todo o corpus;
- tempo e pico de memória na geração;
- necessidade de filtro exato de coleção.

O POC unificado descrito ao fim deste documento resolveu o filtro exato de
coleção com termos protegidos e passou a ser o layout padrão dos scripts de
busca. Ainda falta o benchmark do corpus completo antes de considerar a escolha
definitiva para produção.

## Próximos passos recomendados

1. **Fechar um corpus de consultas de relevância.** Incluir nomes, formas
   flexionadas, acentos, abreviações, prefixos, termos raros, termos comuns e
   expressões booleanas. Comparar resultados Pagefind/custom manualmente.
2. **Rodar um benchmark maior sem varrer tudo de primeira.** Crescer de 25 para
   50/100 volumes e registrar tempo, memória, tamanho lógico/alocado, arquivos,
   quantidade de shards e bytes lidos por consulta.
3. **Comparar índice único e três índices PG/PL/PO no corpus maior.** O POC de
   3.715 páginas favoreceu o índice único; repetir com 50/100 volumes e depois
   com o corpus completo.
4. **Tornar o build portátil.** Decidir entre compilar o indexador no Actions,
   baixar um release asset verificado por SHA-256 ou fazer checkout autenticado
   do repositório privado. Os scripts de produção não devem apontar para
   `.work/`.
5. **Adicionar um manifest versionado do índice.** Incluir versão do formato,
   commit do indexador, parâmetros de shard, contagens e hashes básicos para
   impedir mistura de runtime e shards de versões diferentes.
6. **Integrar o Actions mantendo rollback.** Gerar runtime e índice custom,
   validar arquivos obrigatórios e consultas smoke, executar `astro check` e
   `astro build`, mas manter Pagefind disponível até a validação final.
7. **Fazer o corte integral quando aprovado.** Alterar `prebuild`, workflow e
   frontend no mesmo conjunto de mudanças; só depois remover artefatos e código
   Pagefind que não tenham mais consumidores.
8. **Medir a experiência no navegador.** Registrar cold start, segunda busca,
   bytes transferidos, requests, console e desktop/mobile. O benchmark offline
   atual não substitui essa etapa.

## Critérios sugeridos para aceitar a migração

- build completo reproduzível no GitHub Actions;
- `index.map`, runtime e todos os shards presentes no artefato publicado;
- smoke queries retornando URLs válidas;
- nenhuma dependência de caminho `.work/` no CI;
- latência percebida melhor que Pagefind em cold e warm cache;
- tamanho e quantidade de arquivos dentro de margens confortáveis do Pages;
- recall aceitável no conjunto de consultas de referência;
- prefixo limitado para não expandir termos excessivamente;
- fallback/rollback documentado para o primeiro deploy;
- console e network limpos em desktop e mobile.

## Checklist de retomada rápida

Ao iniciar uma nova sessão:

1. ler este documento e `docs/benchmark_pagefind_vs_indexador.md`;
2. verificar `git status` no repositório principal e no
   `.work/PatrologiaIndexer` separadamente;
3. confirmar os commits `b20a8b3` e `b26f6e0` no indexador;
4. confirmar que os submódulos estão inicializados;
5. rodar os 39 testes nativos antes de mudar formato/bindings;
6. reconstruir o WASM somente se C++/bindings mudaram;
7. regenerar `web/public/indexador/`, pois ele é ignorado;
8. não usar o corpus completo sem `--all` explícito;
9. lembrar que socket HTTP pode falhar com `EPERM` no sandbox e preferir
   `--stdio` para builders/benchmarks;
10. antes de publicar qualquer alteração frontend, validar build, console,
    network e screenshots desktop/mobile.

## POC 2: busca principal de páginas (`/search`)

Em 2026-08-10 foi feito um segundo POC sobre a busca principal, que representa
as aproximadamente 290 mil páginas do corpus. Diferentemente do POC de
`/indices`, esta integração substitui o Pagefind no caminho de execução de
`SearchPage.astro`; não há seletor de engine ou fallback no componente.

O builder novo é:

```text
tools/build_search_indexador_from_shards.mjs
```

Comandos adicionados em `web/package.json`:

```sh
npm run build:search-indexador:dev
npm run build:search-indexador
```

O primeiro usa explicitamente `PG001,PL001,PO002`. O segundo passa `--all` e
deve ser usado com cautela. Ambos dependem, por enquanto, do binário local em
`.work/PatrologiaIndexer/build-codex-clang19/indexador`.

### Layout inicial do experimento

O POC gera três índices independentes:

```text
web/public/indexador/search/pg/
web/public/indexador/search/pl/
web/public/indexador/search/po/
```

O manifest `web/public/indexador/search/manifest.json` informa coleções,
volumes, contagens, tamanhos e configuração. A separação resolve o filtro exato
de coleção sem adicionar metadata estruturada ao formato binário:

- com uma coleção selecionada, somente aquele índice é consultado;
- sem coleção, PG/PL/PO são consultados em paralelo;
- resultados são intercalados por score;
- um único módulo WASM é compartilhado entre as três engines;
- volume e livro são acrescentados como cláusulas booleanas da consulta.

Esse foi o primeiro layout do POC. O experimento posterior com índice único,
descrito abaixo, eliminou a necessidade de intercalar scores entre partições.

### Conteúdo do índice e enriquecimento lazy

Cada página grava somente:

```json
{"name":"PG001 p.98 — ...","url":"/BibliothecaPatristica/viewer?doc=PG001&page=98","content":"..."}
```

`content` segue a mesma fonte lógica usada pelo Pagefind: título, resumo da
página, resumo global, autor, obra, keywords e nomes de livros. O índice não
armazena excerpt nem uma cópia de metadata para renderização.

Depois de obter os documentos da página visível, o frontend:

1. extrai `doc` e `page` da URL;
2. carrega `meta/{volume}.json.gz`;
3. localiza e carrega apenas os page blocks necessários;
4. recupera autor, obra, resumo e URL do OCR bruto;
5. monta um trecho ao redor do termo encontrado e aplica `<mark>` com escaping;
6. reutiliza os labels já presentes em `name` para os chips principais.

Não se baixa mais o catálogo completo de keywords para enriquecer os cards. Na
primeira versão do POC isso causava requests para praticamente todos os shards
alfabéticos, anulando parte da economia do índice.

O trecho exibido continua vindo dos resumos enriquecidos (`summary_page` e,
quando necessário, `summary_global`), não do OCR integral. O OCR bruto só é
aberto pelo link correspondente.

### Configuração de shards

Postings e documentos são configuráveis separadamente:

```text
--max-terms-per-shard
--max-bytes-per-shard
--documents-per-shard
--min-importance
```

Os dois primeiros limitam os shards do índice invertido; o terceiro controla
os document shards. Os valores efetivos são gravados no manifest. O builder
usa os defaults do projeto como ponto inicial: `5000`, `307200`, `5000` e
`2.01`, respectivamente.

As quatro opções também aceitam prefixo `pg-`, `pl-` ou `po-`, permitindo
ajustes independentes. Exemplo:

```sh
node tools/build_search_indexador_from_shards.mjs \
  --pg-max-bytes-per-shard 614400 \
  --pl-documents-per-shard 10000 \
  --po-max-terms-per-shard 7500
```

Cada coleção grava sua configuração efetiva no manifest, mesmo quando usa os
defaults globais.

### Resultados da amostra de páginas

Com PG001, PL001 e PO002:

| Coleção | Páginas | Termos | Índice em disco |
| --- | ---: | ---: | ---: |
| PG | 742 | 20.778 | 451,6 KiB |
| PL | 693 | 21.928 | 422,8 KiB |
| PO | 706 | 14.201 | 313,2 KiB |
| **Total** | **2.141** | — | **1.187,6 KiB** |

Tempo total de geração: `1,12 s` com RPC por stdio.

No navegador local, depois do carregamento inicial, consultas observadas:

- `Trindade`: cerca de `150 ms`, 236 candidatos nas três partições;
- `Trindade` com coleção PG: 43 candidatos;
- prefixo `Cle` com coleção PG: cerca de `143 ms`, 214 candidatos.

Esses números são apenas warm-cache e sobre 2.141 páginas. O tempo de navegação
reportado pela automação não deve ser confundido com o tempo da consulta: a
medição foi feita entre o evento de input e a conclusão do DOM dos resultados.

Após as otimizações, uma busca filtrada em PG baixou um único `.wasm`, o
`index.map`, os term/document shards necessários e os page blocks visíveis. Não
houve request a Pagefind nem aos shards completos do dicionário de keywords.

### Semântica e limites atuais

- busca por prefixo está habilitada a partir de 3 caracteres;
- o POC usa limite de 10.000 documentos por termo/expansão;
- termos comuns da consulta textual mantêm a semântica OR da DSL;
- volume/livro entram por AND; múltiplos valores do mesmo filtro entram por OR;
- coleção seleciona partições em vez de contaminar o ranking com um token;
- busca vazia não enumera 290 mil documentos: mostra uma instrução para digitar
  um termo ou selecionar um volume;
- o estado `exactPhrase` da UI não tem equivalente real no indexador e precisa
  ser revisto antes do corte final;
- scores calculados em índices separados são intercalados diretamente; a
  qualidade dessa ordenação precisa ser comparada com índice único;
- sem stemming, recall morfológico pode continuar menor que no Pagefind.

### Alteração no runtime do indexador

`wasmsrc/indexador-lib.js` agora compartilha a Promise do módulo Emscripten por
`wasmScriptUrl`. Antes disso, consultar PG/PL/PO em paralelo injetava o script e
instanciava o mesmo WASM três vezes. Cada índice ainda mantém seu próprio
`QueryEngine` e caches de shards, como necessário.

### Validação realizada

- amostra gerada com sucesso por `npm run build:search-indexador:dev`;
- override PG validado com `614400` bytes por shard e `10000` documentos por
  shard; os valores efetivos apareceram no manifest;
- testes JS do indexador: 2/2;
- `astro check`: 0 erros (hints preexistentes e warning no JS gerado);
- `npx astro build`: 865 páginas, sucesso;
- browser em `1920x1080` e `375x812`;
- console: 0 erros e 0 warnings;
- network: todos os requests observados retornaram 200;
- correção de overflow horizontal mobile, confirmado com `scrollWidth = 375`;
- screenshots:
  - `.playwright-mcp/search-indexador-desktop.png`;
  - `.playwright-mcp/search-indexador-mobile.png`.

### Pipeline ainda não migrada

Embora `/search` já não use Pagefind no runtime do POC, o repositório ainda
mantém dependência, builders e workflow do Pagefind. Os assets custom continuam
ignorados por Git e o Actions ainda não os gera. Portanto este estado é próprio
para desenvolvimento local e **não deve ser publicado sem alterar o CI no
mesmo corte**.

## POC 3: índice único e exceções de `min_importance`

Em 2026-08-10 o builder e o frontend de `/search` passaram a aceitar um único
índice para PG, PL e PO. O builder agora modela uma lista de índices e suporta:

```sh
--layout split
--layout unified
--keep-terms pg,pl,po
--no-keep-terms
```

O manifest v2 separa os conceitos antes misturados em `collections`:

- `collections`: IDs das coleções disponíveis;
- `indexes`: índices físicos, caminhos, coleções cobertas e volumes;
- `layout`: `split` ou `unified`.

`SearchPage.astro` ainda lê o manifest v1 para compatibilidade, mas no v2 abre
uma única engine e acrescenta o filtro de coleção à DSL booleana. O filtro
`collection=PL` torna-se a consulta textual protegida `pl` no índice unificado.

### Por que `keep_terms` é necessário

A importância global de um termo é:

```text
total de documentos / documentos que contêm o termo
```

No manifest público atual há 288.570 páginas: 119.357 PG, 153.117 PL e 16.096
PO. Assim, as importâncias teóricas dos tokens de coleção são aproximadamente:

| Termo | Importância global |
| --- | ---: |
| `pg` | 2,418 |
| `pl` | 1,885 |
| `po` | 17,928 |

Com o default `min_importance=2.01`, `pl` seria removido do dump unificado.
Baixar o limiar abaixo de 1,885 preservaria `pl`, mas também manteria muitos
outros termos muito comuns. A solução implementada foi `keep_terms`: os termos
listados ignoram somente a poda por `min_importance`; ranking, postings,
tokenização e formato dos shards permanecem iguais.

Cada entrada passa pela pipeline completa do índice:

```text
unaccent_lower_impl -> tokenize -> comparação com a chave normalizada
```

Logo, caixa, acentos, espaços e separadores têm o mesmo tratamento que na
ingestão e na consulta. O teste de regressão usa `" PROTÉCTED,"` para preservar
a chave normalizada `protected`.

### Configuração e APIs

`keep_terms` pode ser definido em todas as superfícies:

- CLI: `--keep-terms pg,pl,po`;
- JSON de inicialização: `index_dump.keep_terms`;
- ambiente: `INDEXADOR_INDEX_DUMP_KEEP_TERMS=pg,pl,po`;
- RPC por operação:

```json
{"type":"DumpIndex","config":{"min_importance":2.01,"keep_terms":["pg","pl","po"]}}
```

- HTTP por operação:

```sh
curl -X POST http://localhost:8080/dump_index \
  -H 'content-type: application/json' \
  -d '{"min_importance":2.01,"keep_terms":["pg","pl","po"]}'
```

RPC e HTTP usam o mesmo parser, merge e validador. Campos omitidos herdam a
configuração inicial, enquanto `"keep_terms": []` limpa a lista explicitamente.
O builder Node envia os parâmetros no `DumpIndex.config`, sem precisar reiniciar
o processo para experimentar outro dump.

### Comparação controlada

Foi usada uma amostra deliberadamente desbalanceada para reproduzir o corpus:
PG001, PL001, PL002, PL003 e PO002, totalizando 3.715 páginas, das quais 2.267
são PL. Nesse conjunto a importância de `pl` é aproximadamente 1,638.

| Variante unificada | Tamanho | Tempo |
| --- | ---: | ---: |
| `2.01`, sem exceções | 1.979,8 KiB | 1,92 s |
| `1.60`, sem exceções | 2.032,1 KiB | 1,86 s |
| `2.01`, `keep_terms=pg,pl,po` | 1.983,4 KiB | 1,93 s |

Baixar o limiar custou 52,3 KiB em relação ao default, enquanto proteger os
três filtros custou somente 3,6 KiB. Nesta amostra, `keep_terms` preservou o
filtro com cerca de 6,9% do crescimento causado pelo limiar reduzido.

Recomendação atual: manter `min_importance=2.01`, usar `keep_terms=pg,pl,po` e
ajustar limites de shard separadamente. Não baixar o limiar global somente para
preservar filtros.

### Validação do frontend unificado

O POC publicado localmente usa um único diretório:

```text
web/public/indexador/search/all/
```

Resultados observados no preview estático:

- `collection=PL`, sem termo: 2.267 resultados, exatamente as páginas PL da
  amostra;
- `trindade AND PL`: 251 resultados;
- somente um `index.map`, uma instância WASM e os shards lexicais/documentais
  necessários foram carregados;
- todos os requests filtrados do runtime/índice retornaram HTTP 200;
- console do preview: 0 erros e 0 warnings;
- desktop 1920×1080 e mobile 375×812 sem overflow horizontal global.

Screenshots:

- `.playwright-mcp/search-unified-desktop.png`;
- `.playwright-mcp/search-unified-mobile.png`.

Validação automatizada:

- PatrologiaIndexer: 43/43 testes C++/JS;
- `astro check`: 0 erros; hints preexistentes em arquivos públicos gerados;
- `npx astro build`: 865 páginas.

## Build integral do índice unificado

Em 2026-08-10 o índice unificado foi gerado sobre todos os 407 volumes do
manifest público:

```sh
cd web
/usr/bin/time -v npm run build:search-indexador
```

Configuração efetiva:

```text
layout = unified
max_terms_per_shard = 5000
max_bytes_per_shard = 307200
documents_per_shard = 5000
min_importance = 2.01
keep_terms = pg,pl,po
```

Resultados:

| Métrica | Resultado |
| --- | ---: |
| Páginas | 288.570 |
| Volumes | 407 |
| Termos antes da poda do dump | 429.343 |
| Tempo do builder | 129,30 s |
| Tempo de parede medido | 129,60 s |
| Pico de memória residente | 730.036 KiB (~713 MiB) |
| Swap | 0 |
| Shards lexicais | 1.222 |
| Shards de documentos | 58 |
| Arquivos do índice, incluindo manifest/mapa | 1.282 |
| Tamanho lógico do índice | 127.124.985 bytes (121,24 MiB) |
| Espaço alocado do índice | 129.654.784 bytes (~123,65 MiB) |

### Benchmark da ingestão paralela do RPC em 2026-09-05

O step `Build custom indices` de `.github/workflows/pages.yml` foi reproduzido
localmente com seus argumentos exatos, incluindo os dois `--all`, layout
unificado, `documents_per_shard=500`, wordlists e as sete smoke queries. A
máquina foi um Ryzen 9 5900X; o build final usou Clang 21, libstdc++ 14 e
oneTBB 2021.8 (`libtbb.so.12`).

O corpus dessa rodada continha 170.269 documentos editoriais em 406 volumes e
288.278 páginas em 407 volumes. A busca indexou 281.682 páginas e omitiu 6.596
páginas administrativas. Todas as variantes produziram as mesmas contagens,
920.725 termos na busca, 603 arquivos editoriais e 3.564 arquivos de busca.

| Implementação | Índices editoriais | Busca principal | Indexação total | Validação | Total validado |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline sequencial, Clang 19 | 30,12 s | 188,68 s | 218,80 s | 6,43 s | 225,23 s |
| TBB, mutex por termo, Clang 21 | 35,27 s | 203,34 s | 238,61 s | 6,26 s | 244,87 s |
| Implementação final TBB/OpenMP, TBB ativo, mutex por documento, Clang 21 | 27,98 s | 160,81 s | 188,79 s | 6,29 s | 195,08 s |

O resultado final reduziu a indexação em 30,01 segundos, ou 13,7%, frente ao
baseline. O mutex por termo foi 9,1% mais lento que o baseline e acumulou muito
tempo de sistema por contenção. A versão mantida tokeniza e calcula frequências
em paralelo, escreve cada documento em um slot previamente reservado por
`resize` e adquire o mutex uma vez por documento para atualizar o mapa e seus
postings.

Os 603 arquivos editoriais e 3.564 arquivos de busca foram comparados com o
baseline por `diff -qr --exclude=manifest.json`: não houve nenhuma diferença.
Os manifestos foram excluídos porque registram metadados da execução.

O range inicial baseado em `std::views::iota` também era um falso paralelo com
a PSTL/libstdc++ usada: o algoritmo selecionava o backend serial por causa da
categoria legada do iterador. O backend TBB final usa um pequeno
`std::vector<size_t>` de índices, com iteradores random-access. Quando TBB não
está disponível, o CMake seleciona OpenMP; só cai no loop serial se nenhum dos
dois backends existir.

Os releases do indexador são ELF estáticos construídos em Alpine 3.21. O pacote
`onetbb-dev` oferece somente `libtbb.so`, enquanto `build-base` oferece
`/usr/lib/libgomp.a`. Por isso os três jobs estáticos passam explicitamente
`OpenMP_gomp_LIBRARY=/usr/lib/libgomp.a`. Um build de validação no mesmo
container Alpine produziu um executável x86-64 totalmente estático e sem seção
dinâmica, com a ingestão OpenMP habilitada.

Validação do código final:

- Clang 21: 53/53 testes C++/JS;
- Clang 19: 53/53 testes C++/JS;
- GCC 14, com TBB desabilitada e OpenMP ativa: 53/53 testes C++/JS;
- Emscripten: target `indexador_wasm` compilado;
- Alpine 3.21/GCC 14/OpenMP: executável estático compilado e inspecionado;
- regressão nova: dois batches com 512 documentos, termos compartilhados e
  exclusivos, verificando continuidade de IDs, metadados, frequências e ausência
  de postings perdidos ou duplicados.

O runtime WASM adiciona 7 arquivos e aproximadamente 0,62 MiB, portanto a
entrega custom completa observada fica em cerca de 121,86 MiB e 1.289 arquivos.

### Comparação com o Pagefind presente em disco

O diretório `web/public/pagefind` contém exatamente 288.570 fragments, indicando
que representa o mesmo conjunto de páginas. Comparação dos artefatos atuais:

| Artefato | Tamanho lógico | Arquivos |
| --- | ---: | ---: |
| Pagefind | 820,21 MiB | 296.975 |
| Indexador custom + runtime | 121,86 MiB | 1.289 |

O custom ficou aproximadamente 6,73 vezes menor em bytes lógicos e usa cerca
de 230 vezes menos arquivos. Em espaço alocado, o Pagefind ocupa cerca de
1,50 GiB contra aproximadamente 124 MiB do índice custom, diferença ampliada
pelo custo de centenas de milhares de arquivos pequenos.

Detalhamento lógico:

- Pagefind fragments: 457,68 MiB em 288.570 arquivos;
- Pagefind postings/index: 353,97 MiB em 8.183 arquivos;
- Pagefind filters: 1,54 MiB em 51 arquivos;
- custom postings: 114,82 MiB em 1.222 arquivos;
- custom documentos: 6,38 MiB em 58 arquivos.

### Feedback de progresso

Como o primeiro build integral ficava silencioso durante a ingestão, o builder
agora aceita:

```sh
--progress-every 10000
```

Esse é o default; `--progress-every 0` desativa as mensagens. O feedback é
emitido pelo processo Node e informa índice, páginas ingeridas, volume atual e
taxa média, sem escrever no stdout JSONL reservado ao RPC do indexador. Smoke
com intervalo 500 confirmou mensagens em 500, 1.000, 1.500 e 2.000 páginas.

## Benchmark de rede móvel e shards de documentos

Em 2026-08-10 o build integral foi servido por `astro preview` e medido com
Playwright/Chromium usando CDP `Network.emulateNetworkConditions`, sempre com
cache frio. Os perfis explícitos foram:

- 4G: 4 Mbps de download, 1 Mbps de upload e 80 ms de latência;
- 5G-like: 25 Mbps de download, 5 Mbps de upload e 20 ms de latência.

O frontend configurava o wrapper com páginas internas de 50 documentos, mas
mostrava 25. Assim, a primeira página materializava 50 documentos e descartava
25. Foi acrescentado `getRange(from, to)` ao wrapper WASM. Com o índice
unificado, `SearchPage.astro` agora pede exatamente a faixa visível e também
deixa de carregar páginas anteriores ao navegar para páginas seguintes.

Redução observada antes de mudar o tamanho dos shards:

| Consulta | Shards de documentos antes | Depois | Bytes antes | Depois |
| --- | ---: | ---: | ---: | ---: |
| João Crisóstomo | 12 | 5 | 1.352.688 | 576.080 |
| Trindade | 21 | 14 | 2.432.409 | 1.610.612 |
| Cle | 30 | 19 | 3.465.177 | 2.205.682 |
| cautio assertoris | 22 | 16 | 2.524.561 | 1.818.174 |

Para `Cle`, foram comparados builds integrais com 5.000, 1.000 e 500
documentos por shard, mantendo postings, `min_importance` e `keep_terms`
idênticos:

| Documentos/shard | Shards baixados | Bytes de documentos | 4G até ranking | 4G completa | 5G até ranking | 5G completa |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5.000 | 19 | 2.205.682 | 6,95 s | 11,21 s | 1,27 s | 2,05 s |
| 1.000 | 22 | 518.256 | 3,55 s | 7,83 s | 0,77 s | 1,59 s |
| 500 | 23 | 277.890 | 3,13 s | 7,40 s | 0,68 s | 1,48 s |

Com 500 documentos por shard, o índice integral usa 578 shards de documentos
e cresce apenas cerca de 450 KiB em relação ao build com 5.000. Esse valor foi
adotado para a busca principal. Testar 250 teria retorno pequeno: com 23 dos 25
resultados já em shards distintos, a economia máxima adicional seria próxima
de 139 KiB, ao custo de dobrar novamente a quantidade de arquivos.

## Roteamento direto aos blocos e template do OCR RAW

Em 2026-08-10 foi removido o hop que a busca fazia pelo manifest de cada
volume antes de descobrir o bloco que contém a página. O builder da busca grava
no URL interno de cada documento o parâmetro `mb`, por exemplo:

```text
viewer?doc=PL003&page=196&mb=meta%2FPL003-pages-004.json.gz
```

`SearchPage.astro` valida que o caminho pertence ao volume solicitado e carrega
diretamente esse bloco. Índices antigos, sem `mb`, continuam funcionando pelo
caminho anterior via `meta/<volume>.json.gz`. O viewer repete a validação antes
de consumir o parâmetro; `mb` é mantido no link de leitura porque permite abrir
o bloco preservado sem aguardar o manifest do volume.

O payload de cada bloco agora usa este contrato:

```json
{
  "raw_base_url": "https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/refs/heads/codex/teste/PL003/text",
  "pages": [
    {"page": 196, "raw": {"file": "dff72eb4-ac88-4008-91f9-8ea12e5a621c-196.txt"}}
  ]
}
```

`raw.url` continua aceito como override por página, mas não é repetido no caso
normal. Busca e viewer resolvem primeiro o override e, na ausência dele,
concatenam `raw_base_url` com `raw.file` codificado.

O material foi regenerado com o comando operacional:

```sh
python tools/render_publication_from_shards.py \
  --index data/shards/enrichment/index.json \
  --out web/public \
  --db data/patristica_keywords.db \
  --keywords-json web/public/dict/keywords.json \
  --resumos-db data/patristica_resumos.db \
  --related-topk 7 \
  --page-block-size 50
```

Auditoria integral dos artefatos:

| Métrica | Resultado |
| --- | ---: |
| Blocos de páginas | 5.967 |
| Páginas | 288.570 |
| Blocos com `raw_base_url` | 5.967 |
| Páginas somente com `raw.file` | 288.570 |
| Páginas com URL redundante ou RAW ausente | 0 |
| Tamanho de `web/public/meta` | 212.234.418 bytes |

O diretório de metadados ocupava 214.466.558 bytes antes do template. A redução
foi de 2.232.140 bytes (aproximadamente 2,13 MiB ou 1,04%). O benefício maior é
o contrato menos redundante; os JSONs comprimidos já eliminavam boa parte da
repetição textual.

### Validação de rede

Na busca local por `Cle`, os 25 resultados visíveis referenciaram 25 blocos.
Antes do roteamento direto eram necessários 25 blocos mais 23 manifests de
volume: 48 requests de metadados e 942.823 bytes codificados. Depois:

| Recurso | Requests | Bytes codificados |
| --- | ---: | ---: |
| Shards do índice | 27 | 531.004 |
| Blocos de metadados | 25 | 931.763 |
| Manifests de volume | 0 | 0 |
| Total medido acima | 52 | 1.462.767 |

A medição foi feita com cache frio, latência de 150 ms, download de 200 KiB/s e
upload de 93,75 KiB/s. Do início da navegação até 25 links RAW renderizados
foram 23,94 s. O total exclui HTML, CSS, fontes e os arquivos fixos do runtime
WASM para isolar o custo variável da consulta. Os 23 hops removidos economizam
latência e contenção, embora apenas cerca de 11 KiB, pois os manifests eram
pequenos.

Validações executadas:

- auditoria de todos os 5.967 blocos, sem inconsistências;
- `pytest -q tests/test_render_publication_raw_urls.py`: 3 testes aprovados;
- `node --check tools/build_search_indexador_from_shards.mjs`;
- `npm run check`: 0 erros e 0 warnings;
- `npx astro build`: 865 páginas;
- Playwright desktop 1920x1080 e mobile 375x812;
- busca e viewer com 0 erros e 0 warnings no console;
- link RAW reconstruído e link do viewer sem exposição do parâmetro `mb`.

Evidências locais da validação visual:

- `.playwright-mcp/routing-clean-desktop.png`;
- `.playwright-mcp/routing-clean-mobile.png`;
- `.playwright-mcp/routing-clean-network.txt`;
- `.playwright-mcp/routing-viewer-network.txt`.

## Cache de navegação busca → viewer

O `Map` original de `dataCache.ts` memoizava promises somente durante a vida do
documento. Como a passagem de `/search` para `/viewer` é uma navegação completa,
esse cache era recriado e dependia da política de cache HTTP do navegador. O
import também estava apenas no frontmatter de `Layout.astro`, portanto não
instalava `window.pdfocrDataCache` no cliente; os componentes usavam seus
fallbacks locais.

O layout agora carrega o módulo no browser. A API
`rememberJsonForNavigation(url, value)` preserva em `sessionStorage`, por até dez
minutos, somente recursos escolhidos explicitamente. O limite é de três entradas
de no máximo 900.000 caracteres cada. A busca não persiste os 25 blocos
visíveis: ao preparar um link de leitura, guarda apenas:

- `volumes.json`;
- o bloco de 50 páginas que contém o resultado escolhido;
- o manifest do volume, quando hover ou foco dá tempo para o prefetch.

No clique/touch, volumes e bloco são gravados novamente para garantir que sejam
as entradas mais recentes. O viewer usa `mb` para consumir o bloco imediatamente
e apresenta título e resumo em um preview enquanto dicionários, segmentos e
snapshots terminam de carregar.

Validação com cache HTTP desativado e perfil de 4G (80 ms, 500 KiB/s):

| Cenário | Preview útil | Viewer completo | Requests de volumes/meta após o clique |
| --- | ---: | ---: | ---: |
| Busca → viewer, cache de navegação | 1,91 s | 10,73 s | 0 |
| Viewer aberto sem cache de sessão | 3,27 s | 10,58 s | 3 |

O preview ficou cerca de 42% mais rápido mesmo forçando o navegador a baixar
novamente HTML/CSS/JS. Com o cache HTTP normal, os assets estáticos também são
reaproveitados. O tempo do viewer completo continua dominado pelos metadados
auxiliares; a melhoria desta etapa é fazer a página útil aparecer antes deles.

A busca sem termo/filtro agora mostra um estado vazio explícito, em vez de uma
linha solta: ícone, título e orientação para pesquisar por autor, tema, obra,
passagem bíblica, palavra-chave ou filtros. O texto existe em PT, EN e IT.
