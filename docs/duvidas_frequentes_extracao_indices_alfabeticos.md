# Dúvidas frequentes da extração de índices alfabéticos

Este documento consolida dúvidas recorrentes observadas nos logs reais de execução do extrator alfabético em `PG`, `PL` e `PO`.

O objetivo não é redefinir o contrato do schema. O objetivo é reduzir retrabalho: várias execuções voltam às mesmas perguntas sobre `section_kind`, `refs`, helper, paginação OCR, builders reutilizáveis e uso de payloads anteriores como molde.

Leia este documento junto com:

- [docs/contrato_extrator_indices_alfabeticos.md](/homessddata/Projects/pdfocr/docs/contrato_extrator_indices_alfabeticos.md:1)
- [docs/levantamento_indices_alfabeticos.md](/homessddata/Projects/pdfocr/docs/levantamento_indices_alfabeticos.md:1)
- [docs/normalizacao_indices_biblicos.md](/homessddata/Projects/pdfocr/docs/normalizacao_indices_biblicos.md:1)
- [docs/taxonomia_indices.md](/homessddata/Projects/pdfocr/docs/taxonomia_indices.md:1)

## 1. Posso usar outros volumes como modelo?

Sim, mas apenas como modelo estrutural e editorial.

Use outros volumes para:

- copiar o shape de `sections`, `nodes`, `entries`, `refs`, `scripture_refs`, `coverage`, `notes`
- confirmar a taxonomia fechada de `section_kind`, `entry_kind`, `ref_kind`
- ver como casos parecidos foram serializados
- reaproveitar builders existentes em `scripts/pipeline_index_extraction/`

Não use outros volumes para:

- copiar conteúdo
- importar lemas, refs ou anchors de outro volume
- justificar target files fora do `source_root` do volume corrente

Payloads que aparecem repetidamente como gabarito estrutural nos logs:

- `PL001`
- `PL025`
- `PL002`
- `PL015`
- `PO003`

Builders e scripts que aparecem repetidamente como gabarito operacional nos logs:

- `assemble_alphabetical_payload.py`
- `build_pl169_alphabetical_payload.py`
- `build_pl154_alphabetical_payload.py`
- `build_pl143_alphabetical_payload.py`
- `build_pl123_ordo_rerum_payload.py`
- `build_pl076_alphabetical_payload.py`

## 2. O que os agentes mais procuram em payloads já existentes?

Os logs mostram quatro necessidades principais quando um agente abre payloads já existentes:

1. confirmar o shape exato do JSON que o importador aceita
2. decidir `section_kind` sem inventar enum novo
3. ver exemplos reais de `refs` e `scripture_refs`
4. comparar como um caso válido preenche `coverage`, `notes` e `raw_json`

Na prática, quando um agente abre outro payload, normalmente ele está buscando:

- granularidade correta de `entries`
- forma de `raw_json`
- como representar `ORDO RERUM`, `ELENCHUS`, `INDEX RERUM`, `INDEX AUCTORUM`, `TABLE ...`
- como separar `entry` de `ref`
- como um caso válido tratou `coverage` e `notes`

## 3. Quando devo usar um payload antigo como molde e quando devo abrir um builder?

Use payload antigo quando a dúvida é de schema ou serialização:

- quais chaves existem
- como `refs` foram modeladas
- como `coverage` foi preenchido
- como um `section_kind` parecido foi representado

Use builder antigo quando a dúvida é de workflow ou parser:

- como montar `helper_request.json`
- como dividir o volume em chunks
- como gerar intermediários
- como fazer merge de `entries.json`, `refs.json` e `scripture_refs.json`
- como associar páginas OCR, anchors editoriais e `target_file_best`

Regra prática:

- dúvida de forma final do JSON: abrir payload
- dúvida de processo de extração: abrir builder

Na prática observada nos logs:

- payloads são consultados para `shape`, taxonomia e exemplos de serialização
- builders são consultados para chunking, helper, page-map, merge e escrita do payload

## 4. Como decidir `section_kind`?

Não invente `section_kind`.

O valor deve vir do enum fechado do importador e do contrato. Se o heading for mais específico do que a taxonomia, preserve a nuance em:

- `heading_raw`
- `heading_norm`
- `section_key`
- `raw_json.section_kind_reason`

Exemplo típico:

- `INDEX AUCTORUM VETERUM QUI IN NOTIS LAUDANTUR.`
- `INDEX AUCTORUM RECENTIORUM QUI IN NOTIS LAUDANTUR.`

Ambos devem cair em:

- `author_index`

Não crie:

- `onomastic_veterum`
- `onomastic_recentiorum`

## 5. `ORDO RERUM` é índice alfabético?

Não necessariamente.

Nos casos reais do projeto, `ORDO RERUM` costuma ser:

- uma seção editorial de fechamento
- um sumário de conteúdos do tomo
- às vezes coexistindo com um índice alfabético real no mesmo volume

Em geral:

- use `section_kind: ordo_rerum`
- trate a seção como bloco próprio
- não force isso a virar `alphabetical_general`

Também é comum que:

- linhas estruturais virem `entry_kind: heading_group`
- linhas com paginação editorial produzam `refs`

## 6. Como separar `entry` de `ref`?

Use `entry` para a unidade lógica visível do índice.

Use `ref` para cada locator material/editorial individual dentro dessa entrada.

Sinais de que algo deve virar `ref`:

- número de página
- intervalo
- coluna
- linha
- locator paralelo

Sinais de que algo deve ficar no nível de `entry`:

- lema principal
- sublema
- nota editorial
- remissão sem locator material

## 7. Remissões como `vid.`, `vide`, `voir`, `cf.` viram `ref`?

Não, se não houver locator material real.

Esses casos devem ficar no nível da entrada, tipicamente como:

- `entry_kind: cross_reference`

ou preservados em:

- `entry_raw`
- `raw_json`

Não emita `refs` vazias só porque existe uma remissão textual.

## 8. Como pensar a relação entre página impressa e arquivo OCR?

Nunca confunda:

- o número do arquivo OCR `...-NNN.txt`
- a página impressa da página de índice
- a página citada dentro da linha do índice

Esses três sistemas frequentemente divergem.

Em volumes com CER alto ou drift de paginação:

- o arquivo OCR correto pode não ter o mesmo número da página impressa
- o locator citado pode bater melhor em um arquivo vizinho
- a melhor confirmação vem da combinação de heading, contexto, neighbors e busca textual

## 9. O que fazer quando o número impresso parece não bater com o arquivo OCR?

Não rejeite a hipótese só por mismatch numérico.

Faça pelo menos:

1. verificar o arquivo candidato
2. verificar um arquivo antes
3. verificar um arquivo depois
4. buscar o lema ou padrão local no `source_root`

Se o padrão do volume sugerir drift maior, expanda a janela local.

## 10. Quando usar `rg` e regex local?

Use quando:

- o helper vier `ambiguous`
- a página impressa não confirmar sozinha o target
- houver CER alto
- houver headings herdados
- o volume usar padrões repetitivos aproveitáveis

Buscas comuns:

- pelo lema
- pelo heading da seção
- por formas abreviadas ou sem diacríticos
- pelo padrão de refs do próprio volume

Exemplos úteis:

```text
rg -n -S "ORDO RERUM" <source_root>
rg -n -S "INDEX AUCTORUM" <source_root>
rg -n -S "Abgar" <source_root>
rg -n -P "(?i)matth\\.?\\s*$|matth\\.?\\s*\\n" <source_root>
```

## 11. Quando usar o helper `index_target_locator.py`?

Use o helper quando existe problema real de locator material.

Casos típicos:

- o índice cita páginas internas do volume
- a linha precisa de `target_file_best`
- há ambiguidade entre páginas vizinhas
- o volume tem drift entre paginação impressa e OCR

Não use o helper para:

- headings sem problema de locator
- linhas puramente estruturais
- inventar target onde o OCR não sustenta nada

## 12. O helper decide o payload?

Não.

O helper fornece:

- candidatos materiais
- probabilidades
- `candidate_role`
- `reason_summary`

O payload final ainda depende de julgamento editorial local.

Quando o helper for relevante, preserve evidência em `raw_json`.

## 13. O que os agentes mais procuram nos exemplos do helper?

Principalmente:

- shape de `helper_request.json`
- `entry_id`
- `query_names`
- `page_hints`
- significado de `candidate_role`
- como interpretar `reason_summary`

Os exemplos em `docs/examples/index_target_locator/*.json` devem ser usados como modelo procedural, não como conteúdo.

## 14. Quando criar script em `scripts/pipeline_index_extraction/`?

Crie script auxiliar quando:

- o volume é grande
- a estrutura se repete
- o payload ficaria grande demais para edição manual
- você já identificou uma transformação local estável

Use script para:

- extrair chunks
- normalizar campos
- fazer merge de intermediários
- renumerar chaves
- corrigir cascatas estruturais

Todo script novo deve:

- ter nome descritivo
- ter comentário curto no topo explicando uso
- evitar paths hardcoded fora dos paths passados em runtime

## 15. Quando usar intermediários?

Use `data/intermediate_payloads/<VOLUME>/` quando isso reduz rework.

Casos típicos:

- volume com várias seções
- rerun após falha
- payload muito grande
- necessidade de dividir por letras ou blocos

Arquivos típicos:

- `sections.json`
- `nodes.json`
- `entries.json`
- `refs.json`
- `scripture_refs.json`
- `coverage.json`
- `notes.json`
- `manifest.json`
- `todo.json`

## 16. Para que serve `todo.json`?

Serve para checkpoint operacional curto.

Use quando o trabalho for chunked ou quando houver reruns.

Ele deve responder rapidamente:

- o que já foi feito
- o que falta
- qual é o foco atual
- o que está bloqueado

Não use `todo.json` como substituto de `entries.json`, `refs.json` ou do payload final.

## 17. Quando `entries` pode ficar vazia?

Só em casos excepcionais.

Se `sections` existir e `entries` estiver vazia, isso precisa ser sustentado por:

- `coverage.entries_status`
- `coverage.entries_status_reason`
- `coverage.evidence_files`

Estados típicos aceitáveis:

- `no_line_items`
- `unrecoverable_ocr`
- `no_index_section`

## 18. Quando `partial_*` é aceitável?

Só quando as tentativas razoáveis já foram feitas.

Antes de cair em `partial_*`, espera-se:

- leitura direta do OCR
- neighbor checks
- busca local no volume
- regex derivada do padrão editorial local
- uso conservador do helper quando cabível

`partial_*` não deve ser atalho para evitar investigação.

## 19. O que fazer quando o importador falha?

Primeiro, identificar se a falha é:

- de schema
- de enum
- de unicidade
- de referência órfã
- de path fora do `source_root`

Os casos mais comuns observados no projeto foram:

- `section_kind` inventado fora do enum
- `entry_key` duplicado
- `ref_order` duplicado por `entry_key`
- remissão textual emitida como `ref` sem anchor
- `entry_key` reiniciado por seção
- inconsistência em cascata após trocar `section_key` só em uma parte do payload

## 20. O que fazer quando houver payload anterior inválido?

Use-o como checkpoint, não como verdade.

Ao reaproveitar payload anterior:

- verifique o erro exato da falha anterior
- localize o objeto apontado, por exemplo `refs[2]`
- corrija o intermediário correspondente quando houver
- remonte o payload final a partir dos intermediários

## 21. Posso apontar `target_file` para outro volume?

Não.

Pela regra de domínio deste pipeline, os índices aqui tratados apontam para referências internas ao próprio volume.

Mesmo quando o projeto aceita path relativo ou absoluto, o path material ainda deve resolver para o `source_root` do volume atual.

## 22. Quais são os gabaritos mais recorrentes observados nos logs?

### Payloads mais consultados como molde

- `PL001`
- `PL025`
- `PL002`
- `PL015`
- `PO003`
- `PL143`

Eles aparecem repetidamente porque ajudam a responder dúvidas sobre:

- shape mínimo do payload válido
- classificação de seções
- modelagem de `refs`
- convenções de `coverage`, `notes` e `raw_json`

### Builders e scripts mais consultados como molde operacional

- `assemble_alphabetical_payload.py`
- `build_pl169_alphabetical_payload.py`
- `build_pl154_alphabetical_payload.py`
- `build_pl107_alphabetical_payload.py`
- `build_pl079_alphabetical_payload.py`
- `build_pg016_03_alphabetical_payload.py`
- `build_pl072_alphabetical_payload.py`
- `build_pl150_alphabetical_payload.py`
- `build_pl143_alphabetical_payload.py`
- `build_pl123_ordo_rerum_payload.py`
- `pl113_ordo_rerum_build.py`
- `build_pl076_alphabetical_payload.py`

Esses scripts aparecem repetidamente porque já resolvem:

- `ORDO RERUM`
- `ELENCHUS`
- índice analítico
- mistura de blocos editoriais e locators materiais
- helper request/output
- intermediários e montagem final

## 23. Regra prática final

Se surgir uma dúvida durante a extração, resolva nesta ordem:

1. contrato fechado do schema
2. docs taxonômicos do projeto
3. payload válido de volume parecido
4. builder de volume parecido
5. OCR local do volume corrente
6. helper e regex local

Essa ordem tende a minimizar:

- enum drift
- shape inválido
- `refs` mal modeladas
- payloads enormes escritos manualmente sem checkpoint
