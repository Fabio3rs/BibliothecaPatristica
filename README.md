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
- Pipeline técnico detalhado: [docs/PIPELINE.md](docs/PIPELINE.md)
- Aplicação web Astro: [`web/`](web/)

## Notas rápidas

- A maior parte dos prompts, resumos e keywords foi escrita originalmente em PT-BR; não há versão em inglês para estes no momento.
- Os textos de OCR já estão versionados em `teste/` (páginas `P*/text/*`).
- A limpeza/melhoria do OCR segue em progresso; PDFs/imagens originais não são versionados.

## Quick notes

- Most prompts, summaries, and keywords were originally written in PT-BR; the English version is a reference translation.
- The OCR texts are already versioned in `teste/` (pages `P*/text/*`).
