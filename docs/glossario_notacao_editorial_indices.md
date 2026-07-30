# Glossário de notação editorial dos índices

Versão: 1

Este glossário combina o significado bibliográfico usual com as decisões
conservadoras exigidas pelo corpus PG, PL e PO. A forma JSON correspondente está
em `data/editorial_notation_glossary.json`.

## Regras gerais

- Preserve sempre a forma impressa em `ref_raw` ou `entry_raw`.
- Resolva herança somente dentro de um contexto estrutural seguro.
- Não transforme uma remissão textual em referência material sem locator.
- Não invente o limite de sequências abertas.
- Marcas locais podem ter significado próprio; procure a legenda da seção antes
  de aplicar uma interpretação global.

## Formas principais

### `ibid.` / `ibidem`

Remete ao lugar ou referência imediatamente anterior. Pode ser resolvido somente
quando o antecedente é inequívoco e não houve mudança de entrada, seção,
cabeçalho ou sistema de paginação. A resolução é derivada; `ibid.` permanece no
literal.

### `seq.`

Indica continuação aberta a partir do locator impresso. Não determine
automaticamente uma página final e não expanda em ocorrências.

### `seqq.`

Indica continuação plural aberta. A quantidade e o limite final não são
dedutíveis somente pela abreviação.

### `fin`, `fin.`, `(fin)`, `/fin`

Indica o fim ou a porção final da unidade citada. Não é página nem versículo.
Preserve como marca editorial auxiliar.

### `passim`

Indica ocorrências dispersas. Não produz uma lista finita de páginas.

### `supra` / `infra`

Remissões relativas a material anterior ou posterior. Exigem contexto local e
normalmente permanecem como remissão, salvo locator explícito.

### `cf.` / `confer`

Convite a comparar. Não prova citação direta nem cria referência material sem
locator.

### `vide`, `vid.`, `voir`, `v.`

Remissão textual para outro lema ou assunto. Sem número, coluna ou linha real,
fica no nível da entrada como `cross_reference`.

### `id.` / `idem`

Repete a identidade imediatamente anterior, não necessariamente todo o locator.
Resolva apenas quando o escopo editorial estiver claro.

### `not. a`, `not. b` e variantes

Identificam notas por letra associadas a uma página ou coluna. Separe
`page_ref_raw` de `note_ref_raw`; não incorpore a letra ao número da página.

### `col.` / `column`

Identifica coluna editorial. Página e coluna são campos distintos.

### `*`

Marca editorial dependente da seção. Pode indicar variante, observação ou limite
de perícope. Preserve e procure legenda ou padrão local; não descarte como ruído.

## Formas desconhecidas

O agente pode pesquisar a forma e ler ocorrências paralelas do volume. Se a
interpretação continuar incerta, deve registrar:

```json
{
  "kind": "unknown_notation",
  "raw": "loc. laud.",
  "source_span": {
    "file": "/caminho/arquivo.txt",
    "line_start": 20,
    "line_end": 20
  },
  "hypotheses": [],
  "recommended_glossary_entry": null
}
```

Uma sugestão só se torna regra global depois de revisão e nova versão do
glossário.

## Fontes externas de referência

Estas fontes sustentam somente o significado bibliográfico padrão. A política de
preservação e não expansão continua sendo uma decisão específica desta pipeline.

- University of Oxford Style Guide, “Abbreviations, contractions and acronyms”:
  `ibid` significa “no mesmo lugar” e depende da referência imediatamente
  anterior:
  https://www.ox.ac.uk/about/the-university/brand/style-guide/abbreviations
- Oxford School of Anthropology, Graduate Studies Handbook: `f.` e `ff.`
  indicam página/páginas seguintes e `et seq.` é uma forma equivalente aberta:
  https://www.anthro.ox.ac.uk/graduate-studies-handbook
- Oxford Standard for Citation of Legal Authorities: `ibid` repete a nota
  imediatamente anterior e `cf.` significa comparar:
  https://www.law.ox.ac.uk/sites/files/oxlaw/oscola_2006.pdf

Os documentos locais `levantamento_indices_alfabeticos.md`,
`normalizacao_indices_biblicos.md` e `contrato_extrator_indices_alfabeticos.md`
fornecem a evidência específica de uso em PG, PL e PO.
