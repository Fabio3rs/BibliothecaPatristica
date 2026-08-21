# Benchmark de resumos: guardrails, hints e fac-símile

Data do estudo: 2026-08-24

## Escopo

Este documento registra o estudo feito com o playground isolado de prompts de
resumo. O playground lê os bancos de produção em modo somente leitura e grava
os experimentos exclusivamente em:

- `data/playgrounds/resumo_prompt_playground.db`

O código analisado está em:

- `scripts/playgrounds/resumo_prompt_playground.py`
- `resumo_serial.py`, usado como baseline legado

As cinco páginas usadas no benchmark foram:

- `PG001:175`: início das *Duas Epístolas às Virgens*;
- `PG020:27`: início do Livro I da *História Eclesiástica*;
- `PG043:166`: abertura da versão antiga de *Libri de XII Gemmis*;
- `PG079:685`: início da dissertação de Suaresii sobre as obras de São Nilo;
- `PL046:475`: fim de sermão, epístola a Pio VII e início do prefácio do editor.

Cada combinação completa foi chamada três vezes para observar estabilidade.

## Experimentos executados

| Modelo | Situação |
|---|---:|
| `gpt-5.4-nano` | 60/60 concluídos |
| `gemma4:cloud` | 60/60 concluídos |
| `gpt-5.4-mini` | 60/60 concluídos |
| `qwen3.5:397b-cloud` | 57/60 concluídos; três timeouts no baseline |
| `gpt-5-mini` | duas tentativas incompletas; quatro respostas concluídas, quase todas as demais com timeout |

O banco continha 259 execuções registradas, sendo 241 concluídas e 18 com erro.
O `PRAGMA quick_check` retornou `ok` e o banco estava em modo WAL.

## Resultado principal

O prompt estruturado resolveu o problema mais grave encontrado no prompt
legado:

- o baseline classificou incorretamente 15 de 57 respostas concluídas como
  `Conteúdo administrativo`;
- nas 180 respostas estruturadas não houve nenhuma classificação
  administrativa;
- a detecção de transição foi de 48/60 somente com guardrails, 55/60 com hints
  de índices e 57/60 com hints, OCR saneado e fac-símile.

O prompt legado não é adequado para páginas editoriais, bibliográficas ou de
abertura de obra.

## Guardrails do prompt estruturado

O `STRUCTURED_SYSTEM_PROMPT` instrui o modelo a atuar como editor de metadados
de uma biblioteca patrística e a produzir material útil para leitura humana,
busca lexical e similaridade semântica, usando apenas as evidências fornecidas.

Os guardrails injetados são:

1. Responder em português do Brasil, preservando entre parênteses títulos,
   nomes e termos técnicos originais quando isso ajudar na identificação.
2. Reconhecer que prefácios, dedicatórias, páginas de título, bibliografias,
   índices, listas de obras e aparatos críticos possuem conteúdo descritível.
3. Usar `page_kind="administrative"` somente para página vazia, encadernação,
   aviso técnico de digitalização ou ruído sem conteúdo bibliográfico,
   histórico, editorial, filosófico ou teológico recuperável.
4. Tratar pistas de catálogo como evidência auxiliar, nunca como licença para
   inventar. Um início indicado pelo índice deve ser confirmado no OCR ou
   fac-símile e a página pode também terminar a obra anterior.
5. Diferenciar autor principal, editor, tradutor, dedicatário e comentador.
6. Produzir `summary_display` legível no site e explicando o conteúdo novo.
7. Produzir `retrieval_text` autocontido, preservando nomes, obras, temas,
   argumentos, referências bíblicas e termos úteis para busca e KNN.
8. Manter `cumulative_summary` representando a obra atual, com no máximo 1.800
   caracteres, reiniciando-a quando houver mudança segura de obra.
9. Não usar Markdown, não comentar a qualidade do OCR e não expor raciocínio
   interno.
10. Retornar JSON puro no schema determinado.

### Taxonomia e schema solicitados

O campo `page_kind` aceita:

- `body`;
- `work_start`;
- `transition`;
- `preface`;
- `dedication`;
- `title_page`;
- `index`;
- `bibliography`;
- `critical_apparatus`;
- `administrative`;
- `illegible`;
- `other`.

O JSON solicitado possui:

```json
{
  "page_kind": "...",
  "author": "...",
  "work": "...",
  "contributors": ["nome — função"],
  "summary_display": "...",
  "retrieval_text": "...",
  "cumulative_summary": "...",
  "transition": {
    "detected": false,
    "previous_work": "",
    "new_work": ""
  },
  "administrative_reason": ""
}
```

OpenAI e vLLM recebem ainda `response_format={"type":"json_object"}`. O
Ollama recebe `format="json"`, `temperature=0.2` e `top_p=0.5`.

## Hints de catálogo

Nas variantes que usam índices, um objeto JSON é inserido em
`<catalog_hints>`. Cada obra candidata contém:

- `work_key`;
- `exact_physical_start`;
- `author_original`;
- `author_pt`;
- `title_original`;
- `title_pt`;
- `editorial_start_page`;
- `editorial_end_page`;
- `confidence`.

O envelope contém:

- `documento`;
- `pagina_fisica`;
- `exact_start_candidates`;
- `containing_work_candidates`;
- `exact_start_ambiguous`;
- `range_ambiguous`.

`exact_physical_start` é calculado pela igualdade entre o basename de
`works.start_file` e o `pagina_file` físico do OCR. Não depende da numeração
editorial. Um candidato abrangente é produzido quando `start_file` e
`end_file` podem ser resolvidos para páginas físicas e a página atual está
dentro do intervalo inclusivo.

Depois do JSON, o prompt informa que pode haver obras abrangentes ou
concorrentes, que `exact_physical_start` é um sinal forte de transição e que o
modelo ainda deve confirmar isso no conteúdo.

### Hints das páginas avaliadas

#### PG001:175

- Um início exato: `PG001:work:epistolae_duae_ad_virgines`.
- Autor: São Clemente I.
- Título traduzido: *Duas Epístolas às Virgens (siríaco e latim), traduzidas
  por D. Cl. Villecourt, Cardeal da S. R. E.*
- Confiança `0.7`.
- A mesma obra também aparece como candidato abrangente.
- Sem ambiguidade.

#### PG020:27

- Um início exato: *História Eclesiástica*, de Eusébio.
- Confiança `0.95`.
- Sem candidato abrangente resolvido.
- Sem ambiguidade.

#### PG043:166

- Um início exato: *Livros sobre doze gemas*, de Epifânio.
- Confiança `0.7`.
- Sem candidato abrangente resolvido.
- Sem ambiguidade.

#### PG079:685

- Dois candidatos exatos correspondentes à dissertação de Suaresii sobre as
  obras de São Nilo.
- Ambos com confiança `0.95`.
- `exact_start_ambiguous=true`.

#### PL046:475

- Epístola de Frangipane a Pio VII, confiança `0.94`.
- `Præloquium Editoris`, confiança `0.91`.
- Ambos aparecem como inícios exatos e candidatos abrangentes.
- `exact_start_ambiguous=true` e `range_ambiguous=true`.

## Outros dados injetados

O prompt estruturado também recebe:

- documento e página física;
- até 6.000 caracteres da síntese global da página anterior;
- autor detectado na página anterior;
- obra detectada na página anterior;
- até 30.000 caracteres do OCR atual;
- marcador explícito quando não há contexto anterior.

Os campos de resumo, autor e obra já existentes para a própria página são
armazenados no snapshot do playground, mas não são mostrados ao modelo. Isso
evita que a resposta antiga seja simplesmente copiada.

A variante `structured_index_hint_no_context` remove a síntese anterior, mas
ainda injeta `previous_author` e `previous_work`. Portanto, apesar do nome, ela
não está totalmente livre de contexto anterior.

## Hint e transporte do fac-símile

Na variante visual, a imagem da mesma página é anexada e o prompt informa:

> O fac-símile da mesma página está anexado. Use-o para conferir hierarquia
> visual, títulos, colunas e transições; não o descreva.

Essa variante também usa OCR saneado. Consequentemente, ela combina três
mudanças ao mesmo tempo:

- hints do catálogo;
- OCR saneado;
- fac-símile.

Ela não manda explicitamente preferir a imagem quando houver conflito com o
OCR. Por isso, não se pode assumir que todo modelo visual validará datas ou
impressos diretamente na imagem.

## Variantes do playground

| Variante | OCR | Índices | Síntese anterior | Metadados anteriores | Imagem |
|---|---|---:|---:|---:|---:|
| `baseline_legacy` | legado com normalização superficial | não | sim | sim | não |
| `structured_guardrails` | bruto | não | sim | sim | não |
| `structured_index_hint` | bruto | sim | sim | sim | não |
| `structured_index_hint_clean` | saneado | sim | sim | sim | não |
| `structured_index_hint_no_context` | saneado | sim | não | sim | não |
| `structured_index_hint_facsimile` | saneado | sim | sim | sim | sim |

Os experimentos registrados executaram apenas:

- `baseline_legacy`;
- `structured_guardrails`;
- `structured_index_hint`;
- `structured_index_hint_facsimile`.

Ainda não foi isolado experimentalmente o efeito do OCR saneado nem a remoção
da síntese anterior.

## Guardrails do baseline legado

O baseline usa o prompt atual de `resumo_serial.py`, que determina:

- resposta integralmente em português;
- latim e grego apenas para conceitos técnicos sem tradução;
- estilo telegráfico e frases nominais;
- máxima densidade;
- ignorar ruído e formatação do OCR;
- extrair exclusivamente o “sumo teológico”, argumentos filosóficos e linha
  narrativa;
- atualizar autor, obra e síntese quando detectar uma transição;
- retornar JSON com `autor`, `obra`, `traducao_compacta` e
  `sintese_acumulada`;
- para índice, capa ou página vazia, retornar exatamente
  `Conteúdo administrativo` e copiar literalmente a síntese anterior.

O foco exclusivo no “sumo teológico” e a inclusão genérica de índices na regra
de estagnação explicam os falsos administrativos encontrados em páginas de
título, dedicatórias, bibliografias e introduções.

No benchmark, o baseline recebe OCR da página, síntese anterior e autor/obra da
página anterior. Ele não recebe hints do catálogo e o fac-símile é
explicitamente desabilitado.

## Comparação qualitativa dos modelos

| Modelo | Pontos fortes | Limitações observadas |
|---|---|---|
| `gemma4:cloud` | Mais estável, conciso, rápido e provavelmente mais barato; 15/15 transições com hints | Tendeu a confiar no OCR quando a imagem o contradizia |
| `gpt-5.4-mini` | Melhor riqueza editorial e teológica; bom texto para o leitor | Mais lento, mais tokens e maior variação textual de autor/obra |
| `qwen3.5:397b-cloud` | Texto equilibrado e boa compreensão de páginas compostas | Latência muito alta e timeouts no baseline |
| `gpt-5.4-nano` | Mais rápido sem imagem | Verboso, menos estável e pior em algumas transições |
| `gpt-5-mini` | Nenhuma conclusão útil nesta amostra | Timeouts recorrentes de aproximadamente 300 segundos e um HTTP 429 |

### Médias da variante `structured_index_hint`

| Modelo | Tokens por chamada | Latência média |
|---|---:|---:|
| `gemma4:cloud` | aproximadamente 3.766 | 20,1 s |
| `gpt-5.4-nano` | aproximadamente 5.202 | 12,3 s |
| `gpt-5.4-mini` | aproximadamente 8.977 | 46,8 s |
| `qwen3.5:397b-cloud` | aproximadamente 7.355 | 162,7 s |

As contagens de tokens retornadas por provedores diferentes não são
necessariamente equivalentes para faturamento, mas ajudam a comparar o volume
operacional observado.

## Evidências concretas do fac-símile

O fac-símile corrigiu erros reais presentes no OCR:

### PG001:175

- OCR: `1855`.
- Imagem: `1853`.
- `gpt-5.4-mini` com imagem respondeu `1853` nas três repetições.
- Gemma e Qwen conservaram o `1855` incorreto do OCR.

### PG043:166

- OCR: `MDCCXLII`, ou 1742.
- Imagem: `MDCCXLIII`, ou 1743.
- Qwen com imagem respondeu `1743` nas três repetições.
- Sem imagem, o mesmo modelo respondeu `1742`.

Isso demonstra valor real do fac-símile em títulos, datas, cabeçalhos e dados
bibliográficos. Também demonstra que apenas anexar a imagem não garante que o
modelo a prefira ao OCR.

## Limitações descobertas no schema

### Uma página pode possuir vários tipos

`PG043:166` contém página de título, dedicatória e início de prefácio. A escolha
de um único `page_kind` força divergências artificiais entre respostas que, no
conteúdo, estão corretas.

Uma evolução possível seria `primary_page_kind` mais `page_kinds`, ou uma lista
de segmentos.

### Uma página pode possuir várias transições

`PL046:475` contém:

1. conclusão do Sermão XXV;
2. epístola de Frangipane a Pio VII;
3. início do `Præloquium Editoris`.

Um único `work` e uma transição `previous_work -> new_work` não representam
essa estrutura. Uma lista ordenada de segmentos seria mais adequada.

### Autor e obra não devem depender de strings livres

As respostas variaram entre formas como “São Clemente I”, “Clemente Romano” e
“S. Clemens Romanus”. Se essas strings forem gravadas como metadados canônicos,
gerarão duplicatas e relações frágeis.

O modelo deveria selecionar um `work_key` ou declarar que nenhum candidato foi
confirmado. Autor e título canônicos seriam recuperados do banco de índices.

Também convém separar:

- obra canônica do catálogo;
- título ou cabeçalho impresso na página;
- declaração específica de edição, tradução ou impressão.

Essa separação é particularmente importante em `PG001:175`, onde o título de
catálogo e a declaração impressa da edição não possuem exatamente a mesma
formulação linguística.

### Contributors está permissivo demais

Algumas respostas classificaram Rufino, Cassiodoro, Cipriano e outros autores
citados como colaboradores da edição. O campo deveria aceitar funções
controladas e exigir evidência direta:

- editor;
- tradutor;
- dedicante;
- dedicatário;
- comentador;
- impressor.

Autores apenas citados devem permanecer no texto de busca, não em
`contributors`.

### Busca lexical e embedding possuem objetivos diferentes

O atual `retrieval_text` tenta atender simultaneamente à busca lexical e ao
KNN. Em páginas bibliográficas, ele pode enumerar dezenas de títulos e dominar
o embedding.

Uma separação recomendada seria:

- `summary_display`: texto conciso para o site;
- `search_text`: nomes, títulos, grafias originais, citações e termos exatos;
- `embedding_text`: síntese semanticamente equilibrada para embedding/KNN;
- `cumulative_summary`: contexto progressivo da obra.

### O score automático não mede fidelidade factual

O score do playground é aplicado somente depois da geração e não é mostrado ao
modelo. Ele verifica:

- falso administrativo em início indexado ou com OCR substancial;
- autor ou obra vazios/divergentes dos hints;
- resumos muito curtos ou longos;
- síntese acumulada excessiva;
- Markdown ou raciocínio interno vazado;
- comentários sobre OCR/fac-símile.

Ele não verifica datas, nomes contra a imagem ou fidelidade semântica. Algumas
respostas receberam score 100 apesar de conservarem datas erradas do OCR.

## Recomendação provisória

Pelos resultados e pelo custo estimado, a estratégia provisória mais atraente
é:

1. usar `gemma4:cloud` com `structured_index_hint` como pipeline geral;
2. usar fac-símile de forma condicionada em inícios de obra, páginas de título,
   prefácios, dedicatórias e páginas com OCR suspeito;
3. preservar autor e obra canônicos a partir do índice, fazendo o modelo
   selecionar `work_key` em vez de inventar strings normalizadas;
4. validar deterministicamente datas, numeração romana e cabeçalhos;
5. encaminhar conflitos OCR/imagem para uma etapa visual específica;
6. separar texto de busca lexical do texto utilizado nos embeddings;
7. ao promover um resumo refeito para produção, invalidar e regenerar seu
   embedding e os derivados HDBSCAN/KNN correspondentes.

Antes de consolidar o prompt de produção, ainda deve ser executado um benchmark
com `structured_index_hint_clean` e `structured_index_hint_no_context`, para
isolar o efeito da limpeza do OCR e medir o viés do contexto anterior.
