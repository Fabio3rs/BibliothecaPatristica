# Plano arquitetural de implementação das tools de pesquisa

## Objetivo

Evoluir as ferramentas de pesquisa do assistente para aceitar consultas simples e
variantes linguísticas sem obrigar modelos pequenos a escrever a micro-DSL do
Indexador.

O primeiro incremento deve:

- preservar as chamadas atuais com `query: string`;
- aceitar alternativas como array;
- tolerar `query` recebido como `string[]` no runtime;
- pesquisar português, latim ou outras formas sem tradução automática implícita;
- agregar os resultados sem duplicar páginas;
- explicar ao modelo qual camada foi pesquisada;
- manter OCR fora das buscas de descoberta;
- não exigir reconstrução dos índices publicados.

Este plano cobre `search_corpus`, `search_indices` e `get_page_ocr`. As tools
`search_scripture` e `list_volumes` continuam especializadas e não devem adotar o
mesmo contrato apenas por uniformidade.

## Estado atual relevante

### `search_corpus`

- Recebe uma única `query: string`.
- A consulta é combinada com coleção e volume e enviada ao Indexador.
- Pesquisa resumos automáticos, keywords e metadados publicados.
- Hidrata os resultados finais com o resumo PT-BR da página.
- Não pesquisa o OCR.
- O prompt exige que o modelo conheça `&&`, `||` e parênteses.

### `search_indices`

- Recebe uma única `query: string`.
- Pesquisa texto editorial original e traduções automáticas em pt-BR, inglês,
  italiano e francês.
- Filtra coleção ou volume depois da busca.
- Também expõe a micro-DSL ao modelo.

### `get_page_ocr`

- Recebe uma `query: string` opcional.
- Faz uma busca literal sem estrutura depois de achatar o OCR.
- Quando a consulta não existe, pode devolver o início da página.
- Não aceita variantes, não preserva caixas/parágrafos e não informa qual
  variante foi encontrada.

### Limitação da micro-DSL

No parser atual, termos separados por espaço são ligados por OR. Portanto:

```text
Spiritus Sanctus
```

significa aproximadamente:

```text
Spiritus OR Sanctus
```

e não uma frase nem a presença obrigatória dos dois termos. Isso deve permanecer
compatível para chamadas antigas, mas não deve ser o contrato principal ensinado
ao modelo.

## Decisão de contrato

### Schema apresentado ao modelo

O schema recomendado evita `oneOf` e outros unions no caminho principal:

```json
{
  "query": "processão do Espírito Santo",
  "alternatives": [
    "processio Spiritus Sancti",
    "processione Spiritus Sancti"
  ]
}
```

Semântica da primeira fase:

- `query` é a consulta principal;
- cada item de `alternatives` é outra consulta completa;
- as consultas são alternativas entre si;
- uma página encontrada por qualquer consulta pode ser candidata;
- páginas encontradas por várias consultas recebem essa informação, mas não uma
  falsa probabilidade;
- no máximo quatro consultas são executadas: uma principal e três alternativas.

O campo `query` continua sendo obrigatório nas tools de descoberta. Isso reduz a
chance de providers OpenAI-compatible rejeitarem o JSON Schema e deixa óbvio para
um modelo pequeno qual é a entrada mínima.

### Tolerância do runtime

Embora o schema recomende `query: string`, o normalizador aceita estas formas:

```json
{ "query": "Clemens" }
```

```json
{ "query": ["Clemens", "Clemente"] }
```

```json
{
  "query": "Clemente",
  "alternatives": ["Clemens", "Clemens Romanus"]
}
```

Todas são convertidas para:

```json
{
  "queries": ["Clemente", "Clemens", "Clemens Romanus"]
}
```

Essa tolerância serve para chamadas recuperadas, providers permissivos e código
interno. Ela não precisa aparecer como union no schema inicial.

### Por que não usar somente `query: string | string[]` no schema

É possível representar isso com `oneOf`, mas há três riscos:

1. providers OpenAI-compatible não implementarem o mesmo subconjunto de JSON
   Schema;
2. modelos pequenos hesitarem entre duas formas equivalentes;
3. ficar impossível explicar no nome do campo se o array significa OR, AND ou
   várias buscas independentes.

`query` mais `alternatives` torna a semântica explícita. Depois do harness, o
schema pode ser ampliado para anunciar também o array diretamente se os profiles
testados se comportarem bem.

## Normalização compartilhada

Criar um módulo puro:

```text
web/src/scripts/agent-query-spec.js
```

API proposta:

```js
normalizeQuerySet({ query, alternatives }, options)
```

Resultado:

```json
{
  "queries": [
    "processão do Espírito Santo",
    "processio Spiritus Sancti"
  ],
  "strategy": "rank_fusion",
  "truncated": false
}
```

Regras determinísticas:

1. aceitar string ou array em `query`;
2. acrescentar `alternatives` quando for array de strings;
3. aplicar Unicode NFC;
4. colapsar espaços;
5. remover entradas vazias;
6. deduplicar preservando a ordem;
7. limitar cada consulta a 200 caracteres;
8. limitar a quatro consultas e 600 caracteres totais;
9. rejeitar objetos, arrays aninhados e valores não textuais;
10. preservar o texto original para auditoria.

Não deve haver tradução automática no normalizador.

Normalizações ortográficas como ligaturas latinas, diacríticos e variantes de
OCR pertencem ao matcher de OCR, não à busca geral.

## Execução de múltiplas consultas

### Estratégia recomendada: buscas independentes e fusão por rank

Para cada consulta normalizada:

```text
query principal ────────┐
latim 1 ────────────────┼──> resultados por consulta
latim 2 ────────────────┘
                                  │
                                  ▼
                         deduplicação por URL
                                  │
                                  ▼
                         Reciprocal Rank Fusion
                                  │
                                  ▼
                        hidratação do top final
```

As buscas podem ser iniciadas com `Promise.all`. O runtime do Indexador já possui
cache de bytes e de requests pendentes, portanto shards compartilhados não devem
ser baixados novamente.

### Por que usar rank fusion

Scores de consultas diferentes não são necessariamente comparáveis. A fusão por
rank usa apenas a posição de cada documento:

```text
RRF(documento) = soma(1 / (k + rank_em_cada_consulta))
```

Começar com `k = 60`, mantendo o valor como constante testável. O score completo
fica em `data` para debug; o `modelContext` recebe somente `rank` e as consultas
que localizaram o candidato.

Critérios de desempate:

1. maior RRF;
2. maior número de consultas que encontraram o documento;
3. melhor posição individual;
4. URL em ordem lexical para determinismo.

### Paginação

Com uma única consulta, conservar `total` e paginação atuais.

Com múltiplas consultas, o total exato exigiria materializar todos os resultados.
Isso é inadequado no navegador. A tool deve retornar:

```json
{
  "total_is_lower_bound": true,
  "candidates_scanned": 80,
  "offset": 0,
  "limit": 5
}
```

Para cada consulta, ler uma janela limitada, inicialmente:

```text
scan_per_query = min(100, max(20, offset + limit * 4))
```

O uso agentic normal deve consumir apenas os primeiros candidatos. Paginação
exaustiva continua sendo responsabilidade da busca dedicada da interface, não da
tool do assistente.

## Comportamento por ferramenta

### `search_corpus`

Contrato proposto:

```json
{
  "query": "processão do Espírito Santo",
  "alternatives": ["processio Spiritus Sancti"],
  "collections": ["PL"],
  "volumes": ["PL016"],
  "offset": 0,
  "limit": 5
}
```

Política:

- usar português primeiro, pois a publicação atual pesquisa principalmente
  resumos e keywords PT-BR;
- usar latim ou outra língua quando o usuário fornecer a expressão, quando se
  tratar de nome/título/termo técnico ou quando a primeira recuperação for fraca;
- nunca alegar que o OCR foi pesquisado;
- aplicar coleção e volume igualmente a todas as consultas;
- hidratar metadados somente depois da fusão e deduplicação;
- não fazer um fetch de metadados por consulta para a mesma página.

### `search_indices`

Contrato proposto:

```json
{
  "query": "Clemente aos Coríntios",
  "alternatives": ["Clemens ad Corinthios"],
  "collection": "PG",
  "limit": 5
}
```

Política:

- lembrar que cada registro já contém original e traduções em quatro idiomas;
- não orientar o modelo a produzir PT, EN, IT e FR para toda consulta;
- acrescentar o original apenas quando ele melhorar precisão ou recall;
- filtrar o escopo de cada lista antes da fusão;
- deduplicar pelo identificador/URL canônico do item de índice.

### `get_page_ocr`

O OCR não precisa de RRF nem de vários fetches. O arquivo é buscado uma vez e
todas as consultas são avaliadas sobre os blocos/parágrafos já parseados.

Contrato intermediário:

```json
{
  "volume_id": "PL016",
  "page": 250,
  "mode": "relevant_excerpt",
  "query": "fermento dos fariseus",
  "alternatives": [
    "fermentum Pharisaeorum",
    "doctrina Pharisaeorum"
  ],
  "max_chars": 2500
}
```

Semântica:

- cada consulta é uma âncora alternativa;
- vence o parágrafo com melhor matching entre todas as âncoras;
- o resultado informa qual consulta venceu;
- `matched: false` não retorna o início da página como se fosse um match;
- `mode: full_page` não exige query e usa cursor quando necessário.

Resultado resumido:

```json
{
  "matched": true,
  "matched_query": "fermentum Pharisaeorum",
  "match_strategy": "normalized_token_coverage",
  "coverage": 1,
  "scripts_detected": ["latino"],
  "locator": {
    "block_id": 2,
    "paragraph_id": 1
  },
  "text": "...",
  "complete": true
}
```

O matcher estruturado depende do parser/limpeza compartilhada do OCR descrito no
documento principal da arquitetura.

## Saída rica e `modelContext`

### `data` para UI e inspector

```json
{
  "query_execution": {
    "requested": ["...", "..."],
    "executed": ["...", "..."],
    "strategy": "rrf",
    "scan_per_query": 20
  },
  "items": [
    {
      "rank": 1,
      "rrf_score": 0.0325,
      "matched_query_indexes": [0, 1],
      "matched_terms": ["..."],
      "source_key": "page:PL016:250"
    }
  ]
}
```

### `modelContext` compacto

```json
{
  "searched_layer": "automated_summary_and_metadata",
  "ocr_searched": false,
  "queries_used": [
    "processão do Espírito Santo",
    "processio Spiritus Sancti"
  ],
  "candidates": [
    {
      "rank": 1,
      "source_id": "s1",
      "volume_id": "PL016",
      "page": 250,
      "found_by": [0, 1],
      "summary": "..."
    }
  ]
}
```

Para `search_indices`:

```json
{
  "searched_layer": "editorial_index_original_and_translations",
  "ocr_searched": false
}
```

Para OCR:

```json
{
  "searched_layer": "automatic_page_ocr",
  "evidence_kind": "automatic_ocr",
  "supports": ["reading", "ocr_quotation"]
}
```

## Evolução opcional: grupos conceituais

Arrays simples resolvem sinônimos e variantes de idioma, mas não representam de
forma explícita:

```text
(Clemente OR Clemens) AND (Coríntios OR Corinthios)
```

Uma segunda versão pode acrescentar:

```json
{
  "query": "Clemente",
  "alternatives": ["Clemens"],
  "required_concepts": [
    {
      "any_of": ["Coríntios", "Corinthios"]
    }
  ]
}
```

Semântica:

- `query + alternatives` formam o primeiro grupo OR;
- cada item de `required_concepts` é outro grupo OR;
- todos os grupos são obrigatórios.

Essa fase não deve ser implementada apenas concatenando strings da DSL. O caminho
correto é adicionar ao runtime do Indexador uma busca estruturada que:

1. tokenize cada variante com o tokenizer WASM já usado pelo índice;
2. combine tokens de uma mesma variante com AND;
3. combine variantes do mesmo conceito com OR;
4. combine conceitos com AND;
5. avalie a AST diretamente;
6. preserve scoring e prefix matching de forma testada.

Isso evita que espaços, stopwords ou caracteres reservados mudem silenciosamente
a semântica.

Como o runtime publicado é exportado do projeto PatrologiaIndexer, essa mudança
deve nascer na fonte do runtime e depois ser exportada para
`web/public/indexador/runtime`. Não se recomenda manter um fork manual apenas no
artefato publicado.

## Compatibilidade e migração

### Chamadas antigas

Estas continuam válidas:

```json
{ "query": "(Clemente || Clemens) && Corinthios" }
```

O runtime detecta sintaxe booleana explícita e usa o caminho legado.

### Chamadas novas

O prompt deixa de ensinar a DSL e passa a recomendar:

```json
{
  "query": "Clemente Corinthios",
  "alternatives": ["Clemens Corinthios"]
}
```

Na primeira fase, cada string ainda segue a busca normal atual do Indexador. A
mudança elimina a necessidade de o modelo construir a expressão OR entre idiomas,
mas não promete busca de frase exata.

### Remoção futura da DSL

A DSL pode continuar aceita indefinidamente pelo runtime. Só deve ser removida
das descrições visíveis ao modelo depois que:

- string simples;
- alternativas;
- grupos conceituais;
- filtros;
- paginação;

estiverem cobertos por testes e pelo harness dos modelos de referência.

## Prompt recomendado

Substituir a regra que ensina os símbolos da DSL por algo próximo de:

```text
Para uma busca normal, preencha query com uma expressão curta. Use alternatives
somente para sinônimos, grafias ou formas originais que possam encontrar material
diferente. Não traduza automaticamente a consulta para todos os idiomas.

Em search_corpus, comece em pt-BR porque a busca usa resumos e palavras-chave
automáticos. Em search_indices, o mesmo registro já inclui texto original e
traduções. Em get_page_ocr, use palavras esperadas no idioma da página; se o
idioma for desconhecido ou a página inteira for necessária, use full_page.

Se a primeira busca não produzir candidatos adequados, faça no máximo duas
reformulações. Não repita a mesma lista de consultas.
```

As descrições das tools devem conter um exemplo simples e um exemplo com
`alternatives`, sem exemplos longos da DSL.

## Limites e segurança

- Máximo de 4 consultas por chamada.
- Máximo de 200 caracteres por consulta.
- Máximo de 600 caracteres somados.
- Máximo de 100 candidatos lidos por consulta para fusão.
- Hidratação apenas do top final.
- Nenhum prefetch automático de OCR.
- Nenhuma tradução automática silenciosa.
- Conteúdo de OCR continua sendo dado não confiável.
- Consultas vazias ou somente com operadores retornam `invalid_arguments`.
- Falha de uma busca alternativa não transforma resultados das outras em erro;
  `partial: true` registra a falha para debug.
- Se todas falharem, a tool retorna erro estruturado.

## Plano de implementação por fases

### Fase 0 — baseline e fixtures

Objetivo: congelar o comportamento atual antes de alterar contratos.

Trabalho:

- criar fixtures pequenas de resultados do Indexador;
- registrar casos PT-BR, latim, grego e PO multilíngue;
- registrar o top atual de consultas simples;
- adicionar teste que demonstra que espaços atualmente significam OR.

Saída:

- baseline reproduzível sem varrer o corpus;
- conjunto inicial do harness agentic.

### Fase 1 — normalizador de consultas

Arquivos:

- novo `web/src/scripts/agent-query-spec.js`;
- novo `web/tests/agent-query-spec.test.mjs`.

Trabalho:

- implementar string, array tolerado e `alternatives`;
- implementar NFC, espaços, dedup e budgets;
- produzir chave canônica estável;
- testar entradas inválidas e truncamento.

Não altera a UI nem exige rebuild do índice.

### Fase 2 — múltiplas consultas nas buscas de descoberta

Arquivos:

- `web/src/scripts/agent-chat-tools.js`;
- novo `web/tests/agent-chat-tools.test.mjs`;
- possivelmente helpers de fixtures do Indexador.

Trabalho:

- aplicar o normalizador em `search_corpus` e `search_indices`;
- executar consultas em paralelo com limite conhecido;
- implementar RRF e deduplicação determinística;
- preservar o caminho atual para uma única string;
- hidratar somente o top combinado;
- informar totais aproximados no modo multi-query;
- adicionar `alternatives` aos schemas;
- tolerar `query` array no executor.

Critério de aceite:

- uma chamada com string mantém o comportamento anterior;
- uma chamada PT-BR + latim devolve a união deduplicada;
- filtros de coleção/volume valem para todas as alternativas;
- uma página comum a duas consultas é hidratada uma única vez.

### Fase 3 — resultados compactos e política linguística

Arquivos:

- `web/src/scripts/agent-chat-tools.js`;
- `web/src/i18n/pt-br.ts`;
- `web/src/i18n/en.ts`;
- `web/src/i18n/it.ts`;
- `web/src/i18n/fr.ts`;
- testes de paridade i18n.

Trabalho:

- produzir `modelContext` em ambas as buscas;
- declarar `searched_layer` e `ocr_searched`;
- ocultar RRF e scores crus do modelo;
- substituir a regra da DSL pela política de consultas e idiomas;
- manter a DSL apenas como compatibilidade interna.

### Fase 4 — variantes no OCR estruturado

Dependências:

- parser compartilhado de `<pagina>/<bloco>`;
- limpeza de hífens por caixa;
- segmentação em parágrafos.

Arquivos prováveis:

- novo `web/src/scripts/ocr-text.js`;
- `web/src/scripts/agent-chat-tools.js`;
- testes unitários do parser e matcher.

Trabalho:

- buscar o OCR uma vez;
- avaliar todas as consultas;
- exact, normalizado, cobertura de tokens, proximidade e fuzzy;
- retornar melhor match e diagnósticos;
- implementar `matched: false` real;
- retornar `scripts_detected`, bloco, parágrafo e coverage;
- acrescentar `mode` e cursor.

### Fase 5 — grupos conceituais, se o harness justificar

Arquivos:

- fonte do runtime PatrologiaIndexer;
- export de `web/public/indexador/runtime`;
- `agent-query-spec.js`;
- schemas das tools;
- testes do runtime e do frontend.

Trabalho:

- adicionar `searchStructured(groups)` ao runtime;
- avaliar AST diretamente;
- acrescentar `required_concepts` ao schema;
- comparar recall, precisão, shards lidos e latência contra RRF.

Esta fase não bloqueia string + alternativas.

### Fase 6 — cache/deduplicação por turno

Depois que a forma canônica da consulta estiver estável:

- usar a chave canônica no cache de tool calls do turno;
- repetir uma chamada devolve o resultado anterior com `reused: true`;
- falhas não entram no cache de sucesso;
- o cache morre no fim do turno;
- resultados completos continuam disponíveis no inspector.

Essa fase se integra à separação maior entre turn state e conversation state,
mas não deve atrasar a melhoria das tools de pesquisa.

## Testes necessários

### Normalização

- `query` string;
- `query` array tolerado;
- `query + alternatives`;
- duplicatas com espaços diferentes;
- Unicode NFC/NFD;
- array vazio;
- tipos inválidos;
- limite de itens e caracteres;
- ordem determinística.

### Fusão

- candidatos disjuntos;
- candidato comum a duas consultas;
- empate determinístico;
- uma alternativa sem resultado;
- uma alternativa com erro;
- filtros aplicados antes da fusão;
- hidratação única por página;
- `total_is_lower_bound` no modo multi-query.

### Idiomas

- consulta PT-BR suficiente em `search_corpus`;
- PT-BR com fallback latino;
- consulta latina fornecida pelo usuário;
- título latino em `search_indices`;
- tradução PT-BR do mesmo índice;
- grego com e sem diacríticos;
- página PO em que latim não é uma expansão adequada;
- nome próprio com grafias em mais de um idioma.

### OCR

- uma variante exata encontrada;
- primeira variante ausente e segunda encontrada;
- variantes em blocos diferentes;
- consulta inexistente;
- hífen reparado dentro da caixa;
- hífen final da caixa preservado;
- script latino, grego e siríaco detectado;
- cursor em OCR longo.

### Agentic harness

- modelo escolhe somente `query` quando basta;
- modelo acrescenta latim apenas quando útil;
- modelo reformula depois de resultado fraco;
- modelo não traduz para quatro idiomas sem necessidade;
- modelo lê OCR depois de selecionar candidato;
- modelo não afirma que `search_corpus` pesquisou o original;
- comparação E4B, 12B e 31B Cloud, com thinking conforme o profile.

## Comandos de validação previstos

Durante a implementação:

```bash
cd web
node --test tests/agent-query-spec.test.mjs
node --test tests/agent-chat-tools.test.mjs
npm run test:chat
npm run test:i18n
npm run check
npm run build
```

Mudanças no runtime do Indexador também exigem os testes próprios do projeto de
origem e reexportação dos assets. Não é necessário regenerar o índice de conteúdo
para as fases 1 a 4.

## Fora de escopo deste incremento

- tradução automática de consultas no runtime;
- embeddings online para pesquisar uma página;
- indexar todo o OCR na busca geral;
- substituir `search_scripture`;
- usar a janela de 256K como orçamento normal (a política implementada mira
  128K e reserva os 32K finais);
- remover o source registry conversacional ou a compactação em épocas já
  implementados no runtime;
- alterar o componente visual do chat antes dos contratos estarem estáveis.

## Pontos para revisão

1. Nome público do array: `alternatives` ou `query_variants`.
   Recomendação: `alternatives`, por explicitar OR sem sugerir idioma.
2. Máximo de consultas por chamada.
   Recomendação inicial: quatro.
3. Fusão por RRF ou uma expressão OR única.
   Recomendação: RRF, porque mantém cada consulta observável e não compara scores
   crus.
4. Anunciar `query: string[]` no schema imediatamente.
   Recomendação: aceitar no runtime, mas anunciar somente depois dos testes de
   compatibilidade.
5. Implementar grupos conceituais já na primeira versão.
   Recomendação: não; medir primeiro string + alternativas.
6. Consulta simples com várias palavras deve permanecer OR ou mudar para AND.
   Recomendação: manter o comportamento atual na fase 1 e decidir a mudança junto
   do `searchStructured`, evitando uma alteração silenciosa de recall.
7. Quantas reformulações por pergunta.
   Recomendação: no máximo duas, impostas pelo runtime/prompt.

## Sequência recomendada

```text
baseline pequeno
  -> normalizador string/array
  -> alternatives + RRF em search_corpus/search_indices
  -> modelContext + prompt sem DSL
  -> OCR estruturado com múltiplas âncoras
  -> harness
  -> decidir se required_concepts/searchStructured é necessário
  -> cache canônico por turno
```

O resultado inicial já permite ao agente pesquisar uma formulação em português e
uma ou duas expressões originais na mesma chamada. A parte mais sofisticada só é
adicionada se os testes mostrarem que sinônimos por OR não resolvem os casos reais.
