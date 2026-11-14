# Correções editoriais dos índices e seus motivos

Em 3 de novembro de 2026, a revisão dos bloqueios do classificador identificou
referências atribuídas ao lema errado, linhas interpretadas como páginas e
numerações de outras edições confundidas com as colunas Migne. Corrigir esses
problemas antes da avaliação semântica evita perguntar ao Jev sobre um par
tema–arquivo cuja localização ainda não foi estabelecida.

O banco principal `data/alphabetical_indices.db` recebeu 2.629 alterações de
referências em cinco volumes e três novas entradas com proveniência. Foram
atualizados também os relatórios de candidatos em
`data/alphabetical_locator_corrected`, consumidos por padrão pelo classificador.
Nenhuma destas alterações promoveu uma referência a `resolved` e nenhuma chamada
à API foi realizada nesta etapa.

## Amostra e alcance

A revisão inicial selecionou 21 casos, com seed 42, até dois por motivo de bloqueio,
entre 20.590 registros ignorados em `bulk_006_.jsonl`. Essa amostra estratificada
serve para descobrir mecanismos de falha; não estima a taxa de recuperação do
conjunto. O primeiro bloqueio registrado pode esconder outros defeitos.

Os resultados originais estão em
`data/jev_validation/blocked_review_seed42/review.json`, com o motivo e a ação
recomendada para cada caso. Esse arquivo conserva o estado anterior à aplicação.
O plano efetivamente aplicado está em `repair_plan.json`, no mesmo diretório.

## Correções individuais e evidências

| Referência | Correção | Motivo e resultado |
| --- | --- | --- |
| PG040, Aristoteles 114, ref 423605 | Candidato físico 356 | O marcador 114 começa em 355; a menção continua em 356 antes de 115. A numeração é da edição de Antuérpia. |
| PG040, Hippocrates 108, ref 423654 | Candidato físico 350 | Menção após o marcador interno 108; cabeçalho Migne 689–690 não é a referência citada. |
| PG040, Democritus 168, ref 423621 | Candidato físico 403 | O marcador 168 começa em 402; a menção está em 403 antes de 169. O OCR omitiu 169, confirmado no fac-símile. |
| PG087.01, Commentarius in Proverbia Salomonis, ref 531845 | Candidato físico 614 | A referência impressa/OCR `121 9` representa 1219, não 121. O fac-símile confirma a obra e as colunas 1219–1220. O literal original foi preservado. |
| PG148, ref 939842 | Transferência de Anaxagoras para Andronicus imp. (is qui Senior dictus est) | A referência 372 pertence ao subtexto sobre a data da Páscoa de Andronicus; Anaxagoras cita 533. Mantido o candidato físico 192 para avaliação semântica da entrada corrigida. |
| PL010, ref 973754 | Transferência de Idem officii et meriti para Inhonorabilis | A fonte 514 imprime `Inhonorabilis. I, 757.`; o lema antigo cita I, 782. Continua bloqueado: falta mapear a edição de Verona, tomo I. |
| PG110, refs 888650 e 888651 | Lema ἐξαρχος, referência 770, 9 | A fonte 662 e o fac-símile mostram uma página e uma linha. O antigo lema `εξαρωγω` estava incorreto; 9 não constitui outra página. Continua faltando o mapa da edição citada. |

As quatro localizações verificadas receberam provas com identidade do registro,
caminho do arquivo e hashes SHA256 do OCR e dos fac-símiles consultados. O
classificador só usa essa localização se as provas ainda conferirem. Isso permite
usar o trecho correto mesmo quando o cabeçalho Migne tem outra numeração.
As correções de atribuição também verificam identidade e hashes das fontes.

A preparação offline no banco principal confirmou **cinco pares aptos**: os quatro
mapeamentos acima e Andronicus. Estar apto significa poder enviar o OCR completo
ao Jev; não equivale a um resultado semântico positivo.

## Casos semelhantes em PG110

A nota impressa na página física 660, linhas 243–245 do OCR, explica que o primeiro
número indica página da edição Petropolitana e o segundo, linha da mesma edição.
`Leo` introduz outra série de referências. A sequência dos cabeçalhos Migne,
mesmo confirmada por vizinhos, não prova um local dessa edição.

A busca foi limitada às entradas `onomastic_mixed` cuja fonte é uma das páginas
661–666. A página 660 contém seções diferentes e ficou fora da aplicação em lote.
Nessa região:

- 2.623 referências pendentes receberam a identificação da edição e o bloqueio
  `editorial_namespace_requires_mapping`, salvo componentes de linha descritos abaixo.
- 52 pares adicionais foram corrigidos por coincidência integral e única com o
  OCR, gramática estrita de página/linha e concordância do lema. Somados ao caso
  ἐξαρχος, são 53 pares corrigidos.
- A referência principal guarda página e linha. O antigo registro independente
  de linha permanece como `target_locator`, ligado ao principal, sem página de
  destino inventada. O classificador exclui esses componentes.
- 51 referências já resolvidas foram listadas para revisão e preservadas. Sua
  presença nessa região exige verificar a evidência de cada uma; não basta para
  declarar que todas estão erradas.

IDs e motivos estão em `similar_cases.json`. Entradas longas, agrupadas ou
interrompidas não foram repartidas por essa regra. Por exemplo, a continuação de
πάκτα ainda precisa ser reconstruída; o bloqueio de edição evita uma nova
associação indevida enquanto isso.

## Banco e relatórios antigos

O script `scripts/repair_reviewed_index_cases.py` separa planejamento, aplicação
e atualização dos relatórios. A aplicação verifica o estado anterior completo
da referência e da entrada, recusa sobrescrever destinos existentes, verifica
hashes, cria backup e executa a alteração em transação. O histórico permanece
na tabela adicional `alphabetical_reviewed_patches`, incluindo as antigas
evidências de localização. As entradas antigas são preservadas.

O backup anterior à aplicação é
`data/jev_validation/blocked_review_seed42/main.before.sqlite`.
`main.apply.json` registra os totais e o hash do plano.
`main_integrity.json` verifica integridade, chaves estrangeiras e preservação das
resoluções anteriores. `main_validation.json` registra os cinco pares preparados
e os casos que continuam bloqueados.

Os relatórios corrigidos de PG003, PG040, PG087.01, PG110, PG148 e PL010 contêm
snapshots atuais. Correções anteriores não relacionadas foram preservadas, e os
relatórios substituídos possuem cópias `.before-<hash>`. Esses arquivos são
relatórios de candidatos para o classificador, não propostas para aplicação pelo
resolvedor mecânico.

PG003 tinha dois registros internos, refs 347582 e 347583, que reapareciam por
relatórios antigos. A seleção agora consulta o tipo atual no banco e exclui
`target_locator`, mesmo quando o relatório antigo ainda o trata como página.

## Reprodução e próxima execução

Para inspecionar os candidatos PG040 e seus payloads, sem API ou alteração do banco:

```bash
python scripts/validate_index_jev.py --tome PG040 --no-api --limit 20 \
  --verbose --show-payload \
  --log data/jev_validation/pg040_after_review_no_api.jsonl
```

Use um caminho de log novo a cada execução. Para outra execução paga, os comandos
existentes com `--apply`, limites de custo e `--resume-log` continuam válidos.
Inclua os logs anteriores para reutilizar avaliações compatíveis. Não é necessário
reaplicar o plano: o banco principal e os relatórios já foram atualizados.

O plano pode ser aplicado a uma cópia do banco anterior, para reprodução:

```bash
python scripts/repair_reviewed_index_cases.py --db /tmp/indices-copy.sqlite \
  --plan data/jev_validation/blocked_review_seed42/repair_plan.json --apply \
  --backup /tmp/indices-copy.before.sqlite --audit /tmp/indices-copy.apply.json
```

Prepare `indices-copy.sqlite` como cópia do backup. Os caminhos de backup e auditoria
devem ser novos. A aplicação exige que as fontes locais referenciadas continuem
disponíveis e com os mesmos hashes; os fac-símiles não são adicionados ao repositório.

## Testes e pendências

Foram aprovados 146 testes distintos: a suíte focada de 145 testes no Native Lab,
mais a regressão de preservação dos relatórios adicionada depois, validada junto
aos 14 testes do módulo de correções. A suíte cobre localização, classificação,
replay, pool, aplicação transacional, fontes alteradas e filtragem de componentes.

Continuam necessários o mapa Petropolitana/Leo de PG110, o mapa Verona I/II de
PL010, a segmentação das entradas agrupadas e a revisão individual das 51
resoluções protegidas. Nos demais casos da amostra, pistas textuais não foram
transformadas em localizações sem prova editorial. A expansão feita aqui cobre
a região identificada de PG110, não uma nova auditoria de todo o corpus.

## Resultado da rodada Jev após as correções

A execução posterior `data/jev_validation/after_editorial_review_001.jsonl`,
realizada pelo usuário, avaliou os cinco pares liberados. Todos receberam decisão
positiva, sem indicação de revisão pelo classificador, e os cinco registros de
aplicação confirmam a promoção a resolução em nível de arquivo.

| Referência | Arquivo físico | Probabilidade Jev |
| --- | --- | --- |
| PG040 Aristoteles 114, ref 423605 | 356 | 0.96 |
| PG040 Democritus 168, ref 423621 | 403 | 0.96 |
| PG040 Hippocrates 108, ref 423654 | 350 | 0.83 |
| PG087.01 Commentarius in Proverbia Salomonis 121 9, ref 531845 | 614 | 0.91 |
| PG148 Andronicus 372, ref 939842 | 192 | 0.71 |

Foram cinco chamadas novas, 12.496 avaliações reutilizadas e custo informado de
US$ 0.000937776. O total de 202 decisões positivas no resumo inclui o cache; não
representa 202 novas resoluções. As probabilidades são estimativas do modelo,
sem demonstração de calibração para esse domínio.

O término `candidates_exhausted` significa que a seleção disponível foi esgotada.
Ainda foram registrados 21.320 candidatos ambíguos e 372 sem página encontrada.
O bloqueio por edição não mapeada passou a 5.426 casos após a identificação da
numeração de PG110; esse aumento registra a barreira editorial antes encoberta.
As investigações abaixo continuam necessárias antes de outra campanha ampla.

O backup desta aplicação é
`data/jev_validation/after_editorial_review_001.before-apply.sqlite`.
Os resultados offline descritos nas seções anteriores correspondem ao estado
anterior a essa rodada paga.

## Investigações e processamento mecânico planejados

As frentes abaixo estão **pendentes de investigação ou implementação**. São
hipóteses de trabalho apoiadas nos problemas observados, não promessas de que
todos os casos possam ser resolvidos automaticamente. Os totais desta seção
referem-se à revisão de 3 de novembro de 2026 e devem ser recalculados no início
de cada execução.

| Frente | Problema e motivo | Resultado esperado |
| --- | --- | --- |
| PG110 Petropolitana e Leo | Página/linha de outra edição foi tratada como coluna Migne. | Mapa de referências por edição e seção, sustentado por âncoras verificadas. |
| PL010 Verona I e II | O número citado depende do tomo da edição de Verona; cabeçalhos Migne iguais não bastam. | Mapas separados por tomo, com a origem do tomo herdado em cada referência. |
| Segmentação dos índices | Entradas agrupadas transferem números para o lema errado; continuações perdem seu lema. | Entradas e subentradas com referências atribuídas ao trecho correto e cobertura contabilizada. |
| Auditoria das 51 resoluções PG110 | Resoluções existentes na região de outra numeração podem ter evidência inadequada. | Parecer individual, distinguindo resolução comprovada, incompatível e inconclusiva. |
| Ampliação em PG040 | A página de Antuérpia pode atravessar arquivos físicos e ter marcadores omitidos pelo OCR. | Mais candidatos com intervalos internos comprovados, além dos casos já revisados. |
| Busca de situações semelhantes | Os mesmos defeitos podem existir em outras seções e volumes. | Inventário priorizado de suspeitas, seguido de validação em amostras pequenas. |

### Mapear as numerações editoriais

Em PG110, começar pela nota da página física 660 e pelas fontes 661–666. Em PL010,
começar pela nota editorial da fonte 462 e pelos exemplos revisados nas fontes
471, 475 e 514. O primeiro objetivo é verificar se existem marcadores recuperáveis
no OCR do corpo e como eles se relacionam com a edição citada.

O processamento proposto deve:

1. Identificar a edição, o tomo e a seção antes de interpretar números.
2. Extrair possíveis marcadores internos com arquivo, linhas e hash da fonte.
3. Validar âncoras e continuidade nos arquivos vizinhos, registrando reinícios,
   lacunas e conflitos. Não presumir um deslocamento fixo entre edições.
4. Construir intervalos de correspondência que admitam uma página editorial
   distribuída por mais de um arquivo físico.
5. Usar nomes ou expressões, inclusive busca aproximada, para levantar pistas e
   distinguir candidatos; a semelhança textual isolada não comprova a edição.

Uma referência só deve liberar um candidato quando a relação editorial estiver
demonstrada. Vários intervalos plausíveis continuam sendo ambiguidade; várias
ocorrências dentro do mesmo arquivo podem ser compatíveis com resolução em nível
de arquivo, desde que a edição e o intervalo estejam comprovados. A linha citada
não precisa ser localizada com precisão para resolver o arquivo, mas não pode ser
convertida em outra página.

O cache deve depender do volume, edição, tomo/seção, versão do algoritmo e hashes
das fontes usadas. Mudanças no OCR ou nas âncoras invalidam as correspondências
afetadas. A entrega deve incluir mapa, cobertura, conflitos e testes de reinício
de numeração, marcador ausente e continuidade entre arquivos. Se os marcadores
não forem recuperáveis, registrar a necessidade de fac-símile ou fonte adicional.

### Segmentar lemas e recuperar continuações

Começar pelos exemplos em `review.json`: agrupamentos em PG113, PG148 e PL010,
além da continuação de πάκτα em PG110. As transferências já comprovadas para
Andronicus e Inhonorabilis servem como casos de regressão.

Usar linhas, pontuação, padrões de referências e recuos quando preservados no OCR.
Reconstruir a hierarquia e o contexto herdado de edição/tomo somente quando houver
evidência explícita. Quando o OCR tiver perdido a estrutura necessária, encaminhar
o trecho para inspeção do fac-símile.

Cada proposta deve preservar o texto original, os spans de origem, a relação com
a entrada anterior e o motivo da atribuição. Toda referência do trecho deve ser
atribuída ou ficar explicitamente pendente; números repetidos exigem identificação
da ocorrência, não apenas uma busca pelo valor. A validação deve detectar perda
ou duplicação de referências e impedir que uma continuação vire lema independente
sem fundamento.

### Auditar as resoluções já existentes

Usar os 51 IDs de `similar_cases.json` como conjunto inicial. Para cada registro,
comparar entrada original, edição citada, destino, prova mecânica e eventual
resultado Jev. Um resultado semântico positivo pode confirmar presença do tema,
mas não demonstra que o arquivo corresponde à página editorial citada.

Classificar a evidência como comprovada, incompatível ou inconclusiva e explicar
o motivo individualmente. A localização na região suspeita não justifica desfazer
todas as resoluções. Qualquer correção proposta deve guardar o destino anterior
e a evidência que contradiz ou substitui sua justificativa.

### Procurar padrões semelhantes e ampliar PG040

Começar por relatórios e metadados existentes, evitando varrer todo o corpus OCR
em desenvolvimento. Levantar entradas muito extensas, referências cujo trecho não
é único, números de linha tratados como página, notas de outra edição e mapas com
reinício de numeração. Esses sinais priorizam inspeção; não autorizam correção por
si mesmos.

Em PG040, testar a expansão dos intervalos entre marcadores de Antuérpia usando
os casos 423605, 423654 e 423621 como regressões. Um marcador ausente no OCR não
autoriza interpolação arbitrária. Os casos visualmente confirmados devem manter
sua evidência separada dos inferidos pelo algoritmo.

Para cada padrão encontrado, selecionar uma amostra reproduzível, conferir a fonte
e só depois ampliar a regra à mesma convenção editorial. Medir candidatos
recuperados, conflitos, correções indevidas e referências que continuam bloqueadas.

### Organização e integração com o Jev

Se houver investigação paralela, a divisão proposta é: PG110 com auditoria das 51
resoluções; PL010 com Verona I/II; e segmentação com busca de padrões semelhantes.
Subagentes podem produzir diagnósticos, regras, testes e propostas em arquivos
separados. A aplicação ao banco deve ser centralizada, sem escritores concorrentes.
Isso não exige invocar agentes Codex via CLI.

Cada frente deve entregar um relatório por caso com IDs, motivo, fontes, hashes,
estado anterior, proposta e limitações. Validar primeiro em dry-run e em cópia do
banco; aplicar com backup, transação e guardas de alteração das fontes. Atualizar
os relatórios consumidos pelo classificador junto com as correções canônicas.

O Jev entra depois da preparação mecânica/editorial, somente para pares localizados.
Reutilizar avaliações anteriores apenas quando entrada, texto e instrução forem
compatíveis. Repetir a campanha sem resolver esses bloqueios não substitui as
investigações acima. Esta lista documenta trabalho futuro; sua inclusão não executa
subagentes, chamadas de API ou novas alterações no banco.

## Investigação da concordância PG040 e dos achados do scanner

O resumo de `data/editorial_scan_001.jsonl` confirma 2.519 arquivos examinados,
110 pistas editoriais, 1.241 eventos de possíveis marcadores e 5.280 progressões
heurísticas, das quais 600 foram gravadas (150 por volume). Esses valores não
representam mapeamentos editoriais validados.

### Concordância Antuérpia e Oxford

O OCR da página física 427 de PG040 contém a tabela intitulada
`CONCORDIA EDITIONIS ANTVERP. CUM OXONIENSI.`. Os pares citados foram confirmados
no OCR: 65–113, 66–114, 108–198, 114–210, 115–213 e 123–229, na ordem
Antuérpia–Oxford. A tabela não fornece diretamente os números dos arquivos Migne.

A extração conservadora encontrou três conjuntos de colunas:

| Páginas de Antuérpia | Linhas Antuérpia | Linhas Oxford | Tratamento |
| --- | ---: | ---: | --- |
| 9–64 | 56 | 43 | Preservadas sem alinhamento; a diferença impede um zip seguro. |
| 65–123 | 59 | 59 | 59 pares alinháveis pelo OCR, ainda sem revisão do fac-símile. |
| 124–181 | 58 | 58 | 58 pares alinháveis pelo OCR, com anomalia de sequência. |

Os qualificadores `49 fin.` e `50 fin.` e os trechos gregos da coluna Oxford foram
preservados. O resultado tem **117 pares alinháveis**, não 117 pares verificados
visualmente. No terceiro conjunto, Antuérpia 138 fica alinhada ao literal Oxford
`381`, entre `257` e `261`. O auditor marca tanto o par 138 quanto o vizinho 139
como afetados pela quebra de monotonicidade. Isso não afirma que `261` esteja
errado: a transição precisa de conferência. Nenhum número foi corrigido por
inferência. As colunas iniciais também precisam de inspeção visual para esclarecer
as linhas não alinháveis.

### Marcadores e referências pendentes de PG040

Foi criado `scripts/audit_pg040_concordance.py`, que lê SQLite em modo somente
leitura e emite arquivos de investigação. O escopo do corpo foi herdado do piloto
documentado de Nemesius: arquivos físicos 253–415; a nota do índice está em 426 e
a tabela em 427. O script recusa uma estrutura de tabela inesperada ou a ausência
da declaração de edição no índice.

Nos 163 arquivos do corpo, a execução encontrou 142 ocorrências numéricas
candidatas em blocos gregos de texto principal. Exclui números entre parênteses
ou colchetes e usa o analisador de sequências existente. Um intervalo só entra
como candidato quando os marcadores consecutivos n e n+1 têm ocorrência única,
suporte de sequência, ordem compatível e arquivos intermediários disponíveis.
Não interpola marcadores ausentes. Os arquivos de ambas as fronteiras ficam
incluídos, pois a página editorial pode atravessar a divisão física.

O resultado foi:

- 112 intervalos editoriais candidatos no corpo;
- 45 das 98 referências ainda pendentes com intervalo candidato delimitado;
- 53 pendências sem fronteiras suficientes ou únicas;
- 4 resoluções existentes preservadas;
- 14 das 45 pendências com correspondência literal normalizada do nome em apenas
  um dos arquivos físicos do intervalo.

Esses 14 casos são uma fila de revisão, não resoluções recuperadas. A busca do
nome considera o corpo completo do arquivo, inclusive a tradução latina, e pode
encontrar uma menção fora do trecho grego delimitado. Marcadores numéricos também
podem ser espúrios apesar da sequência. O resultado mantém `needs_review=true`
e `application_supported=false` em todos os registros.

Exemplos para conferência são Aristoteles 97 (candidato com nome no físico 341),
Democritus 114 (356), Galenus 108 (350) e Galenus 114 (356). Há casos com cautela
editorial adicional, como `Origenes 62 (forte Aristoteles, 91)`, cujo conteúdo não
deve ser reduzido a uma associação simples sem analisar a ressalva impressa.

A junção Oxford–arquivo físico usa o par da tabela e o intervalo de Antuérpia;
ela não constitui uma segunda prova independente. Pares afetados pela anomalia
numérica não recebem essa junção. O achado aumenta a informação editorial
disponível, mas não demonstra, por si, aumento de cobertura do resolvedor anterior.

### Outros achados confirmados

Em PG113, a fonte física 516 declara a edição Lupareana de 1616 e o uso das letras
A/B/C/D. A nota está sob o título das notas de Henri de Valois sobre os excertos
de Constantino Porfirogênito. A convenção deve ser associada a essa seção; não
foi aplicada ao índice inteiro nem às referências de Rhamble ou Roma.

Na fonte física 653 de PG113, as linhas 184 e 185 contêm `Rastotza, 96.` e
`Rhamble, 80.`, e Roma começa na linha 205. Isso confirma a separação das entradas
no OCR e a diferença entre `Rastotza` e o termo procurado `Rastoiza`. A grafia
impressa ainda deve ser distinguida de uma leitura exclusivamente baseada no OCR.

Em PG110, as fontes físicas 9 e 21 corroboram a declaração da edição Petropolitana
da fonte 660. A fonte 9 esclarece que as linhas devem ser contadas a partir do
número destacado, não do início da coluna Migne. Um futuro localizador de linhas
precisa respeitar essa origem e verificar o texto impresso; linhas do arquivo OCR
não equivalem automaticamente a linhas tipográficas.

Em PL010, 13 dos 67 eventos `editorial_note_hint` têm uma expressão `Ms. Veron.`
no contexto (contagem por regex, sem classificar todos os eventos restantes).
A ocorrência de `Veron.` isolada é insuficiente para identificar outra paginação.
O scanner deve distinguir menção a manuscrito, declaração de numeração, título de
concordância e pista inconclusiva, preservando contexto e sinais encontrados.

### Artefatos e reprodução

O relatório atual está em `data/pg040_concordance_audit_20261103_v2/`:

- `summary.json`: totais, escopo, hashes das fontes e snapshot das referências;
- `concordance.json`: colunas, células, linhas e pares, inclusive anomalias;
- `markers.json`: cada ocorrência, contexto, linha, offset, arquivo e hash;
- `intervals.json`: fronteiras, evidências de sequência e arquivos candidatos;
- `references.json`: comparação com cada referência atual, pistas nominais e
  valores do banco usados na investigação.

O diretório anterior sem sufixo `_v2` conserva a primeira execução exploratória.
Para reproduzir com o estado atual do banco, use um diretório de saída novo:

```bash
python scripts/audit_pg040_concordance.py \
  --out-dir data/pg040_concordance_next
```

Foram aprovados 14 testes focados, em
`tests/test_audit_pg040_concordance.py` e
`tests/test_audit_alphabetical_numbering.py`. Incluem colunas desiguais, preservação
de números suspeitos, exclusão de notas/cabeçalhos, marcadores duplicados ou
ausentes, proveniência e execução sem mutação do SQLite. Esta investigação não
alterou resoluções, não alimentou automaticamente os relatórios do Jev e não
executou chamadas de IA.

## Triagem adicional por variantes nominais em PG040

Uma busca exploratória posterior examinou os mesmos 163 arquivos do corpo de
Nemesius, apenas nos blocos de texto principal, com radicais latinos explícitos
para 17 referências que não tinham pista nominal na auditoria anterior. Aplicou
a normalização já existente de ligaturas, acentos e caixa, além de reunir palavras
partidas por hífen no fim da linha. Não fez busca grega ou aproximação por distância
de edição. Índices e aparato crítico ficaram fora da busca.

Houve ocorrências dentro dos intervalos candidatos em 14 dessas 17 referências;
13 ficaram concentradas em um único arquivo e uma em dois. São **13 novas pistas
nominais com arquivo único**, além das 14 da auditoria anterior, ainda sujeitas
à comprovação dos marcadores e da posição relativa ao trecho editorial citado.

| Entrada e referência | Forma encontrada | Arquivo físico |
| --- | --- | ---: |
| Anaxagoras 165 | Anaxagoræ | 400 |
| Daniel 26 | Da- / nielem | 271 |
| Eunomiani 61 | Eunomianis | 308 |
| Hippocrates 37 | Hippocratis | 283 |
| Ibycus 161 | Ibyci | 397 |
| Jobus 154 | Job | 390 |
| Jobus 178 | Job | 411 |
| Lazarus 179 | Lazaro | 411 |
| Manichæi 47 | Manichæorum | 294 |
| Moses 16 | Moyses | 263 |
| Panætius 96 | Panæ- / tius | 340 |
| Paulus apostolus 26 | Paulum | 271 |
| Philopator 138 | Philopater | 377 |

Paulus apostolus 179 apresentou ocorrências em 411 e 412. Não se escolheu um
destino entre eles. Apollinarius 47, Galenus 91 e Hippocrates 65 não apresentaram
ocorrência pelos radicais testados em seus intervalos; isso não prova ausência
do assunto e ainda permite investigar formas gregas, OCR ou citações indiretas.

Entre as outras 53 pendências de PG040, 30 não têm marcador inicial encontrado,
22 têm início mas não marcador final e uma tem ambos, porém sem suporte suficiente
de sequência/ordem. A ausência dos marcadores 28, 74 e 169 afeta sete referências
cada; 30 e 170 afetam cinco cada. As contagens se sobrepõem, pois uma referência
pode depender de duas fronteiras ausentes. Priorizar essas páginas concentra a
inspeção do fac-símile e pode beneficiar vários lemas sem buscas isoladas por nome.

O relatório `data/pg040_string_scan_20261103/results.json` guarda as consultas,
formas encontradas, arquivo, intervalo de linhas, trecho, hash e páginas com hits
no corpo inteiro ou dentro dos intervalos candidatos. `probe.py`, no mesmo
diretório, conserva o procedimento exploratório; a saída existente é protegida
contra sobrescrita. Nenhuma referência foi alterada nem chamada de API executada.

## Viabilidade de deepscan semântico por intervalos

O deepscan é viável como segunda etapa de busca: o localizador entrega uma janela
editorial plausível e o Jev avalia cada arquivo dessa janela. Isso permite examinar
flexões, formas gregas e descrições analíticas sem exigir uma ocorrência literal
prévia. A presença do assunto e a correspondência à referência impressa devem
permanecer evidências distintas.

### Simulação offline em PG040

A simulação reutilizou os marcadores únicos com suporte de sequência do relatório
PG040. Para uma referência n sem intervalo n–n+1 completo, procurou o marcador
suportado mais próximo com número menor ou igual a n e o seguinte com número maior
que n. A janela inclui todos os arquivos entre as duas âncoras, sem interpolar um
marcador inexistente e sem truncar uma janela que exceda o teto. As âncoras continuam
sendo candidatos extraídos do OCR, sujeitos a revisão editorial.

Das 98 referências pendentes, duas foram separadas por ressalvas explícitas:
`Aristoteles 79 (loco Origenis, 91)` e `Origenes 62 (forte Aristoteles, 91)`.
Outras duas, Apollinarius 9 e Aristoteles 9, ficaram sem âncora externa inicial.

| Teto de arquivos por referência | Referências abrangidas | Pares tema–arquivo | Custo estimado |
| --- | ---: | ---: | ---: |
| 3 | 76 | 186 | US$ 0,034–0,035 |
| 5 | 85 | 223 | US$ 0,040–0,042 |
| 7 | 85 | 223 | US$ 0,040–0,042 |
| 13 | 94 | 340 | US$ 0,061–0,064 |

As estimativas usam custos médios observados em `bulk_006_.jsonl` e
`after_editorial_review_001.jsonl`, aproximadamente US$ 0,000180–0,000188 por
chamada. Não são consulta à tabela atual de preços nem garantia de orçamento.
Os totais precedem cache, retries e controles do experimento; tamanho do OCR e
da entrada altera o custo. Não foram feitas chamadas pagas para essa simulação.

A faixa de cinco arquivos já cobre todas as 85 referências alcançadas pelo teto
de sete. As nove referências adicionais precisam da janela física 312–324,
delimitada pelos marcadores suportados 66 e 78. Não há ganho gradual entre sete
e doze arquivos com as âncoras atuais. Essa janela mais ampla deve ser uma etapa
separada, pois aumenta as oportunidades de encontrar o mesmo tema em outro local.

Exemplos de janelas exploratórias: Ammonius 29 → físicos 274–276; Anaxarchus 125
→ 364–366; Aristoteles 28 → 272–274; Aristoteles 168 → 402–405. Nenhum desses
exemplos foi promovido a resolução pela simulação.

### Fluxo proposto e interpretação

1. Selecionar entradas com lema e atribuição de referência utilizáveis. Manter
   bloqueados agrupamentos indevidos e dúvidas sobre qual tema deve ser perguntado.
2. Preparar janelas dentro da mesma obra e convenção editorial. A busca de strings
   pode ordenar candidatos, mas não deve excluir páginas sem correspondência literal.
3. Avaliar o OCR completo de cada arquivo, com exatamente uma pergunta booleana
   `topic_present` por associação. Não concatenar a janela inteira em uma pergunta,
   pois um SIM deixaria indefinido o arquivo onde está a evidência.
4. Concluir todos os candidatos da janela antes de compará-los. Um orçamento
   interrompido deve produzir janela incompleta, não um vencedor presumido.
5. Registrar zero, um ou múltiplos positivos, probabilidades, diferença entre os
   melhores resultados, proveniência, custos e completude da janela.

Uma resposta positiva única pode priorizar uma associação para revisão. Vários
positivos podem representar tema recorrente, continuação legítima entre arquivos
ou janela inadequada; não se deve escolher automaticamente o maior valor. Ausência
de positivos pode decorrer de OCR, classificação ou localização, e não prova ausência
do tema. Não normalizar as probabilidades para somarem um: os eventos de presença
nas diferentes páginas não são mutuamente exclusivos.

Um limiar alto e uma margem entre os dois melhores escores podem ajudar a ordenar
revisões, mas precisam de avaliação empírica. A rodada já conferida teve Hippocrates
com 0,83 e Andronicus com 0,71; exigir 0,90 perderia esses resultados positivos.
A comparação do modelo também não conserta uma correspondência de edição errada.

### Implementação e piloto sugeridos

O script atual `validate_index_jev.py` exige candidato único em `prepare()` e
revalida a localização antes de aplicar. Um modo de busca por janela deve usar um
preparador separado, sem retirar essas verificações da aplicação existente.
Podem ser reutilizados o construtor do payload, o cliente HTTP, o pool, os limites
de chamadas/custo/tempo e a infraestrutura de logs. São necessários novos estados
para janela completa/incompleta, nenhuma correspondência e múltiplos positivos.

Guardar avaliações por modelo, versão da instrução, payload e hash do OCR;
preservar a identidade de cada referência e sua janela ao reutilizar resultados.
Não deduplicar apenas pela grafia do lema, porque sublemas e ressalvas podem mudar
o assunto. Os workers avaliam páginas; o processo principal controla orçamento,
agrega resultados por referência e grava os checkpoints. Requests já em voo podem
ultrapassar o orçamento pelo limite de concorrência, como no pool existente.

Para o primeiro piloto, selecionar aproximadamente 20 referências, incluindo
intervalos curtos, casos sem match literal, nomes frequentes e raros e controles
positivos das quatro resoluções PG040 já verificadas. Avaliar as janelas inteiras
com até 100 tentativas HTTP, incluindo retries, e teto de US$ 0,05. Conferir todas
as escolhas positivas e uma amostra dos negativos no material original; controles
negativos precisam de conferência, não apenas de um NÃO anterior do modelo.
Medir precisão da seleção por arquivo, recuperação dos controles, frequência de
múltiplos positivos, custo e falsos positivos por largura de janela.

A primeira implementação deve produzir candidatos, sem `--apply`. Uma promoção
posterior precisa registrar separadamente a evidência da janela editorial e a
evidência semântica do arquivo, além da revisão dos critérios no piloto. Não se
deve apresentar um resultado de deepscan como localização já revalidada pelo
resolvedor estrito.

Em PG110 e PL010, começar apenas em obras/seções com limite editorial defensável.
Sem esse limite, varrer o volume por um nome raro ainda pode ajudar na investigação,
mas não estabelece página Petropolitana ou Verona. Entradas agrupadas de PG113
e outras seções precisam primeiro recuperar o tema correto.

### Artefatos da avaliação

`data/jev_deepscan_feasibility_20261103/summary_v2.json` contém os cenários e a
base de custo observada. `seven_page_candidates_v2.json` contém as 85 referências
com suas janelas e âncoras externas; o nome conserva o teto de sete usado na
simulação, embora nenhuma janela selecionada exceda cinco arquivos.
`estimate.py` reproduz o cálculo offline a partir do relatório anterior e exige
saídas novas. Os arquivos sem sufixo `_v2` guardam a primeira simulação.

Essa lista é um estudo, não um manifesto executável de inferência. Antes de
despachar chamadas, é necessário reler o banco atual, verificar hashes das fontes,
propriedade das referências e validade das âncoras. A implementação posterior do
deepscan está descrita a seguir; os arquivos desta avaliação continuam sendo um
estudo offline. Nenhuma chamada ao Jev ou mutação de SQLite foi realizada nesta
avaliação.

## Script de deepscan e execução do piloto

Foi implementado `scripts/deepscan_index_jev.py`. Esta primeira versão suporta
**PG040**, reconstrói as janelas a partir do OCR e lê as referências atuais do
banco. Não consome o estudo de viabilidade como se fosse um mapa validado.
O SQLite é aberto com `mode=ro` e `query_only=ON`; não há opção `--apply`.

Por padrão, seleciona 20 referências com seed 42, incluindo até quatro controles
previamente conferidos: Aristoteles 114, Democritus 168, Hippocrates 108 e
Jamblichus 51. As demais são amostradas entre janelas delimitadas com pista nominal,
sem pista literal e janelas ampliadas entre âncoras externas. O arquivo esperado
dos controles fica no log, nunca no payload enviado ao modelo, e não influencia
a construção da janela. Esses controles são casos revisados anteriormente, não
um conjunto independente que demonstre precisão geral.

Para inspecionar o piloto, gravando os payloads completos sem chamadas externas:

```bash
python scripts/deepscan_index_jev.py \
  --no-api --include-payload --max-references 20 --max-files 5 --seed 42 \
  --resume-log data/jev_validation/jev20_2.jsonl \
  --resume-log data/jev_validation/after_editorial_review_001.jsonl \
  --log data/jev_validation/deepscan_pg040_preview_001.jsonl
```

Para executar as avaliações com `AI_GATEWAY_API_KEY` já definida no ambiente:

```bash
python scripts/deepscan_index_jev.py \
  --max-references 20 --max-files 5 --seed 42 \
  --limit 100 --workers 4 --max-cost-usd 0.05 --max-seconds 1800 \
  --resume-log data/jev_validation/jev20_2.jsonl \
  --resume-log data/jev_validation/after_editorial_review_001.jsonl \
  --log data/jev_validation/deepscan_pg040_001.jsonl --verbose
```

O caminho do log deve ser novo. Para retomar, acrescente o log anterior como mais
um `--resume-log` e escolha outro `--log`. A seleção é reproduzível para os mesmos
dados e argumentos. Ao terminar um piloto e querer ampliar a amostra, aumente
`--max-references`; repetir a mesma seleção pode resultar somente em cache.

| Opção | Significado |
| --- | --- |
| `--max-references 20` | Máximo de janelas selecionadas, incluindo os controles. |
| `--limit 100` | Máximo de tentativas HTTP novas, incluindo retries; zero não envia chamadas. |
| `--max-files 5` | Máximo de arquivos na janela inteira; janelas maiores são excluídas, não truncadas. |
| `--controls 0` | Desativa controles automáticos para uma campanha de pendências. |
| `--ref-id ID` | Restringe a IDs explícitos; repetível. Um controle pedido explicitamente pode ser incluído mesmo com controles automáticos desativados. |
| `--max-references 1000 --controls 0` | Seleciona todas as pendências elegíveis atualmente disponíveis em PG040. |
| `--max-files 13` | Permite também a janela ampliada 312–324, para uma etapa posterior. |
| `--include-text` / `--include-payload` | Guarda OCR/payload completo no JSONL; por padrão há hashes e amostra do texto. |
| `--show-payload` | Mostra o payload no terminal; não altera o número de chamadas. |

Em `--no-api`, todas as janelas selecionadas são preparadas independentemente de
`--limit`, que limita chamadas reais. O campo `estimated_uncached_associations`
estima quantas avaliações faltariam, antes de retries. O status `prepared` indica
preparação completa, não avaliação semântica: `complete_windows` continua zero.

### Resultados e retomada

Cada arquivo recebe o OCR completo e exatamente uma pergunta `topic_present`.
O script reutiliza o cliente HTTP, o pool e os limites existentes. Retries para
429/5xx consomem o limite global. O orçamento interrompe novos despachos; pedidos
em voo são registrados ao terminar e podem ultrapassar o teto de custo.

Antes do envio, verifica se as fontes e o registro do banco continuam compatíveis
com o planejamento. Hashes são recalculados quando os metadados do arquivo mudam.
Uma alteração detectada invalida a janela. Páginas ilegíveis, inacessíveis ou grandes
demais não são truncadas e tornam a avaliação da janela incompleta.

O JSONL contém `window_plan`, `deepscan_association`, `window_result` e `summary`,
além da proveniência da execução e dos motivos de exclusão. As avaliações são
gravadas e sincronizadas individualmente. Logs interrompidos podem ser reutilizados;
uma última linha JSON incompleta é ignorada pelo leitor de cache.

Ao fim, cada janela fica como `single_positive`, `multiple_positives`, `no_positive`
ou `incomplete`. O ranking e a diferença entre os dois maiores escores são pistas,
não probabilidades de localização calibradas. `single_positive_file` só aparece
quando todos os arquivos foram avaliados com sucesso e exatamente um foi positivo.
Todos os resultados mantêm `needs_review=true` e `promotion_allowed=false`.

As avaliações do deepscan têm um tipo de registro próprio, que o aplicador antigo
ignora por padrão. O novo leitor pode reutilizar tanto avaliações anteriores do
validador quanto do próprio deepscan, desde que modelo, instrução, identidade da
entrada e hashes do texto/payload coincidam. A janela atual é sempre reagregada;
o cache não transforma uma antiga decisão em uma resolução automática.

### Validação realizada

Foram aprovados **131 testes focados**, incluindo HTTP simulado, workers reais
no Native Lab, limite global, retries, cache, fontes/banco alterados, leitura
completa do OCR, ausência de mutações no SQLite e classificação de janelas parciais.

Nos dados reais, sem API:

- `deepscan_pg040_dry_002.jsonl`: 20 referências, quatro controles e 50 payloads.
- `deepscan_pg040_cached_dry_001.jsonl`: os mesmos 50 payloads, quatro avaliações
  compatíveis encontradas nos logs anteriores e estimativa de 46 chamadas novas.
- `deepscan_pg040_all_dry_001.jsonl`: 85 pendências e 223 payloads, sem controles.

Os logs estão em `data/jev_validation/`. O primeiro arquivo de desenvolvimento,
`deepscan_pg040_dry_001.jsonl`, foi interrompido durante um problema de amostragem
já corrigido e não representa uma execução concluída. Os testes não usaram a API
paga; as únicas chamadas HTTP foram contra o servidor local simulado.

### Revisão do primeiro piloto pago e ampliação

O usuário executou `data/jev_validation/deepscan_pg040_001.jsonl`: 20 janelas
completas, 50 pares avaliados, quatro respostas reutilizadas e 46 chamadas novas,
por US$ 0,00955311. Das 16 referências novas, 15 tiveram um único positivo e uma
teve quatro positivos. Os quatro controles também tiveram um único positivo,
mas suas respostas positivas vieram do cache; isso não mede repetibilidade do modelo.

Na revisão local, os hashes dos 50 arquivos coincidiram com os registrados e as
20 referências continuaram iguais aos snapshots do banco. Amostras positivas
têm evidência textual recuperável: Galenus no físico 350 (`ut Galenus in tertio De
demonstratione`), Paulus no 271 (`Paulum morsu viperæ`), Eunomiani no 308
(`maxime ab Eunomianis`) e Dicæarchus no 274 (`Δικαίαρχος`). Para `Crilias`, o
físico 273 traz `Critias sanguinem`; a grafia do índice ainda exige revisão, sem
correção automática. Esta foi uma inspeção do OCR, não uma nova revisão de fac-símiles.

`Aristoteles, 40` (423595) teve positivos nos físicos 284–287: há menções reais,
inclusive no aparato editorial, enquanto 285 contém discussão no texto principal.
Portanto, não se deve promover 285 apenas por ter o maior escore (0,97). A presença
no arquivo inteiro não demonstra que a ocorrência pertence à página editorial 40.
O controle `Aristoteles, 114` também tem um negativo próximo do limiar em 355 (0,48).
Todos os pares continuam marcados para revisão; não houve atualização do banco.

A ampliação foi preparada sem API em
`data/jev_validation/deepscan_pg040_expansion_preview_001.jsonl`, usando
`--max-references 1000 --controls 0 --max-files 5` e o log pago como cache:
85 referências, 223 pares, 39 reutilizáveis e **184 avaliações novas** antes de
retries. Ao custo médio observado no piloto, a projeção é aproximadamente
US$ 0,0382, sem garantia de preço ou tamanho médio das páginas seguintes.
Recomenda-se manter o teto de cinco arquivos por janela nesta ampliação; nove
referências excedem esse teto, duas não têm âncoras externas suficientes e duas
têm qualificações editoriais que exigem revisão. A ampliação serve para colher
evidências, mantendo a promoção de referências separada.

### Resultado da ampliação para 85 referências

O log `data/jev_validation/deepscan_pg040_002.jsonl` contém 85 janelas completas:
59 com um positivo, 19 com múltiplos e sete sem positivo. Foram 223 pares,
39 reutilizados e 184 chamadas novas, por US$ 0,03845814, sem erros de API ou
alterações do banco. Entre os 59 positivos únicos, 53 têm escore de pelo menos
0,9; isso serve para priorizar revisão, não mede precisão. Manichæi (423665) e
Galenus (423638) têm alternativas negativas próximas do limiar (0,40 e 0,42).

Os sete sem positivo são Apollinarius, 47 (423588); Hippocrates, 65 (423652);
Apollo Cretensium legislator, 151 (423590); Galenus, 91 (423639); Paulus apostolus,
173 (423679); Aristoteles, 103 (423604); Galenus, 84 (423637). Busca de grafias
latinas/gregas em seus arquivos não prova ausência semântica. No caso de Apollo,
o físico 388 contém explicitamente `Cretenses ad Jovem, Lacedæmonii ad Apollinem`:
há Apolo, mas a qualificação da entrada diverge do texto. É necessário conferir
o índice/fac-símile antes de atribuir o negativo a um erro do classificador.

O dry-run `data/jev_validation/deepscan_pg040_wide_preview_001.jsonl` permitiu
até 13 arquivos por janela, com `--controls 0 --max-references 1000` e cache do
log 002. Preparou 94 referências e 340 pares: 223 reutilizáveis e 117 novos,
correspondentes às nove referências antes excluídas pelo teto de cinco arquivos.
Essa opção admite janelas maiores existentes; não alarga automaticamente as
janelas já avaliadas dos sete negativos. Ao custo médio da execução 002, os
117 pares novos custariam aproximadamente US$ 0,0245, antes de retries.
Continuam excluídos dois casos sem âncoras e dois com qualificações editoriais.
Não foram realizadas chamadas pagas nesta revisão.

## Inventário estatístico das pendências — 2026-11-03

Gerador: `scripts/report_alphabetical_pending.py`. Resultado final desta revisão:
`data/alphabetical_pending_inventory_20261103_final/`. As pastas sem sufixo e
com `_v2` foram execuções de desenvolvimento; usar `_final` para este levantamento.
O script consulta SQLite com `mode=ro` e `query_only`, não chama API, não modifica
resoluções e não varre o texto do corpus. Foram aprovados quatro testes unitários.

```bash
python scripts/report_alphabetical_pending.py \
  --log data/jev_validation/after_editorial_review_001.jsonl \
  --log data/jev_validation/deepscan_pg040_002.jsonl \
  --out data/alphabetical_pending_inventory_proxima
```

### Escopo e arquivos

- `volumes.csv`: totais, entradas distintas e participação nas pendências por volume.
- `sections.csv`: tipo, título, intervalo físico cadastrado, numeração editorial
  cadastrada não verificada, caminhos OCR, disponibilidade de imagens e conflitos
  entre fontes das entradas e limites da seção.
- `source_pages.csv`: concentração por arquivo de origem registrado; não é uma
  certificação da proveniência, nem uma lista pronta para envio a um extrator.
- `pending_references.csv`: cada referência pendente com IDs, lema, origem e diagnóstico.
- `entries_without_material_references.csv`: entradas sem referências materiais
  selecionadas; não são automaticamente omissões, pois incluem remissões e escrituras.
- `summary.json`: denominadores, exclusões, hashes dos logs e indicadores globais.

Pendência significa `locator_status != resolved`, em seções alfabéticas próprias,
excluindo referências bíblicas e seções `source_only`. São **34.267 pendências em
35.309 referências materiais**, distribuídas por **41 volumes**, ligadas a
**10.343 registros de entrada**. Há 1.042 resolvidas; as pendências se dividem em
34.243 `unresolved` e 24 `unverified`. Nenhuma pendência desse recorte tem destino
preenchido e nenhuma resolução está sem destino. As 5.526 referências fora do
recorte permanecem contabilizadas separadamente, não foram apagadas.

O banco chamado alfabético/onomástico também contém índices analíticos. Pelo
recorte taxonômico `author_index`, `onomastic_mixed`, `onomastic_person`, há
**9.371 pendências em 10.069 referências**, com 3.464 entradas afetadas. PG110
concentra 7.439 dessas pendências, cerca de 79,4%. A taxonomia atual também pode
precisar de revisão: PG113 e PG066 mostram seções diferentes agrupadas sob um
único tipo. Contagens de entradas não equivalem a verbetes impressos, pois há
registros que concatenam vários lemas.

### Concentração por volume e páginas do índice

Os intervalos abaixo são arquivos físicos, não destinos citados pelo índice.
Os limites são os cadastrados nas seções e precisam das ressalvas indicadas.

| Volume | Referências pendentes | Entradas afetadas | % das pendências | Arquivos físicos das seções do índice |
|---|---:|---:|---:|---|
| PL010 | 7.499 | 313 | 21,88% | 462–520 |
| PG110 | 7.439 | 2.513 | 21,71% | 648–666 |
| PG148 | 3.868 | 101 | 11,29% | 733–756 |
| PG113 | 3.468 | 324 | 10,12% | 644–654; fontes registradas se estendem a 666 |
| PG066 | 1.874 | 864 | 5,47% | 896–903; fontes registradas se estendem a 906 |
| PG020 | 1.733 | 1.302 | 5,06% | 784–786 autores; 786–791 analítico |
| PG127 | 1.252 | 761 | 3,65% | 750–752; 753–758 |
| PG069 | 1.178 | 887 | 3,44% | 740–744; fontes registradas até 746 |
| PG105 | 1.156 | 225 | 3,37% | 718–720 |
| PG076 | 1.088 | 1.030 | 3,18% | 741–747 |
| PG003 | 970 | 312 | 2,83% | 588–594 |
| PO022 | 760 | 278 | 2,22% | 313–318; seção adicional em 321 |
| PG119 | 614 | 510 | 1,79% | 655–659 |
| PG145 | 459 | 441 | 1,34% | 673–675 |
| PO014 | 405 | 191 | 1,18% | 853–856 analítico; 857–860 nomes |
| PO015 | 233 | consultar CSV | 0,68% | 297–299 |
| PG040 | 98 | consultar CSV | 0,29% | 426; aviso editorial e concordância em 426–427 |

Os quatro primeiros somam **65%**. Seus intervalos cadastrados abrangem 113
arquivos físicos distintos, todos com imagem local encontrada: PL010 59,
PG110 19, PG148 24 e PG113 11. Isso não certifica a correspondência das imagens
nem inclui as páginas adicionais em conflito com as seções.

### Problemas predominantes e consequência para a próxima extração

Os diagnósticos abaixo combinam bloqueios do banco atual com observações do log
`after_editorial_review_001` cuja identidade da entrada ainda coincide. Não se
reexecutou o localizador nem se revalidaram seus destinos; são evidências históricas.
O deepscan é registrado separadamente e não substitui esses diagnósticos.

| Diagnóstico | Referências pendentes |
|---|---:|
| Jev negativo no candidato então avaliado | 12.299 |
| Fonte da entrada não identificada unicamente | 5.432 |
| Numeração editorial requer mapeamento | 5.426 |
| Várias páginas físicas candidatas | 4.558 |
| Entrada editorial requer revisão | 4.287 |
| Entrada agrupada requer segmentação | 1.233 |

Um negativo semântico não prova extração ruim nem ausência do tema. Em PL010,
5.002 referências têm `source_anchor_not_unique`, 1.889 dependem de numeração e
608 estão explicitamente agrupadas. Em PG148, 2.701 requerem revisão da entrada
e 451 segmentação; em PG113, são 1.446 e 174, respectivamente. Esses três volumes
são candidatos prioritários à reextração estruturada. PG110 exige sobretudo
preservar a convenção página/linha, a edição Petropolitana e as séries editoriais.

Exemplos para um piloto de fac-símile + OCR, sempre com vizinhos para continuidade:

| Volume e arquivo físico | Referências pendentes atribuídas | Registros de entrada | Motivo para inspeção |
|---|---:|---:|---|
| PL010 481 | 396 | 2 | Um registro chega a 26.321 caracteres; verificar concatenação de verbetes |
| PL010 489 | 471 | 23 | Fonte ambígua e contexto extenso; máximo de 30.877 caracteres |
| PG148 736 | 210 | 3 | Muitos destinos concentrados em poucos registros |
| PG148 739 | 206 | 2 | Confirmar limites de lemas e sublemas |
| PG113 644 | 68 | 2 | Registro de Aaron contém vários outros lemas |
| PG113 651 | 456 | 108 | Segmentação, fontes ambíguas e diversidade de entradas |
| PG113 653 | 434 | 45 | Verificar atribuições após reparos anteriores |
| PG110 660 | 315 | 98 | Nota editorial e transição para referências página/linha |
| PG110 662 | 554 | 135 | 551 referências bloqueadas por numeração editorial |
| PG110 664 | 576 | 120 | 541 bloqueadas por numeração e 35 componentes de linha |

São contagens do banco atribuídas ao arquivo, não transcrições visuais. O piloto
deve reconstruir lema/sublema, referência literal, edição/tomo/série e página/linha,
com IDs de linhas/blocos, recortes de origem, reparos explícitos e fragmentos de
continuação. A comparação deve medir verbetes e referências recuperados, não só
quantidade de registros novos. Nessa fase, produzir staging e diff; o mapeamento
do destino permanece etapa separada.

### Proveniência e limites de seção que precisam de investigação

Há **4.309 referências pendentes** cujas fontes registradas estão fora do intervalo
da seção. Isso pode indicar limites errados, mistura de seções ou destino copiado
como origem; não se deve resolver automaticamente estendendo todos os intervalos.

- PG113: 1.281 fora do intervalo 644–654. O OCR 655 tem o cabeçalho
  `IN EXCERPTA DE LEGATIONIBUS`; 660 ainda tem `INDEX RERUM`, mas 666 já é
  `ORDO RERUM`. É preciso redescobrir as divisões do índice e seu limite final.
- PG066: 647 fora do intervalo analítico 896–903. O OCR 904 é `INDEX AUCTORUM`,
  enquanto 906 já é `ORDO RERUM`. Não agrupar automaticamente esses materiais.
- PG127: as 1.252 pendências têm fontes registradas fora das seções 750–758,
  muitas no corpo da obra. Priorizar reparo da proveniência antes de amostrar por origem.
- PO022: os 758 casos das seções 313–318 têm fontes registradas no corpo; a
  página 313 contém a nota de que as tabelas remetem à paginação entre colchetes.
  Usar a seção impressa para recuperar proveniência, não os ponteiros suspeitos.

Há ainda **2.881 entradas sem referência material selecionada**, listadas em CSV
próprio, incluindo remissões e material bíblico. Não foram somadas às 34.267
pendências de localização. Esse grupo exige uma auditoria de cobertura distinta
antes de concluir que faltam referências ou páginas extraídas.

## 2026-11-05 — Caminhos de OCR ausentes em PG141 e PL123

Após autorização explícita, corrigidos cinco registros de `alphabetical_entries`
no banco `data/alphabetical_indices.db`, substituindo quatro nomes de OCR ausentes
pelos únicos arquivos existentes das mesmas páginas físicas:

| Entradas | Página física | UUID antigo | UUID atual |
| --- | --- | --- | --- |
| `PG141:entry:000018` | PG141/530 | `8f2520ef-4513-47f0-a5b2-0f2cc7ac981e` | `0894b274-5e78-41c8-becf-2785c983baf9` |
| `PL123:entry:0087`, `PL123:entry:0088` | PL123/296 | `6795d98f-b851-48d7-ab40-d53ad0368cdc` | `968f90a7-48cf-4ce2-8726-b1494b8a3589` |
| `PL123:entry:0089` | PL123/298 | `6795d98f-b851-48d7-ab40-d53ad0368cdc` | `968f90a7-48cf-4ce2-8726-b1494b8a3589` |
| `PL123:entry:0095` | PL123/398 | `cd8fe941-b052-4fed-a2d4-ec96f71c7c45` | `90a2e653-a5e5-4bb2-ba62-9391927b1e8c` |

Motivo: restaurar caminhos legíveis para o experimento de parsing. Foram
verificados existência, unicidade da página física e leitura UTF-8 dos OCRs;
as quatro imagens também existem e foram decodificadas sem erro na auditoria.
Isso não comprova equivalência das versões nem valida a atribuição editorial
original. O conteúdo de prosa/prefácio/lista de edições/Auctaria continua como
pendência de revisão de origem.

Backup completo via SQLite Backup API, com bloqueio de escrita durante a operação:
`data/index_entry_parser/source_path_repair_20261105T163917Z/before.sqlite`.
Manifesto com hashes e registros completos antes/depois:
`data/index_entry_parser/source_path_repair_20261105T163917Z/changes.json`.

Aplicação transacional e conferência após commit confirmaram exatamente cinco
entradas alteradas. Os únicos campos alterados nessas entradas foram
`index_entry_source_ocr_file` e seu alias legado `editorial_anchor_file`, sincronizado
pelo trigger existente. Lemas, literais e resoluções foram preservados. Nenhuma
inferência foi realizada. Os relatórios experimentais anteriores permanecem como
registro do estado anterior; uma nova rodada lerá os caminhos corrigidos.
