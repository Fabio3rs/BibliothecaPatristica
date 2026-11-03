# Citações descontínuas — local-quote-v6

O localizador aceita omissões explícitas `[...]`, `[…]`, `...`, `…` e `(...)` entre fragmentos. Cada fragmento é localizado pelo mecanismo v5, com os mesmos limiares e diagnósticos. Todos precisam existir em ordem, sem sobreposição, dentro de um mesmo bloco OCR ou segmento de resumo atual. O fallback de OCR pode localizar a composição inteira em outro bloco da página; não costura idiomas, blocos ou páginas diferentes.

Uma localização única recebe `method: segmented`, `segments` com offsets Unicode originais, texto encontrado, intervalo correspondente na citação, diferenças e notas ignoradas de cada fragmento. `omitted_spans` preserva o texto entre os intervalos. O resultado não inventa um intervalo contínuo: `start`, `end` e `matched_quote` globais ficam nulos. `position_resolved` indica se o conjunto de posições foi resolvido; alternativas repetidas recebem `ambiguous` e preservam os conjuntos candidatos.

`semantic_support` continua `not_evaluated`. A omissão pode remover uma negação ou qualificação importante: localização não aprova essa interpretação. A citação do modelo e o OCR nunca são reescritos. Sem marcador de omissão, a política não inventa lacunas. Fragmentos muito curtos, fragmentos ausentes, ordem invertida e buscas excessivamente ambíguas não são aceitos automaticamente. Limites: 12 fragmentos, 12.000 caracteres e 128 combinações; enumerações truncadas de ocorrências não viram posição única.

## Integração e histórico

- Novos experimentos `navigation-v1-topics-arguments` congelam `local-quote-v6`.
- Planos existentes continuam usando a política arquivada; v2–v5 permanecem disponíveis. O padrão de preparação dos contratos KG antigos permanece v5, preservando compatibilidade com os estudos de verificação congelados; podem ser revalidados explicitamente com v6.
- CLI `validate --matching-policy local-quote-v6` funciona para navegação e extrações KG; não troca a política congelada de experimentos de revisão.
- O botão **Revalidar** usa v6 e acrescenta uma validação. O status original, a resposta e as validações anteriores permanecem intactos.
- A interface mostra fragmentos separados e destaca somente seus intervalos. Relatórios JSON/CSV/Markdown contabilizam `segmented_quotes`, separadamente de correspondências exatas/aproximadas/ambíguas. Âncoras v3.1 procuram apenas nos fragmentos, não no texto omitido.

Para inspecionar sem escrever no banco ou chamar o modelo:

```bash
python scripts/playgrounds/kg_extractor_playground.py validate \
  --run 1733 --matching-policy local-quote-v6 --dry-run \
  --output .work/kg_navigation_v1/segmented_v6/cli_run_1733.json
```

Para anexar a nova validação às duas runs Gemma, sem inferência:

```bash
python scripts/playgrounds/kg_extractor_playground.py validate --run 1733 --matching-policy local-quote-v6
python scripts/playgrounds/kg_extractor_playground.py validate --run 1734 --matching-policy local-quote-v6
```

Reinicie `serve` para carregar o código novo e recarregue a página do navegador. A implementação executou apenas replay em leitura no banco real. A demonstração do botão usou uma cópia isolada em `.work/kg_navigation_v1/segmented_v6/browser.db`, com cópia do asset de imagem para sua própria pasta autorizada.

## Resultados locais

Replay de 13 respostas arquivadas (1716, 1725–1734, 1737–1738): as runs Gemma 1733/1734 passam a ter todas as citações localizadas. Os três exemplos antes rejeitados são compostos de fragmentos exatos. A run GPT-OSS 1738 continua inválida por e1; e2 grega passa com fragmentos localizados e diferenças preservadas. As demais runs mantêm o resultado de validade. Diagnósticos completos: `.work/kg_navigation_v1/segmented_v6/comparison.json` e `run_*.json`. Esses resultados não constituem adjudicação histórica.

## Arquivos e verificação

Implementação: `evidence_locator_v6.py`, `validation_segmented.py`, integração em `validation_current.py`/`experiment_runner.py`, âncoras em `discourse_contract.py`, contagens em `reporting.py` e apresentação em `static/playground.js`, todos sob `tools/knowledge_graph/playground/`. Os módulos v5 e o validador de navegação original não foram alterados.

Testes novos: `tests/test_kg_evidence_locator_v6.py`, fixture real `tests/fixtures/kg_discontinuous_quotes.json`; testes de integração atualizados em `test_kg_navigation_v1.py`, `test_kg_evidence_locator_v5.py` e `test_kg_reporting.py`.

Testes executados pelo Native Lab MCP:

Resultado final: **433 passed in 8.23s**, sem falhas; `node --check` e CLI de revalidação `--dry-run` também concluídos com código zero.

```bash
.venv/bin/python -m pytest -q \
  tests/test_kg_evidence_locator_v6.py tests/test_kg_evidence_locator_v5.py \
  tests/test_kg_evidence_locator_v4.py tests/test_kg_evidence_locator_v3.py \
  tests/test_kg_evidence_matching.py tests/test_kg_evidence_validation.py \
  tests/test_kg_navigation_v1.py tests/test_kg_discourse_v31.py \
  tests/test_kg_statement_v3.py tests/test_kg_statement_review.py \
  tests/test_kg_reporting.py tests/test_kg_service_tier.py \
  tests/test_kg_provider_adapters.py tests/test_kg_playground.py tests/test_kg_playground_web.py
node --check tools/knowledge_graph/playground/static/playground.js
```

Servidor de teste: `.venv/bin/python scripts/playgrounds/kg_extractor_playground.py serve --db .work/kg_navigation_v1/segmented_v6/browser.db --port 8777`.

Browser Native Lab Playwright: `http://127.0.0.1:8777/`, desktop 1920×1080 e mobile 375×812; botão Revalidar, detalhe da evidência e Localizar na fonte exercitados. Dois destaques no OCR conferidos contra os fragmentos, sem destacar o intervalo omitido; sem overflow horizontal. Console sem erros/warnings e requests HTTP 200 na verificação final. A primeira cópia de teste apontava para o asset do banco original, bloqueado corretamente pela proteção de caminho; o asset foi copiado para a pasta da base isolada antes da verificação final.

Screenshots: `.work/kg_navigation_v1/segmented_v6/desktop-evidence.png` e `mobile-evidence.png`. Frontend servido como arquivos estáticos, sem build adicional; verificação sintática Node e testes Python. Nenhuma chamada paga ou mudança na campanha histórica.
