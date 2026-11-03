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
