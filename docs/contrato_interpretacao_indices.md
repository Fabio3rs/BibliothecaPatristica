# Contrato de interpretação dos índices

Versão: 1

Este contrato é a referência semântica única dos prompts da pipeline de índices
alfabéticos. O prompt define a tarefa e os caminhos; este documento define como
preservar, interpretar e tornar auditável o conteúdo.

## 1. Princípios

1. O literal OCR é evidência primária. Toda interpretação derivada conserva o
   texto original.
2. Arquivo OCR, segmento dentro do arquivo, página editorial e referência citada
   são entidades distintas.
3. Toda seção, entrada e referência deve ser rastreável a um `source_span`.
4. Incerteza deve ser estruturada, nunca ocultada por uma normalização provável.
5. Um agente pode investigar livremente dentro do `source_root`, mas escreve
   somente os artefatos pertencentes à fase.
6. Python valida ownership, versões, fingerprints, cobertura e chaves; Python
   também monta o payload canônico.

## 2. Descoberta e fronteiras

O prefilter é evidência inicial, não ground truth. O agente deve seguir a
estrutura editorial e pode ler qualquer arquivo do volume necessário para:

- encontrar o começo e o fim de cada índice possuído;
- distinguir cabeçalhos repetidos de novos índices;
- registrar continuidade entre arquivos;
- encontrar uma fronteira ausente no prefilter;
- separar segmentos diferentes dentro do mesmo arquivo físico.

Papéis de segmento:

- `owned`: material pertencente à pipeline alfabética;
- `boundary`: início de material pertencente a outra pipeline;
- `context`: material lido apenas para tomar uma decisão;
- `uncertain`: segmento cuja propriedade não pôde ser decidida.

`ORDO RERUM`, addenda/corrigenda, errata e outros fechamentos editoriais são
`boundary`. Se começarem no meio de um arquivo, o segmento anterior ainda pode
ser `owned`.

## 3. Unidade semântica

- Uma entrada lógica representa um lema, nome, assunto, passagem bíblica,
  remissão ou nota editorial.
- Uma lista de páginas imprime uma ocorrência material por item.
- Um intervalo impresso fechado permanece uma referência
  `editorial_range`; não invente ocorrências individuais.
- Formas abertas como `seq.`, `seqq.`, `fin` e `passim` nunca são expandidas.
- Uma mesma passagem bíblica com várias páginas materiais é uma entrada bíblica
  com várias referências materiais.
- Passagens bíblicas distintas são entradas distintas, ainda que impressas na
  mesma linha.

## 4. Proveniência

Um `source_span` contém:

- `file`: caminho absoluto dentro do `source_root`;
- `line_start` e `line_end`: linhas inclusivas, positivas;
- `block_type`: tipo de bloco OCR quando disponível;
- `text_sha256`: opcional, para auditoria do trecho.

Entradas devem possuir `source_span`. Referências podem herdar o span da entrada,
mas usam span próprio quando ocupam outra linha ou bloco.

Cada fragmento declara:

- `task_id`;
- `input_fingerprint`;
- `consumed_spans`;
- `residual_spans`;
- `unresolved`;
- `decision_log`.

`residual_spans` explica material não consumido. `unresolved` registra dúvidas
que afetam dados. `decision_log` registra somente decisões editoriais relevantes,
não raciocínio livre.

## 5. Notação editorial

Use o glossário versionado. Cada interpretação contextual pode registrar:

```json
{
  "notation_key": "ibid",
  "raw": "ibid.",
  "resolution_status": "resolved",
  "inherits_from_ref_order": 2,
  "evidence": "antecedente imediato dentro da mesma entrada"
}
```

Valores de `resolution_status`:

- `literal_only`;
- `resolved`;
- `ambiguous`;
- `not_applicable`.

Uma forma ausente do glossário permanece literal e gera um item
`unknown_notation` em `unresolved` ou em `glossary_suggestions.json`. O agente
pode consultar a Web para compreender a forma, mas não altera silenciosamente
uma regra global durante a extração.

## 6. Campos derivados

Campos `*_norm`, chaves bíblicas e resoluções herdadas são derivados. Eles nunca
substituem `lemma_raw`, `entry_raw`, `context_raw` ou `ref_raw`.

Reparos de quebra de linha alfabética são registrados em
`raw_json.soft_wrap_repairs`. Hífens numéricos e lexicais não são removidos sem
evidência estrutural.

## 7. Completude

Um fragmento é completo somente quando todos os seus segmentos atribuídos estão
em `consumed_spans` ou `residual_spans`. Uma extração não pode usar
`unrecoverable_ocr` como sinônimo de ambiguidade ou busca incompleta.

O manifesto é escrito por último. Um checkpoint só pode ser reutilizado quando
fingerprint, versões do prompt, contrato, glossário e schema coincidirem.

