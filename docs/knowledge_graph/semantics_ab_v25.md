# A/B/C/D — contratos semânticos dos objetos do KG

Preparado em 2026-11-01, sem inferência. Experimento: **`EXP-55b6938cd135`**. Este é o ID a executar para a comparação ampliada; não é necessário executar também `EXP-da832a5e4e44`, que foi preservado com suas 192 chamadas ainda pendentes.

## O que a proposta testa

Os casos revisados mostram que uma leitura razoável da fonte pode resultar em objetos de grafo enganosos: uma frase pode resumir corretamente uma controvérsia, mas ser um alvo incorreto de `AFFIRMS` quando contém a posição do participante e a correção do crítico. Isso motiva definir a semântica dos objetos antes das salvaguardas. Não demonstra, por si só, que todos os erros do modelo sejam problemas de especificação.

O braço D apresenta primeiro o uso esperado pelo visitante: descobrir assuntos, encontrar páginas pertinentes, identificar participantes, examinar atribuições e seguir relações verificáveis. Define:

- **Conceito:** assunto pesquisável; `label` conciso e reutilizável, com os qualificadores necessários; `meaning` contextual. Um rótulo reutilizável não deve virar um assunto genérico que reúna páginas pouco relevantes.
- **Entidade:** pessoa, grupo, instituição, obra, lugar ou referência identificável. A identidade bibliográfica de uma obra é distinta de um assunto como sua autenticidade.
- **Proposição:** um conteúdo coerente quanto à voz e à modalidade. Separar posições opostas sem eliminar condições e negações. Quando a voz não é identificável, não fabricar um autor; conservar o conteúdo sustentado e registrar a atribuição pendente. O schema atual não tem campo `holder`; evidências e relações apropriadas tornam a autoria inspecionável.
- **Evento:** ocorrência narrada, com status narrativo e tempo quando sustentados; não uma confirmação histórica independente.
- **Relação:** vínculo cujo ator, predicado e alvo completo possam formar uma frase fiel à passagem. Verbalizar uma relação é um teste de suporte na fonte, não apenas de fluência. Omitir uma aresta enganosa não justifica descartar outras sustentadas.

Não são incluídos no prompt nomes, datas ou respostas das páginas de teste. Eventos foram acrescentados à definição operacional porque já fazem parte do schema, que permanece v2.

O modo `concepts_relations` de D também muda: analisar assuntos e participantes, verificar a existência de argumentação expressa, representar proposições e participantes sustentados e só então construir relações. Claims e eventos úteis fora de argumentos continuam permitidos. Não há contagem mínima, obrigação de inventar atores ou exigência de argumentação em páginas administrativas. `concepts_only` mantém suas estruturas opcionais vazias.

## Comparação controlada

| Braço | Variante | Intervenção |
|---|---|---|
| A | `concepts-v2.3-navigation-confidence` | Controle anterior |
| B | `concepts-v2.4-stance-confidence` | A com conferência final consolidada |
| C | `concepts-v2.4-stance-examples-confidence` | B com três exemplos sintéticos contrastivos |
| D | `concepts-v2.5-semantics-confidence` | A com contratos semânticos iniciais e modo operacional |

**Comparação principal: D−A.** D não inclui o novo check de B nem os exemplos de C. D−B e D−C comparam estratégias diferentes. D é uma intervenção conjunta: este desenho não identifica separadamente os efeitos do preâmbulo, da posição das instruções e da decisão operacional de cobertura. Se houver ganho, uma ablação posterior pode separar esses fatores.

Os prompts A/B/C permanecem byte a byte iguais. D preserva o corpo do v2.3, inclusive confiança, descoberta de assuntos, atribuição e regras bilíngues; acrescenta o preâmbulo e substitui somente o trecho final do modo. São 2433 palavras em A, 2527 em B, 2891 em C e 2985 em D, pela contagem de tokens separados por espaço. Palavras não equivalem a tokens do provider.

32 páginas × 4 braços × 2 repetições = **256 chamadas**, 64 por braço. Mesmas 24 páginas de regressão e 8 páginas diagnósticas do A/B/C; nenhuma nova seleção ou inspeção de holdout. A ordem é novamente embaralhada com a seed herdada `20261102`. As duas repetições de cada braço usam requisições idênticas e ignoram o cache de respostas.

OCR, resumos v2 completos, metadados, imagens arquivadas e bytes de transporte, schema, validação, preços e configuração de inferência são reutilizados dos registros congelados. Modelo `muse-spark-1.3-contributor`, facsímile ligado, limite de saída 32768, **`reasoning_effort` omitido**. Isso deixa o default de esforço a cargo do provider, não configura explicitamente `none`.

Custo estimado para o conjunto: **US$ 0,986**, aproximadamente **US$ 0,99**. A estimativa usa consumo histórico e preços congelados, com aproximação do acréscimo de tokens de prompt; comprimento de saídas e retries podem mudar o total. Nenhuma chamada foi feita na preparação.

## Executar e retomar

Com `META_API_KEY` no ambiente do terminal, um smoke opcional de até oito chamadas:

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-run \
  --experiment EXP-55b6938cd135 --concurrency 2 --max-calls 8
```

Executar ou retomar as restantes:

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-run \
  --experiment EXP-55b6938cd135 --concurrency 20
```

O total é 256, incluindo o smoke; a retomada não repete chamadas terminais. O número de workers não tem teto arbitrário de 16. `invalid` permanece um resultado do experimento, sem reparação por outra chamada. O comportamento de interrupção e `--retry-interrupted` está descrito no [A/B/C v2.4](stance_ab_v24.md).

## Relatório e revisão

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-report \
  --experiment EXP-55b6938cd135
```

Saída padrão: `.work/kg_ab_semantics_v25/report.md`, mais JSON, CSV, `report.review_packets.jsonl` e `report.review_template.json`. O relatório reconhece os quatro braços, as seis comparações entre braços e as repetições de cada um. Pode ser regenerado durante a execução.

A revisão deve comparar, por página e repetição:

1. Utilidade e especificidade dos rótulos, sem premiar assuntos incidentais nem rótulos genéricos.
2. Separação entre rótulo reutilizável e interpretação contextual, conceitos e entidades.
3. Proposições com voz coerente, condições e modalidade preservadas.
4. Fidelidade da frase formada por ator, predicado e **alvo completo**; evidência deve sustentar a relação, não só mencionar os extremos.
5. Argumentos sustentados recuperados e perdidos; atores inventados ou vozes indevidamente identificadas.
6. Variação entre as duas repetições, custo e tamanho da resposta.

O template inclui `object_contract_review` para registrar esses problemas. Esses campos são notas de revisão; não geram automaticamente escores de qualidade. Escores semânticos exigem adjudicação independente; mais nós, menos arestas, citações localizadas ou confiança alta não demonstram melhoria.

Copie o template para um arquivo de decisões separado e, após revisão, forneça-o em `ab-report --reviews caminho/decisions.json`. Os templates são regenerados. O diagnóstico BM25 existente também aceita A/B/C/D; use `ab-search-prepare` e `ab-search-report` com `--output` dentro deste diretório. Métricas de relevância ficam pendentes enquanto faltarem julgamentos independentes.

O caso Dodwell continua tendo dois erros distintos: inversão cronológica no resumo sobre Orígenes e mistura de vozes no KG. A ausência de Dodwell no resumo não é tratada como defeito. Leia a [nota de fonte](../../.work/kg_ab_stance_v24/source_quality_note.md). Os inputs não foram corrigidos para um braço específico.

## Reproduzir a preparação

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-prepare \
  --study semantics-v25 --from-prepared EXP-da832a5e4e44 \
  --output .work/kg_ab_semantics_v25_repeat/preflight.json
```

Cria outro experimento, sem inferência. A seleção, seed e configuração são herdadas de `--from-prepared`; `--extra-pages`, `--seed`, `--source-experiment` e o diretório de auditoria são parâmetros dos preparadores anteriores, não mudam este conjunto congelado. O preparador exige que o A/B/C fonte esteja inteiramente não executado, para não duplicar chamadas pagas silenciosamente. Recusa imagens alteradas, snapshots divergentes, configurações diferentes, prompts antigos modificados ou saída preflight já existente.

Não modifica nem cancela a fila anterior. Execute o novo ID para este estudo; a existência da fila antiga não a inicia automaticamente.

## Arquivos e validação

- `tools/knowledge_graph/prompts/semantics_v2.5_contract.txt`: preâmbulo operacional.
- `tools/knowledge_graph/prompts/semantics_v2.5_modes.json`: instruções de modo específicas de D.
- `tools/knowledge_graph/prompts/extract_concepts_v2.5.system.txt`: prompt completo congelado; registry mantém as variantes anteriores.
- `tools/knowledge_graph/playground/semantics_study.py`: preparação a partir do A/B/C congelado, validação de isolamento e proveniência.
- `prompt_registry.py`: modos específicos de uma variante, sem mudar os defaults históricos.
- CLI, agendamento e relatório: suporte a quatro braços, retomada e revisão dos contratos.

Preflight real: 256 runs `planned`, zero outputs, dois exemplares de cada requisição, WAL confirmado. Hashes das campanhas anteriores e de todas as linhas das 192 runs do A/B/C fonte foram conferidos antes/depois e permaneceram iguais. Comprovantes em `historical_before.json` e `historical_after.json` no diretório de trabalho.

Testes via **Native Lab MCP**: **331 passaram**, incluindo 42 testes de A/B, A/B/C e A/B/C/D. Foram verificados preservação dos prompts históricos, schema, modos, imagens e snapshots; falhas fechadas de hash e holdout; ausência de credenciais na preparação; cache bypass; duas repetições independentes; retomada; relatórios e busca com D. Providers foram simulados. A eficácia semântica do novo prompt ainda não foi medida por inferência real.
