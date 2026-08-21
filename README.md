# BibliothecaPatristica — Projeto Patrística Digital

**[Explorar o acervo na BibliothecaPatristica](https://fabio3rs.github.io/BibliothecaPatristica/)** · **[Acessar os arquivos OCR](teste/)**

Biblioteca digital e corpus OCR das coleções *Patrologia Graeca*, *Patrologia Latina* e *Patrologia Orientalis*. A versão publicada oferece busca, navegação por coleções e índices, resumos, palavras-chave e acesso ao texto OCR das páginas. O material textual efetivamente digitalizado está versionado em [`teste/`](teste/), organizado por volume e página.

As coleções PG e PL foram reunidas e publicadas no século XIX pelo sacerdote e editor francês **Jacques-Paul Migne (1800–1875)**, que ampliou extraordinariamente o acesso aos escritos cristãos antigos e medievais. A PO é uma coleção posterior e independente. Veja o contexto histórico nos READMEs em [português](README.pt-BR.md#jacques-paul-migne-e-as-coleções) e [inglês](README.en.md#jacques-paul-migne-and-the-collections).

> [!WARNING]
> Pipelines Python utilizam LLMs tanto no OCR (gating/juiz combinando Tesseract + LLM) quanto em resumos/keywords. Resultados podem conter alucinações, omissões ou vieses; valide sempre no texto original antes de uso acadêmico/editorial.

> [!WARNING]
> O corpus é massivo (~1B tokens). Evite rodar jobs em todo `teste/` sem filtros; use `--first/--last` (OCR) e `--pattern`/`--limit` (keywords) para não gerar custo excessivo ou travamentos.

Este repositório contém o corpus, as pipelines de processamento e o site estático da BibliothecaPatristica. Escolha o idioma da documentação:
This repository contains the corpus, processing pipelines, and static BibliothecaPatristica website. Choose the documentation language:

- Versão completa em português (PT-BR): [README.pt-BR.md](README.pt-BR.md)
- Full version in English: [README.en.md](README.en.md)
- Pipeline técnico detalhado, incluindo VLM em duas passagens e reconciliação tripla de OCR: [docs/PIPELINE.md](docs/PIPELINE.md#ocr-three-way)
- Aplicação web Astro: [`web/`](web/)

## Notas rápidas

- A maior parte dos prompts, resumos e keywords foi escrita originalmente em PT-BR; não há versão em inglês para estes no momento.
- Os textos de OCR já estão versionados em `teste/` (páginas `P*/text/*`).
- A limpeza/melhoria do OCR segue em progresso; PDFs/imagens originais não são versionados.
- Na reconciliação tripla de OCR, o fac-símile está sempre presente: a VLM lê a imagem sem rascunhos, o Tesseract produz outra leitura da mesma imagem e páginas reprovadas voltam à VLM com o fac-símile e os dois textos para uma transcrição corretiva. É uma VLM em duas passagens, não *two-shot prompting*. A explicação completa está no [pipeline técnico](docs/PIPELINE.md#ocr-three-way).
- O desenho e os limiares dessa pipeline são empíricos, obtidos por comparação visual exploratória de páginas com seus fac-símiles, sem amostragem formal; não resultam de benchmark matemático de BCER.

## Quick notes

- Most prompts, summaries, and keywords were originally written in PT-BR; the English version is a reference translation.
- The OCR texts are already versioned in `teste/` (pages `P*/text/*`).
- In three-way OCR reconciliation, the facsimile is always present: the VLM first reads the image without OCR drafts, Tesseract independently reads the same image, and rejected pages return to the VLM with the facsimile and both texts. This is a two-pass VLM workflow, not two-shot prompting. See the [full English explanation](docs/PIPELINE.md#three-way-ocr-english).
- The pipeline design and thresholds are empirical, based on exploratory visual comparisons of informally selected pages with their facsimiles and no formal sampling design; they were not derived from a mathematical BCER benchmark.
