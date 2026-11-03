# KG v3.1 experimental: vozes, ações e argumentos

Esta versão testa se uma representação mais explícita ajuda a separar a voz que
apresenta uma proposição dos participantes da ação descrita. Não há evidência de
melhoria do modelo ainda: a implementação foi validada offline, sem chamadas
pagas. Prompts, schemas e resultados anteriores permanecem disponíveis.

## Variantes para comparação

| Braço | Prompt | Schema | Mudança |
|---|---|---|---|
| A | `concepts-v3-statements-confidence` | v3 | Controle existente |
| B | `concepts-v3.1-order-confidence` | v3 | Prefixo com sequência de análise; restante do system, schema e payload iguais a A |
| C | `concepts-v3.1-discourse-actions-confidence` | v3.1 | Unidades discursivas e argumentos ancorados por ação |

[Prompt B](../../tools/knowledge_graph/prompts/extract_statements_v3.1-order.system.txt),
[prompt C](../../tools/knowledge_graph/prompts/extract_discourse_v3.1.system.txt),
[schema C](../../tools/knowledge_graph/schemas/kg-extraction-v3.1.schema.json).
Todos os nomes antigos continuam aceitos por `--prompts`. O schema é selecionado
pela variante, sem migração das runs históricas. Mantêm-se os três modos de extração.

B testa a **instrução de sequência**, sem alterar a serialização histórica. C muda
representação, instruções e ordem de apresentação: não isola somente o efeito do
schema. Não assumir que a ordem de campos comprova a ordem do raciocínio interno.

## Contrato novo

`discourse_units` é uma lista de unidades de voz relevantes, com:

- `kind`: `narration`, `quotation`, `reported_speech`, `editorial` ou `uncertain`;
- `speaker_id`: entidade cuja voz é apresentada, ou `null`;
- `parent_unit_id`: unidade que introduz/contém esta voz, ou `null`; sem ciclos;
- `evidence_ids`: passagens que sustentam a delimitação e atribuição.

A voz da unidade não gera automaticamente `AFFIRMS`. Cada claim aponta para sua
`discourse_unit_id` e mantém `proposition`, `mode`, `attributions` e confiança.
Em v3.1, `claims.participants` admite apenas `subject` e `cited_authority`. Papéis
de ações passam a pertencer a `events`.

`events` foi estendido; não existe uma segunda lista `action_frames`. Cada evento
inclui `description`, `discourse_unit_id`, `claim_ids`, `action`, `trigger`,
`modality`, `condition_anchor`, `arguments`, `time_expression`, evidência e confiança.
`claim_ids` aponta para proposições que descrevem a ocorrência, não automaticamente
para a tese combatida. Pode estar vazio.

| Campo | Significado |
|---|---|
| `action` | Verbo inglês em UPPER_SNAKE_CASE que caracteriza a ação narrada |
| `trigger` | Expressão da ação na fonte, com `evidence_id` e `text` |
| `modality` | `narrated`, `hypothetical`, `conditional` ou `uncertain` |
| `condition_anchor` | Trecho da condição; obrigatório para ação `conditional` |
| `arguments[].role` | `agent`, `opponent`, `recipient`, `content`, `theme`, `instrument`, `location`, `other` |
| `node_id` | Entidade participante; para `content`, uma claim; `null` se não resolvido |
| `mention` | Expressão concreta que designa o argumento, inclusive pronome; `null` se implícita |
| `antecedent` | Expressão contextual que resolve o pronome/demonstrativo, ou `null` |

As âncoras usam o texto real de uma evidência. Não recebem offsets inventados pelo
modelo. O localizador procura dentro do trecho da evidência, conserva offsets
Unicode na fonte original e registra posições ambíguas. Há tolerância às diferenças
textuais já admitidas pela política v5; não houve redução global de limiares.

`claim.mode`, `attribution.scope` e `event.modality` são dimensões independentes.
Um narrador pode afirmar diretamente uma proposição condicional que descreve uma
ação também condicional. Identificar o agente de uma ação não atribui a ele a
proposição do narrador. Localizar `illos` tampouco prova quem é seu antecedente.

A projeção offline preserva qualificadores e cria somente conexões dos campos
explícitos: `VOICED_BY`, `INTRODUCED_BY`, `IN_DISCOURSE`, `PRESENTED_IN`,
`DESCRIBES_EVENT`, `HAS_AGENT`, `HAS_OPPONENT`, etc., além das atribuições de claims.
Não deriva `Paulo REFUTES adversários` de um evento condicional. Mantém o evento
como nó e liga os seus argumentos a ele. As conexões ficam em
`validation.graph_projection`, sem reescrever o JSON do modelo. O total dessas
conexões inclui estrutura discursiva; não deve ser interpretado como aumento da
quantidade de afirmações históricas corretas.

## Execução com outro fornecedor

Use a mesma página congelada, imagem e parâmetros entre os braços. No banco local
existente, o case **1034** corresponde a `PG026:72`, utilizado na run **1679**.
`--case-ids` reaproveita a fonte arquivada; `--pages` carrega a fonte disponível no
momento e pode incorporar OCR mais recente.

Exemplo para um endpoint OpenAI-compatible com visão e JSON Schema documentados
pelo fornecedor (substitua os dois placeholders):

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --case-ids 1034 \
  --provider compatible --model 'MODELO' \
  --base-url 'https://ENDPOINT/v1' --api-key-env KG_TEST_API_KEY \
  --vision --structured-output schema \
  --prompts concepts-v3-statements-confidence \
            concepts-v3.1-order-confidence \
            concepts-v3.1-discourse-actions-confidence \
  --mode concepts_relations --facsimile on \
  --repetitions 2 --concurrency 4 --bypass-cache --dry-run
```

Isso prepara **6 inferências**, sem executá-las. Inspecione no `serve`; para iniciar
com a configuração congelada e a chave em sua variável de ambiente:

```bash
python scripts/playgrounds/kg_extractor_playground.py resume \
  --experiment EXP_ID_RETORNADO --concurrency 4
python scripts/playgrounds/kg_extractor_playground.py report \
  --experiment EXP_ID_RETORNADO --format markdown \
  --output .work/kg_discourse_v31/model_report.md
python scripts/playgrounds/kg_extractor_playground.py compare --runs RUN_A RUN_C
python scripts/playgrounds/kg_extractor_playground.py serve
```

O servidor padrão é `http://127.0.0.1:8766`. Reinicie-o após atualizar o código.
`--model-configs ARQUIVO.json` aceita uma lista de perfis para testar vários
modelos/provedores na mesma matriz; `--prompts` aceita quaisquer variantes
registradas, inclusive v2.7 e v2.8. Custo: páginas × modelos × variantes × modos ×
políticas de imagem × repetições. Repetições não reutilizam cache.

Configure capacidades conforme o endpoint: `compatible` usa JSON object por
padrão; `--structured-output schema` habilita o contrato nativo quando suportado.
`--vision` não descobre suporte automaticamente. Só passe `--reasoning-support
--reasoning-effort ...` quando o fornecedor aceitar esse parâmetro; se omitido,
nenhum esforço é enviado. O padrão de orçamento continua 32768, ajustável com
`--max-output-tokens`; o limite aceito depende do modelo. Os adaptadores existentes
Ollama/OpenAI/compatible/Meta foram exercitados com mocks, sem teste pago de um
novo modelo. O adaptador não torna uma API arbitrária compatível.

Este experimento usa `run/resume/report/compare`, que aceita schemas distintos.
Os protocolos históricos `ab-*` e `verify-*` continuam congelados nas versões
para as quais foram criados; não reutilize um experimento antigo para v3.1.

## Implementação e inspeção

- `discourse_contract.py`: referências, ciclos, âncoras e projeção qualificada.
- `validation_current.py`: extensão opt-in; despacho histórico preservado.
- `ordered_requests.py`: preserva a ordem `discourse_units` antes de `claims` no
  schema do payload e do transporte, apesar da ordenação canônica usada no banco.
  Persiste versão, ordem e hash dos bytes; valida o hash antes de enviar. Pedidos
  antigos mantêm os bytes originais. O nome de schema enviado usa `v3_1` para não
  conter ponto em provedores que restringem nomes; o JSON mantém `kg-extraction-v3.1`.
- Runner, armazenamento, comparação e relatório aceitam as novas estruturas.
- Interface mostra unidades, eventos, papéis, condições, âncoras e relações
  projetadas; cada âncora localizada abre o texto correspondente. Revisões são
  independentes da resposta original. Relatórios separam relações emitidas e
  projetadas, unidades, eventos e âncoras localizadas/ambíguas.

A comparação permanece conservadora. Descrições iguais não significam equivalência
histórica; modalidades diferentes não são igualadas silenciosamente. Relações
v2 sem qualificadores não são automaticamente equivalentes às projeções v3/v3.1.

## Validação offline e limites

`tests/test_kg_discourse_v31.py` cobre os contratos, compatibilidade, referências,
ciclos, ações condicionais, atores distintos, Unicode, hifenização, localização
restrita à evidência, ambiguidades, ordem real dos pedidos com os quatro provedores,
cache, repetições, retomada, revisão, comparação, relatórios e estruturas vazias.

```bash
python -m pytest tests/test_kg_discourse_v31.py -q
node --check tools/knowledge_graph/playground/static/playground.js
```

Artefatos ignorados em `.work/kg_discourse_v31/`:

- `preflight.json` e pedidos preparados: fonte real de `PG026:72`, sem inferência.
  O pedido A reproduziu exatamente o da run 1679, inclusive a imagem; B mudou
  apenas o system no conteúdo enviado. Hashes dos 16 arquivos do protocolo
  histórico de revisão também permaneceram idênticos ao preflight anterior.
- `demo.db`, `demo_experiment.json`, `demo_report.md`: **fixture sintética**, três
  variantes × duas repetições, sem chamadas a modelos e sem medição de acurácia.
- `desktop.png`, `mobile.png`: inspeção pelo Native Lab Playwright.

Para abrir essa demonstração isolada:

```bash
python scripts/playgrounds/kg_extractor_playground.py serve \
  --db .work/kg_discourse_v31/demo.db --port 8877 \
  --benchmark-dir .work/kg_discourse_v31/no-benchmark
```

Nenhuma campanha ou run histórica foi modificada. A nova representação ainda pode
receber um antecedente, uma voz ou uma tese errados: o validador verifica referência
e localização, não a análise histórica. Autoavaliação de confiança continua não
calibrada. A estrutura maior pode aumentar tokens e truncamentos.

Para comparar modelos, revisar previamente os mesmos critérios: voz/titular,
agente, destinatário/oponente, conteúdo citado, condição, evidência e assuntos de
busca preservados. Incluir acertos como controles; uma resposta com mais eventos
não é automaticamente melhor. A evidência inicial sobre uma única página não
generaliza para o corpus. Depois do smoke test, ampliar para páginas congeladas
de obras distintas, mantendo duas repetições e excluindo holdout de calibração.

Validação desta implementação (2026-11-02): **576 testes KG aprovados via Native
Lab**, incluindo 38 casos do módulo novo; `node --check` aprovado. Native Lab
Playwright conferiu `http://127.0.0.1:8877/` em 1920×1080 e 375×812, seleção de
unidades/argumentos, destaque do pronome no OCR, revisão persistida e comparação
v3/v3.1, sem erros de console ou requests malsucedidos. Abrir uma run do histórico
agora também atualiza a aba de prompt para o pedido arquivado correto. Evidências
em `.work/kg_discourse_v31/validation_report.md` e `tests.xml`. Não há build de
frontend neste playground. O servidor de teste foi encerrado após a validação.
