# Estratégia da Pipeline de Extração de Índices Alfabéticos

Este documento descreve a estratégia operacional da pipeline de extração de índices alfabéticos, analíticos, onomásticos, remissivos, bíblicos e de concordância.

O foco aqui não é o schema final do payload. O foco é explicar por que a pipeline foi desenhada para:

- trabalhar um volume por vez
- separar extração semântica de localização material
- dividir as citações em shards estáveis
- salvar estado parcial em disco
- usar scripts Python auxiliares para montagem e correção estrutural
- reduzir dependência da memória de trabalho do LLM

Para o contrato do payload, ver:

- `docs/contrato_extrator_indices_alfabeticos.md`
- `docs/normalizacao_indices_biblicos.md`

Para dúvidas operacionais recorrentes, ver:

- `docs/duvidas_frequentes_extracao_indices_alfabeticos.md`

Ordem normativa: o prompt runtime decide apenas paths e ownership da fase; o contrato de schema
vem em seguida; a normalização bíblica especializa esse contrato; este documento governa o
workflow. O levantamento e o FAQ são descritivos.

## 1. Problema operacional

Volumes de `PG`, `PL` e `PO` podem ter:

- várias seções editoriais diferentes no mesmo OCR tail
- OCR ruidoso
- drift entre paginação impressa e sufixo físico do arquivo OCR
- payloads grandes demais para edição manual segura
- necessidade de reparo localizado após falhas de validação ou localização

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

## 3. Arquitetura vigente: semântica, localização e montagem

O driver padrão executa:

1. um agente semântico por volume, que acompanha o índice completo e grava um fragmento por seção
2. um estimador determinístico de paginação para PG/PL, ou o localizador auxiliar para PO
3. para referências bíblicas, um perfil de formato por seção e uma varredura regex invertida do
   volume, lendo cada arquivo uma vez e excluindo a própria seção de índice
4. um item independente por citação material, identificado por `(entry_key, ref_order)`
5. shards de 40 citações para agentes localizadores em contextos novos
6. montagem e validação integralmente em Python
7. um único agente de reparo somente quando há citação pendente ou shard inválido; ambiguidade
   material genuína pode permanecer `ambiguous` depois dele

Para tabelas bíblicas regulares, a ordem de evidência é:

1. JSON semântico já extraído
2. parser determinístico da tabela (`livro/cabeçalho → passagem → páginas editoriais`)
3. índice invertido de ocorrências no corpo, com bônus explícito para blocos
   `tipo="aparato_critico"`
4. top-N candidatos compactos por ocorrência
5. micro-prompt especializado apenas para linhas que o parser não conseguiu fechar

O `citation_format_profile` registra aliases realmente observados, separadores, ordem de campos,
herança do cabeçalho e estilo de coluna. O micro-prompt recebe esse perfil, as linhas pendentes e
o JSON parcial; nunca recebe páginas inteiras.

Nenhum agente final lê todos os fragmentos montados. O payload canônico é produzido pelo montador
determinístico. `ORDO RERUM` pertence à pipeline geral; aqui funciona somente como limite de
parada, inclusive dentro de um arquivo físico misto.

O mesmo vale para `addenda/corrigenda`, errata e outros `editorial_closure`: pertencem à pipeline
geral ou delimitam a varredura. `ordo_rerum` e `editorial_closure` são enums legados e não podem
ser emitidos por novos payloads alfabéticos.

## 4. Por que dividir para conquistar

O padrão recomendado é `divide-and-conquer`.

O volume não precisa ser resolvido como um único problema monolítico. Depois da leitura semântica,
as localizações são quebradas em shards estáveis:

- por seção editorial
- por citação material
- por faixa determinística de `(entry_key, ref_order)`

Exemplos de chunking útil:

- fragmentos semânticos separados por seção
- citações 1–40, 41–80 e assim por diante
- uma entrada com várias citações distribuída em itens localizadores distintos

Esse desenho é melhor porque:

- reduz o tamanho do contexto ativo
- permite validar uma parte sem reabrir o volume inteiro
- facilita retries localizados
- evita que correções tardias destruam trechos já estabilizados

## 5. Intermediários como checkpoint real

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

Com isso, um rerun não precisa "lembrar" tudo. Ele pode ler o checkpoint e continuar somente
quando o driver confirmar o fingerprint de inputs, `source_root`, versão do contrato/prompt e
taxonomia. Artefato estruturalmente válido com fingerprint antigo deve ser regenerado.

## 6. `todo.json` não é payload

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

## 7. Por que usar scripts Python auxiliares

Em muitos volumes, o pior caminho é pedir que o agente reescreva um JSON enorme manualmente.

Scripts e módulos em `scripts/pipeline_index_extraction/` e `tools/indexing/` existem para
tratar etapas mecânicas e repetíveis, como:

- criar shards de citações com ownership exato
- normalizar campos
- validar chaves e resultados
- aplicar localizações aos refs
- montar o payload final a partir dos fragmentos semânticos
- construir pedidos compactos de reparo

Isso é melhor do que edição manual porque:

- reduz erro de digitação estrutural
- deixa o procedimento reexecutável
- facilita revisão
- reduz contexto desnecessário no LLM

Em outras palavras:

- o LLM faz julgamento editorial
- o script faz montagem e transformação determinística

## 8. Por que isso ajuda o LLM

Essa estratégia não existe por estética. Ela existe porque LLMs não são bons lugares para manter estado operacional grande e frágil.

O desenho atual reduz a necessidade de:

- atenção constante ao volume inteiro
- reconstrução mental de decisões já tomadas
- reedição de blobs longos
- dependência de contexto acumulado entre retries

O agente pode trabalhar assim:

1. detectar estrutura
2. salvar fragmentos por seção
3. resolver um shard de citações
4. salvar resultados localizadores
5. deixar o Python montar e validar
6. corrigir apenas as citações pendentes

Esse fluxo é muito mais robusto do que:

1. ler tudo
2. improvisar tudo
3. escrever um JSON gigantesco
4. descobrir no final que uma chave duplicou

## 9. Relação com validação e retries

O importador e o validador existem para falhar cedo e com erro localizável.

Quando a pipeline salva estado parcial corretamente:

- uma falha em `refs[2]` não exige reconstruir `sections`
- uma falha de `section_kind` não exige reextrair `entries`
- uma falha de `entry_key` duplicado pode ser corrigida por script

Isso só funciona bem quando o volume já foi offloadado em intermediários pequenos e estáveis.

Sem isso, toda falha vira retrabalho amplo.

## 10. Papel do helper e do estimador

O helper material (`index_target_locator`) e o estimador de paginação editorial são apoios, não a fonte canônica do payload.

Eles ajudam a:

- sugerir `target_file`
- confirmar ou tensionar `page_hints`
- reduzir ambiguidade material

Mas a estratégia geral continua a mesma:

- salvar evidência
- preservar rastreabilidade em `raw_json`
- evitar decisões implícitas não documentadas

## 11. O que é canônico e o que é auxiliar

Canônico para importação:

- `data/alphabetical_index_payloads/<VOLUME>_alphabetical_indices.json`

Auxiliar:

- `data/intermediate_payloads/<VOLUME>/*`
- `semantic/manifest.json` e fragmentos por seção
- `editorial_page_map.json`
- `locator_workplan.json` e shards de resultados
- `repair_request.json`, apenas quando necessário

### Checkpoints mecânicos internos

A execução compacta não trata mais localização como um único bloco descartável. Os seguintes
artefatos formam handoffs verificáveis e podem ser reutilizados separadamente:

1. `ocr_source_snapshot.json`: identidade de conteúdo do volume;
2. `mechanical_analysis.json`: leitura regex/Aho-Corasick do material do índice;
3. `editorial_page_map.json`: inferência editorial por cabeçalhos e vizinhos;
4. `deterministic_text_stage.json`: busca literal/fuzzy de nomes, contexto e locators internos;
5. `scripture_candidate_stage.json`: busca bíblica persistida ou por varredura;
6. `locator_items.json`: fusão canônica de candidatos e contrato v2;
7. `locator_cache/`: decisões terminais por `(entry_key, ref_order)`;
8. `repairs/repair-XXXX_{request,result}.json`: reparos grandes divididos nos mesmos limites dos
   shards de localização;
9. `assembly_report.json`: fingerprint do payload montado.

Os candidatos em `locator_items.json`, `analysis_locator_items.json` e nos shards podem conter
`facsimile_hint.image_path`. O pareamento é determinístico, mas permanece marcado como não
inspecionado até o agente abrir a imagem. O hint não é copiado para resultados locator nem para o
payload canônico; somente a conclusão editorial vinculada ao OCR pode influenciar a decisão.

Os sidecars `*.checkpoint.json` validam tanto a entrada quanto o SHA-256 da saída. O snapshot de
OCR invalida automaticamente as etapas dependentes quando um arquivo muda, mesmo que o payload
filtrado permaneça igual. Checkpoints do agente continuam sujeitos aos fingerprints dos contratos
versionados.

O banco da pipeline em etapas registra os arquivos presentes nos resumos de execução em
`analysis_stage_artifacts` e oferece `analysis_occurrences_canonical` para consultas sem aliases
espaciais ambíguos.
- logs em `data/alphabetical_index_logs/*`
- scripts em `scripts/pipeline_index_extraction/`

Os auxiliares existem para produzir e sustentar o payload canônico, não para substituí-lo.

## 12. Quando essa estratégia deve ser preferida

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

## 13. Regra prática

Se o trabalho parece grande demais para o agente escrever corretamente em um único JSON final sem checkpoints intermediários, então ele já deveria estar usando:

- chunking
- intermediários por seção
- shards por citação
- montagem determinística
- reparo somente das pendências

Essa é a estratégia padrão recomendada desta pipeline.
