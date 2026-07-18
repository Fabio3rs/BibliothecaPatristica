# Normalização dos índices bíblicos

Este documento define princípios e regras de normalização para índices bíblicos extraídos de volumes `PG`, `PL` e `PO`, especialmente para casos como:

- `INDEX DES CITATIONS DES ÉCRITURES`
- `TABLE DES CITATIONS DE LA BIBLE`
- `INDEX LOCORUM EX SCRIPTURA SACRA`
- `TABLE DES PÉRICOPES DE L'ÉCRITURE`
- tabelas mistas em que referências bíblicas aparecem ao lado de outros sistemas editoriais

O objetivo aqui não é impor uma transformação agressiva sobre o OCR.

O objetivo é:

- preservar o que o volume realmente imprime
- criar uma camada canônica estável para busca e indexação
- evitar corrigir cedo demais convenções bíblicas variantes
- alinhar o futuro extrator de índices com a infraestrutura já existente no projeto para referências bíblicas
- impedir que o agente invente formatos bíblicos novos durante a conversão

Este documento deve ser lido junto com:

- [levantamento_indices_alfabeticos.md](/homessddata/Projects/pdfocr/docs/levantamento_indices_alfabeticos.md:1)
- [contrato_extrator_indices_alfabeticos.md](/homessddata/Projects/pdfocr/docs/contrato_extrator_indices_alfabeticos.md:1)
- [taxonomia_indices_po.md](/homessddata/Projects/pdfocr/docs/taxonomia_indices_po.md:188)

Casos reais que sustentam estas regras:

- [PO025, TABLE DES CITATIONS DE LA BIBLE](/homessddata/Projects/pdfocr/teste/PO025/text/e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt:1)
- [PO025, TABLE DES PÉRICOPES DE L'ÉCRITURE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-483.txt:1)
- [PO025, TABLE DES PERICOPES DE L'ECRITURE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-484.txt:1)
- [PO025, TABLE DE CONCORDANCE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-490.txt:1)

## 1. Princípio central

Toda informação bíblica extraída deve preservar pelo menos duas camadas:

- `ref_raw`
- `ref_norm`

Onde:

- `ref_raw` é a referência exatamente como aparece no volume, com abreviações, pontuação, numeração romana, colchetes, hífens e eventuais anomalias de OCR
- `ref_norm` é uma forma canônica interna, voltada para busca e agregação

Se não houver confiança suficiente, a referência pode existir apenas em `ref_raw`.

Normalização bíblica nunca deve apagar o literal do índice.

## 2. Relação com o código já existente

O projeto já possui infraestrutura útil para citações bíblicas:

- [scripture_ref_normalizer.py](/homessddata/Projects/pdfocr/scripture_ref_normalizer.py:1)
- [tests/test_scripture_ref_variants.py](/homessddata/Projects/pdfocr/tests/test_scripture_ref_variants.py:1)
- [tools/fill_canon_keywords.py](/homessddata/Projects/pdfocr/tools/fill_canon_keywords.py:120)

Essa infraestrutura foi escrita principalmente para:

- keywords
- resumos
- canonicalização de citações detectadas em texto corrido

Ela é um ponto de partida importante, mas o novo extrator de índices bíblicos deve ser mais conservador, porque trabalha sobre material editorial estruturado, não apenas sobre texto livre.

Consequência prática:

- a forma canônica usada hoje no projeto continua sendo a referência preferencial para `ref_norm`
- o extrator de índices não deve depender apenas do normalizador atual para entender a estrutura editorial do índice
- o agente não deve chamar algo de “normalizado” se essa forma não puder ser sustentada pelo contexto impresso

No contrato final do novo extrator:

- a referência material dentro do volume vai em `alphabetical_refs`
- a referência bíblica parseada vai em `alphabetical_scripture_refs`

Essa separação é obrigatória.

## 3. Tipos bíblicos já observados

O corpus já mostra que “índice bíblico” não é uma classe única.

### 3.1 Citação bíblica por livro

Exemplo:

- [PO025, TABLE DES CITATIONS DE LA BIBLE](/homessddata/Projects/pdfocr/teste/PO025/text/e2ff8472-852c-459a-bd8f-e86b9c01ad9b-826.txt:1)

Estrutura observada:

- cabeçalho do livro (`GALATES`, `EPHESIENS`, `HEBREUX`, etc.)
- abaixo dele, referências bíblicas abreviadas
- na mesma linha, páginas do volume onde a citação aparece

Unidade natural:

- uma referência bíblica individual

### 3.2 Perícope ou leitura longa

Exemplo:

- [PO025, TABLE DES PÉRICOPES DE L'ÉCRITURE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-483.txt:1)

Estrutura observada:

- nome do livro
- faixa longa (`xxii, 29-xxiii, 6*`, `xii, 2-xiii, 10`)
- marcas editoriais (`fin`, `*`, `/fin`)
- páginas do volume

Unidade natural:

- uma perícope, não um versículo isolado

### 3.3 Concordância com componente bíblico

Exemplo:

- [PO025, TABLE DE CONCORDANCE](/homessddata/Projects/pdfocr/teste/PO025/text/de4224d9-6414-41e0-bbe2-ceb7bdd253c7-490.txt:1)

Estrutura observada:

- rubricas litúrgicas ou editoriais
- leituras bíblicas incorporadas
- coluna paralela com códigos e aparato

Unidade natural:

- a entrada principal é uma rubrica/concordância
- as citações bíblicas são componentes anexos

Consequência geral:

- o agente deve distinguir `citation`, `pericope` e `concordance_component`
- o agente não pode reduzir tudo a uma única classe `bible_ref`

## 4. Unidade de normalização

A unidade mínima não deve ser “um pedaço de string”, mas uma referência bíblica individual ou uma perícope identificável.

Exemplos simples:

- `Psal. CXLVI, 4.`
- `Dan. VII, 10.`
- `Luc. 1.`
- `II Tim. iv, 3, 4.`
- `Exod. 3, 14-15`

Exemplos de perícopes reais:

- `xxii, 29-xxiii, 6*`
- `xii, 2-xiii, 10`
- `xviii, 28-40 /fin`
- `xiii, 1-64 fin. (Histoire de Suzanne)`

Muitas entradas reais trazem listas ou grupos:

- `Exod. 3, 14; Joan. 8, 58`
- `Psal. 4, 9; 91, 1`
- `Matth. 27, 2; Luc. 3, 1; Act. 4, 27`

Portanto, a modelagem futura deve permitir:

- uma entrada de índice
- uma ou mais referências bíblicas parseadas dentro dela
- ou uma perícope com metadados editoriais adicionais

Não é seguro colapsar a entrada inteira em uma única string canônica.

## 5. Convenção editorial de saída canônica

Enquanto regra de trabalho para `ref_norm`, adote a convenção canônica já usada no projeto para buscas modernas em PT-BR:

- nome do livro em português
- capítulo em arábico
- separador de capítulo/versículo com vírgula
- intervalos com hífen

Exemplos:

- `2 Tm 2:19` -> `2 Timóteo 2,19`
- `II Cor 5:15` -> `2 Coríntios 5,15`
- `Luc. 1.` -> `São Lucas 1`
- `Psal. CXLVI, 4.` -> `Salmos 146,4`

Observação importante:

- essa forma canônica é uma camada interna de busca
- ela não substitui a forma impressa do volume

## 6. Livros bíblicos

### 6.1 Regra geral

O nome do livro deve ser reconhecido em variantes:

- latinas
- francesas
- portuguesas
- gregas, quando aplicável
- abreviadas
- com ou sem ordinal

Mas a saída canônica deve ser unificada em um nome português estável.

### 6.2 Base de nomes

A base preferencial para `ref_norm` deve ser compatível com a lista já presente em [scripture_ref_normalizer.py](/homessddata/Projects/pdfocr/scripture_ref_normalizer.py:120), que contém livros em português, latim e grego.

### 6.3 Formas de entrada esperadas

Exemplos típicos que o extrator deve aceitar como equivalentes, desde que o contexto seja realmente bíblico:

- `II Tim.`
- `2 Tim.`
- `2 Tm`
- `II Timotheum`
- `Ad Timotheum II`

e normalizar para:

- `2 Timóteo`

Também devem ser reconhecidas formas francesas reais do corpus, como:

- `Luc`
- `Jean`
- `Actes`
- `I Corinthiens`
- `Hebreux`

## 7. Ordinais e numeração dos livros

### 7.1 Regra canônica

Na saída normalizada, use ordinais arábicos:

- `1 Samuel`
- `2 Samuel`
- `1 Reis`
- `2 Reis`
- `1 Coríntios`
- `2 Coríntios`

### 7.2 Entrada aceitável

Devem ser reconhecidas formas como:

- `I`
- `II`
- `III`
- `IV`
- `1`
- `2`
- `3`
- `4`

### 7.3 Casos de cautela

Em tradições latinas mais antigas:

- `Regum`
- `Paralipomenon`
- `Esdrae`

podem mapear para convenções modernas diferentes.

Nesses casos, o extrator deve:

- preservar `ref_raw`
- gerar `ref_norm` apenas quando o mapeamento estiver bem definido
- registrar ambiguidade quando houver possibilidade de mais de um livro moderno correspondente

## 8. Capítulos, versículos e perícopes

### 8.1 Convenção canônica

Para `ref_norm`, use:

- `Livro capítulo`
- `Livro capítulo,versículo`
- `Livro capítulo,versículo-inicial-final`
- `Livro capítulo-inicial,versículo-inicial-capítulo-final,versículo-final`, quando a perícope cruzar capítulo

Exemplos:

- `São João 8`
- `São João 8,58`
- `Êxodo 3,14-15`
- `Isaías 52,13-53,12`

### 8.2 Capítulos sem versículo

Quando o índice mencionar apenas o capítulo:

- preserve isso como capítulo
- não invente versículo

Exemplo:

- `Luc. 1.` -> `São Lucas 1`

### 8.3 Listas de versículos

Casos como:

- `II Tim. iv, 3, 4`

podem ser tratados de dois modos:

- `2 Timóteo 4,3-4`, se a pontuação e a convenção do índice indicarem claramente sequência contínua
- ou duas referências internas, se o contexto editorial recomendar separação

Como regra de prudência:

- preferir representar como sequência quando a mesma entrada imprime capítulo único + versos contíguos
- preservar `ref_raw` sempre

### 8.4 Faixas que cruzam capítulos

Exemplos reais:

- `xii, 2-xiii, 10`
- `xxii, 29-xxiii, 6*`
- `lii, 13-liii, 12 fin`

Regra:

- preservar a faixa literal inteira em `ref_raw`
- gerar `ref_norm` apenas quando o parser conseguir determinar claramente o par capítulo/versículo inicial e final
- preservar marcadores como `fin` ou `*` em campo auxiliar, não no canônico

## 9. Intervalos, listas e agrupamentos

### 9.1 Intervalos

Exemplos:

- `3,14-15`
- `3,14 ad 15`
- `3,14-15.`

Saída canônica:

- `Êxodo 3,14-15`

### 9.2 Listas simples

Exemplos:

- `3,14; 3,15; 3,17`
- `Matth. 27,2; Luc. 3,1; Act. 4,27`

Tratamento:

- não colapsar tudo numa só string opaca
- produzir várias referências parseadas associadas à mesma entrada

### 9.3 Continuação implícita do livro

Exemplo:

- `Psal. 4,9; 91,1`

Se o segundo item não repete o livro, o livro deve ser herdado apenas quando:

- a pontuação da entrada deixar isso claro
- não houver troca de contexto

### 9.4 Herança do livro por seção

Em tabelas como `TABLE DES CITATIONS DE LA BIBLE`, o cabeçalho do livro costuma valer para várias linhas abaixo.

Regra:

- o agente pode herdar o livro até o próximo cabeçalho explícito
- essa herança deve ser considerada contextual e não reescrita como se estivesse impressa em cada linha

## 10. Marcadores editoriais especiais

Os índices bíblicos reais trazem marcas que não são parte da referência canônica, mas também não podem ser descartadas.

Exemplos reais:

- `fin`
- `(fin)`
- `/fin`
- `*`
- anotações entre parênteses como `(Histoire de Suzanne)`

Regra:

- preservar essas marcas em `ref_raw`
- quando necessário, registrá-las em campo auxiliar no `raw_json`
- não incorporar essas marcas dentro de `ref_norm`

## 11. `ibid.`, `seq.`, `seqq.` e formas análogas

Esses casos exigem especial cuidado.

### 11.1 `ibid.`

`ibid.` não deve ser transformado sozinho em `ref_norm`.

Ele depende de uma referência imediatamente anterior.

Regra:

- preservar `ibid.` em `ref_raw`
- resolver a herança apenas se o parser tiver contexto estrutural seguro
- se resolver, guardar a resolução em campo derivado, nunca substituindo o literal

### 11.2 `seq.` e `seqq.`

Exemplos:

- `310 seq.`
- `377 seqq.`

Essas formas são abertas.

Elas não identificam um limite fechado de versículos.

Regra:

- não converter automaticamente para intervalo fechado
- preservar como indicação de continuação
- quando necessário, normalizar apenas como âncora do primeiro ponto, com metadado `open_ended = true`

## 12. Páginas do volume, colunas, linhas e paginações paralelas

Nem toda referência numa tabela bíblica aponta para capítulo e versículo.

Na `PO`, por exemplo, podem aparecer:

- página da obra
- linha
- nota
- numeração entre colchetes
- paginação editorial paralela

Regra:

- separar sempre a referência bíblica da referência interna do tomo
- não fundir página do volume com capítulo bíblico
- quando a linha impressa misturar as duas coisas, guardar ambas em campos distintos

## 13. Deuterocanônicos e tradições textuais

### 13.1 Regra de prudência

Não assumir automaticamente que toda referência segue uma única tradição moderna protestante.

Os volumes patrísticos e editoriais aqui tratados circulam em tradições:

- latina
- católica
- grega
- oriental

Isso afeta:

- nomes dos livros
- numeração
- presença de deuterocanônicos
- organização de certos livros

### 13.2 Diretriz de normalização

Para `ref_norm`, use o catálogo canônico católico já compatível com o projeto, incluindo:

- Tobias
- Judite
- Sabedoria
- Eclesiástico
- Baruc
- 1 Macabeus
- 2 Macabeus

Mas sempre com preservação do literal.

### 13.3 Casos ambíguos

Se o mapeamento entre forma antiga e livro moderno não estiver claro:

- não inventar equivalência
- manter apenas `ref_raw`
- marcar `normalization_status = ambiguous`

## 14. Latim, francês, grego e mistos

Os índices bíblicos reais do corpus podem aparecer em:

- latim
- francês
- grego
- misturas de latim e francês
- misturas com aparato editorial

Regra:

- a língua da entrada não determina a forma canônica de saída
- a saída canônica pode ser portuguesa
- o idioma e a forma impressa original devem ser preservados como metadados

## 15. Quando não normalizar automaticamente

Não gerar `ref_norm` automaticamente quando ocorrer qualquer uma destas situações:

- nome do livro ilegível ou truncado
- ordinal ambíguo
- referência que pode ser bíblica ou apenas interna ao volume
- `ibid.` sem contexto confiável
- `seq.` ou `seqq.` sem política fechada para o caso
- mistura de paginação editorial e referência bíblica sem separação segura
- possível variante tradicional sem mapeamento estável
- perícope longa cuja fronteira textual não ficou suficientemente clara

Nesses casos:

- guardar `ref_raw`
- marcar a falha ou ambiguidade explicitamente

## 16. Níveis de confiança recomendados

Para o extrator futuro, faz sentido pelo menos três níveis:

- `high`
- `medium`
- `low`

Exemplos:

- `high`: `II Cor 5:15` claramente legível e mapeável
- `medium`: `Psal. CXLVI, 4.` com romanização resolvida sem ruído
- `low`: `Ibid.` herdado de referência anterior, livro parcialmente truncado ou perícope com marcador editorial duvidoso

## 17. Casos de teste mínimos recomendados

O futuro extrator de índices bíblicos deve ser validado pelo menos contra:

- abreviações latinas simples
- abreviações com ordinal romano
- abreviações com ordinal arábico
- livro em francês
- capítulo sem versículo
- capítulo + lista de versículos
- múltiplas referências na mesma entrada
- faixa que cruza capítulo
- perícope com `fin`
- perícope com `*`
- `ibid.`
- `seq.` / `seqq.`
- deuterocanônicos
- casos mistos `latim + francês`

Também convém aproveitar amostras já cobertas por:

- [tests/test_scripture_ref_variants.py](/homessddata/Projects/pdfocr/tests/test_scripture_ref_variants.py:1)

## 18. O que o agente pode e não pode fazer

O agente pode:

- herdar o livro do cabeçalho acima quando a tabela estiver claramente agrupada por livro
- separar referência bíblica e paginação do volume quando ambas aparecerem na mesma linha
- registrar marcadores editoriais fora de `ref_norm`

O agente não pode:

- criar uma forma canônica se o livro estiver incerto
- transformar toda perícope em versículo isolado
- descartar `fin`, `*`, `/fin` como ruído
- fundir códigos de concordância com referência bíblica
- “corrigir” numeração tradicional sem base suficiente

## 19. Lacunas ainda abertas

Ainda precisamos esclarecer:

- a tabela exata de correspondência entre abreviações encontradas nos índices e os nomes canônicos do projeto
- a política final para `ibid.`
- a política final para `seq.` e `seqq.`
- os casos de numeração tradicional em `Regum`, `Paralipomenon`, `Esdrae` e livros afins
- a convenção para entradas compostas por várias referências heterogêneas
- a relação entre índices bíblicos da `PO` e convenções usadas em fascículos orientais multilíngues

## 20. Relação com o contrato final

Este documento não define sozinho o payload do extrator.

Ele governa especificamente:

- como preencher `alphabetical_scripture_refs`
- quando criar também `alphabetical_refs` para a paginação editorial correspondente
- o que pode ou não pode entrar em `ref_norm`
- que ambiguidade deve permanecer explícita em `raw_json`

Em outras palavras:

- o contrato de schema vem de `docs/contrato_extrator_indices_alfabeticos.md`
- as regras bíblicas daqui dizem como usar corretamente a parte bíblica desse contrato

## 21. Resumo executivo

A normalização bíblica do novo extrator deve ser conservadora.

Ela deve:

- preservar sempre a forma impressa
- produzir forma canônica apenas quando houver confiança
- suportar múltiplas referências por entrada
- distinguir referência bíblica de paginação editorial
- distinguir citação, perícope e concordância
- reutilizar, quando possível, as convenções já presentes em `scripture_ref_normalizer.py`

Ela não deve:

- apagar o literal
- forçar equivalências duvidosas
- fechar intervalos abertos artificialmente
- tratar toda linha do índice como uma única referência simples
- inventar classes bíblicas que não apareçam no contrato documental
