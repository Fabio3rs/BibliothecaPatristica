# Unificação Semântica para Saídas de LLMs: uma segunda camada com embeddings e agrupamento

## Resumo
Este documento descreve a motivação, arquitetura e práticas recomendadas para adicionar uma segunda camada de similaridade semântica quando se trabalha com grandes modelos de linguagem (LLMs). Devido à natureza não determinística e à variabilidade textual das respostas — mesmo quando semanticamente equivalentes — é necessário aplicar embeddings, redução de dimensionalidade e agrupamento (clustering), seguido por estratégias de unificação (canonicalização) para obter consistência, indexabilidade e qualidade em pipelines de processamento de palavras-chave, citações e normalizações.

## Introdução
LLMs fornecem excelentes capacidades de interpretação e geração de texto. Contudo, para tarefas como normalização de keywords, extração de citações e criação de vocabulários canônicos, respostas semanticamente equivalentes podem ter superfícies textuais distintas. Essa variabilidade atrapalha deduplicação, busca por similaridade e análise estatística. Uma solução robusta é combinar o poder do LLM com uma camada algorítmica baseada em embeddings que agrupe e unifique resultados.

## Problema
- LLMs são não determinísticos: pequenas diferenças de prompt, temperatura, ou contexto podem produzir variações textuais.
- Mesma unidade semântica => múltiplas formas textuais (abreviações, erros OCR, variantes linguísticas, ordem de palavras).
- Precisamos de um identificador canônico e persistente para: indexação, filtros, links entre bases e avaliação humana.

## Proposta: segunda camada semântica
1. Gerar embeddings para itens textuais (palavras-chave, frases, respostas do LLM).
2. Reduzir dimensionalidade (ex.: UMAP) para preparar clustering eficiente em alta escala.
3. Aplicar clustering hierárquico ou denso (ex.: HDBSCAN) para obter grupos semânticos.
4. Selecionar uma âncora canônica por grupo (por exemplo, via score de pertença + heurísticas de comprimento) e criar uma tabela de nomes canônicos.
5. Para itens submetidos ao LLM (ex.: normalizações propostas), aplicar uma etapa de "tournament" ou redução onde o LLM sugere merges e um algoritmo de agregação (ou outro LLM) decide a canonicalização final.
6. Persistir mapeamentos (original -> canonical) em banco de normalizações e usar esses mapeamentos em toda a pipeline.

## Arquitetura e mapeamento para o projeto
Componentes do repositório que implementam ou suportam partes desta arquitetura:

- Embeddings e armazenamento
  - Tabela SQLite `keyword_embedding` (cada linha: keyword_id, model, embedding blob) — ver `hdbscan_embedding.py`.

- Redução + clustering
  - `hdbscan_embedding.py`: carrega embeddings, aplica UMAP e HDBSCAN, grava `keyword_clusters`, `keyword_cluster_meta` e popula `keywords.hdbscan_group_id`.

- Seleção de nomes canônicos
  - Script SQL/logic em `fill_canon_keywords.py`: cria `cluster_canonical_names` escolhendo anchor por `membership_probability` e proximidade ao tamanho médio do grupo.

- Normalizações via LLM
  - `ollama_keywords.py` implementa: construção de prompt, chamada a backend (Ollama/OpenAI), lógica de torneio (`tournament_reduce`), persistência em `normalizations` DB e filtro de já-normalizados.

- Integração e flags
  - `fill_canon_keywords.py` também contém heurísticas para detectar citações bíblicas e marcar `is_scripture_citation`.

## Contrato mínimo (inputs/outputs)
- Inputs:
  - Conjunto de strings (keywords, respostas LLM).
  - Embeddings (float32 numpy arrays) gerados por modelo fixo.
  - Metadados: id do item, group_id (quando aplicável), model.
- Outputs:
  - Tabela clusterizada com membership probabilities e outlier scores.
  - Tabela de canonical names: group_id -> canonical string (+ metadata: score_ancora).
  - Tabela de normalizações: original -> canonical, persistente.
- Erros e modos de falha:
  - Falha nas chamadas LLM: retries, backoff, cache; gravar falhas para revisão manual.
  - Dados sínvios ou embeddings inválidos: validar shapes e tipos antes de agrupar.

## Estratégias de implementação e heurísticas
- Embeddings: usar modelo único e consistente para todo o dataset de keywords. Manter versão de modelo no metadado.
- UMAP: reduzir para 10–50 dimensões dependendo da cardinalidade; manter seed para reprodução quando possível.
- HDBSCAN: usar `membership_probability` e `outlier_score` para filtrar ruído e identificar âncoras.
- Escolha de âncora canônica: ordenar por membership_probability DESC, depois heurística de comprimento (proximidade à média) e finalmente alfabética para determinismo; essa heurística já está esboçada em `fill_canon_keywords.py`.
- LLM tournament_reduce: enviar batches para LLM (ex.: 20–50 itens), receber propostas de `change_to`, aplicar redução transitiva e repetir até convergir. Usar cache com chave determinística (batch sorted + sistema + modelo).
- Persistência: normalizações (`normalizations` table) devem ser idempotentes e únicas por (original, group_id).

## Métricas e validação
- Purity / Cluster precision: percentagem de itens do mesmo conceito que ficaram no mesmo cluster.
- Anchor accuracy: frequência com que a âncora escolhida corresponde à escolha humana.
- Reduction stability: distância entre iterações do tournament (medir variação ou divergência).
- Human-in-the-loop: amostragem estratificada de mapeamentos para revisão manual; registrar decisões humanas no DB (campo reviewed_by, timestamp).

## Edge cases e limitações
- Variações OCR muito grandes podem produzir embeddings ruidosos; aplicar normalização pré-embeddings (remoção de ruído, caracteres estranhos).
- Documentos multilíngues: embeddings cross-lingual mitigam, mas definir políticas (manter original vs traduzir) conforme uso.
- Citações sem capítulos/versículos (apenas o livro): requer heurística separada — em `fill_canon_keywords.py` há detecção por `extract_citations_from_value_cached`.
- Termos muito curtos (1–2 caracteres) geram embeddings pobres; tratar como caso especial.

## Experimentos sugeridos
- A/B entre âncoras escolhidas por: (a) probabilidade pura, (b) prob + comprimento, (c) frequentismo (mais ocorrência no corpus).
- Avaliar sensibilidade do clustering a parâmetros UMAP/HDBSCAN (n_neighbors, min_cluster_size).
- Medir impacto de batch size e temperature do LLM no `tournament_reduce` (estabilidade e qualidade).

## Segurança, ética e governança
- Registrar prompts e respostas do LLM para auditoria. Evitar armazenamento de conteúdo sensível sem revisão.
- Incluir mecanismo para "reverter" normalizações programaticamente se revisão humana reprovar uma mudança.

# Unificação Semântica para Saídas de LLMs: uma segunda camada com embeddings e agrupamento

_Also available in English: [English version](LLM_unification_paper_en.md)_

## Resumo
Este documento descreve a motivação, arquitetura e práticas recomendadas para adicionar uma segunda camada de similaridade semântica quando se trabalha com grandes modelos de linguagem (LLMs). Devido à natureza não determinística e à variabilidade textual das respostas — mesmo quando semanticamente equivalentes — é necessário aplicar embeddings, redução de dimensionalidade e agrupamento (clustering), seguido por estratégias de unificação (canonicalização) para obter consistência, indexabilidade e qualidade em pipelines de processamento de palavras-chave, citações e normalizações.

## Introdução
LLMs fornecem excelentes capacidades de interpretação e geração de texto. Contudo, para tarefas como normalização de keywords, extração de citações e criação de vocabulários canônicos, respostas semanticamente equivalentes podem ter superfícies textuais distintas. Essa variabilidade atrapalha deduplicação, busca por similaridade e análise estatística. Uma solução robusta é combinar o poder do LLM com uma camada algorítmica baseada em embeddings que agrupe e unifique resultados.

## Problema
- LLMs são não determinísticos: pequenas diferenças de prompt, temperatura, ou contexto podem produzir variações textuais.
- Mesma unidade semântica => múltiplas formas textuais (abreviações, erros OCR, variantes linguísticas, ordem de palavras).
- Precisamos de um identificador canônico e persistente para: indexação, filtros, links entre bases e avaliação humana.

## Proposta: segunda camada semântica
1. Gerar embeddings para itens textuais (palavras-chave, frases, respostas do LLM).
2. Reduzir dimensionalidade (ex.: UMAP) para preparar clustering eficiente em alta escala.
3. Aplicar clustering hierárquico ou denso (ex.: HDBSCAN) para obter grupos semânticos.
4. Selecionar uma âncora canônica por grupo (por exemplo, via score de pertença + heurísticas de comprimento) e criar uma tabela de nomes canônicos.
5. Para itens submetidos ao LLM (ex.: normalizações propostas), aplicar uma etapa de "tournament" ou redução onde o LLM sugere merges e um algoritmo de agregação (ou outro LLM) decide a canonicalização final.
6. Persistir mapeamentos (original -> canonical) em banco de normalizações e usar esses mapeamentos em toda a pipeline.

## Arquitetura e mapeamento para o projeto
Componentes do repositório que implementam ou suportam partes desta arquitetura:

- Embeddings e armazenamento
  - Tabela SQLite `keyword_embedding` (cada linha: keyword_id, model, embedding blob) — ver `hdbscan_embedding.py`.

- Redução + clustering
  - `hdbscan_embedding.py`: carrega embeddings, aplica UMAP e HDBSCAN, grava `keyword_clusters`, `keyword_cluster_meta` e popula `keywords.hdbscan_group_id`.

- Seleção de nomes canônicos
  - Script SQL/logic em `fill_canon_keywords.py`: cria `cluster_canonical_names` escolhendo anchor por `membership_probability` e proximidade ao tamanho médio do grupo.

- Normalizações via LLM
  - `ollama_keywords.py` implementa: construção de prompt, chamada a backend (Ollama/OpenAI), lógica de torneio (`tournament_reduce`), persistência em `normalizations` DB e filtro de já-normalizados.

- Integração e flags
  - `fill_canon_keywords.py` também contém heurísticas para detectar citações bíblicas e marcar `is_scripture_citation`.

## Contrato mínimo (inputs/outputs)
- Inputs:
  - Conjunto de strings (keywords, respostas LLM).
  - Embeddings (float32 numpy arrays) gerados por modelo fixo.
  - Metadados: id do item, group_id (quando aplicável), model.
- Outputs:
  - Tabela clusterizada com membership probabilities e outlier scores.
  - Tabela de canonical names: group_id -> canonical string (+ metadata: score_ancora).
  - Tabela de normalizações: original -> canonical, persistente.
- Erros e modos de falha:
  - Falha nas chamadas LLM: retries, backoff, cache; gravar falhas para revisão manual.
  - Dados sínvios ou embeddings inválidos: validar shapes e tipos antes de agrupar.

## Estratégias de implementação e heurísticas
- Embeddings: usar modelo único e consistente para todo o dataset de keywords. Manter versão de modelo no metadado.
- UMAP: reduzir para 10–50 dimensões dependendo da cardinalidade; manter seed para reprodução quando possível.
- HDBSCAN: usar `membership_probability` e `outlier_score` para filtrar ruído e identificar âncoras.
- Escolha de âncora canônica: ordenar por membership_probability DESC, depois heurística de comprimento (proximidade à média) e finalmente alfabética para determinismo; essa heurística já está esboçada em `fill_canon_keywords.py`.
- LLM tournament_reduce: enviar batches para LLM (ex.: 20–50 itens), receber propostas de `change_to`, aplicar redução transitiva e repetir até convergir. Usar cache com chave determinística (batch sorted + sistema + modelo).
- Persistência: normalizações (`normalizations` table) devem ser idempotentes e únicas por (original, group_id).

## Métricas e validação
- Purity / Cluster precision: percentagem de itens do mesmo conceito que ficaram no mesmo cluster.
- Anchor accuracy: frequência com que a âncora escolhida corresponde à escolha humana.
- Reduction stability: distância entre iterações do tournament (medir variação ou divergência).
- Human-in-the-loop: amostragem estratificada de mapeamentos para revisão manual; registrar decisões humanas no DB (campo reviewed_by, timestamp).

## Edge cases e limitações
- Variações OCR muito grandes podem produzir embeddings ruidosos; aplicar normalização pré-embeddings (remoção de ruído, caracteres estranhos).
- Documentos multilíngues: embeddings cross-lingual mitigam, mas definir políticas (manter original vs traduzir) conforme uso.
- Citações sem capítulos/versículos (apenas o livro): requer heurística separada — em `fill_canon_keywords.py` há detecção por `extract_citations_from_value_cached`.
- Termos muito curtos (1–2 caracteres) geram embeddings pobres; tratar como caso especial.

## Experimentos sugeridos
- A/B entre âncoras escolhidas por: (a) probabilidade pura, (b) prob + comprimento, (c) frequentismo (mais ocorrência no corpus).
- Avaliar sensibilidade do clustering a parâmetros UMAP/HDBSCAN (n_neighbors, min_cluster_size).
- Medir impacto de batch size e temperature do LLM no `tournament_reduce` (estabilidade e qualidade).

## Segurança, ética e governança
- Registrar prompts e respostas do LLM para auditoria. Evitar armazenamento de conteúdo sensível sem revisão.
- Incluir mecanismo para "reverter" normalizações programaticamente se revisão humana reprovar uma mudança.

## Implementação prática (passos mínimos)
1. Gerar/confirmar embeddings consistentes para todo o corpus (armazenar model/version).
2. Rodar `hdbscan_embedding.py` para gerar clusters iniciais.
3. Criar `cluster_canonical_names` usando a SQL/heurística de `fill_canon_keywords.py` (ou via processo que considere membership_probability + tamanho médio).
4. Para grupos que precisam de normalização fina: executar `ollama_keywords.py --group <id>` para obter propostas LLM e aplicar `tournament_reduce`.
5. Salvar mapeamentos em DB `normalizations` e manter integração com buscas/indexação.

## Conclusão
Combinar LLMs com uma segunda camada algorítmica baseada em embeddings, UMAP e HDBSCAN permite transformar saídas variantes e não determinísticas em vocabulários canônicos e estáveis. Essa abordagem equilibra a flexibilidade do LLM com a rigidez necessária para indexação, avaliação e interoperabilidade entre bases.

## Referências internas (arquivos do repositório)
- `hdbscan_embedding.py` — pipeline embeddings → UMAP → HDBSCAN → gravação.
- `fill_canon_keywords.py` — heurísticas SQL para escolher nomes canônicos; flagging de citações bíblicas.
- `ollama_keywords.py` — construção de prompt, wrappers para Ollama/OpenAI, `tournament_reduce`, persistência em DB de normalizações.
- Tabelas SQLite esperadas: `keywords`, `keyword_embedding`, `keyword_clusters`, `keyword_cluster_meta`, `cluster_canonical_names`, `normalizations`.

## Próximos passos
- Adotar experimentos listados e registrar resultados em planilha de avaliação.
- Implementar UI de revisão humana para acelerar correções em massa.
- Automatizar pipeline (ETL) que atualize normalizations e re-indexe consumidores (busca/fulltext) após mudanças aprovadas.

---

Documento gerado automaticamente a partir do estado atual do repositório; destinado a orientar implementações e reportes técnicos.
