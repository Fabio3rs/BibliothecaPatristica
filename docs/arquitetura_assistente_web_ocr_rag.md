# Proposta de arquitetura do assistente web: OCR, recuperação e citações

Status: proposta para discussão e implementação incremental.

## Objetivos

- Corrigir hífens introduzidos por quebras de linha no viewer sem unir texto entre caixas diferentes.
- Facilitar a cópia do texto de cada caixa do OCR.
- Tornar inequívoco para modelos pequenos quando usar metadados, resumos, índices, KNN ou OCR.
- Reduzir chamadas manuais e contexto desnecessário durante pesquisas em múltiplas páginas.
- Produzir respostas apoiadas por fontes verificáveis e locators estáveis.

## Princípios

1. A limpeza visual e textual deve respeitar os limites das caixas do OCR.
2. O runtime e as ferramentas devem executar operações mecânicas; o modelo deve tomar apenas decisões semânticas pequenas.
3. Metadados e resumos servem para descoberta e triagem. O OCR serve como evidência textual.
4. Recuperação adicional deve acontecer apenas quando necessária.
5. O modelo não deve construir identificadores de fonte a partir de memória ou texto livre.
6. Resultados completos para a interface e contexto compacto para o modelo devem ser separados.

## Viewer: limpeza de hífens e cópia

### Escopo da correção de hífens

A correção deve ser aplicada separadamente ao conteúdo de cada caixa. Uma quebra como:

```text
inter-
pretação
```

torna-se `interpretação` somente quando as duas partes estão dentro da mesma caixa.

Não se deve unir texto entre caixas. Se a caixa terminar com hífen, o hífen deve sobreviver, pois indica que o texto pode continuar em outra região:

```text
fim da caixa: inter-
```

Uma expressão de referência compatível com letras Unicode é:

```js
/(\p{L})-[\t\f\v ]*\r?\n[\t\f\v ]*(?=\p{L})/gu
```

Ela deve ser aplicada após separar os blocos e antes de achatar o texto para exibição ou busca.

### Botão de cópia

Cada caixa pode ter um botão discreto no canto superior direito. O botão deve:

- copiar apenas o texto limpo daquela caixa;
- usar o mesmo resultado de limpeza exibido no viewer;
- oferecer feedback curto de sucesso ou erro;
- ter rótulo acessível e funcionar por teclado;
- não alterar a seleção manual do texto.

## Contexto explícito da página atual

Uma ferramenta chamada `get_current_page_ocr` é ambígua fora do viewer. Em vez disso, o frontend deve informar explicitamente o contexto da rota:

```json
{
  "page_context": {
    "kind": "viewer",
    "volume_id": "PL016.03",
    "page": 100
  }
}
```

Fora do viewer:

```json
{
  "page_context": {
    "kind": "none",
    "route": "search"
  }
}
```

Regra sugerida para o prompt:

```text
As expressões "esta página", "aqui" e "este texto" referem-se a
page_context somente quando page_context.kind == "viewer".

Quando page_context.kind == "none":
- não invente volume ou página;
- use a busca do corpus quando houver assunto, autor ou obra pesquisável;
- peça ao usuário que indique ou abra uma página somente quando não houver
  informação suficiente para pesquisar.
```

A ferramenta pública pode continuar genérica: `get_page_metadata(volume_id, page, ...)`.

## Contrato proposto para leitura de página

Um enum é mais claro para modelos pequenos do que a combinação de `with_ocr` e `full_page`:

```json
{
  "volume_id": "PL016.03",
  "page": 100,
  "ocr_mode": "none | relevant_excerpt | full_page",
  "query": "opcional",
  "max_chars": 3000,
  "cursor": "opcional"
}
```

Semântica:

- `none`: retorna metadados, resumos, palavras-chave e referências de páginas relacionadas.
- `relevant_excerpt`: pesquisa a consulta no OCR e retorna um trecho delimitado semanticamente.
- `full_page`: retorna a página inteira, eventualmente em partes com cursor.

`with_ocr` pode ser mantido como alias de compatibilidade, mas não deve ser o contrato principal apresentado ao modelo.

Para respostas parciais:

```json
{
  "coverage": {
    "complete": false,
    "returned_chars": 5000,
    "total_chars": 11840,
    "next_cursor": "..."
  }
}
```

Isso impede que o modelo trate um recorte inicial como se fosse o OCR completo.

## Busca de uma consulta dentro do OCR

Não é necessário criar embeddings para pesquisar uma única página. Uma página é pequena o bastante para busca lexical estruturada.

### Preparação

1. Parsear o OCR preservando caixas ou blocos.
2. Aplicar a limpeza e a correção de hífens dentro de cada caixa.
3. Dividir cada caixa em parágrafos, preservando `block_id` e `paragraph_id`.
4. Manter duas representações:
   - texto limpo original, usado na resposta e na citação;
   - texto normalizado, usado somente para busca.

Normalização de busca sugerida:

- Unicode NFC;
- casefold;
- espaços normalizados;
- hífens de quebra já reparados;
- fallback opcional sem diacríticos e com ligaturas expandidas.

A forma sem diacríticos não deve substituir o texto original e não deve ser a primeira estratégia, especialmente para grego.

### Estratégia de correspondência

Pesquisar em cascata:

1. frase exata no texto normalizado;
2. cobertura de palavras da consulta;
3. bônus por ordem e proximidade das palavras;
4. trigramas ou distância de edição como fallback para erros de OCR.

Distância de edição deve ser calculada sobre parágrafos ou janelas próximas ao tamanho da consulta, nunca contra a página inteira.

### Recorte

O resultado deve conter o parágrafo inteiro, e não uma janela arbitrária de caracteres. Quando necessário, pode incluir os parágrafos imediatamente anterior e posterior.

```json
{
  "volume_id": "PL016.03",
  "page": 100,
  "query": "processão do Espírito Santo",
  "match_kind": "token_proximity",
  "score": 0.91,
  "block_id": 4,
  "paragraph_id": 2,
  "text": "...parágrafo completo...",
  "before": "...parágrafo anterior...",
  "after": "...parágrafo seguinte..."
}
```

Quando nenhuma correspondência for encontrada, a ferramenta deve dizer isso explicitamente. Ela não deve retornar silenciosamente o começo da página como se fosse uma correspondência.

## Pipeline de pesquisa no corpus

Fluxo recomendado:

```text
pergunta do usuário
  -> busca no corpus ou nos índices
  -> top 3-5 candidatos com resumos hidratados
  -> avaliação de relevância
  -> OCR de 1-3 candidatos selecionados
  -> resposta com citações verificadas
```

### Descoberta

O resultado entregue ao modelo deve ser compacto e estável:

```json
{
  "candidates": [
    {
      "candidate_id": "c1",
      "volume_id": "PL016.03",
      "viewer_page": 100,
      "printed_page": "85",
      "work": "...",
      "page_summary": "...",
      "why_retrieved": "consulta encontrada no resumo"
    }
  ],
  "next_actions": [
    {
      "action": "read_candidate",
      "candidate_id": "c1",
      "call": {
        "tool": "get_page_metadata",
        "arguments": {
          "volume_id": "PL016.03",
          "page": 100,
          "ocr_mode": "relevant_excerpt",
          "query": "..."
        }
      }
    }
  ]
}
```

`next_actions` reduz erros de reconstrução dos argumentos e incentiva chamadas subsequentes quando ainda falta evidência.

Ao pesquisar entradas de índices de obras, a ferramenta deve hidratar em paralelo os resumos das 3-5 páginas mais promissoras. Deve-se distinguir sempre:

- `viewer_page`: página digitalizada usada para fetch e navegação;
- `printed_page`: página impressa ou locator editorial.

### Seleção

Instrução sugerida:

```text
Selecione no máximo três candidate_id cujos page_summary respondam à intenção
do usuário. Se nenhum for suficiente, pesquise novamente com termos
alternativos. Se o usuário pedir leitura, citação, tradução ou confirmação do
texto, obtenha o OCR dos candidatos selecionados antes de responder.
```

### Evidência

OCR deve ser carregado somente para os candidatos selecionados. Carregar OCR de todos os resultados aumenta latência, ocupa contexto e pode reduzir a precisão de modelos pequenos.

## Páginas relacionadas por KNN

O KNN atual armazena `volume`, `page` e `distance`. Como a vizinhança é calculada a partir dos embeddings dos resumos de página:

- resumos são suficientes para explicar a relação temática estimada;
- OCR é necessário para confirmar a relação no texto original;
- a distância não deve ser apresentada como probabilidade ou confiança calibrada.

Extensão proposta:

```json
{
  "expand_related": "none | summaries",
  "related_limit": 3
}
```

Com `summaries`, a ferramenta busca os metadados dos vizinhos em paralelo e devolve:

```json
{
  "related_pages": [
    {
      "rank": 1,
      "volume_id": "PG161",
      "page": 416,
      "basis": "automatic_page_summary_knn",
      "summary": "..."
    }
  ]
}
```

Pode-se também iniciar prefetch dos metadados em background para aquecer o cache. Prefetch sozinho, porém, não elimina a chamada seguinte do modelo; por isso a expansão explícita dos resumos é mais importante.

Não se recomenda prefetch automático dos OCRs relacionados.

## Chamadas em múltiplas rodadas

O prompt deve tornar a continuação da pesquisa uma obrigação quando ainda falta evidência, e não apenas uma possibilidade:

```text
Não responda com base apenas em identificadores de resultados.

Continue chamando ferramentas enquanto ocorrer uma destas condições:
- a pergunta pede o que uma página diz e você ainda possui somente metadados;
- a resposta exige citação, tradução ou verificação e você ainda não leu o OCR;
- a busca retornou candidatos, mas nenhum resumo sustenta claramente a resposta;
- uma página relacionada parece relevante, mas a relação ainda não foi verificada.

Pare quando houver evidência suficiente ou quando as ferramentas indicarem que
não foi encontrada evidência. Nesse último caso, declare a limitação.
```

Exemplos completos de duas ou três rodadas devem acompanhar as descrições das ferramentas. Para modelos pequenos, exemplos tendem a ser mais confiáveis do que regras abstratas isoladas.

## Citações

### Identidade interna e apresentação

O modelo deve continuar citando identificadores internos registrados, por exemplo `[s1]`. O renderer pode apresentar um locator humano:

```text
[PL016.03,100]
```

Fluxo:

```text
modelo escreve [s1]
  -> registro resolve s1 como page:PL016.03:100
  -> interface mostra [PL016.03,100]
```

Isso impede que o modelo invente citações visualmente válidas.

O número simples deve ser documentado como página digitalizada. Quando for necessário informar também a paginação impressa:

```text
[PL016.03, dig.100; ed.85]
```

### Trecho citado

Não se recomenda colocar a frase dentro do identificador, como `[PL016.03,100,"frase"]`. Vírgulas, aspas, grego e variações do OCR tornam esse formato frágil.

O trecho pode ser armazenado separadamente:

```json
{
  "source_key": "page:PL016.03:100",
  "locator": {
    "block_id": 4,
    "paragraph_id": 2
  },
  "quote": "...",
  "start_offset": 830,
  "end_offset": 914
}
```

No futuro, o clique na citação pode abrir o viewer e destacar a caixa ou o parágrafo correspondente.

## Controle do contexto entregue ao modelo

Separar dois formatos de resultado:

- `data`: estrutura rica para interface, depuração, cache e registro de fontes;
- `modelContext`: representação curta e inequívoca enviada ao modelo.

Orçamento inicial sugerido:

- 3-5 candidatos;
- 400-700 caracteres de resumo por candidato;
- no máximo 2-3 excertos de OCR;
- 1.200-2.500 caracteres por excerto;
- uma única página completa por vez.

Os resultados devem indicar sempre:

- de onde vieram;
- se contêm resumo automático ou OCR original;
- se o conteúdo está completo ou truncado;
- qual é a ação recomendada seguinte.

### Compactação conversacional em épocas

O histórico entre perguntas permanece append-only no percurso normal para
preservar o prefixo reutilizável pelo KV cache. O contexto da rota atual também é
encapsulado somente na nova mensagem do usuário, com o pedido demarcado, em vez
de ser inserido no início do prompt a cada chamada.

Para Gemma 4, o perfil usa a janela declarada de 256K como teto e uma histerese
em torno do alvo de 128K:

- 112K: início da zona de pressão, sem reescrever o histórico;
- 128K: alvo nominal após redução;
- 144K: gatilho de compactação;
- 224K: limite operacional duro, reservando 32K para tools e saída.

Ao cruzar 144K, o runtime simula uma cascata antes de fazer um único commit:

1. remove pares completos antigos de `assistant.tool_calls` + mensagens `tool`;
2. se o resultado ainda exceder 128K, resume o prefixo antigo em um checkpoint;
3. preserva os dois turnos de usuário mais recentes literalmente;
4. inclui somente IDs de fonte válidos em um ledger compacto;
5. substitui o prefixo uma única vez, incrementando a época de contexto.

Assim, a compactação causa uma invalidação deliberada do prefixo apenas quando
necessária, em vez de produzir uma invalidação por turno. O `sourceRegistry` é
estado da conversa: um `source_id` nunca muda de significado até “Nova conversa”.

## Matriz de decisão para o modelo

| Intenção do usuário | Primeira fonte | Quando buscar OCR |
| --- | --- | --- |
| Visão geral da página | Metadados e resumo | Se pedir confirmação textual |
| "O que esta página diz?" | Página atual com OCR | Imediatamente |
| Tema em todo o corpus | Busca e resumos dos candidatos | Após selecionar candidatos |
| Entrada em índice de obra | Índice e resumos hidratados | Para ler páginas-alvo |
| Por que duas páginas são relacionadas? | Resumos usados pelo KNN | Para comprovar no original |
| Citação, tradução ou transcrição | OCR | Sempre antes da resposta |
| Fora do viewer com "esta página" | Nenhuma página implícita | Pesquisar ou pedir referência |

## Ordem de implementação sugerida

1. Consolidar uma função compartilhada de limpeza do OCR por caixa.
2. Aplicar a mesma função ao viewer, à cópia e às ferramentas do assistente.
3. Substituir o recorte por caracteres por busca estruturada em parágrafos.
4. Adicionar `ocr_mode` e informação de cobertura ao contrato de página.
5. Criar `modelContext` compacto nas ferramentas.
6. Hidratar automaticamente os principais candidatos e vizinhos KNN.
7. Adicionar `next_actions` e exemplos de chamadas em múltiplas rodadas.
8. Manter citações internas verificadas e exibir locators `[volume,página]`.
9. Adicionar cursor para OCRs extensos.
10. Medir qualidade e custo com um conjunto pequeno de perguntas representativas.

## Casos de teste recomendados

- palavra hifenizada dentro de uma caixa;
- caixa que termina com hífen;
- duas caixas em que a primeira termina com hífen;
- consulta exata com diferenças de caixa e espaços;
- consulta com diacríticos;
- consulta com erro típico de OCR;
- consulta que não existe na página;
- OCR maior que `max_chars`;
- pergunta dêitica dentro e fora do viewer;
- volume com subvolume, como `PL016.03`;
- diferenças entre página digitalizada e impressa;
- KNN com vizinhos no mesmo bloco de metadados;
- resposta que exige duas chamadas de ferramenta;
- tentativa do modelo de citar uma fonte não registrada.

## Referências conceituais

- [Toolformer: Language Models Can Teach Themselves to Use Tools](https://arxiv.org/abs/2302.04761)
- [Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection](https://arxiv.org/abs/2310.11511)
- [Corrective Retrieval Augmented Generation](https://arxiv.org/abs/2401.15884)

Essas referências sustentam, em linhas gerais, o uso de APIs simples, recuperação adaptativa e avaliação do material recuperado antes da geração. A proposta acima procura aplicar esses princípios sem introduzir um framework novo e mantendo compatibilidade com a arquitetura JavaScript existente.
