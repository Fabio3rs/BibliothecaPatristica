# Contrato dos campos da extração KG — v2.6 experimental

Revisão de 2026-11-02. A variante `concepts-v2.6-field-contract-confidence` torna explícitas as convenções do projeto para campos, enums e direção de relações. Mantém `kg-extraction-v2`, a finalidade de navegação, os modos do v2.5 e o protocolo de confiança. Não define termos gerais como OCR ou Patrística.

## O que foi conferido

- `prompt_registry.py`: montagem do prompt, modos, schema e hashes enviados ao provedor.
- `snapshot_v2.py` e `ocr_adapter.py`: fontes presentes no payload e preparação do texto dos blocos.
- `kg-extraction-v2.schema.json`: propriedades, enums e limites efetivamente aceitos.
- `validation.py`: validação estrutural, localização das citações e revisão semântica separada.
- Prompts v2.3–v2.5 e o verificador de segunda passagem.
- Leitura da base do playground em `mode=ro`, incluindo os prompts arquivados das runs 1542/1595 (`PG001:463`), 1419 (`PG004:276`), 1590 (`PG001:595`) e 1640 (`PG001:638`, verificação). Nenhum holdout foi lido para esta revisão.

O prompt D arquivado na run 1542 é idêntico ao v2.5 registrado. Seu hash é `8926f528d8a0a540b836f5d288acdacf869fe56998f538d3089148cfed4111ec`. O payload contém todos os segmentos atuais, OCR, contexto bibliográfico, contexto acumulado marcado como tal, indicação de fac-símile e schema. A lacuna principal era a definição operacional de campos, não a ausência desses dados.

## Lacunas encontradas e convenções propostas

O v2.5 explica bem os objetos e várias salvaguardas, mas quase todos os campos do schema não têm `description`. O modelo recebia alguns enums apenas como nomes possíveis. O novo glossário cobre cada campo de saída e cada valor enumerado, sem mudar o schema histórico.

| Campo | Convenção operacional |
|---|---|
| `concept.label` / `meaning` | Assunto pesquisável e reutilizável / interpretação e qualificações nesta página. Um assunto secundário distinto não precisa desaparecer dentro do significado de outro. |
| `grounding` | `explicit`: conteúdo diretamente expresso; `inferred`: exige passo interpretativo defensável. Tradução ou rótulo moderno, por si sós, não tornam o assunto inferido. |
| `salience` | `primary`: central numa discussão substantiva; `secondary`: tratamento útil de apoio/comparação; `incidental`: mera menção, normalmente omitida. Pode haver vários assuntos primários. |
| `entity.type` | Distingue indivíduo, coletivo, instituição, obra, lugar, referência a passagem e participante fora dessas classes. `WORK` não é intercambiável com `SOURCE_REFERENCE`. |
| `entity.origin` | Base da identificação: página atual, incluindo resumo atual, ou metadados bibliográficos. Não é origem histórica da pessoa nem prova de sua postura. |
| `claim.proposition` | Posição coerente, preservando condições e negações; não mistura vozes opostas nem a correção editorial com a opinião corrigida. |
| `claim.mode` | `asserted`: voz corrente afirma; `reported`: atribuição a outra voz; `hypothetical`: suposição/objeção imaginada; `questioned`: pergunta/dúvida aberta; `uncertain`: conteúdo tentativo ou leitura/status não resolvido. |
| Sobreposição de modos | Mantém todas as qualificações na proposição; hipótese/pergunta prevalece quando é o status operativo, mesmo dentro de um relato. Uma posição relatada não muda automaticamente de modo porque outro ator a refuta. |
| `event.narrative_status` | `reported` registra ocorrência narrada, sem confirmação independente; `hypothetical` registra ocorrência imaginada/condicional; `uncertain` registra dúvida ou leitura não resolvida. |
| `event.time_expression` | Expressão temporal fornecida para o evento; `null` se ausente. Data editorial não é automaticamente data do evento narrado. |
| `source_id` / `target_id` | Nós emitidos nesta extração, na direção do predicado. Não são IDs de evidência nem localizadores de entrada. |
| `evidence.source` / `locator` / `quote` | Camada textual / ID exato do bloco ou segmento / passagem contígua no alfabeto original. O `source` da entrada é metadado de proveniência, com outra função. |
| `warnings.code` / `message` | Tipo de limitação e explicação localizada. Não se emite alerta apenas porque há duas línguas ou várias obras. |
| `model_confidence` | Estimativa não calibrada de fidelidade do item às fontes. Não equivale à certeza da voz histórica, verdade externa, importância temática ou similaridade da citação. |

Uma proposição hipotética fielmente extraída pode ter confiança alta. Um assunto central pode depender de inferência. Uma citação perfeitamente localizada pode acompanhar uma relação semanticamente errada. Os campos medem dimensões diferentes.

## Predicados: convenções, não enum fechado

`relations.predicate` é uma string livre no schema. Na leitura agregada das saídas analisáveis de `EXP-55b6938cd135`, apareceram 28 nomes, incluindo `EDITED` e `EDITED_BY`. O validador não verifica automaticamente se esses nomes expressam a ação correta nem se seus endpoints têm os tipos semanticamente adequados.

O glossário define os predicados centrais com direção explícita:

| Relação | Leitura |
|---|---|
| ator → `AFFIRMS` → proposição | Afirma ou endossa aquela posição. |
| ator → `DEFENDS` → proposição | Argumenta em favor daquela posição. |
| ator → `REFUTES` → proposição | Argumenta contra aquela posição precisa; não contra a descrição da refutação. Não certifica que a refutação foi bem-sucedida. |
| proposição → `ATTRIBUTES_TO` → ator | A fonte atribui aquela posição ao ator. O alvo é o titular, não o relator nem a pessoa criticada. |
| ator/obra/proposição → `CITES` → obra/referência | Invoca o texto referido. |
| ator/obra/proposição → `INTERPRETS` → texto/conceito | Explica seu significado. |
| tradutor → `TRANSLATES` → obra | Traduz a obra, sem confundir tradução com exegese. |
| proposição/evento/obra → `ABOUT` → conceito | Trata substantivamente do assunto. |
| ator → `PARTICIPATES_IN` → evento | Participa da ocorrência. |

Outros predicados continuam possíveis, com significado e direção inequívocos. O glossário desencoraja duplicatas por sinônimos, inversão e `AFFIRMS`/`DEFENDS` para o mesmo ato. Não há novo enum, normalização retroativa ou restrição estrutural de tipos. Uma ontologia fechada exigiria outra mudança versionada e outra comparação.

## Implementação e riscos

Arquivos:

- `tools/knowledge_graph/prompts/fields_v2.6_contract.txt`: glossário isolado para revisão.
- `tools/knowledge_graph/prompts/extract_concepts_v2.6.system.txt`: prompt completo, com o glossário após a abertura semântica do v2.5.
- `tools/knowledge_graph/prompts/registry.json`: seleção explícita da nova variante; padrão preservado.
- `tests/test_kg_field_contract.py`: compatibilidade histórica, cobertura dos campos/enums, isolamento da requisição Meta e execução offline sintética.

Remover o glossário do novo prompt reproduz exatamente o v2.5 em cada modo. Não foram alterados o schema, o protocolo de confiança, as instruções bilíngues, a montagem do OCR, o validador, os prompts antigos, a campanha ou o verificador. O glossário está no prompt efetivamente enviado, não apenas nesta documentação.

O prompt passa de 20.886 para 27.534 caracteres (2.985 para 3.864 palavras por separação em espaços). São 879 palavras adicionais para explicitar a entrada e o contrato de saída. Essa é uma intervenção de definições; o custo em contexto e possíveis conflitos com instruções anteriores precisam ser medidos. Uma futura consolidação pode remover repetições, mas não foi misturada a este teste.

Limitações do schema continuam visíveis: a proposição tem um único `mode`, sem campo próprio para seu titular, e a relação não possui modalidade independente. O glossário preserva qualificações na proposição e exige omitir uma aresta que não possa ser apresentada fielmente. Isso não garante que o Muse obedeça nem substitui a conferência independente.

Os testes verificam contrato, montagem, persistência e preservação histórica. Não demonstram melhora semântica do modelo. Nenhuma inferência paga foi executada nesta revisão.

Validação offline realizada:

- Native Lab MCP: suíte de contrato/confiança/A-B, 86 testes aprovados; após explicitar os papéis de entrada, suíte de contrato/evidências/adaptadores, 109 aprovados (15 testes de contrato em comum). A execução inicial anterior às alterações teve dois erros de setup `Unknown prompt variant`; ambos passaram isoladamente e a suíte completa passou na repetição, sem alteração dos testes A/B ou da montagem de prompts. A causa da intermitência não foi estabelecida.
- Quatro requisições históricas (1542, 1595, 1419, 1590), em três páginas distintas: snapshots, payloads, imagens arquivadas e hashes de transporte reproduzidos; somente o sistema muda na preparação do candidato. Manifesto local em `.work/kg_field_contract_v26/frozen_preflight.json`; nenhuma nova execução foi criada na base histórica.
- Demonstração inteiramente sintética pela CLI: duas variantes × duas repetições, quatro execuções `completed`, sem inferência, em `.work/kg_field_contract_v26/synthetic_demo.db`, experimento `EXP-2eea69fde71f`. Isso testa o fluxo, não a compreensão do Muse.

## Experimentar

Prévia sem inferência, usando as duas variantes na mesma página atual:

```bash
python scripts/playgrounds/kg_extractor_playground.py run \
  --pages PG026:71 --provider meta --model muse-spark-1.3-contributor \
  --prompts concepts-v2.5-semantics-confidence concepts-v2.6-field-contract-confidence \
  --mode concepts_relations --facsimile on --repetitions 2 --dry-run
```

Esse comando prepara quatro execuções. Para executá-las posteriormente, use `resume --experiment EXP_ID` com a chave no terminal; a preparação por si só não chama o provedor. As duas repetições ignoram o cache. Não especificar `--reasoning-effort` preserva sua omissão na requisição. Planos já preparados continuam com seus prompts congelados.

Se o OCR atual tiver mudado em relação ao resumo, o carregador informa o conflito. `--allow-source-drift` permite estudar explicitamente essa combinação, mas ela não é o mesmo snapshot do benchmark anterior. Para comparar com resultados históricos, reutilize os snapshots arquivados, sem substituir silenciosamente o OCR pelas correções novas.

Comece por poucos casos de atribuição, hipótese, bibliografia e assuntos secundários já estudados, com duas repetições por variante. Confira uso de enums, direção das relações, retenção de assuntos úteis e citações, além de tamanho, truncamento e custo. Mais nós ou confiança maior não são critérios de sucesso. O comando `run` oferece a matriz exploratória; os estudos A/B congelados existentes permanecem inalterados e não incorporam automaticamente a nova variante.
