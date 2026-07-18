# Taxonomia dos índices nos volumes PO

Este documento descreve os tipos de índices e tabelas editoriais que aparecem nos arquivos de `teste/PO*/text/*`, com foco nas posições estruturais mais recorrentes dentro da coleção `Patrologia Orientalis`.

A regra principal para leitura desses arquivos é separar quatro coisas diferentes:

- `nome do arquivo` como identificador técnico do OCR
- `número impresso` no cabeçalho ou rodapé
- `número citado` dentro da entrada do índice, que aponta para página, coluna, seção ou linha
- `paginação entre colchetes`, quando presente, que muitas vezes representa paginação editorial paralela ou herdada de fascículos

O sufixo do arquivo `*.txt` não é paginação editorial. A paginação válida é a impressa no próprio volume. Quando houver dúvida, o OCR literal deve ser preservado e a ambiguidade anotada.

## 1. Característica geral da coleção

Diferentemente de `PG` e `PL`, a `PO` não é organizada em torno de um único vocabulário latino estável como `ELENCHUS`, `INDEX CAPITUM` e `ORDO RERUM`.

O padrão dominante da `PO` é:

1. página de título do `TOMUS`
2. inventário do tomo por `FASC.` ou por lista equivalente
3. abertura editorial de cada fascículo
4. texto principal da obra ou edição crítica
5. tabelas ou índices próprios do fascículo
6. fechamento editorial do tomo

Em consequência, o parser não deve assumir que existe:

- um índice geral único logo no começo do tomo
- um índice de capítulos em todas as obras
- um fechamento editorial fixo com `FINIS TOMI`

O que existe é uma família de estruturas recorrentes, com bastante variação por tomo e por fascículo.

## 2. Começo do volume

No início do tomo, o bloco principal costuma ser uma página de título do volume, muitas vezes com a enumeração latina do tomo:

- `TOMUS SECUNDUS`
- `TOMUS SEXTUS`
- `TOMUS VICESIMUS QUINTUS`

Exemplos:

- [PO002, página de tomo](/homessddata/Projects/pdfocr/teste/PO002/text/e864031f-59cd-41cb-9156-b68e2e816b03-007.txt)
- [PO006, página de tomo](/homessddata/Projects/pdfocr/teste/PO006/text/74c6644a-c3d5-4439-9036-5c7b69bc93a3-009.txt)
- [PO025, página de tomo](/homessddata/Projects/pdfocr/teste/PO025/text/2fbd31ea-9bfd-4f60-882c-014f92b95a80-007.txt)

### 2.1 `volume_title`

Use esta classe para a página de título ou capa interna do tomo.

Sinais típicos:

- presença de `TOMUS ...`
- nomes dos editores da coleção
- menção a `PATROLOGIA ORIENTALIS`
- ausência de listagem densa de entradas remissivas

### 2.2 `volume_table`

Depois do `volume_title`, o tomo frequentemente apresenta uma `TABLE DES MATIÈRES` com a lista dos fascículos contidos naquele volume.

Exemplos:

- [PO006, tabela do tomo](/homessddata/Projects/pdfocr/teste/PO006/text/4c78809c-1ec7-47fd-a989-430be3a9acd5-716.txt)
- [PO017, tabela do tomo](/homessddata/Projects/pdfocr/teste/PO017/text/f06319d9-2073-4044-8357-c88185a2b931-888.txt)
- [PO021, tabela do tomo](/homessddata/Projects/pdfocr/teste/PO021/text/2e8903be-9e03-4337-8c2d-25dad00e6187-892.txt)

Sinais típicos:

- título `TABLE DES MATIÈRES`
- enumeração `Fasc. I`, `Fasc. II`, etc.
- títulos longos de obras
- autores, tradutores ou editores
- referências de páginas de início ou faixas de páginas

### 2.3 `fascicle_inventory`

Trate cada linha ou bloco `FASC. ...` da tabela do tomo como inventário de fascículo.

Ele é a unidade editorial mais importante da `PO`.

O que esse inventário faz:

- enumera as obras autônomas ou semi-autônomas do tomo
- identifica o editor da peça textual
- indica a paginação ou a posição do fascículo no tomo
- em muitos casos já antecipa índices ou tabelas finais da peça

## 3. Começo do fascículo ou da obra

Na `PO`, a abertura de uma obra é muito mais variável do que em `PG/PL`. Em vez de `INDEX CAPITUM`, aparecem fórmulas editoriais modernas, quase sempre em francês, mas também às vezes em inglês, latim ou italiano.

### 3.1 `work_front_matter`

Use esta classe para a abertura editorial do fascículo.

Títulos típicos:

- `AVERTISSEMENT`
- `INTRODUCTION`
- `PRÉFACE`
- `PROLOGUE`
- combinações como `AUTHOR'S PREFACE`

Exemplos:

- [PO025, avertissement](/homessddata/Projects/pdfocr/teste/PO025/text/2fbd31ea-9bfd-4f60-882c-014f92b95a80-015.txt)
- [PO006, introduction](/homessddata/Projects/pdfocr/teste/PO006/text/ed8ed943-5599-48c7-b8c7-e1632b8f2b49-655.txt)
- [PO019, préface](/homessddata/Projects/pdfocr/teste/PO019/text/b3268972-e840-4e4f-9b02-d55a690edef0-645.txt)

Sinais típicos:

- discussão de manuscritos, versões, edição, tradução, fontes
- aparato bibliográfico
- paginação preliminar ou dupla paginação
- ausência de entradas remissivas em série

### 3.2 `work_internal_table`

Algumas obras têm uma `TABLE DES MATIÈRES` própria logo após a abertura ou perto do fim do fascículo.

Exemplos:

- [PO006, table contenues dans ce livre](/homessddata/Projects/pdfocr/teste/PO006/text/661f644d-637e-4d26-b06d-a692725ee9bb-482.txt)
- [PO022, table interne](/homessddata/Projects/pdfocr/teste/PO022/text/48338044-d88b-47b6-9133-5d327e80b571-322.txt)

Sinais típicos:

- `TABLE DES MATIÈRES`
- `TABLE DES MATIÈRES CONTENUES DANS CE LIVRE`
- enumeração de capítulos, partes, seções ou rubricas

Essa classe não deve ser confundida com a `volume_table`.

## 4. Texto principal e aparato

A parte central da `PO` costuma trazer:

- texto original
- tradução
- aparato crítico
- notas editoriais

Pode haver mistura de:

- francês
- inglês
- latim
- grego
- siríaco
- copta
- etíope
- armênio
- árabe

Isso é o miolo textual do fascículo, não uma seção de índice por si só.

## 5. Índices e tabelas de fascículo

Na `PO`, o fechamento de um fascículo costuma trazer tabelas e índices especializados. Eles variam mais do que em `PG/PL`, mas podem ser classificados em famílias.

### 5.1 `work_index_nominal`

Use esta classe para índices de nomes próprios, nomes geográficos ou listas onomásticas.

Títulos típicos:

- `TABLE DES NOMS PROPRES`
- `TABLE DES NOMS PROPRES SYRIAQUES`
- `INDEX DES NOMS PROPRES`
- `TABLE ALPHABÉTIQUE DES NOMS PROPRES`

Exemplos:

- [PO015, table des noms propres](/homessddata/Projects/pdfocr/teste/PO015/text/127a4528-a58f-4ad4-8e84-43f9817c2384-297.txt)
- [PO023, noms propres syriaques](/homessddata/Projects/pdfocr/teste/PO023/text/6f06fe5b-2e04-403d-a2d8-9092173f0442-176.txt)
- [PO025, noms propres syriaques](/homessddata/Projects/pdfocr/teste/PO025/text/e2ff8472-852c-459a-bd8f-e86b9c01ad9b-817.txt)

Sinais típicos:

- organização alfabética
- nomes próprios
- referências de página, linha, seção ou nota
- forte presença de transliterações ou grafias em várias línguas

### 5.2 `work_index_scripture`

Use esta classe para índices de citações ou passagens bíblicas.

Títulos típicos:

- `INDEX DES CITATIONS DES ÉCRITURES`
- `Index des passages de la sainte Écriture`

Exemplos:

- [PO025, index des citations](/homessddata/Projects/pdfocr/teste/PO025/text/e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-622.txt)
- [PO018, index des passages](/homessddata/Projects/pdfocr/teste/PO018/text/cd2a5d23-7480-4116-a783-fb49cc50a195-521.txt)

Sinais típicos:

- referências bíblicas como entradas principais
- números à direita apontando para páginas, linhas ou seções
- organização por livro bíblico, não por capítulo do fascículo

### 5.3 `work_index_alphabetical`

Use esta classe para índices alfabéticos gerais.

Títulos típicos:

- `TABLE ALPHABÉTIQUE`
- `TABLE ALPHABÉTIQUE DES MATIÈRES`

Exemplos:

- [PO008, table alphabétique](/homessddata/Projects/pdfocr/teste/PO008/text/b44b3c65-b6a1-4d11-84f8-04b95b885221-706.txt)
- [PO006, table alphabétique des matières](/homessddata/Projects/pdfocr/teste/PO006/text/4c78809c-1ec7-47fd-a989-430be3a9acd5-713.txt)

Sinais típicos:

- ordenação alfabética
- mistura de assuntos, nomes, termos técnicos ou rubricas
- estrutura em colunas

### 5.4 `work_index_analytic`

Use esta classe para índices analíticos ou sintéticos de assuntos.

Títulos típicos:

- `TABLE ANALYTIQUE DES MATIÈRES`

Exemplos:

- [PO014, table analytique](/homessddata/Projects/pdfocr/teste/PO014/text/740adcbf-b82c-420a-9452-141b681730bc-853.txt)
- [PO017, table analytique](/homessddata/Projects/pdfocr/teste/PO017/text/f06319d9-2073-4044-8357-c88185a2b931-885.txt)

Sinais típicos:

- entradas temáticas mais desenvolvidas
- relações entre temas, autores, episódios ou conceitos
- às vezes mais densidade semântica do que um simples índice alfabético

## 6. Fecho editorial

### 6.1 `editorial_closure`

Use esta classe para blocos explícitos de correção editorial.

Títulos típicos:

- `ADDENDA`
- `CORRIGENDA`
- `ADDENDA AND CORRIGENDA`

Exemplos:

- [PO017, addenda and corrigenda](/homessddata/Projects/pdfocr/teste/PO017/text/c9aa7c42-dfb4-4f60-9184-95e47e455c26-329.txt)
- [PO019, corrigenda](/homessddata/Projects/pdfocr/teste/PO019/text/ced07bbc-e86b-4202-b8d2-87016ccf3630-624.txt)
- [PO025, addenda](/homessddata/Projects/pdfocr/teste/PO025/text/e1a2562f-9409-4b2a-a1d8-7ffde83fdce4-624.txt)

Sinais típicos:

- correções localizadas
- emendas de grafia
- observações editoriais de última hora

Esse fecho pode pertencer a um fascículo específico ou ao tomo como um todo, dependendo da posição material e da referência interna.

## 7. Tabelas retrospectivas

### 7.1 `retrospective_table`

Alguns tomos trazem tabelas cumulativas de tomos anteriores, ou catálogos parciais da coleção, e isso pode inflar artificialmente a contagem de `FASC.`.

Exemplo:

- [PO010, tables des matières des tomes I à X](/homessddata/Projects/pdfocr/teste/PO010/text/c05699c3-9b43-4f96-a187-8460f36bf4d5-672.txt)

Sinais típicos:

- referências a vários tomos diferentes na mesma página
- enumeração de `Tome V`, `Tome VI`, `Tome VII`, etc.
- fascículos que não pertencem ao volume atual

Essa classe é crucial para evitar que o parser atribua ao volume corrente obras que pertencem a tomos anteriores.

## 8. Regras de classificação

Ao classificar uma página ou seção da `PO`, use a evidência editorial mais forte, nesta ordem:

1. `volume_title`
2. `volume_table`
3. `fascicle_inventory`
4. `work_front_matter`
5. `work_internal_table`
6. `work_index_nominal`
7. `work_index_scripture`
8. `work_index_alphabetical`
9. `work_index_analytic`
10. `editorial_closure`
11. `retrospective_table`

Se uma página tiver mais de uma função, classifique pelo bloco dominante visível e preserve os outros sinais no JSON bruto.

## 9. Regras práticas para números e referências

Os volumes `PO` misturam vários sistemas de numeração:

- paginação do arquivo OCR
- paginação impressa do tomo
- paginação herdada do fascículo
- numeração de seções, homilias, capítulos ou peças
- referências em colchetes, subscritos ou sobrescritos

### Regras práticas

- não trate o número do nome do arquivo como paginação editorial
- preserve paginações em colchetes como aparecem
- não normalizar cedo números duvidosos
- se uma entrada remete a `page/line`, preserve o literal
- quando houver dupla paginação, registrar ambas no `raw_json` ou no campo de nota

## 10. Implicações para a pipeline

Um extrator para `PO` não deve assumir:

- que o índice do tomo seja um `ELENCHUS`
- que o índice interno seja `INDEX CAPITUM`
- que exista um único índice final padronizado
- que toda ocorrência de `FASC.` pertença ao volume atual

O que ele deve fazer é:

- detectar `TOMUS` e a `TABLE DES MATIÈRES` do tomo
- separar inventário de fascículos de tabelas retrospectivas
- reconhecer front matter editorial moderno
- classificar os índices finais por tipo funcional, não por um vocabulário latino fixo
- preservar a redação literal das entradas
- registrar incertezas em vez de corrigir cedo demais

## 11. Resposta curta à pergunta principal

Não existe um único mapeamento rígido para todos os volumes `PO`, mas existe uma taxonomia estável o suficiente para uso de pipeline:

- `volume_title`
- `volume_table`
- `fascicle_inventory`
- `work_front_matter`
- `work_internal_table`
- `work_index_nominal`
- `work_index_scripture`
- `work_index_alphabetical`
- `work_index_analytic`
- `editorial_closure`
- `retrospective_table`

Essa taxonomia descreve melhor a `Patrologia Orientalis` do que a taxonomia usada hoje para `PG/PL`.
