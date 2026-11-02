# Benchmark de conferência de proposições e relações

Este estudo compara a saída **D/v2.5 já armazenada** com uma cópia derivada após uma chamada de conferência ao Muse. Não executa novamente a extração original. Não promove resultados a gold e não altera a campanha, seus snapshots, prompts ou respostas.

## Experimento preparado neste ambiente

**Resultado posterior à preparação:** `EXP-98fa183ea933` já foi executado pelo usuário: 24 respostas estruturalmente válidas, todas com término normal, custo incremental US$ 0,0894953. A política congelada classificou 12 como completed e 12 como invalid por citações. A comparação offline opcional com `local-quote-v4` aceita a localização de evidências em 16/24, preservando os status originais. A análise está em `.work/kg_verify_stance_v1_meta_compatible/analysis.md` e a comparação em `locator_v4/report.md` nesse diretório. **Não é necessário repetir a inferência para usar a política nova.**

O restante desta seção descreve o preflight original, antes dessa execução.

O experimento corrigido **`EXP-98fa183ea933`** está preparado, com 24 runs em estado `planned` e nenhuma chamada de inferência realizada nesta preparação. Não é necessário executar `verify-prepare` novamente. O preflight está em `.work/kg_verify_stance_v1_meta_compatible/preflight.json`; a estimativa congelada é **US$ 0,131367**, baseada nos custos das runs de origem, não uma cotação garantida.

As 24 requisições passaram pela conferência offline de fontes, imagens e hashes. O banco está em WAL; as impressões digitais das campanhas históricas conferidas permaneceram iguais antes e depois da preparação. A implementação inicial passou em 363 testes; a correção de compatibilidade passou em **218 testes via Native Lab MCP**, incluindo regressões de schema, erros, retomada e pipelines existentes. Esses testes usam mocks e não comprovam aceitação remota nem qualidade semântica da futura resposta do Muse.

O relatório inicial corrigido está em `.work/kg_verify_stance_v1_meta_compatible/report.md`. Os pacotes cegos ficam vazios até existirem respostas aplicáveis. O diagnóstico de busca permanece no diretório original `.work/kg_verify_stance_v1/`: já tem 72 rankings (12 consultas × 3 representações × 2 repetições) em `search_report.json`, mas nenhuma métrica de relevância; os 384 julgamentos ainda estão pendentes em `search_judgments.json`.

### Rejeição de schema no primeiro experimento

As 24 tentativas do experimento anterior `EXP-1e3654a50668` receberam HTTP 400 com a mensagem `JSON schema keyword uniqueItems is not supported for structured outputs`. Não houve resposta gerada nem uso de tokens informado. A [documentação da Meta](https://dev.meta.ai/docs/structured-output) descreve a validação do schema antes da decodificação; a restrição específica a `uniqueItems` foi observada nas respostas reais armazenadas.

O adaptador agora remove `uniqueItems` somente do schema de transporte Meta. O schema completo continua no payload e na validação local, que rejeita evidências repetidas. Prompt, fontes, imagens, configuração, modalidade e regras semânticas não mudaram. A projeção `meta-structured-schema-1` registra os caminhos removidos na requisição arquivada e participa do hash de entrada. Schemas anteriores sem esse keyword continuam produzindo requisições idênticas.

Não se reescreveram as 24 falhas. O novo experimento usa as mesmas fontes e ordem, e `original_errors.json` e `execution_readiness.json` registram o diagnóstico e as verificações no diretório corrigido. O CLI agora mostra a causa devolvida pelo provider no progresso e no resumo de erros, inclusive quando há zero novas submissões.

## Desenho congelado

- Fonte: `EXP-55b6938cd135`, braço D, duas repetições originais de cada página.
- 12 páginas: PG001:595, PG001:463, PG004:276, PG001:100, PG001:350, PG001:638, PG001:455, PG001:503, PG001:514, PG001:5, PG004:149, PG004:192.
- **A:** saída original congelada. **B:** mesma saída após uma conferência.
- **24 chamadas novas**, uma por saída original, sem cache de inferência. São duas extrações originais independentes por página, cada uma conferida uma vez; não são duas conferências de uma mesma extração.
- Mesmos modelo, configuração, tarifas, snapshots, preparação do OCR e imagem efetivamente anexada da run fonte. `reasoning_effort` permanece omitido. A imagem é verificada contra arquivo arquivado e hash de transporte; não se substitui por imagem atual.
- Ordem embaralhada com seed `20261103`. Shadow continua identificado como shadow. Não se consulta novamente resumo/OCR corrente e não se abre holdout.
- PG001:2 e PG004:2 permanecem controles negativos offline com suas saídas D já existentes; não recebem chamadas novas.
- Amostra intencional de regressão em dois volumes, não um teste independente de generalização.

Não há garantia de que a segunda passagem do mesmo modelo detecte seus erros. O benchmark mede isso. A conferência por modelo também não substitui a avaliação independente do resultado.

## Contrato da conferência

O prompt versionado é `tools/knowledge_graph/prompts/verify_stance_v1.system.txt`. O schema `kg-stance-review-v1` é construído em `verification_contract.review_schema()` reutilizando os objetos de substituição do schema v2 congelado. Uma cópia exata é exportada no preflight como `verifier.schema.json`.

O modelo recebe todos os segmentos v2, OCR preparado, contexto bibliográfico, imagem e os objetos candidatos. Não recebe scores/rationales de `model_confidence`, warnings do gerador ou raciocínio interno. As instruções de extração originais não são concatenadas ao prompt de conferência; o campo `response_schema` do payload também passa a apontar ao contrato de decisões.

Cada claim e cada relação original precisa de exatamente uma decisão:

| Objeto | Ações | Regras |
|---|---|---|
| Claim | keep, remove, replace, split | Uma substituição em replace, pelo menos duas em split. |
| Relação | keep, remove, replace | Replace pode retargetar ou desdobrar uma aresta original. |

Decisões registram motivo curto em português e evidências. Novos claims usam `pV*`, relações `rV*` e evidências `evV*`; IDs não podem colidir. Toda referência ao claim substituído deve ser atualizada ou removida. Não se acrescentam claims/relações sem mapeamento a um objeto original.

Conceitos, entidades e eventos permanecem idênticos, inclusive sua confiança original; esta é preservada no armazenamento, mas não mostrada ao conferidor. Evidências e warnings originais permanecem preservados; novos itens são anexados. Objetos substitutos usam o schema v2, incluindo uma nova estimativa de confiança do conferidor, que também não será exibida ao adjudicador cego.

Não há regra que rejeite automaticamente `AFFIRMS`/`REFUTES` sobre claims hipotéticos: uma afirmação condicional real pode ser legítima. A avaliação deve distinguir isso de atribuir uma atuação hipotética como fato. Um autor bibliográfico não é automaticamente a voz de uma nota, e uma autoridade citada para parte de uma frase não recebe automaticamente toda a reconstrução do redator.

O modelo não pode corrigir identidades congeladas. Se uma aresta só puder ser fiel alterando uma entidade, deve omiti-la e registrar a limitação. Este custo potencial precisa aparecer na avaliação; não representa uma solução geral de resolução de entidades.

## Preparar e inspecionar, sem chamadas

```bash
python scripts/playgrounds/kg_extractor_playground.py verify-prepare \
  --source-experiment EXP-55b6938cd135 \
  --output .work/kg_verify_stance_v1_meta_compatible/preflight.json
```

Imprime o novo ID, as 24 chamadas planejadas, verificações e estimativa baseada na soma dos custos das 24 runs D de origem. O orçamento preliminar de US$ 0,12 pressupunha custo médio semelhante; o valor congelado no preflight usa as páginas efetivamente selecionadas. Entrada adicional, saída e retries podem alterar o custo. A chave não é lida na preparação.

O banco continua `data/playgrounds/kg_extractor_playground.db`, com WAL. Somente novas linhas de experimento, prompt e runs são inseridas. Fontes existentes são lidas; bases de produção não são abertas para escrita. Os hashes do snapshot, OCR, imagem, prompt, schema, configuração, resposta original e requisição preparada permitem detectar alterações antes de executar.

O diretório do preflight contém `verifier.system.txt` e `verifier.schema.json`. A requisição individual exata pode ser exportada sem inferência:

```bash
python scripts/playgrounds/kg_extractor_playground.py inspect \
  --run NOVO_RUN_ID --output .work/kg_verify_stance_v1_meta_compatible/request_inspection.json
```

Não sobrescreva o preflight para repetir a preparação: use outro diretório/arquivo e outro experimento explícito. Reexecutar `verify-prepare` no mesmo caminho é recusado.

## Executar e retomar explicitamente

Após configurar `META_API_KEY` no terminal:

```bash
python scripts/playgrounds/kg_extractor_playground.py verify-run \
  --experiment EXP-98fa183ea933 --concurrency 2 --max-calls 2

python scripts/playgrounds/kg_extractor_playground.py verify-run \
  --experiment EXP-98fa183ea933 --concurrency 20
```

O smoke faz parte das 24 chamadas. A segunda execução processa somente o restante. Não há teto arbitrário de 16; a fila limita tarefas em voo à concorrência solicitada e às tarefas restantes. Usa threads para transporte HTTP, como o runner A/B existente; o runner de corpus com processos permanece separado e inalterado.

SIGINT/SIGTERM deixam terminar chamadas em voo e interrompem novos envios. Após hard kill, o desfecho remoto pode ser desconhecido: a run vira `interrupted`, não é reenviada automaticamente e requer `--retry-interrupted` para criar outra tentativa. Respostas `invalid` ou erros terminais não são refeitos silenciosamente. Retentativas limitadas de transporte usam o adapter existente, com jitter/backoff e Retry-After. Uma falha de conexão pode ter custo remoto desconhecido.

O runner nunca usa o cache, mesmo para requisições iguais. Preserva JSON bruto inválido, envelope, tokens totais/de raciocínio, latência, custo estimado, motivo de término e erros. Não usa LLM para reparar JSON. Uma conferência aplicável pode continuar `invalid` por alguma citação original preservada: conferir relações não significa reparar automaticamente todas as evidências da extração.

## Relatório e revisão cega

```bash
python scripts/playgrounds/kg_extractor_playground.py verify-report \
  --experiment EXP-98fa183ea933 --output .work/kg_verify_stance_v1_meta_compatible/report.md
```

Relatório Markdown e JSON: estados, contagens antes/depois, ações escolhidas, localização de evidências da cópia derivada, custo incremental, uso e falhas. Não declara melhora semântica por redução/aumento de arestas. As tentativas de transporte/interrupção não são confundidas com o custo histórico da primeira extração.

Arquivos separados:

- `blind/packets.jsonl`: fontes completas e saídas X/Y, com ordem embaralhada por par, sem modelo, IDs de run de extração, confiança ou motivos das edições.
- `blind/judgments.template.json`: formulário por claim/relação, com decisões correct, incorrect, partially_correct, redundant ou uncertain; notas para conteúdo útil ausente.
- `private/unblinding.json`: mapeamento X/Y para A/B e hashes, destinado ao coordenador.

Somente pares com uma cópia derivada estruturalmente aplicável entram nos pacotes. Casos ainda planejados, erros ou respostas truncadas continuam no relatório como falhas, sem preencher B com a saída de A. Validação de evidência pendente não impede inspeção semântica de uma cópia aplicável.

Entregue apenas os dois arquivos de `blind/` ao revisor, não o relatório nem a chave. O mascaramento é de identidade do braço: estilo e IDs de substitutos ainda podem dar pistas. Os documentos X/Y excluem confiança, mas preservam ressalvas substantivas em warnings.

Copie o template para `decisions.json`. Preencha `reviewer`, `independent_of_generator`, `source_checked` e, por objeto, `status=adjudicated`, `decision`, `comment` e `source_refs` com tipo/locador válidos. Julgue contra a fonte; similaridade lexical ou score do modelo não constituem prova.

```bash
python scripts/playgrounds/kg_extractor_playground.py verify-report \
  --experiment EXP-98fa183ea933 \
  --output .work/kg_verify_stance_v1_meta_compatible/report.md \
  --reviews .work/kg_verify_stance_v1_meta_compatible/decisions.json
```

Após desempate, o relatório separa erro corrigido, erro removido, conteúdo correto preservado, removido ou danificado e erro remanescente. Julgamentos parciais/incertos ficam separados. Não calcular precisão global a partir de poucos objetos revisados nem tratar omissões não anotadas como recall. A reaplicação gera arquivos; não reescreve a validação ou a revisão das runs originais.

O comando genérico `validate --run` reconhece as novas runs e pode anexar outra validação local preservando a política congelada. Para este estudo, usar `verify-report` e os pacotes exportados como interface de revisão; não foi acrescentada uma tela específica ao playground web.

## Tolerância de pequenas diferenças de OCR: política v4

`evidence_locator_v4.py` e `validation_micro.py` acrescentam uma política explícita. Os arquivos e o comportamento das políticas v2/v3 permanecem preservados; nenhuma mudança de prompt é necessária. O limiar geral de busca não foi reduzido.

A política localiza fragmentos fiéis cortados no próprio fim do bloco, caracteres latinos visualmente equivalentes isolados dentro de palavras gregas, pequenas diferenças de marcas/iota subscrito e uma diferença de glifo em palavras comuns longas com contexto quase idêntico. A regra de glifos limita a três edições e 3% do trecho; preserva terminações, nomes identificados e termos sensíveis. Números, operadores, negações e palavras potencialmente diferentes continuam exigindo revisão; por exemplo, `οὐ` não é normalizado para `οὗ`. Não se aceitam transliterações de frases como cópias do grego. Ocorrências repetidas continuam ambíguas.

O texto original, a citação, os offsets Unicode e as operações de edição são preservados. A política registra a regra aplicada e mantém `semantic_support=not_evaluated`. Tolerância lexical não garante fidelidade da interpretação.

Comparação offline de todas as 24 respostas, sem gravar validações no banco:

```bash
python scripts/playgrounds/kg_extractor_playground.py verify-report \
  --experiment EXP-98fa183ea933 --matching-policy local-quote-v4 \
  --output .work/kg_verify_stance_v1_meta_compatible/locator_v4/report.md
```

O relatório diferencia status original e validação atual, com diagnósticos por evidência no JSON. Para anexar explicitamente uma nova validação de uma run, preservando resposta, status e validações anteriores:

```bash
python scripts/playgrounds/kg_extractor_playground.py validate \
  --run 1627 --matching-policy local-quote-v4
```

O mesmo comando serve para runs de extração e de conferência. Sem a opção, os comandos continuam usando sua política anterior/congelada. Não foi alterado o padrão de campanhas nem o botão da interface. A revalidação v4 tem versão própria e hashes dos dois módulos novos. O benchmark foi comparado offline; nenhuma validação v4 foi anexada automaticamente às runs reais.

## Diagnóstico de busca, sem novas chamadas

O diagnóstico continua no experimento original de 32 páginas. Não mistura seus braços A/D com os nomes A/B da conferência.

```bash
python scripts/playgrounds/kg_extractor_playground.py ab-search-prepare \
  --experiment EXP-55b6938cd135 --arms A D \
  --query-file tools/knowledge_graph/playground/fixtures/navigation_queries_v25.json \
  --missing-output-policy summary_only \
  --output .work/kg_verify_stance_v1/search_judgments.json

python scripts/playgrounds/kg_extractor_playground.py ab-search-report \
  --experiment EXP-55b6938cd135 \
  --judgments .work/kg_verify_stance_v1/search_judgments.json \
  --output .work/kg_verify_stance_v1/search_report.json
```

São **12 consultas × 32 páginas = 384 julgamentos** de relevância pendentes, compartilhados entre três representações: resumo, resumo + conceitos A/v2.3, resumo + conceitos D/v2.5. A projeção é `label + meaning`, sem sinônimos gerados. BM25, tokenização e parâmetros permanecem iguais; IDF depende naturalmente do conteúdo indexado em cada braço. As duas repetições são avaliadas separadamente.

As duas saídas D truncadas mantêm suas páginas como resumo somente, **explicitamente listadas** em `summary_only_fallback_pages`. Isso mede o resultado entregue pela pipeline, incluindo falhas de entrega. A política default dos comandos antigos continua `strict`, que suspende resultados quando faltam extrações. Não comparar D em um subconjunto escolhido e A no conjunto completo sem informar isso.

O arquivo `search_judgments.sources.jsonl` fornece snapshots para julgar sem ver rankings. Há graus 0/1/2 para irrelevância, discussão secundária útil e discussão substantiva. Títulos que anunciam um assunto podem ser úteis, mas não equivalem automaticamente a desenvolvimento extenso. Não há páginas positivas preenchidas automaticamente. Queries são exploratórias, propostas após inspeção deste conjunto; não são validação externa. O design e texto das consultas têm hash separado dos julgamentos.

Precision@k, recall@k, MRR e nDCG só aparecem quando todas as páginas da consulta têm julgamento independente e há ao menos uma relevante. Enquanto isso, rankings são diagnóstico, não demonstração de ganho de busca. Mudanças de consultas, braços, política ou motor exigem nova preparação. Nunca sobrescrever decisões existentes.

## Implementação e testes

- `verification_study.py`: seleção, isolamento, congelamento e preflight.
- `verification_contract.py`: schema, decisões, aplicação conservadora e validação.
- `verification_runner.py`: execução explícita, concorrência, interrupção e retomada.
- `verification_reporting.py`: comparação, controles offline, cegamento e transições adjudicadas.
- CLI: `verify-prepare`, `verify-run`, `verify-report`; extensões opcionais dos comandos de busca.
- `tests/test_kg_verification_benchmark.py`: mocks de provider, preservação histórica, WAL, schema, citações gregas, truncamento, hashes/imagens, cache, retomada, cegamento e contagem de conteúdo correto perdido.

As fixtures são sintéticas e não demonstram qualidade semântica do Muse. Nenhuma chamada paga é necessária para os testes. A preparação real também termina antes da primeira chamada; só `verify-run` com a chave no terminal inicia inferência.
