# Estudo futuro: ranges hierárquicos e ranking de pesquisas bíblicas no RAG

Status: proposta para estudo futuro; não implementada.

Data do levantamento: 2026-09-19.

## Motivação

O agente consegue restringir `search_corpus` às páginas físicas associadas a uma
referência bíblica por meio dos sidecars `scripture-docids`. O filtro é aplicado
sobre os `doc_id` do índice amplo antes do carregamento dos documentos.

Os testes manuais mostraram duas limitações diferentes:

1. Uma consulta por capítulo em modo exato não herda normalmente as páginas
   associadas aos seus versículos ou sub-ranges.
2. Mesmo quando o escopo bíblico está correto, o ranking lexical não distingue
   automaticamente uso doutrinal de homônimos, aparato editorial, gramática,
   glossário ou índice remissivo.

Exemplo: uma pesquisa por `João 1:1` e `Verbo` recuperou páginas de gramática
gótica e latina porque elas usam “verbo” como categoria gramatical e citam João
como exemplo linguístico. Essas páginas estão corretamente associadas à
referência, mas são pouco relevantes para uma intenção cristológica.

## Estado atual

O parser já reconhece:

- capítulos, como `João 1`;
- versículos, como `João 1:1`;
- ranges no mesmo capítulo, como `João 1:1-18`;
- ranges entre capítulos, como `João 1:1-2:3`;
- ranges de capítulos, como `João 1-3`;
- listas descontínuas, como `João 3:14-16; 3:18`;
- separadores e abreviações latinas usuais.

Internamente, `relationForScriptureSegments` já classifica pares de referências
como:

- `exact`;
- `contains_query`;
- `contained_by_query`;
- `overlap`;
- `none`.

A API pública reduz essas relações a apenas dois modos:

- `exact`: conserva somente `exact`;
- `overlap`: conserva qualquer relação diferente de `none`.

Portanto, a principal lacuna inicial não é o reconhecimento do hífen, mas a
exposição controlada das relações de inclusão entre intervalos.

## Medições com João 1

As medições foram feitas sobre os artefatos locais de produção:

| Escopo | Referências canônicas | Páginas físicas únicas |
|---|---:|---:|
| `João 1` exato | 1 | 2.131 |
| `João 1:1` exato | 1 | 6.495 |
| Interseção física entre os dois conjuntos exatos | — | 189 |
| `João 1` + referências inteiramente contidas | 127 | 12.093 |
| `João 1` no `overlap` atual | 133 | 12.096 |

Apenas 2,91% das páginas exatas de `João 1:1` já estavam no conjunto exato de
`João 1`. Consequentemente, o modo exato de capítulo não deve ser interpretado
como uma expansão automática para os versículos do capítulo.

O conjunto “capítulo + descendentes” ficou quase igual ao `overlap`, mas possui
semântica mais precisa: as seis referências parcialmente sobrepostas adicionam
somente três páginas ao caso medido.

## Resultados manuais de relevância

Consulta lexical em português:

```text
scripture_reference = "João 1"
query = "Trindade || Verbo || Encarnação"
```

Resultado observado: 3 documentos. Dois eram tematicamente úteis (`PL169 p.33`
e `PL094 p.18`) e um era fraco ou ambíguo (`PL191 p.431`, em que *verbum*
aparece em outro contexto).

Com `João 1:1`, a mesma consulta retornou 6 documentos. Entre eles:

- `PL018 p.598`, `p.651` e `p.727`: gramática e glossário da língua gótica de
  H. C. von der Gabelentz e J. Lœbe, traduzidos por F. Tempestini;
- `PL032 p.706`: *De grammatica liber*, colocado no apêndice de obras outrora
  atribuídas erroneamente a Agostinho;
- `PG006 p.853`: índice remissivo;
- `PG075 p.330`: resultado doutrinal pertinente, do *Thesaurus de sancta et
  consubstantiali Trinitate*.

O resultado pertinente de `PG075` apareceu depois de páginas gramaticais.

Experimentos adicionais:

| Estratégia | João 1 | João 1:1 | Observação |
|---|---:|---:|---|
| Português com `Trindade || Verbo || Encarnação` | 3 | 6 | Recall pequeno, com ambiguidade de “verbo” |
| `Verbo && (Cristo || Filho || Deus)` mais os outros temas | 1 | 0 | Precisão maior, mas perda excessiva de recall |
| Expansão latina (`Trinitas`, `Trinitate`, `Incarnatio`) | 105 | 475 | Recall alto e muito ruído potencial |
| RRF com as três consultas portuguesas separadas | 3 | 6 | Não corrigiu a ordenação das páginas gramaticais |

Conclusão provisória: expansão lexical, conjunção rígida e RRF isoladamente não
resolvem a intenção semântica.

## Proposta A: modos hierárquicos de range

Estudar a ampliação de `match_mode` para:

| Modo proposto | Relações aceitas | Uso esperado |
|---|---|---|
| `exact` | `exact` | Enumerar onde a citação idêntica foi associada |
| `within` | `exact`, `contained_by_query` | Pesquisar temas dentro de capítulo ou range |
| `covering` | `exact`, `contains_query` | Incluir comentários indexados por ranges que cobrem um versículo |
| `overlap` | todas as relações não `none` | Exploração ampla explicitamente solicitada |

Defaults sugeridos:

- `search_scripture`: continuar com `exact` por padrão;
- `search_corpus` com capítulo ou range temático: permitir `within` explícito;
- consulta a versículo isolado: começar com `exact` e oferecer `covering` quando
  o caller desejar comentários associados a ranges maiores;
- não transformar `overlap` em default implícito.

O resultado deve preservar:

- relação responsável pela inclusão;
- referência canônica encontrada;
- referência consultada;
- máscaras e tipos de fonte;
- todas as referências que justificam a mesma página física.

Para expressões bíblicas booleanas, a expansão de cada operando deve ocorrer
antes da união (`||`) ou interseção física (`&&`).

### Impacto esperado

Esta fase pode ser implementada em JavaScript com os sidecars atuais. O sidecar
de João já contém os mapeamentos necessários e tinha 29.018 bytes gzip no
levantamento. Expandir `João 1` não exige baixar um novo artefato, apenas formar
um conjunto maior de páginas e `doc_id` em memória.

Não é necessária uma nova tag do PatrologiaIndexer para o filtro binário.

### Guardrails necessários

- limite de referências expandidas;
- limite ou aviso para páginas expandidas;
- progresso com contagens de referências e páginas;
- cancelamento durante carregamento e mapeamento;
- deduplicação por `volume_id:page`;
- paginação que não confunda quantidade de referências com quantidade de
  páginas;
- indicação de resultados truncados;
- testes para listas descontínuas e expressões booleanas.

## Proposta B: reranking offline por tipo documental

Ranges melhores aumentam o recall, mas não resolvem a ambiguidade de “Verbo”.
Uma etapa posterior pode gerar, durante o build, uma anotação compacta por
`doc_id`.

Taxonomia inicial possível:

- texto patrístico primário;
- comentário ou homilia bíblica;
- tratado teológico;
- aparato editorial moderno;
- índice ou concordância;
- gramática ou glossário;
- tradução ou edição filológica;
- autoria autêntica, disputada, espúria ou desconhecida.

Essas classes podem ser produzidas progressivamente por:

1. regras auditáveis sobre título, autor, obra, seção e resumo;
2. correções manuais versionadas;
3. classificação offline durante o build;
4. revisão por amostras e casos de regressão.

Um byte de categoria por 281.682 documentos custaria cerca de 282 KiB antes de
compressão. Uma representação esparsa ou por ranges de `doc_id` pode ser menor.

O reranking pode considerar a intenção estimada da consulta:

- intenção doutrinal: aumentar textos patrísticos, comentários e tratados;
- intenção filológica: não penalizar gramáticas, glossários e edições;
- intenção bibliográfica: preservar índices e aparatos.

As categorias devem alterar pontuação, não remover definitivamente documentos.

## Proposta C: camada conceitual ou semântica offline

Antes de embeddings completos no browser, estudar uma camada conceitual
controlada:

- conceitos como Trindade, Logos, Encarnação e Cristologia;
- aliases em português, latim, grego, inglês, italiano e francês;
- associações página-conceito calculadas offline;
- pesos e confiança auditáveis;
- índice compacto por conceito ou bitsets por `doc_id`.

Essa abordagem evita inicialmente o download e a inicialização de um modelo de
embeddings no navegador. O BYO provider pode ajudar a identificar a intenção ou
o conceito, mas a recuperação deve continuar determinística e utilizável sem
provider.

Embeddings completos devem ser avaliados somente depois de medir:

- tamanho do modelo de consulta;
- custo de inicialização em WASM/ONNX;
- memória em dispositivos móveis;
- tamanho do índice vetorial quantizado;
- qualidade multilíngue;
- possibilidade de execução inteiramente offline.

## Ranking por relação bíblica

O filtro atual por `doc_id` é binário. Uma etapa posterior poderia ponderar:

```text
exact > covering > within > overlap
```

Há duas alternativas:

1. buscar grupos separados no JavaScript e mesclar por score ou RRF;
2. adicionar pesos de escopo ao PatrologiaIndexer.

A segunda alternativa provavelmente exige alteração no `.work`, novos testes do
runtime, atualização de dependências e publicação de nova tag. Ela não deve ser
misturada com a primeira implementação de ranges hierárquicos.

## Plano de estudo recomendado

### Etapa 1 — conjunto de avaliação

Criar consultas com julgamentos manuais de relevância, incluindo:

- João 1 + Trindade, Verbo e Encarnação;
- João 1:1 + Logos;
- João 1:14 + Encarnação;
- Mateus 28:19 + Trindade;
- Romanos 5 + graça e pecado;
- consultas filológicas em que gramáticas e glossários sejam relevantes;
- ranges entre capítulos e listas descontínuas;
- expressões bíblicas com `&&` e `||`.

Cada resultado deve poder ser marcado como relevante, parcialmente relevante ou
ruído, com justificativa.

### Etapa 2 — ranges hierárquicos

- expor `within` e `covering`;
- preservar relações no retorno;
- adicionar guardrails, progresso e cancelamento;
- validar custos sobre ranges pequenos e amplos;
- não alterar ainda o ranking lexical.

### Etapa 3 — classificação documental offline

- gerar taxonomia inicial;
- publicar sidecar versionado por `build_id`;
- medir precisão e recall antes e depois do reranking;
- revisar manualmente falsos positivos importantes.

### Etapa 4 — conceitos e semântica

- comparar ontologia controlada, expansão lexical e embeddings;
- medir armazenamento, rede, CPU, memória e relevância;
- promover apenas o algoritmo que melhorar o conjunto de avaliação.

## Critérios de aceitação futuros

- `João 1` com `within` inclui referências como `João 1:1` e `1:14-18`;
- `exact` mantém o comportamento atual;
- `covering` inclui um range que cobre o versículo solicitado;
- nenhuma relação é apresentada como exata quando não é;
- paginação e contagens distinguem referências de páginas;
- abortar a operação produz evento terminal e não bloqueia caches compartilhados;
- os sidecars continuam vinculados ao `build_id` do índice amplo;
- ranges amplos não congelam a interface;
- a relevância é comparada contra julgamentos manuais, não apenas por quantidade
  de resultados;
- buscas doutrinais rebaixam páginas gramaticais sem prejudicar consultas
  filológicas explícitas.

## Decisões deixadas em aberto

- nome final dos modos públicos;
- se `search_corpus` deve inferir `within` ou exigir argumento explícito;
- limite máximo de referências e páginas expandidas;
- forma de agregar paginação entre subreferências;
- taxonomia documental inicial;
- armazenamento do sidecar de categorias;
- necessidade de score ponderado dentro do PatrologiaIndexer;
- estratégia para `ss.`, `sqq.` e `ff.`;
- tratamento de diferenças de versificação, especialmente em Salmos e textos
  deuterocanônicos.

