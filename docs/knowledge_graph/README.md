# Grafos da Patrística — documentação reunida

Este índice reúne o trabalho de descoberta do benchmark, o playground de extração,
as decisões de interpretação e os resultados observados. O objetivo é um **grafo
navegável da Patrística**, com fontes inspecionáveis e possibilidade de revisão.

O estado documentado é de **1 de novembro de 2026**. Já há extração por página,
interface de inspeção e uma campanha de corpus iniciada pelo usuário. Resolução
global, publicação e avaliação semântica ampla continuam pendentes.

## Leitura principal

| Documento | Conteúdo |
|---|---|
| [Estado, decisões e resultados](estado_e_resultados_2026-11-01.md) | Relato consolidado em português: o que foi construído, o que observamos, decisões, resultados e próximos passos. |
| [Fotografia dos registros](snapshot_2026-11-01.json) | Contagens e metadados selecionados, consultados em leitura; inclui momento da captura e resultados dos testes já registrados. |
| [Manual do playground](../kg_extractor_playground.md) | Arquitetura, CLI/UI, providers, fontes, persistência, comparação, execução contínua e limites operacionais. |
| [Confiança autodeclarada](../kg_model_confidence.md) | Pesquisa consultada, protocolo por item, implementação e plano de calibração. |
| [Ferramentas de benchmark](../../tools/knowledge_graph/benchmarks/README.md) | Comandos de descoberta, seleção, preparação e validação dos artefatos. |

## Relatórios e evidências locais

Os links abaixo apontam para artefatos reais em `.work/`, diretório ignorado pelo
Git. São registros de trabalho; não substituem uma avaliação independente.

| Registro | O que documenta |
|---|---|
| [Descoberta do benchmark](../../.work/kg_benchmark_v1/discovery_report.md) | Distribuições, exemplos com locadores, lacunas, pares e pacotes de revisão. |
| [Manifesto do benchmark](../../.work/kg_benchmark_v1/manifest.json) | Escopo shadow, seed, hashes e inventário dos pacotes. |
| [Auditoria de shadow](../../.work/kg_benchmark_shadow_audit/audit.json) | Por que término de geração não significou promoção; situação dos lotes PG na auditoria. |
| [Revalidação administrativa](../../.work/kg_benchmark_shadow_audit/administrative_revalidation.json) | Replay de 13 respostas, sem regravar gerações nem chamar modelos. |
| [Run 5 — Gemma4](../../.work/kg_playground_review/run5/review.md) | Primeira revisão detalhada do grafo de PG026:71. |
| [Run 9 — Muse](../../.work/kg_playground_review/run9/review.md) | Boa cobertura conceitual, mas erros de atribuição apesar de citações localizadas. |
| [Run 13 — Muse](../../.work/kg_playground_review/run13/review.md) | Melhoria nas atribuições, ausência do autor e limites das notas de confiança. |
| [Runs 20 e 21 — Muse](../../.work/kg_playground_review/runs20_21/review.md) | Texto bilíngue, autoria, variabilidade e esclarecimento do critério de navegação. Ler os adendos finais junto da crítica inicial. |
| [Executor e browser — Native Lab](../../.work/kg_corpus_runner/validation.md) | Testes, processos, retomada, WAL, monitor e capturas desktop/mobile. |
| [Dry-run com OCR divergente](../../.work/kg_source_drift/real-proof.json) | PG001:3 preparada com as duas proveniências preservadas, sem inferência. |
| [Política aplicada à campanha](../../.work/kg_source_drift/campaign-policy.json) | Autorização persistida para usar OCR atual quando difere do histórico. |

## Contratos e prompts

| Arquivo | Papel |
|---|---|
| [Schema v1](../../scripts/playgrounds/kg-extraction-v1.schema.json) | Contrato inicial do starter kit, preservado. |
| [Schema v2](../../tools/knowledge_graph/schemas/kg-extraction-v2.schema.json) | Contrato com confiança autodeclarada por conceito, entidade, proposição, evento e relação. |
| [Registro de prompts](../../tools/knowledge_graph/prompts/registry.json) | Variantes versionadas e composição de cada prompt. |
| [Prompt-base v1.4](../../tools/knowledge_graph/prompts/extract_concepts_v1.4.system.txt) | Atribuição, cobertura do autor e tratamento de texto original/tradução. |
| [Protocolo de confiança](../../tools/knowledge_graph/prompts/model_confidence_v2.txt) | Regras usadas pelas variantes `*-confidence`. |
| [Starter kit original](../../scripts/playgrounds/kg-extractor-playground-README.md) | Especificação inicial; consultar o manual atual para o comportamento implementado. |

## Coleta dos documentos

O pacote local [kg_documentacao_2026-11-01.zip](../../.work/kg_documentation_2026-11-01/kg_documentacao_2026-11-01.zip)
reúne estes documentos, relatórios de revisão, contratos, prompts, pequenos comprovantes
e um inventário SHA-256. Mantém os caminhos relativos do repositório.

É um pacote documental: não inclui bancos, chaves, imagens do corpus, respostas brutas
com payloads extensos, páginas/pacotes do holdout ou código completo da aplicação.
Referências ao código exigem o checkout do repositório. Para reproduzir experimentos,
preservar separadamente o banco do playground e seus assets; para reproduzir o
benchmark, preservar o diretório completo `.work/kg_benchmark_v1/`.

As revisões são provisórias e revisáveis. `completed`, citação localizada e confiança
alta representam coisas diferentes; nenhum deles, sozinho, equivale a gold histórico.
