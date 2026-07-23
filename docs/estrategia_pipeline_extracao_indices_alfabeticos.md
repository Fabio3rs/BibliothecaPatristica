# Estratégia da Pipeline de Extração de Índices Alfabéticos

Este documento descreve a estratégia operacional da pipeline de extração de índices alfabéticos, analíticos, onomásticos, remissivos, bíblicos e de concordância.

O foco aqui não é o schema final do payload. O foco é explicar por que a pipeline foi desenhada para:

- trabalhar um volume por vez
- dividir volumes grandes em chunks estáveis
- salvar estado parcial em disco
- usar scripts Python auxiliares para montagem e correção estrutural
- reduzir dependência da memória de trabalho do LLM

Para o contrato do payload, ver:

- `docs/contrato_extrator_indices_alfabeticos.md`
- `docs/normalizacao_indices_biblicos.md`

Para dúvidas operacionais recorrentes, ver:

- `docs/duvidas_frequentes_extracao_indices_alfabeticos.md`

## 1. Problema operacional

Volumes de `PG`, `PL` e `PO` podem ter:

- várias seções editoriais diferentes no mesmo OCR tail
- OCR ruidoso
- drift entre paginação impressa e sufixo físico do arquivo OCR
- payloads grandes demais para edição manual segura
- necessidade de retries após falhas de validação, importação ou helper

Pedir que um LLM resolva tudo isso de uma vez, mantendo o volume inteiro "na cabeça", é a estratégia errada.

Os problemas principais são:

- contexto grande demais
- risco de esquecer decisões anteriores
- risco de reescrever partes boas ao corrigir uma parte ruim
- baixa auditabilidade quando tudo vira um único blob JSON editado manualmente

## 2. Princípio central: offload de memória para disco

A pipeline foi organizada para tirar da memória de trabalho do agente o que pode ser persistido de forma explícita.

Em vez de exigir que o modelo:

- lembre de todas as seções
- lembre do que já foi extraído
- lembre do que ainda falta
- lembre das correções feitas após uma falha de validação

o fluxo salva isso em disco como artefatos intermediários.

Isso reduz a carga cognitiva do agente e melhora:

- resumibilidade
- repetibilidade
- auditabilidade
- correção incremental

Em termos práticos, o disco vira a memória externa do run.

## 3. Por que dividir para conquistar

O padrão recomendado é `divide-and-conquer`.

O volume não precisa ser resolvido como um único problema monolítico. Ele pode ser quebrado em chunks estáveis, por exemplo:

- por seção editorial
- por letra
- por faixa de entries
- por passo estrutural
- por família de refs

Exemplos de chunking útil:

- `sections` primeiro, `entries` depois
- `analytic_subject` separado de `ordo_rerum`
- letras `A-C`, depois `D-H`, depois `I-Z`
- `entries.json` em um passe e `refs.json` em outro

Esse desenho é melhor porque:

- reduz o tamanho do contexto ativo
- permite validar uma parte sem reabrir o volume inteiro
- facilita retries localizados
- evita que correções tardias destruam trechos já estabilizados

## 4. Intermediários como checkpoint real

O diretório recomendado por volume é:

- `data/intermediate_payloads/<VOLUME>/`

Arquivos típicos:

- `volume.json`
- `sections.json`
- `nodes.json`
- `entries.json`
- `refs.json`
- `scripture_refs.json`
- `coverage.json`
- `notes.json`
- `manifest.json`
- `todo.json`

Esses arquivos existem para separar responsabilidades:

- `sections.json` guarda a estrutura editorial detectada
- `entries.json` guarda as unidades lógicas extraídas
- `refs.json` guarda as referências materiais
- `scripture_refs.json` guarda a camada bíblica parseada
- `coverage.json` e `notes.json` guardam estado editorial e limites do run
- `manifest.json` documenta o checkpoint salvo
- `todo.json` guarda progresso operacional curto

Com isso, um rerun não precisa "lembrar" tudo. Ele pode ler o checkpoint e continuar.

## 5. `todo.json` não é payload

`todo.json` tem função operacional, não estrutural.

Ele serve para registrar:

- foco atual
- o que foi concluído
- o que falta
- o que está bloqueado

Ele não substitui:

- `entries.json`
- `refs.json`
- o payload final

O valor dele é permitir retomada rápida sem depender de memória implícita do agente.

## 6. Por que usar scripts Python auxiliares

Em muitos volumes, o pior caminho é pedir que o agente reescreva um JSON enorme manualmente.

Scripts em `scripts/pipeline_index_extraction/` existem para tratar etapas mecânicas e repetíveis, como:

- extrair chunks estáveis
- normalizar campos
- renumerar chaves
- propagar mudanças estruturais
- montar o payload final a partir de fragmentos
- corrigir cascatas de `section_key`, `entry_key` ou `ref_order`

Isso é melhor do que edição manual porque:

- reduz erro de digitação estrutural
- deixa o procedimento reexecutável
- facilita revisão
- reduz contexto desnecessário no LLM

Em outras palavras:

- o LLM faz julgamento editorial
- o script faz montagem e transformação determinística

## 7. Por que isso ajuda o LLM

Essa estratégia não existe por estética. Ela existe porque LLMs não são bons lugares para manter estado operacional grande e frágil.

O desenho atual reduz a necessidade de:

- atenção constante ao volume inteiro
- reconstrução mental de decisões já tomadas
- reedição de blobs longos
- dependência de contexto acumulado entre retries

O agente pode trabalhar assim:

1. detectar estrutura
2. salvar checkpoint
3. resolver um chunk
4. salvar checkpoint
5. montar payload final
6. validar
7. corrigir apenas o ponto quebrado

Esse fluxo é muito mais robusto do que:

1. ler tudo
2. improvisar tudo
3. escrever um JSON gigantesco
4. descobrir no final que uma chave duplicou

## 8. Relação com validação e retries

O importador e o validador existem para falhar cedo e com erro localizável.

Quando a pipeline salva estado parcial corretamente:

- uma falha em `refs[2]` não exige reconstruir `sections`
- uma falha de `section_kind` não exige reextrair `entries`
- uma falha de `entry_key` duplicado pode ser corrigida por script

Isso só funciona bem quando o volume já foi offloadado em intermediários pequenos e estáveis.

Sem isso, toda falha vira retrabalho amplo.

## 9. Papel do helper e do estimador

O helper material (`index_target_locator`) e o estimador de paginação editorial são apoios, não a fonte canônica do payload.

Eles ajudam a:

- sugerir `target_file`
- confirmar ou tensionar `page_hints`
- reduzir ambiguidade material

Mas a estratégia geral continua a mesma:

- salvar evidência
- preservar rastreabilidade em `raw_json`
- evitar decisões implícitas não documentadas

## 10. O que é canônico e o que é auxiliar

Canônico para importação:

- `data/alphabetical_index_payloads/<VOLUME>_alphabetical_indices.json`

Auxiliar:

- `data/intermediate_payloads/<VOLUME>/*`
- `data/alphabetical_index_payloads/<VOLUME>_helper_request.json`
- `data/alphabetical_index_payloads/<VOLUME>_helper_output.json`
- logs em `data/alphabetical_index_logs/*`
- scripts em `scripts/pipeline_index_extraction/`

Os auxiliares existem para produzir e sustentar o payload canônico, não para substituí-lo.

## 11. Quando essa estratégia deve ser preferida

Ela deve ser o default quando houver:

- volume grande
- várias seções
- OCR ruim
- retries esperados
- payload extenso
- necessidade de correções localizadas

Ela é especialmente importante quando:

- o volume não cabe bem num único contexto estável
- o agente precisará voltar ao mesmo volume mais de uma vez
- a extração exige tanto julgamento editorial quanto transformação mecânica

## 12. Regra prática

Se o trabalho parece grande demais para o agente escrever corretamente em um único JSON final sem checkpoints intermediários, então ele já deveria estar usando:

- chunking
- intermediários
- `todo.json`
- script de montagem ou correção

Essa é a estratégia padrão recomendada desta pipeline.
