# Levantamento dos índices alfabéticos e remissivos

Este documento consolida a exploração manual feita sobre os índices finais dos volumes em `teste/PG*/text/*`, `teste/PL*/text/*` e `teste/PO*/text/*`, com foco no desenho de um fluxo novo, separado do extrator atual, para índices alfabéticos, remissivos, onomásticos e bíblicos.

O objetivo original aqui não era ainda fechar o schema final, mas fechar o suficiente para que um agente LLM não improvise estrutura nova durante a conversão para JSON.

Esse estágio já foi concluído no contrato em:

- [contrato_extrator_indices_alfabeticos.md](/homessddata/Projects/pdfocr/docs/contrato_extrator_indices_alfabeticos.md:1)

Portanto, este documento deve agora ser lido como:

- levantamento empírico que justifica o schema final
- catálogo de formatos materiais reais que o contrato precisa cobrir
- registro das lacunas ainda abertas fora do núcleo do schema

Este levantamento é descritivo e não normativo. Em caso de divergência, prevalecem, nesta ordem,
o contrato runtime da fase para paths/ownership, `contrato_extrator_indices_alfabeticos.md`,
`normalizacao_indices_biblicos.md` e a estratégia vigente. Exemplos e lacunas históricas abaixo
não reabrem decisões já fechadas no contrato.

Ele precisa responder a quatro perguntas:

- que tipos editoriais realmente existem no corpus
- que formatos materiais esses tipos assumem na página OCR
- o que o agente pode inferir com segurança
- o que o agente nunca deve inventar

## 1. Escopo do problema

O extrator atual em `scripts/run_index_extraction.py` foi desenhado para:

- inventário editorial do começo do volume
- índices de abertura de obra
- índices finais do volume, ainda no schema `works / sections / entries`

Esse modelo funciona razoavelmente bem para:

- `ELENCHUS`
- `INDEX CAPITUM`
- `ORDO RERUM`

Mas ele é insuficiente para vários índices remissivos reais encontrados no fim dos volumes, porque:

- uma entrada pode ter muitas referências
- uma entrada pode continuar em várias linhas ou páginas
- uma entrada pode ser hierárquica
- uma entrada pode conter remissões (`vide`, `vid.`), não apenas páginas
- um índice pode misturar páginas, colunas, linhas, aparelhos editoriais e referências bíblicas
- o OCR pode fragmentar a entrada sem destruir a estrutura do índice

Conclusão: faz sentido ter um fluxo novo, separado, com scanner, prompt, payload e import próprios.

## 2. Regra operacional básica

Ao ler esses volumes, é obrigatório separar:

- `arquivo_ocr`: identificador técnico do OCR
- `pagina_impressa`: número impresso no cabeçalho/rodapé
- `referencia_citada`: número citado dentro da entrada do índice
- `numeracao_paralela`: colchetes, colunas, linhas ou paginações herdadas

O sufixo `*.txt` não é paginação editorial.

Também não é seguro assumir que:

- o índice está exatamente nos últimos arquivos OCR
- o último arquivo do volume contém índice útil
- todos os índices finais de uma coleção compartilham o mesmo formato

Em vários volumes, o índice final relevante aparece alguns arquivos antes do fim material da pasta.

## 3. Princípio para uso com agente

Como o fluxo novo será baseado em agente LLM, a documentação precisa eliminar espaço para invenção estrutural.

Portanto:

- o agente só pode emitir classes explicitamente previstas neste documento
- o agente só pode usar campos conceituais explicitamente previstos neste documento ou no contrato do payload
- quando o caso real não couber com confiança em uma classe, o agente deve marcar ambiguidade, não criar subtipo novo
- quando houver dúvida entre “entrada lógica completa” e “fragmento OCR”, o agente deve preservar o fragmento com baixa confiança

Em outras palavras: falhar de forma conservadora é melhor do que produzir JSON criativo.

## 4. Amostragem dirigida

Esta é a amostragem sugerida para estabilizar a documentação e, depois, o contrato do agente.

### 4.1 PG

- `PG003`
  - esperado: `INDEX RERUM ET VERBORUM`; `ORDO RERUM` apenas como fronteira de parada
  - motivo: índice remissivo denso, com muitos lemas e múltiplas referências
- `PG011`
  - esperado: `INDEX ANALYTICUS`
  - motivo: índice analítico grande, com OCR danificado e fragmentação
- `PG036`
  - esperado: `INDEX ANALYTICUS` + `INDEX ORATIONUM`; `ORDO RERUM` como fronteira
  - motivo: caso híbrido, bom para distinguir tipos finais diferentes
- `PG021`
  - esperado: índice analítico denso
  - motivo: caso adicional de índice remissivo volumoso
- `PG097`
  - esperado: `INDEX SCRIPTORUM`
  - motivo: índice de autores citados, não apenas de assuntos

### 4.2 PL

- `PL143`
  - esperado: `INDEX ONOMASTICUS`, `ELENCHUS PERSONARUM`, `ELENCHUS LOCALIS`;
    `ORDO RERUM` como fronteira
  - motivo: caso excelente de índice hierárquico de nomes e lugares
- `PL163`
  - esperado: índices finais com cobertura parcial
  - motivo: volume útil para testar irregularidade
- `PL170`
  - esperado: índice final menos regular
  - motivo: amostra de PL com formato menos limpo
- `PL198`
  - esperado: `INDEX IN ADAMUM SCOTUM`; `ORDO RERUM` como fronteira
  - motivo: separa índice de obra específica de sumário final
- `PL207`
  - esperado: índice final relativamente rico
  - motivo: bom caso de controle em PL

### 4.3 PO

- `PO025`
  - esperado: nomes próprios siríacos, palavras siríacas, palavras gregas, citações bíblicas, citações patrísticas, perícopes, concordância
  - motivo: melhor caso de variedade interna
- `PO012`
  - esperado: fechamento de obra mais variado, possivelmente bilíngue e menos próximo de `PG/PL`
  - motivo: verificar distância editorial em relação a `PO025`
- `PO022`
  - esperado: múltiplos índices finais com boa densidade
  - motivo: segundo caso forte de PO
- `PO006`
  - esperado: tabelas/índices próprios de obra
  - motivo: caso PO mais “normal”
- `PO017`
  - esperado: índices menores
  - motivo: caso PO de complexidade relativamente menor

### 4.4 Prioridade máxima

Se for preciso reduzir o escopo inicial, os seis volumes mais informativos são:

- `PG003`
- `PG036`
- `PL143`
- `PL198`
- `PO025`
- `PO022`

## 5. Casos já lidos e o que eles mostram

### 5.1 `PG003`

Arquivos relevantes:

- [PG003, INDEX RERUM ET VERBORUM](/homessddata/Projects/pdfocr/teste/PG003/text/fbf896b3-ed57-422e-9668-d79cc8e21f05-591.txt:1)
- [PG003, ORDO RERUM](/homessddata/Projects/pdfocr/teste/PG003/text/fbf896b3-ed57-422e-9668-d79cc8e21f05-596.txt:1)

Padrões observados:

- índice remissivo de matérias e palavras
- entradas longas em latim
- muitas referências por entrada
- uso de `ibid.`
- letras de agrupamento no gutter (`A`, `B`, `C`, etc.)
- `ORDO RERUM` coexistindo com o índice analítico, mas como coisa editorialmente distinta

Implicação:

- o modelo não pode limitar cada entrada a uma única referência
- letras de agrupamento devem poder ser preservadas
- é preciso distinguir índice analítico de sumário final
- a unidade de extração não pode ser “linha OCR”

### 5.2 `PG011`

Arquivo relevante:

- [PG011, INDEX ANALYTICUS](/homessddata/Projects/pdfocr/teste/PG011/text/b52f0a60-de4c-4137-9a0f-a5102e881925-966.txt:1)

Padrões observados:

- índice analítico muito denso
- OCR com manchas e deslocamentos
- fragmentos partidos no meio da página
- ainda assim, a estrutura geral do índice permanece reconhecível

Implicação:

- o schema e o prompt devem aceitar entradas fragmentárias
- confiança deve poder existir em nível de entrada e também de referência
- nem toda página do índice será normalizável de forma limpa

### 5.3 `PG036`

Arquivos relevantes:

- [PG036, INDEX ANALYTICUS](/homessddata/Projects/pdfocr/teste/PG036/text/4bcea90d-6809-4174-804c-e363c596f952-696.txt:1)
- [PG036, INDEX ORATIONUM](/homessddata/Projects/pdfocr/teste/PG036/text/4bcea90d-6809-4174-804c-e363c596f952-665.txt:1)

Padrões observados:

- índice analítico forte
- presença também de `INDEX ORATIONUM`
- convivência de mais de um índice final no mesmo volume
- `INDEX ORATIONUM` compara `Novus Ordo` e `Vetus Ordo`, não sendo um remissivo alfabético comum

Implicação:

- o scanner do novo fluxo precisa separar seções finais por tipo
- um volume pode ter mais de um índice final útil e não apenas um bloco final
- formatos de correspondência/ordenação merecem classe própria

### 5.4 `PL143`

Arquivos relevantes:

- [PL143, INDEX ONOMASTICUS](/homessddata/Projects/pdfocr/teste/PL143/text/3f6bc174-597b-4055-810f-331a363722e9-802.txt:1)
- [PL143, INDEX IN CHRONICON / ELENCHUS ONOMASTICUS LOCALIS GERMANIÆ](/homessddata/Projects/pdfocr/teste/PL143/text/3f6bc174-597b-4055-810f-331a363722e9-805.txt:1)

Padrões observados:

- índice onomástico de pessoas e lugares
- hierarquia editorial explícita
- macrodivisões como `ELENCHUS PERSONARUM...`
- subgrupos como `PERSONALE ECCLESIASTICUM`, `SUMMI PONTIFICES`, `ARCHIEPISCOPI`
- subseções numeradas (`I.`, `II.`)
- remissões explícitas `Vid.` e `Vide`
- comentários editoriais longos dentro do índice

Implicação:

- índice de nomes não é lista plana
- o payload futuro deve suportar hierarquia opcional acima do lema
- faz sentido distinguir pessoa, lugar, grupo eclesiástico e remissão cruzada
- o agente precisa conseguir preservar comentário editorial sem tratá-lo como lema novo

### 5.5 `PL198`

Arquivos relevantes detectados:

- [PL198, INDEX IN ADAMUM SCOTUM](/homessddata/Projects/pdfocr/teste/PL198/text/73ee1ffc-330e-41ca-89d7-38bd826f3273-932.txt:1)
- [PL198, INDEX IN ADAMUM SCOTUM](/homessddata/Projects/pdfocr/teste/PL198/text/73ee1ffc-330e-41ca-89d7-38bd826f3273-939.txt:1)

Padrões observados:

- o material final útil não estava no último arquivo OCR da pasta
- o scanner por cabeçalho é mais confiável do que “pegar os últimos N arquivos”

Implicação:

- o fluxo novo deve detectar janelas finais por título, não por posição absoluta

### 5.6 `PO025`

Arquivos relevantes detectados:

- [PO025, INDEX DES CITATIONS DES ÉCRITURES](/homessddata/Projects/pdfocr/teste/PO025/text/e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-622.txt:1)
- [PO025, TABLE DES NOMS PROPRES SYRIAQUES](/homessddata/Projects/pdfocr/teste/PO025/text/e2ff8472-852c-459a-bd8f-e86b9c01ad9b-817.txt:1)
- [PO025, TABLE DES CITATIONS DE LA BIBLE](/homessddata/Projects/pdfocr/teste/PO025/text/e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt:1)
- [PO025, TABLE DES PÉRICOPES DE L'ÉCRITURE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-483.txt:1)
- [PO025, TABLE DE CONCORDANCE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-490.txt:1)
- [PO025, TABLES no sumário final](/homessddata/Projects/pdfocr/teste/PO025/text/b73c79c4-2a40-4b45-9516-1b504f5e1f0e-184.txt:1)

Padrões observados:

- a `PO` multiplica os tipos de índice final
- alguns índices são de nomes
- outros são de palavras
- outros são de citações bíblicas
- outros são de citações patrísticas
- outros são perícopes
- outros são concordâncias
- o sumário final confirma editorialmente a sequência dessas tabelas

Implicação:

- o novo fluxo não pode ter uma classe única “índice alfabético”
- a taxonomia precisa ser mais fina para `PO`
- “índice bíblico” não é classe única: já há pelo menos citação por livro, perícope e concordância com componente bíblico

## 6. Tipos editoriais já estabilizados

Com base na leitura atual, faz sentido separar pelo menos estas famílias.

### 6.1 Sumário/inventário final

Exemplos:

- `ORDO RERUM`
- `ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR`

Função:

- inventário editorial do conteúdo
- ordem de obras, capítulos, apêndices, seções

Não é o mesmo que índice remissivo.

Na pipeline alfabética, `ORDO RERUM` não é uma seção possuída: é evidência de
`stop_boundary`, e sua extração continua sob responsabilidade da pipeline geral.

### 6.2 Índice analítico de matérias

Exemplos:

- `INDEX ANALYTICUS`
- `INDEX RERUM ET VERBORUM`
- `INDEX RERUM`

Função:

- localizar temas, conceitos, pessoas e termos dispersos

### 6.3 Índice onomástico

Exemplos:

- `INDEX ONOMASTICUS`
- `TABLE DES NOMS PROPRES`
- `TABLE DES NOMS PROPRES SYRIAQUES`

Função:

- localizar pessoas, lugares e nomes próprios

### 6.4 Índice de autores ou citados

Exemplos:

- `INDEX SCRIPTORUM`
- `INDEX AUCTORUM`
- `TABLE DES CITATIONS DES PÈRES DE L'ÉGLISE`

Função:

- localizar autores ou obras citadas dentro do volume

### 6.5 Índice bíblico de citações

Exemplos:

- `INDEX DES CITATIONS DES ÉCRITURES`
- `TABLE DES CITATIONS DE LA BIBLE`

Função:

- organizar citações ou passagens bíblicas por livro e referência

### 6.6 Índice bíblico de perícopes

Exemplos:

- `TABLE DES PÉRICOPES DE L'ÉCRITURE`

Função:

- organizar leituras e blocos litúrgicos longos
- mapear faixas bíblicas para páginas do volume

### 6.7 Índice lexical / de palavras

Exemplos:

- `INDEX GRÆCITATIS`
- `TABLE DES MOTS SYRIAQUES ÉTRANGERS OU REMARQUABLES`
- `TABLE DES MOTS GRECS CITÉS DANS LES MSS.`

Função:

- localizar vocábulos, formas linguísticas, termos técnicos e palavras em língua específica

### 6.8 Concordância e tabelas especiais

Exemplos:

- `TABLE DE CONCORDANCE`

Função:

- relacionar sistemas editoriais, seções, ofícios, manuscritos ou versões

### 6.9 Índice de ordenação ou correspondência

Exemplos:

- `INDEX ORATIONUM ... IN QUO NOVUS ORDO CUM VETERI COMPARATUR`

Função:

- alinhar duas ordenações editoriais
- mapear item enumerado para outra sequência, página ou ordem antiga

## 7. Formatos materiais concretos já observados

Além da taxonomia por tipo editorial, já existem formatos materiais suficientemente claros para orientar o parser.

### 7.1 Remissivo corrido em duas colunas

Exemplo:

- [PG003, INDEX RERUM ET VERBORUM](/homessddata/Projects/pdfocr/teste/PG003/text/fbf896b3-ed57-422e-9668-d79cc8e21f05-591.txt:1)

Características:

- lema seguido de prosa longa
- múltiplas referências dentro da mesma frase
- `ibid.` e equivalentes
- letras no gutter
- continuidade sem quebra rígida por linha

Consequência:

- a unidade de extração não pode ser “linha OCR”
- é preciso segmentar por entrada lógica

### 7.2 Onomástico hierárquico

Exemplo:

- [PL143, INDEX IN CHRONICON / ELENCHUS ONOMASTICUS LOCALIS GERMANIÆ](/homessddata/Projects/pdfocr/teste/PL143/text/3f6bc174-597b-4055-810f-331a363722e9-805.txt:1)

Características:

- cabeçalhos geográficos ou temáticos em caixa alta
- subseções numeradas
- grupos internos
- entradas simples e entradas comentadas
- `Vide`, `Vid.`, `seqq.`, `ibid.`

Consequência:

- o payload futuro precisa suportar:
  - `section_heading`
  - `subsection_heading`
  - `lemma`
  - `cross_reference`
  - `note/commentary`

### 7.3 Bíblico por livro e versículo

Exemplo:

- [PO025, TABLE DES CITATIONS DE LA BIBLE](/homessddata/Projects/pdfocr/teste/PO025/text/e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt:1)

Características:

- agrupamento por livro
- herança do livro até o próximo cabeçalho
- referência bíblica na esquerda
- páginas do volume na direita
- múltiplas páginas do volume para a mesma referência

Consequência:

- o extrator deve separar referência bíblica de referência interna do volume

### 7.4 Bíblico por perícope

Exemplo:

- [PO025, TABLE DES PÉRICOPES DE L'ÉCRITURE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-483.txt:1)

Características:

- nome do livro em francês
- faixa bíblica longa, às vezes cruzando capítulos
- marcadores editoriais como `fin`, `*`, `/fin`
- saída principal sendo intervalo de páginas do volume

Consequência:

- este formato não deve ser reduzido a “livro + versículo isolado”
- a unidade normal é uma perícope, não um versículo solto

### 7.5 Concordância litúrgica/editorial

Exemplo:

- [PO025, TABLE DE CONCORDANCE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-490.txt:1)

Características:

- coluna esquerda com calendário/ofício/leitura
- citações bíblicas integradas à rubrica
- coluna direita com códigos editoriais, siglas e aparato
- a página mistura mais de um sistema de referência

Consequência:

- não é seguro tratar toda ocorrência bíblica como “entrada de índice bíblico”
- aqui o objeto principal talvez seja uma concordância, com componentes bíblicos anexos

### 7.6 Índice lexical multilíngue

Exemplo:

- [PO025, TABLE DES NOMS PROPRES SYRIAQUES](/homessddata/Projects/pdfocr/teste/PO025/text/e2ff8472-852c-459a-bd8f-e86b9c01ad9b-817.txt:1)

Características:

- lema principal em siríaco
- glosas ou identificações em francês
- referências com página, linha, nota e marcas incompletas
- herança por travessão (`—`)

Consequência:

- o extrator precisa separar forma original e glosa
- o agente não pode transliterar automaticamente se a transliteração não estiver impressa

## 8. Regras operacionais já maduras

Estas regras já são fortes o bastante para orientar scanner, prompt e payload.

### 8.1 Scanner

O scanner deve:

- localizar seções finais por cabeçalho
- suportar múltiplos índices finais no mesmo volume
- registrar intervalo provável de páginas por seção
- distinguir material textual comum de material indexatório
- registrar subtítulos de transição dentro da mesma página
- aceitar que uma mesma página possa conter transição de uma seção para outra

Critérios de detecção já sustentados pelo corpus:

- cabeçalho explícito em caixa alta
- repetição do título em páginas consecutivas
- confirmação por sumário final quando existir
- presença de padrão tabular/remissivo incompatível com texto corrido

### 8.2 Agente extrator

O agente deve:

- preservar literal OCR onde houver dúvida
- separar `lema`, `sublema`, `texto_da_entrada` e `referencias`
- manter relações hierárquicas quando existirem
- anotar incerteza explicitamente
- não colapsar cedo demais variantes editoriais
- separar referências de naturezas diferentes na mesma entrada
- reconhecer que travessão ou `—` frequentemente herda o lema anterior
- preservar notas explicativas longas que às vezes aparecem dentro do índice

O agente não deve:

- criar novo `index_kind` não documentado
- inferir normalização de nome quando ela não estiver segura
- transformar `vide` em página
- transformar comentário editorial em lema novo
- resolver `ibid.` ou `seq./seqq.` sem contexto suficiente

### 8.3 Payload conceitual mínimo

Sem ainda fixar schema SQLite/JSON final, o payload precisa comportar pelo menos:

- `index_kind`
- `entry_kind`
- `section_heading`
- `subsection_heading`
- `group_letter`
- `lemma_raw`
- `lemma_display`
- `entry_raw`
- `cross_refs[]`
- `page_refs[]`
- `biblical_refs[]`
- `editorial_refs[]`
- `notes[]`
- `confidence`

Esses nomes ainda são conceituais, não necessariamente os nomes finais do schema.

## 9. O que o agente pode inferir

O agente pode inferir:

- herança do livro bíblico até o próximo cabeçalho, quando a tabela for claramente agrupada por livro
- herança do lema anterior em linhas iniciadas por travessão, quando a materialidade da página deixar isso claro
- que letras isoladas no gutter em índices como `PG003` são agrupamento alfabético
- que `Vid.` e `Vide` introduzem remissão cruzada, não paginação

O agente só pode fazer essas inferências quando o contexto imediato da página sustentar a leitura.

## 10. O que o agente não pode inventar

O agente não pode:

- criar novos tipos editoriais “porque parece parecido”
- fundir pessoas, lugares e assuntos em um mesmo subtipo sem marcação de ambiguidade
- transformar todo índice bíblico em lista simples de livro/versículo
- fundir referência bíblica e paginação do volume em um único campo
- supor transliteração, expansão de abreviação ou equivalência moderna não impressa
- fechar automaticamente intervalos abertos de `seq.` ou `seqq.`
- remover marcas editoriais como `fin`, `*`, `/fin` antes de registrá-las

## 11. Lacunas ainda abertas

Esta é a parte mais importante do documento neste momento.

### 11.1 Taxonomia ainda incompleta

Ainda não sabemos se a taxonomia listada acima basta para todos os casos de `PG`, `PL` e `PO`.

Em aberto:

- surgem subclasses adicionais em `PL` tardia?
- `INDEX IN ...` deve virar classe própria ou subclasse de índice de obra?
- concordâncias da `PO` entram como índice ou como tabela editorial separada?

### 11.2 Estrutura final do lema

Ainda não está totalmente fechado qual deve ser a unidade canônica persistida:

- lema simples
- lema com subentrada
- bloco de entrada lógica
- entrada lógica com comentários e remissões como filhos

O que já está claro:

- a unidade mínima para persistência não pode ser a linha OCR
- a unidade mais promissora é “entrada lógica” com filhos opcionais

### 11.3 Hierarquia

Ainda precisamos medir melhor:

- quantos índices são planos
- quantos são hierárquicos
- quantos usam apenas letras
- quantos usam grupos explícitos (`SUMMI PONTIFICES`, etc.)

### 11.4 Referências múltiplas

O levantamento encontrou:

- lista simples de páginas
- intervalos
- referências com `ibid.`
- `seq./seqq.`
- referências com coluna
- referências compostas por página e linha
- referências paralelas em paginações diferentes

O contrato final agora determina:

- listas explícitas geram uma ocorrência por locator
- intervalos impressos permanecem uma única `editorial_range`
- `ibid.` só herda com antecedente estrutural seguro
- `seq.`, `seqq.`, `fin` e `passim` permanecem formas abertas não expandidas
- página, coluna, linha e sistemas paralelos ocupam campos distintos

### 11.5 Normalização de nomes

Ainda não sabemos até onde vale normalizar no momento da extração:

- expandir variantes latinas
- fundir `Joannes / Ioannes`
- unificar formas gregas, latinas e francesas
- distinguir lema editorial de forma canônica de busca

### 11.6 Convenção bíblica

A primeira convenção operacional está fechada em
`docs/normalizacao_indices_biblicos.md`: catálogo católico de 73 livros, perfil Vulgata/Migne
contextual para as formas `Regum` (`I-IV` no corpus PL; `II-IV` confirmadas na amostra PG001) e
`I-II Esdrae`, e perfil francês editorial antigo para
`I-IV Rois` dentro de tabelas bíblicas explícitas da PO. Índices de citações, perícopes,
concordâncias e aparatos `source_only` permanecem classes distintas.

Ainda convém ampliar continuamente os fixtures reais de abreviações, sobretudo para OCR grego,
idiomas orientais e títulos muito degradados; novas formas não devem ser promovidas a aliases
globais sem contexto suficiente.

### 11.7 Relação com a web

Ainda não está decidido:

- se isso entra em uma página nova
- se isso entra como aba nova de `/indices`
- se a navegação principal será por volume ou por letra
- se a busca será global ou segmentada por tipo

### 11.8 Amostragem ainda insuficiente

Apesar dos casos fortes já lidos, ainda faltam:

- mais exemplos de `PO` fora de `PO025`
- mais exemplos de índices bíblicos
- mais exemplos de índices de autores citados
- pelo menos um ou dois casos onde o índice final é mínimo ou muito degenerado

Mas a amostragem atual já basta para fechar o primeiro contrato do extrator.

## 12. Entregáveis documentais

Os três documentos-base do fluxo novo agora são:

1. `docs/levantamento_indices_alfabeticos.md`
   - levantamento empírico e justificação material
2. `docs/normalizacao_indices_biblicos.md`
   - regras editoriais para referências bíblicas
3. `docs/contrato_extrator_indices_alfabeticos.md`
   - schema, invariantes e exemplos de payload

## 13. Resumo executivo

O levantamento já permite afirmar com segurança:

- vale a pena ter um fluxo separado para índices alfabéticos e remissivos
- `PG` e `PL` compartilham famílias estáveis, mas não um único formato
- `PO` é ainda mais diversa e exige taxonomia própria
- o schema atual de `index_entries` é estreito demais para vários desses casos
- índices bíblicos já aparecem em mais de um formato editorial real
- já existem formatos materiais suficientemente claros para orientar scanner e payload
- a documentação já pode ser usada como base do primeiro protótipo do extrator

O que ainda não podemos afirmar com segurança:

- a estratégia exata de normalização de lemas
- políticas de aceitação para casos fronteiriços ou muito degradados
- o desenho final da UX na web

O que já está decidido:

- o schema final do extrator
- a separação entre `sections`, `nodes`, `entries` e `refs`
- a necessidade de uma tabela própria para referências bíblicas parseadas
