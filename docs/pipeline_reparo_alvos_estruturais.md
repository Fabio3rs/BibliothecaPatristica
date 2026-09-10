# Pipeline de reparo de alvos estruturais

`scripts/repair_structural_targets.py` localiza deterministicamente o início físico de
capítulos e de outras divisões descritas nos índices gerais já extraídos. O script não usa
LLM e não altera arquivos durante o `dry-run`.

## Escopo reconhecido

O classificador cobre capítulos, livros, partes, tratados, sermões, homilias, epístolas,
questões/interrogações, artigos, orações, lições, cânones, distinções, proposições,
fórmulas e fascículos. São aceitos:

- rótulos latinos, franceses, gregos e árabes;
- numerais romanos, arábicos, gregos e arábico-indianos;
- ordinais latinos, franceses e árabes por extenso;
- ordinais antes do rótulo, como `I. CAP.`;
- sequências sem rótulo quando `entry_kind` ou o contexto determina a família;
- cabeçalhos físicos armazenados pelo OCR como `cabecalho`, desde que tenham marcador
  estrutural explícito.

A leitura ignora rodapés, notas e aparato crítico. Citações em prosa como `cap. 2, n. 6`
e rótulos minúsculos como `epist. 5` não entram no catálogo de cabeçalhos.

## Dry-run

```bash
python scripts/repair_structural_targets.py \
  --volume PL020 \
  --workers 20 \
  --report /tmp/PL020_structural_targets.json
```

Use mais de um `--volume` ou `--all-volumes`. `--workers` é o orçamento máximo de
processos. Volumes com matching pesado usam um pool interno; volumes pequenos são
distribuídos em um pool persistente entre volumes. A agregação sempre volta à ordem
determinística de volume, arquivo e linha, portanto `--workers 1` e `--workers N`
produzem as mesmas decisões.

O driver evita trabalho sem utilidade: payloads sem entradas estruturais elegíveis não
abrem os OCRs, e o estimador de páginas não roda quando nenhuma entrada possui page hint.
O cache editorial calcula misses antes de abrir uma transação curta de escrita, evitando
serializar os workers em cache frio. Em uma medição de referência com 407 volumes e cache
quente, `--workers 20` reduziu o dry-run de cerca de quatro horas para 4 min 22 s; use essa
medição apenas como ordem de grandeza, pois cache, CPU e payloads alteram o tempo real.
Ranges e enumerações realmente multiordinais são registrados como
`ineligible_multi_ordinal`: um único `target_file` não representa com segurança todos os
membros do intervalo. Uma forma singular como `CAP. II, 119-244`, porém, é interpretada
como capítulo II mais faixa editorial/de versos; os números da faixa nunca viram ordinais
nem alvos físicos.

Cada entrada elegível recebe uma tentativa explícita:

- `resolved`: cabeçalho único ou sequência monotônica local sustentada por títulos;
- `ambiguous`: há candidato plausível, mas a evidência não basta;
- `unresolved`: não há cabeçalho suficientemente forte no range seguro;
- `conflict`: o candidato diverge de um alvo existente que já não é um simples anchor
  genérico.

O manifesto registra SHA-256 do payload completo, do algoritmo e de cada `entry_raw`, índices físicos,
`entry_key` quando disponível, range pesquisado, candidatos, método e proveniência do
bloco OCR. Um alvo antigo sem evidência também pode ser substituído quando ao menos duas
âncoras fortes determinam uma sequência monotônica, o arquivo antigo não contém o heading
adequado e o novo candidato respeita o envelope físico dos vizinhos.

O scanner ainda:

- une apenas em memória títulos quebrados em linhas/entradas consecutivas, preservando o
  JSON original e sua identidade;
- aceita proveniência direta de seções explicitamente marcadas como headings corporais;
- trata `CAP.` no índice e `TIT.`/`TITULUS` no corpo como aliases somente dentro de uma
  sequência ancorada;
- detecta listas estruturais densas por bloco e posição, exclui suas linhas como falsos
  headings e ainda permite encontrar o corpo que começa mais abaixo no mesmo scan;
- compara sequências de tokens inteiros, evitando que fragmentos curtos de OCR sejam
  promovidos por mera substring.

Exceções comprovadas por revisão do OCR podem ser fornecidas por um documento versionado:

```bash
python scripts/repair_structural_targets.py \
  --volume PG011 --volume PL068 \
  --reviewed-overrides data/structural_target_reviewed_overrides.json \
  --workers 20 \
  --report /tmp/reviewed_structural_targets.json
```

Cada override exige volume, seção, `entry_key` (quando existente), SHA-256 de `entry_raw`,
alvo anterior exato e decisão revisada. Ele pode substituir um alvo ou remover um link
comprovadamente falso sem inventar destino. As decisões ficam incorporadas ao manifesto,
são recomputadas no preflight e seguem a mesma transação do restante dos patches.

## Apply e reimportação

O `apply` consome exatamente o manifesto do `dry-run` e exige um diretório de backup novo:

```bash
python scripts/repair_structural_targets.py \
  --apply-report /tmp/PL020_structural_targets.json \
  --backup-dir /tmp/PL020_structural_targets_backup
```

Para atualizar também o catálogo SQLite no mesmo fluxo:

```bash
python scripts/repair_structural_targets.py \
  --apply-report /tmp/PL020_structural_targets.json \
  --backup-dir /tmp/PL020_structural_targets_backup \
  --reimport-db data/patristic_indices.db
```

Antes de escrever, o apply executa novamente o localizador e exige igualdade integral com
as decisões do manifesto; depois os hashes, identidades e o diff permitido são revalidados.
O backup contém os JSON originais e, quando usado `--reimport-db`, uma cópia consistente do
SQLite. A importação usa `scripts/import_index_json.py --replace` somente para volumes com
patches, primeiro em um SQLite staged.

O apply e o importador compartilham um lock cooperativo em
`data/.patristic-index-write.lock`. Backups e substituições recebem `fsync`; um journal
persistente em `data/.structural-target-repair` registra as fases da transação. `SIGINT`,
`SIGTERM` e `SIGHUP` são atendidos em checkpoints seguros. Depois de `SIGKILL` ou queda de
energia, o próximo apply recupera idempotentemente todo o estado anterior se a transação
não chegou a `COMMITTED`, ou finaliza o estado posterior se já chegou. Estados externos
desconhecidos bloqueiam a recuperação, exceto durante a fase explícita de substituição do
SQLite, quando o backup válido é reinstalado por troca atômica.

Entradas ambíguas ou não resolvidas ficam no relatório em `observations`, sem aumentar o
payload. Normalmente somente `resolved` gera patch. A exceção é um override revisado que
remove um alvo falso: ele grava evidência `unresolved` e deixa `target_file` ausente. Se já
houver alvo ou evidência física de outro produtor, ambos são preservados e o caso aparece
em `preserved_existing`, salvo decisão revisada explícita.

Depois de cada coorte, valide novamente o material instalado contra o JSON e o SQLite:

```bash
python scripts/validate_structural_target_apply.py \
  --report data/index_payload_audits/structural_targets_cohort_01.json \
  --db data/patristic_indices.db
```

O verificador usa lock compartilhado, rejeita uma transação ativa, compara cada evidência
e `target_file` do manifesto com o payload e com a linha SQLite correspondente, e encerra
com `integrity_check` e `foreign_key_check`. A identidade da linha considera a chave de
seção original/canônica, `entry_order` e `entry_raw`, pois payloads legados podem repetir
ordens ou ter chaves que o importador canoniza.

Em varreduras longas, `--resume` reutiliza somente partes cujo SHA do payload e do algoritmo
continuam iguais. `--recover-only` conclui rollback/finalização de uma transação interrompida
sem iniciar um novo apply.

## Política de promoção em coortes

`apply_ready` é um pré-requisito técnico, não um limiar de confiança. Para a aplicação no
corpus completo, use coortes disjuntas e manifeste novamente cada coorte imediatamente antes
do apply. A implantação de 2026-09-05 avançou nesta ordem:

1. volumes cujos patches eram todos `monotonic_heading_sequence`;
2. volumes em que todo match único tinha similaridade `1.0` e margem mínima `0.40`;
3. similaridade mínima `0.95` e margem mínima `0.30`;
4. similaridade mínima `0.90` e margem mínima `0.25`.

Pare antes de reduzir esses limites. Volumes com conflitos, formatos `bis`/paralelos ou
qualquer item da quarentena precisam de revisão própria. Uma coorte aprovada deve ter zero
`semantic_validation_errors`, zero conflitos, todos os alvos existentes dentro de
`source_root` e validação pós-apply bem-sucedida.

## Limites deliberados

- Inventários cujo texto do título não reaparece no corpo ficam sem resolução automática.
- Obras ausentes ou apenas mencionadas continuam `unresolved`.
- Alvos existentes com evidência física anterior nunca são sobrescritos em silêncio.
- Aplicações de corpus devem ser feitas em coortes progressivas e validadas entre lotes;
  não aplique automaticamente ranges, enumerações ou seções temáticas/remissivas.
- Matches `unique_heading_title_match` devem ser promovidos por gates explícitos de
  similaridade e margem; o fato de o relatório ser `apply_ready` significa ausência de
  conflito estrutural, não autorização para aplicar todo o corpus de uma vez.
- Quando índice e corpo começam no mesmo scan, somente headings fora do bloco denso da
  lista podem ser candidatos; bbox, bloco e ordem de linha fornecem a separação intrapágina.
- O script corrige os payloads gerais e opcionalmente o SQLite; shards do site continuam a
  ser materializados pelo fluxo que lê esse SQLite.
