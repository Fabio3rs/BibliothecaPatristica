# Assuntos para navegação, localização offline e A/B v2.3

Implementação de 2026-11-01. O experimento **`EXP-13073b566bc1`** está preparado com **96 chamadas pendentes e nenhuma inferência executada**. A campanha `EXP-76db1db8b118` permanece congelada. Este trabalho não altera OCR, resumos, keywords, site público ou canonicalização global.

## Prompt experimental

Variante registrada: `concepts-v2.3-navigation-confidence`.

- Prompt completo: [`extract_concepts_v2.3.system.txt`](../../tools/knowledge_graph/prompts/extract_concepts_v2.3.system.txt).
- Delta: [`navigation_v2.3_delta.txt`](../../tools/knowledge_graph/prompts/navigation_v2.3_delta.txt).
- Registro: [`registry.json`](../../tools/knowledge_graph/prompts/registry.json).

O prompt conserva **byte a byte** a base completa arquivada da campanha v2.2, inserindo um delta de 518 palavras antes do contrato de confiança. O ponto de partida foi a proposta de 405 palavras do audit. O arquivo histórico `proposed_system_prompt.txt` não foi substituído: o preflight registra seu hash como proposta anterior e congela o hash da variante refinada.

| Conteúdo | SHA256 |
|---|---|
| A: v2.2 arquivado | `a0d9c13066957dfac30c9185c553cff7bf05b305bddaf6b538eab4db36afda89` |
| B: v2.3 completo | `1ad5574d6d6c686769ec26514408282fdded3b4686147e3dbf65790560626c61` |
| Schema v2 compartilhado | `46574a4da45c0728704cd368ae8364ffb60a50d20e64e29082402492dc323e30` |

O registro distingue prompts completos de componentes. Assim, o contrato de confiança e `EXPERIMENT MODE` aparecem uma única vez. O A/B usa `concepts_relations`; no uso comum da variante, outros modos substituem somente o sufixo de modo reconhecido.

| Mudança | Benefício esperado | Risco a avaliar |
|---|---|---|
| Assuntos principais e secundários desenvolvidos; sem quota | Menos menções incidentais transformadas em temas | Omitir assuntos secundários úteis |
| Rótulos específicos e compreensíveis em português | Encontrabilidade fora do contexto da página | Perder precisão de um termo histórico |
| Abstrações modernas semanticamente sustentadas | Recuperar assuntos sem exigir coincidência literal entre idiomas | Projetar doutrinas posteriores |
| Obras, pessoas e seções corretamente classificadas | Preservar bibliografia sem criar conceitos a partir de títulos | Suprimir um problema bibliográfico real |
| Revisar ator, modalidade e alvo completo de cada posição | Separar tese adversária, réplica, premissa concedida e hipótese | Menos relações quando a voz é ambígua |
| Evidência suficiente, sem duplicação bilíngue automática | Reduzir tokens mantendo significado e autoria | Uma versão omitir uma divergência relevante |

Continuam presentes IDs locais, distinção entre tradução e exegese, limites de obra/voz, hipóteses, relações `AFFIRMS`/`DEFENDS`/`REFUTES`, grounding e autoconfiança não calibrada. Não há exemplos com respostas das páginas do A/B, listas de sinônimos ou consultas hipotéticas adicionadas à saída.

### Fundamentos usados

A [análise conceitual da UN Library](https://www.un.org/depts/dhl/unbisref_manual/indexpolicy/concept.htm) orienta selecionar assuntos efetivamente tratados, considerando especificidade e necessidades de busca; a [seleção de termos](https://www.un.org/depts/dhl/unbisref_manual/indexpolicy/term.htm) distingue essa análise da escolha de vocabulário. As páginas oficiais retornaram 403 na abertura direta; o conteúdo disponível no índice de busca oficial foi consultado.

[KPEval, ACL 2024](https://aclanthology.org/2024.findings-acl.117/) motiva separar fidelidade, diversidade e utilidade de mera coincidência lexical. [Absent keyphrase generation, NAACL 2024](https://aclanthology.org/2024.findings-naacl.102/) sustenta a distinção entre expressão literal e assunto ausente lexicalmente; seus resultados em outros conjuntos não demonstram eficácia na Patrística.

[SKOS Primer](https://www.w3.org/TR/skos-primer/) orienta manter conceitos e suas formas lexicais separados. Aliases globais ficam para outra etapa. [Document Expansion by Query Prediction](https://arxiv.org/abs/1904.08375) informa uma possível projeção futura de busca; consultas geradas não são relevância independente nem devem inflar a extração.

## Política de localização de evidências

`evidence_matching.py` e a política **`local-quote-v2`** permanecem disponíveis, inclusive como padrão para runs comuns sem política explícita. A nova **`local-quote-v3`**, em [`evidence_locator.py`](../../tools/knowledge_graph/playground/evidence_locator.py), é usada no replay comparativo e nos dois braços do A/B (`kg-validation-7`). Não houve redução do limiar fuzzy de 0,90.

A política preserva a citação do modelo e o texto fonte. Retorna método, aceitação/revisão, localização, resolução da posição, substring original, offsets em pontos de código Unicode, operações de edição e marcadores editoriais ignorados com suas posições e definições. `semantic_support` permanece `not_evaluated`.

- NFKD/casefold, ligaturas e diacríticos permitem localizar diferenças tipográficas; offsets continuam apontando para os caracteres originais, inclusive sequências combinantes.
- Hifenização de quebra de linha só é unida entre letras, considerando marcas combinantes; operações e números não são removidos globalmente.
- Sobrescritos e `(29)` só podem ser ignorados quando há definição explícita do mesmo número em bloco de notas/aparato da página e contexto de prosa sem indicadores numéricos. Marcadores não comprovados permanecem candidatos à revisão.
- Mudanças de números, operadores, negação, nomes/palavras capitalizadas, grafias gregas substantivas e scripts misturados são conservadoramente encaminhadas à revisão. Confusáveis visuais não autorizam aceitação.
- Ocorrências repetidas não ganham uma posição única inventada. Citações cortadas dentro de palavras ou terminadas em hifenização incompleta exigem revisão.
- `located=true` pode significar que há candidato para inspeção. Só `accepted=true` e `position_resolved=true` autorizam o destaque automático de uma posição única. Nenhum desses campos aprova a assertiva histórica.

Há limites de tamanho e de candidatos para evitar buscas combinatórias. Essas regras podem aumentar a carga de revisão; não se deve usar a taxa de aprovação do localizador como medida isolada de qualidade semântica.

### Resultado real do replay

Relatório: [`.work/kg_campaign_prompt_audit/locator_comparison.md`](../../.work/kg_campaign_prompt_audit/locator_comparison.md). Acompanham `.json` agregado e `.jsonl` com diagnósticos por run e evidência.

Foram reproduzidas **1.031 respostas / 18.034 evidências**, com **zero divergências entre o replay v2 e a validação histórica**:

| Método | v2 | v3 |
|---|---:|---:|
| Exata | 6.506 | 6.479 |
| Normalizada | 9.384 | 9.323 |
| Fuzzy aceita | 1.138 | 137 |
| Marcador editorial aceito | 0 | 18 |
| Ambígua | 3 | 41 |
| Candidato somente para revisão | 0 | 1.770 |
| Não encontrada | 1.002 | 265 |
| Locator inexistente | 1 | 1 |

**21 aceitações novas**: 18 com marcadores editoriais e 3 por normalização. **740 evidências antes não aceitas agora têm passagem ou candidato localizado**, incluindo essas 21. Há **1.092 regressões de aceitação automática**: 491 diferenças lexicais gregas, 271 alterações de nomes/palavras capitalizadas, 80 alterações de palavra, 70 diferenças de script, 48 substituições lexicais maiores, 45 terminações truncadas, 26 diferenças de diacríticos em negativos gregos, 25 cortes de palavra, 19 ambiguidades e 17 diferenças protegidas. Isso não significa que todas sejam erros do modelo: muitas são OCR a revisar.

A aprovação automática por página muda de **605 para 390**. O localizador ficou mais conservador; esse resultado não é uma melhoria semântica demonstrada e não altera os status históricos. Os dois braços novos usam a mesma política, para não confundir efeito de prompt com efeito de validação.

Exemplos verificáveis:

| Run / página / evidência | Resultado |
|---|---|
| 195 / PG001:174 / ev2 | Hifenização depois de marca grega combinante localizada por normalização |
| 740 / PG001:719 / ev10 | `(59)` relacionado a nota editorial; substring fonte preservada |
| 936 / PG004:173 / ev11 | `²⁹` localizado com definição de nota da mesma página |
| 659 / PG001:638 / ev19 | `70` em texto simples permanece protegido, sem remoção automática |
| 804 / PG004:41 / ev1, ev19 | Marcadores não comprovados permanecem para revisão |
| 1006 / PG004:243 / ev11, ev13 | Passagem repetida; nenhuma posição única fabricada |
| 232 / PG001:211 / ev11 | Citação corrompida com scripts distintos não convertida artificialmente em siríaco |
| 1056 / PG026:72 / ev9 | Caso suplementar de `⁹⁷`: candidato localizado, mas definição editorial não comprovada; continua em revisão |

As fixtures incluem ainda runs 121, 779, 989 e controles de negação, números, operadores, nomes, truncamento e scripts misturados. São testes de localização lexical, não gold semântico.

## Executar o A/B manualmente

No terminal local, a partir de `/homessddata/Projects/pdfocr`, com `.venv` ativado:

```bash
# Já executado: prepara outro experimento, sem chamadas, se necessário.
python scripts/playgrounds/kg_extractor_playground.py ab-prepare \
  --audit-dir .work/kg_campaign_prompt_audit \
  --name "KG navigation v2.3 A/B" \
  --output .work/kg_ab_navigation_v23/preflight.json
```

O experimento entregue já é **`EXP-13073b566bc1`**; não é necessário preparar novamente. Seu preflight verifica 24 snapshots, todos os blocos/segmentos, os hashes de OCR e imagens arquivadas e a reprodução do transporte das imagens. Os pedidos de A são idênticos aos originais. Em B, só muda a mensagem de sistema. O schema, usuário, imagem, modelo, configuração e preços são iguais. `reasoning_effort` está **omitido no pedido**, não enviado como `null` nem definido para `low`.

O plano conserva a ordem aleatória da semente `20261101`. Não consulta resumos ou OCR atuais para substituição. Imagem ausente, hash divergente ou pedido não reproduzível provoca erro explícito. Nenhuma chave é lida durante preparação; os pedidos gravados não contêm cabeçalhos de autorização.

```bash
# Somente você executa esta etapa paga. A chave não fica no código ou na base.
read -rsp 'META_API_KEY: ' META_API_KEY
export META_API_KEY

# Smoke de até 8 chamadas lógicas; a agenda restante continua pendente.
python scripts/playgrounds/kg_extractor_playground.py ab-run \
  --experiment EXP-13073b566bc1 --concurrency 2 --max-calls 8

# Retoma o mesmo experimento até esgotar a agenda.
python scripts/playgrounds/kg_extractor_playground.py ab-run \
  --experiment EXP-13073b566bc1 --concurrency 2
```

São **96 inferências independentes**: 24 páginas × A/B × duas repetições. O cache é ignorado em todas. Saídas completas, inclusive JSON inválido, são terminais e não são reexecutadas no resume. Não há reparo por outra chamada de modelo.

O pequeno agendador A/B usa o executor de requests existente, com pool limitado de threads, submissão progressiva e lock por experimento. `--concurrency` aceita qualquer inteiro positivo, inclusive 20; o pool efetivo é limitado às chamadas pendentes após aplicar `--max-calls`. O antigo teto local de 16 foi removido. O `corpus-run` existente continua usando multiprocessing/spawn. SQLite continua em **WAL**. Retries HTTP preservam o perfil congelado da campanha: até 3 retries por chamada, jitter/backoff, timeout de 300 s, somente falhas transitórias. Assim, `--max-calls 8` limita execuções lógicas, não tentativas HTTP nem cobrança remota.

`Ctrl-C`/`SIGTERM` deixam de agendar e drenam requisições em andamento. Após encerramento abrupto, registros em execução tornam-se `interrupted`; não são repetidos automaticamente. Uma nova tentativa, com outra linha de run, exige `--retry-interrupted`. Esse caso tem resultado remoto e custo potencial desconhecidos. Erros já terminais não são repetidos silenciosamente. Código de saída 1 indica runs inválidas/erro/interrompidas; não significa que o plano inteiro precisa ser executado de novo.

O plano congela também os hashes dos arquivos da política de validação. Se essa implementação mudar, `ab-run` falha e pede outro experimento, evitando comparar resultados sob políticas diferentes.

### Orçamento

Tarifas congeladas fornecidas pelo usuário: Meta Contributor, US$ 0,10/M entrada e US$ 0,20/M saída. As 24 execuções originais consumiram 322.586 tokens de entrada e 265.046 de saída, com custo estimado de US$ 0,0852678.

Quatro execuções semelhantes por página dão **US$ 0,3410712**. Espere aproximadamente **US$ 0,35** se o comportamento permanecer semelhante; o prompt B é um pouco maior e a saída pode mudar. Com todas as 96 saídas chegando a 32.768 tokens, usando a entrada histórica como aproximação, o valor seria cerca de **US$ 0,76**, mais o delta de entrada e eventuais retries. Isso não é teto de cobrança. Tokens de raciocínio já estão incluídos na saída total, sem soma duplicada.

## Relatório e revisão

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-report \
  --experiment EXP-13073b566bc1 \
  --output .work/kg_ab_navigation_v23/report.md
```

Exporta Markdown, JSON, CSV, `report.review_packets.jsonl` (24 páginas com fontes e quatro saídas) e `report.review_template.json`. O relatório preparado agora contém zero resultados e 96 decisões pendentes; não afirma superioridade de B.

As métricas automáticas incluem validade estrutural, localização/exatidão, duplicação lexical, comprimento, tokens, latência, custo e variabilidade entre repetições. `object_comparisons` usa o comparador conservador existente: descrições/tipos/contextos, diferenças de evidência, relações e correspondências incertas. IDs locais isolados não são tratados como equivalência.

Copie o template para um arquivo separado, por exemplo `decisions.json`. Para adjudicar, preencha revisor, confirmação de inspeção da fonte, independência do gerador e notas; cada dimensão recebe 0, 1, 2 ou `null`. O hash da resposta é verificado. Dimensões: utilidade de rótulos, cobertura/omissões, conceito versus entidade, não redundância, abstrações sustentadas, atores, alvos/posições, hipóteses e divergências bilíngues. A comparação semântica só calcula diferenças B−A nos pares efetivamente adjudicados.

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-report \
  --experiment EXP-13073b566bc1 \
  --reviews .work/kg_ab_navigation_v23/decisions.json \
  --output .work/kg_ab_navigation_v23/report.md
```

Os templates são regenerados; o arquivo separado de decisões nunca é reescrito. Autoavaliação de confiança não conta como adjudicação. Mais conceitos ou relações não demonstra ganho.

## Diagnóstico de busca

[`navigation_queries.json`](../../tools/knowledge_graph/playground/fixtures/navigation_queries.json) contém quatro intenções em português: debate de fatalismo/providência, autenticidade das Recognitiones, conhecimento divino/humano e cosmogonias alegóricas. Oito exemplos positivos/negativos estão ligados às fontes, mas são **propostas provisórias**, não relevância independente. Não foi atribuída artificialmente relevância à defesa da divindade do Filho em uma página deste conjunto apenas por ser uma intenção desejada.

```bash
# Já gerado para o experimento entregue. Recusa sobrescrever decisões existentes.
python scripts/playgrounds/kg_extractor_playground.py ab-search-prepare \
  --experiment EXP-13073b566bc1 \
  --output .work/kg_ab_navigation_v23/search_judgments.json

python scripts/playgrounds/kg_extractor_playground.py ab-search-report \
  --experiment EXP-13073b566bc1 \
  --judgments .work/kg_ab_navigation_v23/search_judgments.json \
  --output .work/kg_ab_navigation_v23/search_report.json --k 5
```

O revisor deve julgar todas as 24 páginas por consulta: 0 = não relevante, 1 = discussão secundária útil, 2 = discussão substancial. O avaliador exige `status=adjudicated`, revisor, fonte conferida, independência, justificativa e snapshot correto. Páginas não julgadas não viram negativos. Só produz precision@k, recall@k, reciprocal rank e nDCG@k quando há pool completamente adjudicado e ao menos um positivo; saídas ausentes também suspendem a métrica daquele braço.

O mesmo BM25 local, tokenizador e parâmetros são usados em resumo atual sozinho, resumo + conceitos A e resumo + conceitos B, separadamente por repetição. Projeta `label + meaning`, sem consultas/sinônimos gerados, acumulados de páginas anteriores ou ID global. O relatório inclui rankings e hashes de documentos/configuração. É diagnóstico de 24 páginas, sem integração ao Indexador de produção, stemming ou embeddings; não permite concluir ganho no corpus inteiro.

Na PG001:595, a própria fonte resumida tem uma divergência: o resumo diz autor posterior a Orígenes, enquanto o OCR b2 diz `ante Origenem` e `Origene junior dici non potest, sed paulo senior`. A proposta de relevância registra isso. Revisar contra o OCR evita atribuir todo problema de fidelidade ao prompt KG.

## Reproduzir a validação offline

```bash
python scripts/playgrounds/kg_extractor_playground.py locator-replay \
  --campaign EXP-76db1db8b118 --expected-runs 1031 \
  --output .work/kg_campaign_prompt_audit/locator_comparison.md
```

Conexão SQLite `mode=ro` e `query_only`; resultados processados por run. O replay não inicializa `Store`, não usa provider e não grava validações na base. O fingerprint histórico antes/depois permanece `aab4ae5bdffa8792b6d8f44ebaa8030c8948ddb9689d2271d6241583db9eff7c`. O verificador do audit também confirmou plano, fontes, segmentos e amostra preservados.

Para uma run **nova** que você queira revalidar e registrar, `validate --run ID --matching-policy local-quote-v3` é uma ação explícita que acrescenta uma validação; não reescreve saída/status original. Não foi executada para a campanha histórica nesta entrega.

## Arquitetura e arquivos

| Arquivo/módulo | Responsabilidade |
|---|---|
| `playground/evidence_locator.py` | Política v3 com offsets e revisão; v2 preservada |
| `playground/validation.py` | Seleção explícita de política; estrutura e semântica separadas |
| `playground/validation_replay.py` | Replay somente leitura, fingerprint, MD/JSON/JSONL |
| `playground/ab_runner.py` | Preflight, pedidos congelados, agenda, interrupção/cache/retries |
| `playground/ab_reporting.py` | Pares, métricas, comparação, pacotes e adjudicação |
| `playground/retrieval_diagnostic.py` | Fichas de relevância e BM25 diagnóstico |
| `playground/prompt_registry.py`, `prompts/registry.json` | Variante completa sem duplicar componentes |
| `playground/experiment_runner.py` | Impede que resume genérico contorne o executor A/B |
| `scripts/playgrounds/kg_extractor_playground.py` | Novos comandos e seleção de política no validate |
| `.gitignore`, `docs/kg_extractor_playground.md` | Ignorar os novos artefatos locais e documentar o fluxo |
| `tests/test_kg_evidence_locator_v3.py`, `tests/fixtures/kg_locator_audit.json` | Regressões reais e controles negativos |
| `tests/test_kg_ab_runner.py` | Agenda, congelamento, mocks, resume, proteção e relatórios |

Os módulos ficam em `tools/knowledge_graph/`. Os bancos e grandes artefatos de `.work/` são locais/ignorados, sem commit de resultados gerados.

## Testes e limites

Testes executados via Native Lab MCP, com mocks e servidores locais de teste, sem chamada paga: **313 aprovados na suíte**, seguidos por **17 aprovados no fechamento A/B** (15 repetidos, dois novos; **315 casos distintos**). Os XMLs JUnit estão em `.work/kg_ab_navigation_v23/tests.xml` e `ab_tests_final.xml`; `delivery_checks.json` registra contagens, ausência de respostas novas e fingerprint histórico. A suíte cobre schema/referências, evidências, confiança, providers, playground, preparação OCR, corpus runner, preços e endpoints locais, além dos novos testes A/B.

Não houve alteração de frontend; não se aplica build do site público. Sanitizers nativos não se aplicam a estes módulos Python. O A/B real ainda não possui respostas novas, adjudicações ou ganho de recuperação demonstrado. As inferências seguintes devem ser iniciadas explicitamente pelo usuário.
