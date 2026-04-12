# Projeto Patrística Digital (PT-BR)

Digitalização e processamento das coleções *Patrologia Graeca*, *Latina* e *Orientalis* para gerar texto OCR, resumos e keywords em português. Os PDFs/imagens originais **não** são versionados; apenas os `.txt` de OCR são mantidos. Próximos passos incluem busca FTS5 e RAG/agent.

> Nota de proveniência: a maior parte dos prompts, resumos e keywords foi criada em PT-BR e esses artefatos ainda não têm versão em inglês; o README em inglês é apenas uma tradução de referência. Os arquivos de OCR já estão versionados em `teste/`, e a limpeza/melhoria desses textos permanece em andamento.

## Escopo e volume de dados
- Corpus OCR versionado em `teste/P*/text/*`: ~289.852 arquivos de página (~1 bilhão de tokens).
- PDFs/imagens ficam fora do repositório por tamanho e direitos autorais.

## Scripts essenciais
- `main2.py` — OCR página a página com Tesseract + OpenCV (lat/grc), paralelismo com limites OMP.
- `resumo_serial.py` — Gera resumos/keywords por página a partir dos `.txt` (Ollama padrão ou OpenAI).
- `download/` — Ver `download/README.md` para baixar PDFs (`download.py`) e checar faltantes (`checkfaltantes.py`).
- `requirements.txt` — Dependências mínimas: numpy, opencv-python, Pillow, pytesseract, pdf2image.

## Como rodar (rápido)
- OCR: `python main2.py <arquivo.pdf> --lang lat` (ajuste idioma; configs internas setam DPI/threshold).
- Resumo: `python resumo_serial.py --volume-dir teste/PL001` (Ollama). Para OpenAI: `--provider openai --model gpt-5-mini`.
- Download: siga o guia em `download/README.md`.

## Pré-requisitos rápidos
- Python 3.10+, Tesseract (lat/grc) e Poppler (`pdftocairo`).
- `pip install -r requirements.txt`.
- OpenAI: `OPENAI_API_KEY` definido. Ollama: serviço local (ex.: `qwen3:30b`).
- Dados linguísticos (verificador de keywords):
  - NLTK: `python - <<'PY'\nimport nltk\nfor pkg in ['punkt','stopwords','floresta','words','omw-1.4']:\n    nltk.download(pkg)\nPY`
  - CLTK: `cltk download lat grc syr hye ara hbo gez`

## Smoke test
- OCR: rodar `python main2.py` em um PDF pequeno e conferir `.txt` gerado.
- Resumo: `python resumo_serial.py --volume-dir <dir>` e verificar escrita em `data/patristica_resumos.db`.
- Download: executar README em `download/` e checar nomes `PL/PG/PO###.pdf`.

## Dados publicados
- OCR completo disponível em `teste/` (páginas `P*/text/*`).
- `ocr_out/` e `pages/` guardam intermediários; `data/` mantém índices enriquecidos.
- Resumos/keywords em PT-BR estão nos bancos e artefatos do repositório (não há variante em inglês); a limpeza dos textos ainda é trabalho em progresso.

## Estado / Roadmap
- OCR estável; resumos/keywords em produção.
- Melhorias em limpeza/normalização de OCR e fix de lacunas seguem em progresso.
- Próximo foco: indexação FTS5 + agente de RAG bibliotecário.

## Aviso sobre IA, cobertura e revisão humana
- **Cobertura incompleta**: nem todo o corpus está processado; páginas ou volumes podem faltar.
- **Erros de OCR**: ruído, diacríticos ou colunas podem degradar o texto e afetar resumos/keywords.
- **Falsos negativos**: se a busca não retorna um tema, ele pode existir — confira o original.
- **Falsos positivos**: uma keyword sugerida pode não refletir fielmente o texto; valide antes de usar.
- **Interpretação automática**: modelos podem introduzir anacronismos ou simplificações teológicas. Metadados registram data/modelo/provedor para transparência.

## Aviso sobre a Patrologia Orientalis
Volumes da *Patrologia Orientalis* combinam várias línguas (francês, siríaco, grego etc.) e aparato crítico denso, o que torna o OCR tradicional menos confiável. Este projeto usa um modelo multimodal para transcrição inicial e segmentação; o Tesseract é reservado para trechos específicos. O `main2.py` aplica checagens de integridade do XML, validação visual (vazia/capa/texto), comparação token a token e por bloco com o Tesseract e reruns seletivos para reduzir alucinações e omissões. Ainda assim, sempre valide contra o original, especialmente para uso acadêmico.

## Citações bíblicas
Seguem o padrão em português: vírgula separa capítulo e versículo (ex.: `João 3,16`), hífen para intervalos (`Mateus 5,3-12`), ponto e vírgula para múltiplas referências (`João 3,16; 4,1-3; 5,24`).
