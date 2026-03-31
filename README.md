# Projeto Patrística Digital

Digitalização e processamento das coleções *Patrologia Graeca*, *Latina* e *Orientalis* para gerar texto OCR, resumos e keywords em português. Os PDFs/imagens originais **não** são versionados; apenas os `.txt` de OCR são mantidos. Próximos passos incluem busca FTS5 e RAG/agent.

## Escopo e volume de dados
- Corpus OCR versionado em `teste/P*/text/*`: ~289.852 arquivos de página.
- Volume total estimado: perto de **1 bilhão de tokens** de texto bruto.

## Scripts essenciais
- `main2.py` — OCR página a página com Tesseract + OpenCV (lat/grc), paralelismo com limites OMP.
- `resumo_serial.py` — Gera resumos/keywords por página a partir dos `.txt` (Ollama padrão ou OpenAI).
- `download/` — Ver `download/README.md` para baixar PDFs (`download.py`) e checar faltantes (`checkfaltantes.py`).
- `requirements.txt` — Dependências mínimas: numpy, opencv-python, Pillow, pytesseract, pdf2image.

## Como rodar (rápido)
- OCR: `python main2.py <arquivo.pdf> --lang lat` (ajuste idioma conforme série; configs internas já setam DPI/threshold).
- Resumo: `python resumo_serial.py --volume-dir teste/PL001` (Ollama). Para OpenAI: `--provider openai --model gpt-5-mini`.
- Download: siga o guia em `download/README.md`.

## Pré-requisitos rápidos
- Python 3.10+, Tesseract (com traineddata lat/grc) e Poppler (`pdftocairo`).
- Pip do `requirements.txt`.
- Para OpenAI: `OPENAI_API_KEY` definido. Para Ollama: serviço local com modelo (ex.: `qwen3:30b`).
- Dados linguísticos (recomendado para verificador de keywords):
  - NLTK: `python - <<'PY'\nimport nltk\nfor pkg in ['punkt','stopwords','floresta','words','omw-1.4']:\n    nltk.download(pkg)\nPY`
  - CLTK: `cltk download lat grc syr hye ara hbo gez`

## Smoke test
- OCR: rodar `python main2.py` em um PDF pequeno e conferir `.txt` gerado.
- Resumo: `python resumo_serial.py --volume-dir <dir>` e verificar escrita em `data/patristica_resumos.db`.
- Download: executar os passos do README em `download/` e checar nomes `PL/PG/PO###.pdf`.

## Licenças
- Código-fonte sob MIT (`LICENSE`).
- Textos gerados pelo projeto (OCR, resumos, keywords, metadados) dedicados ao domínio público via CC0 1.0 (`LICENSE-CC0`).
- Os PDFs/imagens originais **não** ficam no repositório; direitos podem variar por país/fonte. Baixe por sua conta e risco.
- Dependências externas (Tesseract, OpenCV, Ollama/OpenAI etc.) mantêm suas próprias licenças.

## Estado/Roadmap
OCR estável; resumos/keywords em produção. Próximo foco: indexação FTS5 + agente de RAG bibliotecário.

## Aviso sobre IA, cobertura e revisão humana
Este projeto usa IA (LLMs) para acelerar a catalogação do corpus patrístico e facilitar a localização rápida de temas. Isso traz ganhos de velocidade e alcance, mas também limitações:

- **Cobertura incompleta**: nem todo o corpus está processado; páginas ou volumes podem faltar.
- **Erros de OCR**: páginas com ruído, diacríticos ou colunas podem gerar texto defeituoso e influenciar resumos/keywords.
- **Falsos negativos**: se a busca não retorna um tema, ele pode existir — confira o original.
- **Falsos positivos**: uma keyword sugerida pode não refletir fielmente o texto; não use sem validação.
- **Interpretação automática**: modelos podem introduzir anacronismos, vieses ou simplificações teológicas.

## Aviso sobre a Patrologia Orientalis
Optical character recognition (OCR) tools such as Tesseract work very well on clean, modern printed text, especially when the page layout is simple and the script is consistent. However, volumes of the *Patrologia Orientalis* present a particularly difficult scenario for traditional OCR systems. These books typically contain multiple languages and scripts within the same page—most commonly French, Syriac, Greek, and sometimes other Oriental languages—often combined with dense scholarly apparatus, marginal references, and editorial annotations. Such heterogeneous layouts significantly complicate automatic character recognition. OCR engines are known to struggle with historical documents that include unusual fonts, complex page structures, or degraded scans, which frequently leads to large numbers of recognition errors.

In addition to the multilingual nature of the text, the typographic conventions used in scholarly editions further increase the difficulty. Footnotes, variant readings, marginalia, line numbers, and mixed scripts within a single line create segmentation problems for OCR engines that expect relatively uniform text regions. While modern OCR systems support many languages, their accuracy drops sharply when encountering historical typography, uncommon scripts, or noisy page layouts. For this reason, the project uses a multimodal language model (LLM) to perform an initial transcription and layout segmentation of each page, with classical OCR tools such as Tesseract reserved for targeted reprocessing of specific regions when necessary.

### Por que está em um formato diferente?
- Tesseract é bastante determinístico, mas acaba produzindo resultados que podem variar dependendo da qualidade do texto de entrada e do layout da página. Isso significa que, em alguns casos, o texto extraído pode não corresponder exatamente ao original, especialmente em documentos complexos ou danificados, muitas vezes, enchendo de ruído no meio do texto.
- Precisei implementar técnicas via LLM usando modelos Vision para melhorar a precisão da extração de texto e a segmentação de layout.
- O uso de LLMs permite uma compreensão mais profunda do contexto e da estrutura do texto, resultando em uma extração mais precisa e relevante.

Entretanto, em vários casos a LLM pode despejar textos que não estão na página, o que chamamos de alucinações ou então retornar que a página está em branco mesmo quando há conteúdo. Por isso, preparei algumas alterações no script para averiguar a intersecção do texto extraído via LLM com o Tesseract. Em casos de falhas óbvias, o script solicita a LLM um novo processamento da página, enviando o rascunho obtido pelo Tesseract no prompt.

O `main2.py` acumula várias heurísticas porque o pipeline precisa decidir automaticamente, e com baixo custo, se uma página pode ser aceita ou deve ser reprocessada: checamos integridade do XML (estado, fechamento), cruzamos tinta/bordas com a declaração da LLM para detectar alucinações ou omissões, ajustamos capas/guardas para não penalizar textura, usamos classificação visual (vazia/capa/texto) para validar o atributo `estado`, calculamos sobreposição token a token com o Tesseract e agora também bloco a bloco/parágrafo a parágrafo para pegar casos de “um trecho bom + lixo”. Essas regras evitam gastar GPU/LLM em reruns desnecessários, mas, sobretudo, bloqueiam falsos positivos (LLM inventando texto ou marcando vazio quando há conteúdo), mantendo a qualidade do OCR sem explodir custo ou I/O.

Portanto, todo resultado deve ser verificado no texto original e, para uso acadêmico, revisado por especialistas antes de citação ou análise. Registramos data, modelo e provedor de IA nos metadados para transparência.
