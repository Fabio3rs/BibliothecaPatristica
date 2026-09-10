# Taxonomia dos índices nos volumes PG e PL

Este documento descreve os tipos de índices que aparecem nos arquivos de `teste/PG*/text/*` e `teste/PL*/text/*`, com foco em três posições editoriais:

1. começo do volume
2. começo do livro/obra
3. final do volume

A regra principal para leitura desses arquivos é separar três coisas diferentes:

- `nome do arquivo` como identificador técnico do OCR
- `número impresso` no cabeçalho ou na linha do índice
- `número citado` dentro da entrada do índice, que aponta para coluna, página ou seção

O sufixo do arquivo `*.txt` não é paginação editorial. A paginação válida é a impressa no próprio texto do índice. Quando houver dúvida, o OCR literal deve ser preservado e a ambiguidade anotada.

## 1. Começo do volume

No início do tomo, o índice funciona como inventário editorial do volume inteiro. Em geral ele lista autores, obras e subobras com suas referências de coluna/página.

### Formato mais comum

- título curto em destaque, como `ELENCHUS`
- subtítulo explicativo, como `AUCTORUM ET OPERUM QUI IN HOC TOMO ... CONTINENTUR`
- lista de autores e obras
- referências no fim da linha, normalmente em `col.` ou em número simples

### Exemplo na Patrologia Graeca

- [PG001, `ELENCHUS`](/homessddata/Projects/pdfocr/teste/PG001/text/00d25913-d4ca-4c02-9a5a-67b37111f59b-004.txt)
- [PG001, inventário do tomo](/homessddata/Projects/pdfocr/teste/PG001/text/00d25913-d4ca-4c02-9a5a-67b37111f59b-004.txt#L3)
- [PG005, `ELENCHUS` com marca de biblioteca](/homessddata/Projects/pdfocr/teste/PG005/text/ef560d22-90e3-471f-80a6-56fb4536d925-010.txt)

### Exemplo na Patrologia Latina

- [PL102, `ELENCHUS`](/homessddata/Projects/pdfocr/teste/PL102/text/9a177bbf-0564-4b58-905f-a07f11a198af-006.txt#L3)
- [PL102, subtítulo do tomo](/homessddata/Projects/pdfocr/teste/PL102/text/9a177bbf-0564-4b58-905f-a07f11a198af-006.txt#L5)

### O que esse índice faz

- diz o que existe no tomo
- agrupa autores por blocos editoriais
- apresenta a sequência de obras principais e apêndices
- usa paginação de referência, não paginação do arquivo OCR

## 2. Começo do livro ou da obra

Aqui o índice já não lista o tomo inteiro. Ele resume a estrutura interna de uma obra específica, normalmente por capítulos, seções ou artigos.

Esse grupo aparece com fórmulas como:

- `INDEX CAPITUM`
- `INDEX CAPITUM LIBRI PRIMI`
- `INDEX CAPITUM SCRIPTURÆ SACRÆ`
- `INDEX CAPITUM OCTAVII`
- variações com `PROLEGOMENA`, `CAPUT`, `CAP.`, `LIBER`, `SECTIO`

### Características recorrentes

- capítulos em numeração romana
- uma frase-resumo por capítulo
- paginação do corpo do texto no fim da linha
- quebras de linha preservadas, mas a unidade semântica é a entrada inteira

### Exemplo na Patrologia Graeca

- [PG001, `INDEX CAPITUM`](/homessddata/Projects/pdfocr/teste/PG001/text/6f3c6a24-457b-444d-abb0-5a057fcab6bb-100.txt#L24)
- [PG001, capítulos I a XXI](/homessddata/Projects/pdfocr/teste/PG001/text/6f3c6a24-457b-444d-abb0-5a057fcab6bb-100.txt#L26)

### Exemplo na Patrologia Latina

- [PL102, `ORDO RERUM QUÆ IN HOC TOMO CONTINENTUR.`](/homessddata/Projects/pdfocr/teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-568.txt#L2)
- [PL102, índice interno por capítulos](/homessddata/Projects/pdfocr/teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-568.txt#L18)

### O que esse índice faz

- resume a ordem de leitura da obra
- aponta capítulos, seções e subseções
- costuma aparecer no início da obra e não do tomo
- em muitos volumes, é o índice mais útil para navegação semântica

## 3. Final do volume

No final do tomo, o padrão continua sendo um `ORDO RERUM`, mas agora ele funciona como fecho editorial. Ele encerra o volume com a listagem final das peças contidas nele e, muitas vezes, termina com um marcador explícito de fim.

### Características recorrentes

- título de encerramento, normalmente `ORDO RERUM`
- continuação da lista de capítulos ou obras
- apêndices e notas finais
- marcador de fim do tomo, quando presente, como `FINIS TOMI ...`

### Exemplo na Patrologia Latina

- [PL102, índice final do tomo](/homessddata/Projects/pdfocr/teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-570.txt#L1)
- [PL102, continuação do índice final](/homessddata/Projects/pdfocr/teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-572.txt#L11)
- [PL102, fecho do volume](/homessddata/Projects/pdfocr/teste/PL102/text/3075140d-d30f-4238-ab39-3a7e602d640a-572.txt#L75)

### O que esse índice faz

- fecha a composição do tomo
- traz a sequência final de obras, cartas, apêndices e notas
- marca o término editorial do volume
- é um bom ponto para detectar “fim de tomo” sem depender apenas do nome do arquivo

## 4. Tipos de índice observados

### 4.1 `ELENCHUS`

Inventário geral do tomo. É a forma mais direta de sumário editorial de volume.

### 4.2 `INDEX CAPITUM`

Índice de capítulos ou seções de uma obra específica. Frequente em introduções, prolegômenos e obras longas.

### 4.3 `ORDO RERUM`

Índice ordenado do conteúdo. Pode aparecer tanto no começo quanto no fim do volume, mas em muitos volumes o fecho editorial usa essa forma.

### 4.4 `INDEX ANALYTICUS`

Índice analítico, normalmente alfabético e temático. Reúne assuntos, pessoas, conceitos e referências dispersas.

- exemplo: [PG002, `INDEX ANALYTICUS`](/homessddata/Projects/pdfocr/teste/PG002/text/1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-631.txt#L1)

### 4.5 `INDEX RERUM ET VERBORUM`

Índice de coisas e palavras. É um índice alfabético de entradas temáticas, geralmente com grande densidade de referências.

- exemplo: [PG003, `INDEX RERUM ET VERBORUM`](/homessddata/Projects/pdfocr/teste/PG003/text/fbf896b3-ed57-422e-9668-d79cc8e21f05-588.txt#L1)

### 4.6 `INDEX GRÆCITATIS`

Índice grego ou índice de vocábulos gregos. Organiza entradas em grego, com referências numéricas e marcação alfabética grega.

- exemplo: [PG002, `INDEX GRÆCITATIS`](/homessddata/Projects/pdfocr/teste/PG002/text/1a90d6e8-76c6-4731-a9b6-eb5649af1ba3-639.txt#L1)

### 4.7 Índices híbridos ou especializados

Também aparecem índices por:

- autor
- obra
- livro
- seção
- letra do alfabeto
- coleção de títulos ou rubricas litúrgicas

Exemplos observados:

- `INDEX ANALYTICUS AUCTORUM`
- `INDEX ANALYTICUS OPERUM ...`
- `INDEX CAPITUM LIBRI PRIMI`
- `INDEX CAPITUM SCRIPTURÆ SACRÆ`
- `INDEX RERUM ET VERBORUM INSIGNIORUM`

## 5. Como ler a numeração com segurança

Esses volumes misturam três sistemas de numeração que não devem ser confundidos:

- paginação impressa do volume
- numeração interna das obras, capítulos ou seções
- identificação técnica do arquivo OCR

### Regras práticas

- se a linha terminar com um número, trate-o primeiro como referência editorial, não como OCR
- `Ibid.` significa a mesma página ou coluna da referência imediatamente anterior
- `col.` aponta para coluna, não para página do PDF ou para o nome do arquivo
- números errados por OCR devem ser preservados literalmente e marcados como suspeitos, não corrigidos automaticamente

### Casos de cautela já vistos

- numeração fora de sequência dentro do próprio índice
- páginas duplas com dois números no cabeçalho
- volumes em que o cabeçalho do índice foi lido com um número errado pelo OCR
- entradas cujo número final pertence à coluna/página da obra citada, não ao índice atual

## 6. Implicações para a pipeline

Para a extração futura, o parser não deve assumir que:

- o número no nome do arquivo é uma página impressa
- a sequência de OCR é monotônica em relação à paginação do livro
- todo índice é um sumário do volume inteiro

O que ele deve fazer é:

- classificar a página por tipo de índice
- preservar a redação literal da entrada
- separar cabeçalho, entrada e referência numérica
- registrar incertezas de OCR em vez de normalizar cedo demais

### 6.1 Localização física de capítulos durante o chunking

O chunk semântico está autorizado a pesquisar, em modo somente leitura, qualquer arquivo dentro do
`source_root` do volume para localizar o cabeçalho corporal correspondente a uma entrada que ele
possui. O limite do chunk determina quais entradas ele pode emitir, não quais páginas do mesmo
volume ele pode consultar como evidência.

Para `INDEX CAPITUM` e estruturas equivalentes:

- separar livros/partes quando a numeração reiniciar;
- normalizar ligaturas, caixa, espaços, pontuação e ruído OCR apenas em memória;
- procurar frases distintivas do título, não apenas o numeral;
- excluir ocorrências no próprio índice, em outros sumários, catálogos ou cabeçalhos correntes;
- exigir sequência física monotônica e apoio dos capítulos vizinhos;
- aceitar deslocamento ordinal somente quando o texto e a sequência provarem inserção, omissão ou
  reordenação editorial;
- preencher `target_file` mesmo sem página editorial quando houver evidência forte;
- manter `target_file` nulo quando restarem candidatos empatados ou apenas coincidência numérica.

Todo destino resolvido pelo chunk deve registrar `raw_json.physical_target_evidence` com a consulta
literal, o cabeçalho encontrado, o método, os arquivos inspecionados e a razão da decisão. O formato
completo está em
`.codex/skills/patristic-index-extractor/references/chapter-target-localization.md`.

### 6.2 Fronteira entre obra bíblica e índice de citações

A presença de um livro bíblico, de `Scriptura` ou de `Psalmus` não define a pipeline. A função
editorial define:

- `HOMILIA IN PSALMUM X`, `EXPOSITIO IN MATTHAEUM`, `COMMENTARIUS IN SCRIPTURAM` e seus capítulos
  são obras ou unidades estruturais e pertencem ao índice geral de obras;
- `INDEX LOCORUM SCRIPTURAE`, `TABLE DES CITATIONS DE LA BIBLE`, concordâncias e listas remissivas
  de passagens pertencem à pipeline alfabética;
- uma citação completa como `Psal. X, 3` dentro de uma entrada onomástica, analítica ou temática
  continua pertencendo à pipeline alfabética e pode gerar uma referência bíblica associada àquela
  entrada;
- a simples menção lexical a um salmo ou livro bíblico, sem passagem analisável, permanece apenas
  no texto/contexto da entrada e não deve ser promovida a obra nem a referência bíblica navegável.

Assim, regexes bíblicas podem ajudar a reconhecer a forma da entrada, mas nunca funcionam sozinhas
como veto de ownership da pipeline geral.

## 7. Resposta curta à pergunta principal

Sim, a Patrologia Latina usa o mesmo padrão geral da Graeca:

- índice geral do tomo no começo do volume
- índice de capítulos/obra no começo da peça textual
- índice ordenado ou analítico no final do volume

A diferença está no vocabulário e no estilo editorial de cada tomo, não na lógica estrutural.
