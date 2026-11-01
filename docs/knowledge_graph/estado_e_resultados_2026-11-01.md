# Grafos da Patrística: estado, decisões e resultados

Registro consolidado em **2026-11-01** para o repositório
`Fabio3rs/BibliothecaPatristica`. O objetivo final é transformar o corpus em um
grafo navegável, permitindo encontrar conceitos, autores, argumentos e passagens
relacionadas, com acesso à sua proveniência.

**Já temos um benchmark de descoberta, um playground funcional e um executor
persistente por página física.** O piloto produziu grafos úteis, revelou erros de
atribuição e orientou melhorias de prompt e validação. A campanha ampla foi
iniciada pelo usuário; ainda não temos grafo global publicado, resolução global
de entidades ou medição adjudicada de precisão/recall.

Os números do benchmark pertencem ao snapshot de 31/10. Os números da campanha
foram consultados em **01/11, 17:06:08 UTC / 14:06:08 America/Sao_Paulo**, em uma
transação de leitura. A [fotografia JSON](snapshot_2026-11-01.json) registra essa
distinção. Este documento não atualiza automaticamente durante a execução.

## 1. Etapas realizadas

| Etapa | Entrega e resultado |
|---|---|
| Investigação das fontes | Inspeção de implementações e schemas reais; uso de resumo v2, OCR, índices e OCR histórico, sem substituir por `keywords_v1` ou resumos legados. |
| Descoberta do benchmark | 1.100 candidatos, 240 páginas selecionadas, 43 pares e 39 pacotes de revisão. |
| Auditoria de shadow | Identificação da diferença entre gerar, concluir e promover; revisão da regra `false_administrative_risk`. |
| Playground local | CLI, browser, modelos intercambiáveis, prompts versionados, snapshots, validação, grafo, comparação e revisão. |
| Piloto PG026:71 | Chamadas reais do usuário com Gemma4 e Muse; revisões do OCR, resumo, imagem e resultados armazenados. |
| Refinamento experimental | Maior orçamento de saída, preços configuráveis, localização aproximada de evidências, deshifenização por bloco, confiança por item, autoria e páginas bilíngues. |
| Execução contínua | Fila SQLite em WAL, pool de processos, retries de transporte, retomada, descoberta incremental e monitoramento pelo `serve`. |
| Divergência de OCR | Opção persistida para usar a revisão atual do OCR mantendo identidade da página, hashes e aviso de conflito. |

Esta consolidação é documental: não executou inferência, não alterou prompts,
validadores, respostas, fontes ou a campanha em andamento.

## 2. Fontes e regras de proveniência

Uma página física corresponde a um job atômico. Pode conter várias obras, autores,
notas editoriais ou idiomas. O modelo recebe todos os segmentos atuais do resumo
v2, blocos OCR da mesma página, contexto bibliográfico e, quando selecionado e
suportado, fac-símile. Não se impõe um número fixo de conceitos ou autores.

| Fonte | Uso |
|---|---|
| `data/patristica_resumos.db`, `resumo_generations` | Geração, segmentos, contexto, status, conflitos e hashes. |
| `data/patristic_indices.db`, `works` | Autoria/título e referências bibliográficas disponíveis por `work_key`. |
| `data/ocr_versions.db`, `ocr_results` | OCR histórico vinculado à geração do resumo. |
| `teste/<volume>/text/` | OCR atual, incluindo estrutura, scripts e coordenadas existentes. |
| Imagem resolvida da página | Inspeção visual e entrada multimodal opcional; disponibilidade e envio efetivo são registrados separadamente. |

O playground prefere `is_current=1`, `status IN ('valid','metadata_pending')` e
ausência de taint. Na falta de current elegível, usa o shadow elegível de maior
ID, conforme autorização do usuário. Seleção explícita de geração/run continua
disponível. **Shadow não é sinônimo de inválido, e uso de shadow não promove a geração.**

O `cumulative_summary` é contexto, não evidência citável de fatos desta página.
Os segmentos atuais podem identificar o autor mesmo quando seu nome não aparece
no OCR. Metadados bibliográficos, voz editorial e teses citadas precisam conservar
suas origens distintas. Página física, paginação impressa e coluna Migne não são
locadores intercambiáveis.

O `source_hash` do resumo v2 é composto; não é o SHA-256 do arquivo OCR. São
preservados separadamente hashes do OCR, segmentos, prompt, schema, imagem e
request. O `output_hash` v2 é conferido com o algoritmo existente. Runs antigas
retêm seus inputs; revalidações e revisões acrescentam histórico.

### Por que os resumos estavam em shadow

A [auditoria de 31/10](../../.work/kg_benchmark_shadow_audit/audit.json) encontrou
168.998 gerações v2, nenhuma current ou com timestamp de promoção. O lote PG
inspecionado continha 164 jobs, nenhum com promoção solicitada. O modo padrão era
shadow; terminar a geração não executava a promoção.

Nas runs mais recentes por volume PG, 149 estavam `completed` e 15 `blocked`.
Os manifestos somavam 119.517 páginas esperadas, 112.728 armazenadas e 6.789
ausentes; 13 armazenadas eram inelegíveis. Esse recorte de runs mais recentes
difere da contagem de todas as gerações PG usadas no perfil do benchmark.

A regra antiga tratava comprimento de texto administrativo como risco bloqueante.
A correção passou a usar `administrative_long_text_review` como pendência de
metadados, sem interromper a cadeia. Um início de obra indicado apenas pelo índice
gera `administrative_index_conflict`; o bloqueio `false_administrative_risk` exige
corroboração do título no OCR/cabeçalho. Páginas mistas conservam obrigações de
contexto. Não foi criada uma exceção por conter a palavra “Google”.

No replay de 13 respostas já salvas, **12 passaram a `metadata_pending` e uma
permaneceu `context_provisional`**. Foi revalidação em leitura, não reclassificação
persistida nem promoção em massa. Regras e versão estão documentadas no
[pipeline v2](../pipeline_resumos_v2.md).

## 3. Benchmark obtido

A descoberta usou seed **20261031**, SQLite em leitura e seleção limitada em
memória. Heurísticas buscaram candidatos em resumos e metadados; apenas os OCRs
selecionados para inspeção foram abertos. Citação bíblica detectada e coocorrência
servem para recuperação, não provam relação exegética.

| Medida | Resultado do snapshot de descoberta |
|---|---:|
| Gerações elegíveis no escopo shadow explícito | 168.982 |
| Runs shadow selecionadas | 252 |
| PG / PL elegíveis | 117.005 / 51.977 |
| `valid` / `metadata_pending` | 132.445 / 36.537 |
| Gerações promovidas elegíveis / PO disponível | 0 / 0 |
| Shortlist limitada / pool final | 4.375 / 1.100 |
| Páginas selecionadas | 240 |
| Volumes / work keys existentes na seleção | 175 / 219 |
| Páginas sem work key | 29 |
| Páginas com leitura substantiva de fonte | 29 |
| Páginas preparadas ainda sem essa revisão | 211 |
| Pares de resolução | 43 |
| Pacotes de revisão | 39 |

| Split | Páginas | `valid` | `metadata_pending` | PG | PL |
|---|---:|---:|---:|---:|---:|
| Calibração | 24 | 14 | 10 | 13 | 11 |
| Representativo | 120 | 63 | 57 | 61 | 59 |
| Difícil | 48 | 25 | 23 | 31 | 17 |
| Holdout cego | 48 | 33 | 15 | 25 | 23 |

As 24 páginas de calibração também foram reservadas para repetições. A seleção
é diversificada, não uma amostra probabilística do corpus. Gêneros editoriais e
`metadata_pending` foram sobreamostrados. Os 48 exemplos de holdout reservam
24 volumes inteiros, além da separação por obras/famílias bibliográficas.
O browser normal mostra 192 páginas; modo de avaliação libera o holdout.

No pool, 59 candidatos tinham OCR atual diferente do histórico e foram excluídos
da seleção. A permissão posterior de drift no executor **não altera retroativamente
esse benchmark congelado**. Duplicatas exatas e semelhanças lexicais foram
controladas; isso não elimina todos os paralelos traduzidos.

Os 43 pares incluem 39 pares entre páginas e quatro controles locais. Classificação
provisória: 4 `EQUIVALENT`, 4 `DIFFERENT`, 2 `RELATED`, 1 `BROADER`, 1 `NARROWER`
e 31 `UNCERTAIN`. A incerteza é uma abstenção; não significa entidades diferentes.
Ainda falta um conjunto mais equilibrado de homônimos e relações entre escolas.

Na leitura delimitada das fontes, 25 páginas têm evidência classificada como
`verified`, duas como `contradictory` e 213 como `uncertain`. `verified` refere-se
ao trecho inspecionado, não ao conteúdo inteiro da página ou à verdade histórica.
Todas as anotações permanecem revisáveis e **não gold**.

A validação dos artefatos verificou 240 páginas e 43 pares, com zero erros e zero
avisos. Foram preparados aproximadamente **1.929.891 tokens de entrada** para
revisão, estimados por caracteres/3, com faixa de 1.447.393–2.894.805. Não é
tokenização específica de modelo; exclui saídas e inclui contexto repetido dos pares.
Descoberta e preparação não consumiram inferência paga.

### Exemplos efetivos para ampliar a avaliação

| Página física / geração | Caso e capacidade testada |
|---|---|
| PG043:346 / 60887 | Distinção de homônimos chamados Nonnus, com identificação explicitamente incerta. |
| PL010:69 / 5031 | Objeção/resposta sobre unidade; separar opinião adversária, autor e aparato. |
| PL064:84 / 160125 | Boécio, categorias e substância: pertencer à mesma categoria não torna homem, pedra e ouro sinônimos. |
| PG068:59 / 73004 | Atribuições divergentes em manuscritos; distinguir autoria atribuída e dúvida editorial. |
| PL011:637 / 9067 | Atas com falantes nomeados e leitura de documento; evitar transformar `Maximianistis` em um imperador emissor. |
| PL019:437 / 16239 | Epitáfios de heróis troianos; persona literária não equivale a testemunho histórico. |
| PL015:560 / 13603 | Controle quase vazio; marca de digitalização não produz autoria patrística. |

Os locadores completos e trechos originais estão no
[relatório de descoberta](../../.work/kg_benchmark_v1/discovery_report.md).
Esses exemplos são distintos do piloto repetido em PG026:71.

## 4. Playground implementado

| Camada | Implementação |
|---|---|
| Entrada | `snapshot_v2.py` e `ocr_adapter.py`: fontes em leitura, todos os segmentos, IDs determinísticos e avisos de parsing. |
| Prompts | Registry versionado, modos `concepts_only`, `concepts_relations`, `full_multimodal`, variantes com/sem exemplo e revisão customizada. |
| Providers | Adapters Ollama, OpenAI, OpenAI-compatible, Meta e fixture offline; capacidades declaradas, timeouts e retries. |
| Execução | Snapshots exatos, hashes/cache, repetições deliberadas, matrizes e retomada. |
| Validação | Schema, IDs/referências, evidência localizada e diagnósticos para revisão semântica. |
| Persistência | SQLite separado em `data/playgrounds/kg_extractor_playground.db`; experimentos, casos, prompts, runs, validações, revisões e fila. |
| Interface | HTTP local da biblioteca padrão, JS/CSS/SVG; fontes, fac-símile com bbox, grafo selecionável, comparação e histórico. |
| Exportação | JSONL, relatórios Markdown/CSV/JSON e anotações separadas das respostas originais. |

O schema possui `concepts`, `entities`, `claims`, `events`, `relations`, `evidence`
e `warnings`. Conceitos são o alvo principal; relações não são obrigatórias quando
faltam fundamentos. IDs são locais à página. A comparação busca correspondências
conservadoras por rótulo/contexto, não pela coincidência de IDs.

Os adapters foram exercitados com mocks e servidores sintéticos nos testes.
**Há também respostas reais armazenadas das execuções do usuário com
`gemma4:cloud` e `muse-spark-1.3-contributor`.** Isso não comprova funcionamento
real de todos os outros modelos/endpoints disponíveis, nem desempenho geral.

### OCR, imagem e evidência

A limpeza de hifenização atua apenas na cópia do texto interno de cada bloco
enviada ao modelo. Mantém ordem, IDs, atributos, scripts, tipos e bbox; não junta
blocos nem páginas. O XML original permanece intacto. A entrada é a representação
estruturada dos blocos em JSON, não uma regravação do XML. A preparação registra
versão, quantidade de blocos alterados e hashes antes/depois.

O validador atual é `kg-validation-6`, com localizador `local-quote-v2`:

1. Procura correspondência exata no bloco/segmento referenciado.
2. Tenta normalização conservadora de grafia, ligaturas, acentos, espaços e quebras.
3. Procura trecho contíguo por distância de Levenshtein em busca limitada,
   com score mínimo 0,90 e controles adicionais por token.
4. Conserva diff, offsets e texto original; ambiguidades e mudanças protegidas
   em negações, números ou operadores são encaminhadas à revisão.

Um score lexical alto não basta para aceitar mudança de sentido. Por outro lado,
esses controles ainda podem rejeitar homóglifos ou hifens graficamente inofensivos.
Não há correção silenciosa de citações. Texto do resumo e citação original ficam
separados; nunca se exige igualdade entre uma tradução portuguesa e o latim/grego.

Há três dimensões distintas: **estrutura**, **localização da evidência** e
**qualidade semântica**. Encontrar uma frase não prova que ela sustenta toda a
proposição, nem identifica automaticamente quem a defende. O status original de
uma run é preservado mesmo quando uma revalidação posterior consegue localizar
suas citações.

## 5. Evolução de prompt e contrato

| Variante | Objetivo da mudança |
|---|---|
| `concepts-v1`, `concepts-v1-fewshot` | Starter kit, com comparação sem/com exemplo sintético. |
| `concepts-v1.1` | JSON sem envoltório, namespaces de IDs e cópia de evidência. |
| `concepts-v1.2` | Dono da proposição, crítico versus citado, modalidade e evidência suficiente. |
| `concepts-v2-confidence` | Mesma base v1.2, schema v2 e confiança por item. |
| `concepts-v1.3`, `concepts-v2.1-confidence` | Cobertura do autor e suas ações argumentativas quando sustentados pelo resumo atual/metadados. |
| `concepts-v1.4`, `concepts-v2.2-confidence` | Possibilidade de original/tradução em página bilíngue; evitar duplicação e preservar divergências. |

As versões antigas continuam disponíveis. O `run` comum mantém default v1;
`corpus-create` usa v2.2-confidence por padrão. Para comparações reproduzíveis,
especificar a variante. Retomar um plano não troca seu prompt.

O campo v2 `model_confidence` contém score 0–1 e justificativa curta por conceito,
entidade, proposição, evento e relação. O protocolo `source-fidelity-v1` pergunta
pela aceitação do item inteiro por um revisor das mesmas fontes, considerando
significado, identidade, atribuição, modalidade e direção. Não é probabilidade
de verdade de uma doutrina, nota global ou medida de completude.

A [pesquisa sobre confiança verbalizada](../kg_model_confidence.md), incluindo
trabalhos consultados de 2026, fundamentou uma hipótese de experimento. Não
demonstramos calibração: a implementação mostra e exporta notas brutas, não aceita
relações por limiar e não calcula Brier/ECE sem referências adjudicadas.

## 6. O que obtivemos em PG026:71

Fonte do piloto: geração **45218**, run de resumo **268**, `shadow/valid`, obra
`PG026:work:001`, *Orationes adversus Arianos*. Arquivo:
`teste/PG026/text/1b132de1-447b-40a8-9de4-e57e20a4d01b-071.txt`.
OCR SHA-256: `6ecc0fc3b0f591cdedfc8c197b198ef0a8ac00aae8a431c88c4949326b13aad7`.
Há um segmento atual e 11 blocos OCR, incluindo grego e latim paralelos, referências
e aparato. O fac-símile foi arquivado para inspeção.

O resumo atual identifica Atanásio e sua refutação da leitura ariana de `factus`.
A página contrapõe o Filho às coisas feitas, discute gerar/fazer e superioridade,
e relata posições sobre anjos atribuídas a Valentim e Carpócrates. É um bom teste
de interpretação, modalidade e autoria; não representa sozinho a diversidade do corpus.

| Run | Resultado observado e aprendizado |
|---|---|
| 3–4, Gemma4 | Problemas de formato/IDs levaram a saídas inválidas; tratamento auditável do envoltório Markdown e reforço do contrato. |
| 5, Gemma4 | 5 conceitos, 9 entidades, 3 proposições, 4 relações. Temas centrais úteis; faltaram autor/refutação e qualificações de modalidade. |
| 6, Muse | Limite de 8.192 tokens consumido, incluindo 6.824 de raciocínio; JSON truncado. O orçamento padrão passou a 32.768. |
| 8, Muse + imagem | JSON estruturalmente válido; três diferenças de citação bloquearam a regra exata. Revalidação posterior localizou 30/30 evidências. |
| 9, Muse | 7 conceitos e 5 relações, 17 citações localizadas. Duas atribuições confundiram a crítica do autor com teses dos adversários. |
| 13, Muse | Atribuições mais coerentes, mas Atanásio e sua refutação ausentes; confiança alta não revelou essas omissões. |
| 16 e 19, Muse | Status original inválido; revalidação v6 localizou, respectivamente, 11/11 e 15/15 evidências. Não houve nova inferência por isso. |
| 18, Muse | Duas evidências continuaram bloqueadas por diferenças numéricas; não se relaxou a regra apenas para aprovar a run. |
| 20–21, Muse | Autor recuperado e tratamento bilíngue melhor; resultados diferentes com o mesmo input. Run 21 útil para navegação, com refinamentos de atribuição/evidência ainda possíveis. |

### Comparação das runs 20 e 21

Ambas usaram `concepts-v2.2-confidence`, schema v2, `concepts_relations`, imagem
anexada, término `stop` e o mesmo hash de entrada. Foram novas inferências, não
respostas recuperadas do cache local.

| Medida | Run 20 | Run 21 |
|---|---:|---:|
| Conceitos / entidades | 6 / 5 | 8 / 4 |
| Proposições / relações | 7 / 6 | 5 / 5 |
| Evidências localizadas / total | 9 / 13 | 22 / 22 |
| Exatas / normalizadas / fuzzy | 6 / 1 / 2 | 21 / 0 / 1 |
| Status original | `invalid` | `completed` |
| Entrada / saída total | 16.078 / 15.408 | 16.078 / 11.300 |
| Raciocínio, já incluído na saída | 8.895 | 7.322 |
| Latência | 126,60 s | 96,58 s |
| Custo estimado pelo catálogo | US$ 0,0046894 | US$ 0,0038678 |

Na run 20, os quatro bloqueios eram localizáveis na leitura: `o` latino no lugar
de omicron grego, `nou/non` e hifens em palavras. A run 21 identificou Atanásio
com evidência do segmento atual, evitou duplicação sistemática das duas línguas e
produziu um grafo navegável. A diferença `arripuere/arriperent` foi localizada com
diff; a inspeção visual favoreceu a leitura do modelo nesse trecho.

Ainda há hipóteses que podem parecer afirmações incondicionais quando a aresta é
lida isoladamente e citações estreitas para itens que o restante da página sustenta.
A run 20 distinguiu melhor algumas proposições relatadas; não houve melhora
uniforme em todas as dimensões. Duas execuções não estabelecem estabilidade geral
nem superioridade de um modelo.

Na run 13, os scores ficaram entre 0,86 e 0,95 apesar da omissão do autor. Na run
21, entre 0,72 e 0,96. Isso mostra por que confiança por item não mede recall e
por que uma nota alta ainda exige revisão. Não calculamos precisão/recall ou
calibração com base nessas revisões provisórias.

Os pareceres e anotações foram preservados separadamente. A
[revisão das runs 20–21](../../.work/kg_playground_review/runs20_21/review.md)
contém 30 anotações iniciais e esclarecimento posterior, sem reescrever respostas.

## 7. Critério de qualidade: navegação

O usuário esclareceu que o produto deve permitir explorar a Patrística. **Rótulos
explicativos e aproximações conceituais sustentadas pelo contexto são aceitáveis,
mesmo sem aparecer literalmente na página.** Por exemplo, uma associação temática
com consubstancialidade pode ajudar a navegar um argumento sem transformar essa
palavra em citação textual.

A revisão inicial deu peso excessivo à precisão terminológica. O critério foi
ajustado na documentação e nas anotações: distinguir refinamentos úteis de erros
que levam o leitor ao autor, assunto ou posição errados. Isso não exigiu nova run
nem mudança de prompt/schema/validador naquele momento.

Prioridade de revisão: inversão entre defender/refutar, atribuição ao crítico de
uma tese adversária, identidade errada e associação sem apoio contextual.
Diferença de nomenclatura e recorte de evidência melhorável têm peso menor quando
a página sustenta o tema. Interpretação inferida continua distinguível de menção
explícita. A nota já salva de confiança conserva seu alvo original de fidelidade;
não foi reinterpretada como probabilidade de utilidade para navegação.

## 8. Executor contínuo e campanha atual

O executor de corpus usa `ProcessPoolExecutor` com `spawn`, preparação e validação
nos workers, conexões independentes e SQLite **WAL**. A descoberta é paginada por
volume, com 500 metadados por lote e sem leitura antecipada de todo o OCR. Uma
campanha fixa modelo, prompt, schema e geração por página; os inputs completos
são congelados quando cada job é preparado.

Retries são limitados a falhas de transporte/status transitório, com backoff
exponencial, full jitter e respeito a `Retry-After`. Saídas `invalid` não entram
em repetição automática. Tentativas e runs anteriores permanecem no histórico.
`--follow` acrescenta páginas inéditas elegíveis; geração nova para uma página já
selecionada exige outra campanha. Locks evitam coordenadores concorrentes para a
mesma campanha e protegem a recuperação de workers sobreviventes.

A interface `serve`, em **Histórico & relatórios → Campanhas do corpus**, consulta
o banco a cada cinco segundos com a aba visível. Exibe fila, atividade, tentativas
e links para os resultados. Iniciar/retomar a campanha continua sendo ação CLI.

### Fotografia da campanha EXP-76db1db8b118

Configuração armazenada: Meta `muse-spark-1.3-contributor`, prompt
`concepts-v2.2-confidence`, schema v2, `concepts_relations`, fac-símile `on`,
limite de saída 32.768 e modo de avaliação desligado.

| Estado em 01/11/2026, 14:06:08 de São Paulo | Páginas |
|---|---:|
| Total da fila | 154.522 |
| `completed` | 69 |
| `invalid` | 24 |
| `running` registrado | 20 |
| `pending` | 154.409 |

O log de descoberta apresentado pelo usuário registrou 14.765 páginas excluídas
pelas reservas de holdout. Esse contador é do evento de descoberta, não uma nova
contagem feita nesta documentação. O estado `running` é o registro da fila, não
uma inspeção de processos vivos.

As 93 chamadas da campanha com uso reportado somavam 1.219.735 tokens de entrada
e 935.340 de saída, dos quais 541.707 de raciocínio, com custo salvo estimado de
**US$ 0,3090415**. Não inclui chamadas ainda em andamento nem custos remotos sem
uso registrado. Não é projeção do custo final, nem medição de qualidade semântica.

### Aceitação explícita de OCR atualizado

PG001:3 mostrou o caso: a geração 8241 usou OCR histórico 1132980, mas o arquivo
foi reprocessado depois. A comparação do texto XML inteiro detectava inclusive
atributos, bbox e divisão em blocos, não apenas conteúdo histórico relevante.
Por padrão, o loader recusava essa divergência antes de chamar o modelo.

A pedido do usuário, a campanha recebeu uma revisão persistida de política em
01/11 às 17:00:08 UTC, habilitando `allow_source_drift`. Novos jobs podem usar o
OCR atual, com `SOURCE_CONFLICT`, hash atual e ID/hash histórico. Página física
errada e fallback de nome sem prova continuam bloqueados.

A retomada recoloca na fila somente erros prévios desse tipo, anteriores à
inferência. Não reexecuta concluídas ou inválidas; outros erros requerem
`--retry-errors`. A política não altera planos e snapshots já preparados. Um
dry-run isolado de PG001:3 comprovou a preparação com ambas as proveniências,
sem request ao modelo.

## 9. Custos e orçamento de saída

As tarifas foram fornecidas pelo usuário em 01/11/2026 e registradas em catálogo
editável. **Não são preços verificados independentemente nesta documentação.**

| Modelo | Entrada US$/milhão | Saída US$/milhão |
|---|---:|---:|
| Muse Spark 1.3 Contributor | 0,10 | 0,20 |
| Muse Spark 1.3 Standard | 1,25 | 4,25 |
| Muse Spark 1.2 Contributor | 0,10 | 0,20 |
| Gemma4 — Ollama Cloud | 0,14 | 0,40 |
| GPT-OSS 20B — Ollama Cloud | 0,07 | 0,30 |

O custo usa tokens reportados e preços configurados. Raciocínio já incluído na
saída não é cobrado novamente na conta local. Cache de entrada só recebe tarifa
própria quando configurada. Sem preços/uso, custo fica desconhecido. Planos novos
congelam a tarifa; resultados antigos sem custo podem receber estimativa de
visualização identificada, sem reescrita histórica.

O limite de 32.768 é orçamento máximo, não consumo obrigatório nem garantia de
resposta completa. Em modelos com raciocínio, parte dele pode consumir esse
orçamento. `finish_reason=length` permanece diagnóstico de interrupção; o sistema
não inventa o restante do JSON.

## 10. Verificação técnica realizada

Os registros de teste abaixo foram executados anteriormente via **Native Lab MCP**
e conferidos nos JUnit para esta consolidação. Não são uma nova execução da suíte.

| Entrega | Resultado registrado |
|---|---|
| Executor contínuo, suíte KG | 274 testes, zero falhas/erros. |
| Após otimização da descoberta por volume | 16 testes focados, zero falhas/erros; há sobreposição com a suíte anterior. |
| Política de divergência de OCR e regressões relacionadas | 103 testes, zero falhas/erros; também há sobreposição. |

Não somar esses números como testes distintos. Os casos cobrem fontes, segmentos
múltiplos, scripts, OCR malformado/ausente, shadow/current, leitura de produção,
schema/IDs, citações, requests simulados, cache, repetição, retomada, WAL, processos,
sinais, falhas e backoff. Fixtures históricas sintéticas não entram no benchmark real.

O monitor foi aberto pelo Native Lab Playwright em desktop 1920×1080 e mobile
375×812; foram conferidos atividade da campanha, parada, abertura de run, fonte e
imagem. Console sem mensagens e requests observados sem falhas; capturas e
relatório estão em `.work/kg_corpus_runner/`. Compilação Python, sintaxe JS e
Prettier passaram na entrega. Não há build separado do frontend nem alteração do
site Astro. Testes não chamaram APIs pagas.

## 11. Uso e recuperação dos resultados

Na raiz do repositório, com o ambiente Python do projeto:

```bash
# Interface local; abrir http://127.0.0.1:8766
python scripts/playgrounds/kg_extractor_playground.py serve

# Estado da campanha existente, sem disparar inferência
python scripts/playgrounds/kg_extractor_playground.py corpus-status \
  --campaign EXP-76db1db8b118

# Preparar outra página/prompt, sem chamada ao modelo
python scripts/playgrounds/kg_extractor_playground.py run \
  --pages PG026:71 --provider meta --model muse-spark-1.3-contributor \
  --prompts concepts-v2.2-confidence --mode concepts_relations \
  --facsimile on --dry-run

# Inspecionar/comparar resultados já existentes
python scripts/playgrounds/kg_extractor_playground.py inspect \
  --run 21 --output .work/run21-inspection.json
python scripts/playgrounds/kg_extractor_playground.py compare \
  --runs 20 21 --output .work/runs20-21-comparison.json

# Carregar exemplo do benchmark sem inferência
python scripts/playgrounds/kg_extractor_playground.py run \
  --benchmark-ids KG-BENCH-000049 --provider meta \
  --model muse-spark-1.3-contributor --prompts concepts-v2.2-confidence --dry-run
```

Para retomar execução real da campanha, após encerrar outro coordenador dessa
mesma campanha, o comando usado é:

```bash
python scripts/playgrounds/kg_extractor_playground.py corpus-run \
  --campaign EXP-76db1db8b118 --workers 5 --follow --allow-source-drift
```

Esse último comando **faz inferência e pode gerar cobrança**. O `serve` apenas
monitora a campanha iniciada pelo CLI. Se mudar o caminho do banco, usar o mesmo
`--db` em todos os comandos. Mais exemplos e opções estão no
[manual operacional](../kg_extractor_playground.md).

## 12. Limitações e próximos passos

1. **Revisar além de PG026:71.** Usar calibração, controles negativos e gêneros
   distintos; preservar o holdout até congelar decisões de avaliação.
2. **Separar métricas técnicas e de produto.** Medir localização, autoria/posição,
   cobertura de assuntos e utilidade para navegação. `completed` não mede isso.
3. **Tratar os falsos bloqueios conhecidos.** Avaliar homóglifos e hifens com casos
   positivos/negativos, preservando proteção a negação e números e o diff auditável.
4. **Avaliar estabilidade e confiança.** Repetições planejadas, julgamento
   independente e calibração por modelo/estrato; notas atuais são exploratórias.
5. **Completar a cobertura do benchmark.** PO, material promovido, terminologia
   entre escolas, datas de autor/evento/editor e pares com decisão fundamentada.
6. **Comparar estratégias.** Uma passagem versus duas etapas, modelos pequenos e
   grandes, com/sem imagem e com/sem confiança ainda não têm comparação ampla
   adjudicada. O playground permite organizar esses experimentos.
7. **Operação em escala.** Não há limitador global de RPM/tokens; workers limitam
   concorrência. Crash após resposta remota pode deixar cobrança sem confirmação
   local. Relatórios analíticos antigos materializam métricas; status e exportação
   JSONL paginada são as opções para acompanhamento do corpus.
8. **Construir o grafo global numa fase posterior.** Canonicalização entre páginas,
   resolução de homônimos, navegação entre obras, shards e publicação não foram
   implementados. Os IDs e evidências locais dão a base para essa etapa.

O ganho já demonstrado é a capacidade de carregar uma fonte real, executar e
preservar uma extração, inspecionar suas relações e voltar ao trecho que as
motivou. A campanha amplia a produção de candidatos; qualidade global e
organização do grafo ainda precisam ser estabelecidas sobre esses registros.
