# A/B/C de atribuição e proposições — v2.4

Preparado em 2026-11-01, sem inferência paga. Experimento local: `EXP-da832a5e4e44`.

Para a comparação ampliada com contratos semânticos, use o [A/B/C/D v2.5](semantics_ab_v25.md), experimento `EXP-55b6938cd135`. O A/B/C descrito aqui foi preservado e não precisa ser executado também.

## Hipótese e braços

Queremos preservar assuntos úteis para busca e diminuir relações historicamente enganosas. O estudo não altera o schema v2, o contrato de confiança, o localizador de evidências ou os prompts antigos.

| Braço | Prompt | Diferença controlada |
|---|---|---|
| A | `concepts-v2.3-navigation-confidence` | Controle exato do braço B do experimento anterior |
| B | `concepts-v2.4-stance-confidence` | Substitui somente `FINAL CHECK` por conferência de proposição, ator, direção, evidência e modalidade |
| C | `concepts-v2.4-stance-examples-confidence` | B mais três demonstrações sintéticas contrastivas |

O novo check acrescenta 94 palavras líquidas ao controle; as demonstrações acrescentam 364 palavras a B. O bloco de descoberta de assuntos, as salvaguardas bilíngues e as instruções de confiança são preservados byte a byte. As proposições podem ser separadas por voz; os conceitos de navegação não devem ser fragmentados artificialmente.

Os exemplos tratam de opinião versus correção editorial, relato de refutação versus proposição refutada e ato hipotético versus resposta efetivamente expressa. Incluem relações que devem permanecer, para reduzir o risco de omissão excessiva. São microexemplos explicativos, sem respostas das páginas de teste, sem schema alternativo e sem notas de confiança pré-fabricadas. O campo legado `few_shot` permanece falso para não anexar também a demonstração v1; C usa `synthetic_contrastive_examples: true` e um prompt completo congelado.

Esta é uma adaptação experimental de verificação no mesmo passe. Não implementa verificador supervisionado, Chain-of-Verification independente ou correção por uma segunda chamada. A eficácia ainda precisa ser medida.

## Amostra e congelamento

32 páginas × 3 braços × 2 repetições = **192 chamadas**, seed `20261102`.

- 24 páginas do A/B anterior são controles de regressão. As fontes são suas runs B, repetição 1; o novo A reproduz exatamente as requisições v2.3 anteriores.
- 8 páginas diagnósticas adicionais são selecionadas por volume/status, com ordenação SHA256 determinada pela seed. São excluídas páginas registradas na auditoria, suas vizinhas imediatas e o blind holdout. Não são obras independentes nem um novo holdout formal.
- Cada fonte conserva OCR, todos os segmentos v2, metadados, imagem arquivada, transporte de imagem, configurações e preços históricos. Nas 8 fontes vindas da campanha v2.2, somente o system prompt é substituído pelo controle v2.3; os hashes originais e novos ficam registrados separadamente.
- Cada braço difere exclusivamente no system prompt. Modelo `muse-spark-1.3-contributor`, provider Meta, facsímile ligado, limite 32768, `reasoning_effort` omitido. Não equivale a forçar esforço `none` no servidor.
- Cache de respostas desativado; repetições são chamadas independentes. SQLite em WAL. A campanha original e o A/B anterior permanecem intactos.

Páginas adicionais: `PG001:503` (run 524), `PG001:350` (371), `PG004:192` (955), `PG004:25` (788), `PG001:514` (535), `PG001:455` (476), `PG004:285` (1048), `PG004:101` (864). Esses IDs são fontes históricas, não resultados novos.

Custo estimado: **US$ 0,736** para as 192 chamadas, usando consumo histórico, preços congelados e uma aproximação do acréscimo de tokens de prompt. Não é um teto: comprimento da saída e retries de transporte podem alterar o total.

## Executar o experimento já preparado

A chave `META_API_KEY` deve estar no ambiente do terminal. Preparação, relatórios e testes não a usam.

Smoke opcional de até 8 chamadas da ordem aleatória:

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-run \
  --experiment EXP-da832a5e4e44 --concurrency 2 --max-calls 8
```

Executar ou retomar as restantes:

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-run \
  --experiment EXP-da832a5e4e44 --concurrency 20
```

Não há limite arbitrário de 16. A concorrência deve ser um inteiro positivo; o executor abre no máximo o número de tarefas pendentes. Reexecutar esse comando não refaz outputs terminais, inclusive `invalid`. `Ctrl+C` interrompe o agendamento e aguarda chamadas em voo. Depois de encerramento abrupto, `--retry-interrupted` autoriza explicitamente outra tentativa para desfechos remotos desconhecidos, potencialmente com custo adicional. Não há reparação automática por LLM.

Relatório durante ou depois da execução:

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-report \
  --experiment EXP-da832a5e4e44
```

O diretório padrão para este estudo é `.work/kg_ab_stance_v24/`, sem sobrescrever o relatório v2.3. Também é possível fornecer `--output caminho/report.md`. A interface existente pode ser aberta com `python scripts/playgrounds/kg_extractor_playground.py serve` para inspeção das runs armazenadas; a comparação pareada A/B/C está no relatório CLI.

## Preparar outro experimento equivalente

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-prepare \
  --study stance-v24 --source-experiment EXP-13073b566bc1 \
  --audit-dir .work/kg_campaign_prompt_audit \
  --extra-pages 8 --seed 20261102 \
  --output .work/kg_ab_stance_v24_repeat/preflight.json
```

Isso cria outro experimento, sem inferência. Um caminho de preflight já existente é recusado: retome o ID original ou escolha outro caminho. `--extra-pages 0` permite apenas a regressão de 24 páginas, totalizando 144 chamadas. O comportamento original de `ab-prepare` sem `--study` continua preparando o A/B de navegação v2.3.

## Revisão dos resultados

O relatório exporta Markdown, JSON, CSV, `report.review_packets.jsonl` e `report.review_template.json`. Compara B−A, C−A e C−B, além das duas repetições de cada braço. Métricas automáticas incluem estrutura, localização de evidência, contagens, tokens, raciocínio, latência e custo. Nenhuma é tratada como comprovação semântica.

Copie o template para `decisions.json`; os templates são regenerados. Preencha os escores, verifique as fontes e identifique o revisor antes de usar:

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-report \
  --experiment EXP-da832a5e4e44 \
  --reviews .work/kg_ab_stance_v24/decisions.json
```

A revisão deve registrar arestas incorretas, corretas e corretas ausentes; claims com vozes misturadas; condições perdidas; e assuntos úteis preservados ou omitidos. Os campos `stance_review` são registros para revisão, não contagens automaticamente adjudicadas. A comparação de objetos é conservadora e nunca usa só IDs locais. Os escores pareados são apresentados também por estrato. Um grafo vazio não ganha por evitar todos os erros; relações corretas e cobertura temática são controles necessários.

O diagnóstico de busca existente aceita os três braços, com o mesmo BM25:

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-search-prepare \
  --experiment EXP-da832a5e4e44 --output .work/kg_ab_stance_v24/search_judgments.json
python scripts/playgrounds/kg_extractor_playground.py ab-search-report \
  --experiment EXP-da832a5e4e44 \
  --judgments .work/kg_ab_stance_v24/search_judgments.json \
  --output .work/kg_ab_stance_v24/search_report.json
```

Sem julgamentos independentes completos, as métricas de relevância permanecem pendentes. Esta amostra não demonstra ganhos para o corpus inteiro.

## Defeito conhecido da entrada: PG001:595

A geração v2 `13211`, segmento `s1`, diz que o autor das Recognitiones é **posterior** a Orígenes. O OCR `b2` afirma `ante Origenem` e `Origene junior dici non potest, sed paulo senior`: o proêmio sustenta anterioridade quanto à redação. Trata-se de inversão relevante no resumo, não de mera diferença terminológica.

A ausência de Dodwell e das duas datações no resumo não constitui, por si só, um defeito: o resumo seleciona informações e não precisa inventariar todos os atores e argumentos da página. O extrator recebe também o OCR completo, onde Dodwell propõe 218–231 e o autor do proêmio corrige para 211–217. Na run 1083, `p2` recupera a anterioridade corretamente pelo OCR, mas `p7` mistura as duas posições e `r3` atribui a proposição inteira a Dodwell. O defeito identificado no resumo é a inversão cronológica relativa a Orígenes; a mistura de vozes é um erro específico da extração. Estes números são posições relatadas pelo proêmio, não datas estabelecidas por esta avaliação.

A fonte permanece congelada nos três braços. A revisão deve marcar separadamente: erro herdado do resumo, recuperação pelo OCR e erro introduzido pelo extrator. Corrigir o resumo apenas para um braço confundiria o experimento. A nota detalhada e as citações estão em `.work/kg_ab_stance_v24/source_quality_note.md`.

## Arquivos e validação

Implementação: `tools/knowledge_graph/playground/stance_study.py`; extensão CLI; relatório e diagnóstico de busca agora reconhecem todos os braços da fila. Prompts completos: `tools/knowledge_graph/prompts/extract_concepts_v2.4.system.txt` e `extract_concepts_v2.4-examples.system.txt`. Os dois componentes versionados `stance_v2.4_final_check.txt` e `stance_v2.4_contrastive_examples.txt` permitem conferir exatamente a transformação; o preparador recusa diferenças extras.

Preflight real: 32 snapshots e imagens reproduzidos, 192 runs `planned`, zero chamadas. Hashes da campanha e do A/B anterior e agregados dos jobs foram conferidos antes/depois; registros estão no diretório de trabalho. Testes via Native Lab MCP: 321 passaram na suíte focada, incluindo providers mockados, compatibilidade dos prompts históricos, retomada, independência das repetições, falhas de hashes/imagens, seleção determinística, proteção de holdout e relatórios A/B/C. Nenhuma avaliação paga das novas variantes foi executada.
