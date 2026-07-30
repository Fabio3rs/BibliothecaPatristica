# Proposta de frontend para índices alfabéticos

Este documento é um rascunho de arquitetura e layout para o frontend dos índices alfabéticos extraídos para `data/alphabetical_indices.db`.

Não é a versão final da interface.

O objetivo aqui é registrar uma proposta coerente com:

- o formato real do material já importado
- os resultados da análise de shards
- a necessidade de navegação manual estilo dicionário
- a necessidade de busca textual separada via Pagefind

Leia este documento junto com:

- `docs/estrategia_pipeline_extracao_indices_alfabeticos.md`
- `docs/duvidas_frequentes_extracao_indices_alfabeticos.md`
- `docs/contrato_extrator_indices_alfabeticos.md`

## 1. Problema de produto

O material alfabético não se comporta como uma lista pequena nem como uma busca full-text pura.

Ele tem características de:

- dicionário
- índice remissivo
- índice onomástico
- índice bíblico
- navegação lexical por faixa

Além disso, o tamanho do material varia muito por domínio:

- `subjects`: muito grande
- `names`: médio
- `scripture`: relativamente pequeno

Isso impede uma solução ingênua do tipo:

- uma página por letra
- um único JSON monolítico
- um único índice de busca misturado ao corpus principal

## 2. Objetivo da UX

A proposta de UX deve permitir duas formas principais de uso:

1. navegação manual por faixa lexical
2. busca textual rápida por lema original ou tradução

O modelo mental mais útil para isso é o de um dicionário:

- cada página tem tamanho aproximadamente estável
- cada página é identificada por uma faixa lexical visível
- o usuário localiza manualmente um bloco por:
  - letra
  - primeira palavra da página
  - última palavra da página

Exemplo de identificação pública:

- `Abba - Abraham`
- `Acedia - Actio`
- `Caritas - Causa`

## 3. Proposta central

Separar a solução em duas camadas:

1. shard técnico
2. página pública de dicionário

### 3.1. Shard técnico

O shard técnico existe para:

- build
- fetch eficiente
- compressão
- balanceamento de tamanho
- rebuild estável

Ele não precisa ser exposto diretamente ao usuário.

### 3.2. Página pública de dicionário

A página pública existe para:

- navegação humana
- localização manual fácil
- paginação estável no frontend

Ela deve mostrar:

- `headword_start`
- `headword_end`
- contagem de entradas
- lista de itens da página

Em outras palavras:

- o backend pode shardear de forma técnica
- o frontend apresenta páginas rotuladas por faixa lexical

## 4. O que a análise de shards já mostrou

O script relevante aqui é:

- `scripts/index_extraction/analyze_alpha_sharding.py`

Esse script já analisa estratégias de shard sobre o banco `alphabetical_indices.db`.

### 4.1. `subjects`

`subjects` é o domínio mais pesado.

Consequências práticas:

- shard por 1 letra é grande demais
- shard por 2 letras ainda cria hot spots
- shard balanceado por tamanho é a melhor base técnica

Conclusão:

- `subjects` deve usar shard técnico balanceado
- a faixa lexical deve ser um metadado de apresentação, não a regra física de particionamento

### 4.2. `names`

`names` é menor, mas ainda tem concentração desigual.

Consequências práticas:

- shard por letra ainda pode ficar ruim
- há buckets não alfabéticos relevantes, especialmente `#`

Conclusão:

- `names` também deve usar shard técnico balanceado
- o frontend deve tratar explicitamente itens fora de prefixo alfabético limpo

### 4.3. `scripture`

`scripture` é pequeno o suficiente para uma estratégia mais simples.

Conclusão:

- `scripture` pode usar shard mais simples
- ainda assim, vale manter o mesmo contrato de frontend sempre que possível

## 5. Domínios públicos recomendados

A primeira proposta pública deve separar o material em três domínios:

- `subjects`
- `names`
- `scripture`

Motivo:

- cada domínio tem tamanho, semântica e forma de navegação diferentes
- isso melhora performance
- isso melhora relevância de busca
- isso evita uma UI única demais para material heterogêneo

## 6. Proposta de navegação

### 6.1. Tela inicial

A tela inicial do frontend dos índices deve oferecer:

- seleção de domínio
- explicação curta do que cada domínio contém
- campo de busca
- navegação por letra ou faixa

Exemplo:

- `Assuntos`
- `Nomes`
- `Escrituras`

### 6.2. Navegação por página de dicionário

Cada página pública deve se parecer mais com uma página de dicionário do que com uma tela de filtro genérico.

Topo sugerido:

- faixa lexical: `Abba - Abraham`
- domínio
- quantidade de entradas
- navegação anterior / próxima

Corpo sugerido:

- lista de itens
- cada item em linha curta
- contexto mínimo
- link para abrir o verbete ou contexto

### 6.3. Navegação por letra

Vale ter atalhos por letra, mas não como unidade física obrigatória do dado.

A letra serve como:

- ponto de entrada
- agrupamento visual
- atalho de navegação

Não deve servir como:

- única estratégia de particionamento

## 7. Proposta de item público

Cada item exibido no frontend deve priorizar legibilidade.

Forma recomendada:

- `original — tradução`

Exemplo:

- `Abba — Pai`
- `Abraham — Abraão`

Metadados curtos possíveis:

- coleção (`PG`, `PL`, `PO`)
- volume
- tipo de seção

Se necessário, uma segunda linha pode mostrar:

- heading da seção
- contexto resumido

## 8. Papel da tradução

A tradução deve ser tratada como camada de frontend, não como substituta do original.

Regra recomendada:

- mostrar sempre o original
- mostrar a tradução no idioma do frontend quando houver

Exemplo:

- `original — tradução[locale]`

Se a tradução não existir:

- mostrar só o original
- não bloquear a navegação por falta de tradução

## 9. Proposta de schema para página pública

Exemplo de página pública de dicionário:

```json
{
  "page_id": "subjects-00412",
  "domain": "subjects",
  "letter": "A",
  "headword_start": "Abba",
  "headword_end": "Abraham",
  "item_count": 84,
  "items": [
    {
      "original": "Abba",
      "translation": {
        "pt-BR": "Pai",
        "en": "Father"
      },
      "volume_id": "PG084",
      "collection": "PG",
      "section_kind": "onomastic_person",
      "entry_key": "PG084:entry:0123",
      "href": "/indices/alpha?volume=PG084&entry=PG084:entry:0123"
    }
  ]
}
```

## 10. Proposta de separação entre shard técnico e página pública

Uma página pública não precisa coincidir exatamente com um shard físico.

Esse desacoplamento é desejável.

Ele permite:

- rebalancear shards sem mudar a UX
- manter páginas públicas estáveis
- evitar hot spots de letras grandes

Modelo recomendado:

- shard técnico como unidade de armazenamento e download
- página pública como unidade de navegação

## 11. Papel do Pagefind

Vale ter um índice Pagefind especial para esse material.

O repositório já aponta nessa direção com:

- `tools/build_indices_pagefind_from_json.mjs`

Mas o frontend dos índices alfabéticos deve ter um Pagefind próprio para esse domínio, separado do índice principal do corpus.

### 11.1. Por que um Pagefind separado

Motivos:

- o universo semântico é diferente do corpus principal
- a busca precisa privilegiar lema e tradução
- a navegação por índices tem outra relevância e outra UX

### 11.2. O que o Pagefind deve indexar

Campos prioritários:

- lema original
- tradução
- volume
- coleção
- `section_kind`
- heading da seção

### 11.3. O que o Pagefind não substitui

O Pagefind não substitui:

- o shard técnico
- a navegação por faixa lexical
- a noção de página pública estilo dicionário

Papel correto:

- `shards/pages` para browse
- `Pagefind` para search

## 12. Tratamento do bucket `#`

A análise de shards mostra que itens sem prefixo alfabético limpo podem concentrar ruído e volume relevante.

Por isso o frontend deve ter uma política explícita para esse grupo.

Opções plausíveis:

- aba `Outros`
- seção `Símbolos e formas irregulares`
- agrupamento separado por normalização mínima

O importante é:

- não esconder esse material
- não misturar silenciosamente tudo em uma letra arbitrária

## 13. Direção de layout

Como proposta de layout, não como tela final:

### 13.1. Landing

- escolha de domínio
- busca principal
- explicação curta do que é o índice

### 13.2. Lista de páginas

- letras ou grupos
- páginas identificadas por:
  - primeira palavra
  - última palavra
  - contagem de itens

### 13.3. Página de verbetes

- cabeçalho com faixa lexical
- lista de verbetes
- cada verbete como:
  - `original — tradução`
  - metadados mínimos
  - link para abrir contexto

## 14. Requisitos técnicos mínimos

Uma primeira implementação deveria garantir:

- JSONs pequenos o bastante para fetch incremental
- suporte a gzip
- página pública com tamanho aproximadamente estável
- busca separada via Pagefind
- fallback gracioso quando não houver tradução
- URLs estáveis para `volume_id` e `entry_key`

## 15. Decisões provisórias recomendadas

Para não paralisar o desenho, estas são boas decisões provisórias:

1. manter três domínios públicos:
   - `subjects`
   - `names`
   - `scripture`
2. usar shard técnico balanceado para:
   - `subjects`
   - `names`
3. usar shard mais simples para:
   - `scripture`
4. exibir páginas públicas por faixa lexical:
   - `headword_start - headword_end`
5. exibir cada item como:
   - `original — tradução`
6. manter Pagefind separado como camada de busca

## 16. O que ainda não está decidido

Este documento não fecha ainda:

- visual final
- tipografia
- filtro exato por coleção/volume
- contrato final do JSON público
- política final de i18n para traduções
- se `scripture` terá navegação lexical, por livro, ou híbrida

Esses pontos podem ser refinados depois que existirem:

- sketches de layout
- export piloto de shards públicos
- primeira rodada de teste de performance no frontend
