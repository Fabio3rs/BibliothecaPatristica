import os
import time
import main2
from pathlib import Path


PROMPT_REWRITE = """
Você é um especialista em paleografia e transcrição de documentos históricos e edições críticas (Patrologia Orientalis).
Sua missão é comparar a imagem da página com o rascunho de OCR abaixo e produzir uma transcrição fiel, corrigindo erros do Tesseract e descartando qualquer trecho que não apareça na imagem.

<rascunho_ocr>
{tesseract_text}
</rascunho_ocr>

Falhas conhecidas do Tesseract:
- Erros de reconhecimento em caracteres especiais
- Dificuldade com fontes manuscritas
- Problemas de alinhamento em documentos escaneados
- Dificuldade em lidar com texto em várias colunas
- Letras incorretas por causa de caracteres de grafia semelhante

No caso específico da Patrística:
- Mistura de idiomas (orientais e ocidentais) e scripts
- Uso de caracteres especiais e diacríticos
- Anotações manuscritas

### ETAPA 1: ANÁLISE VISUAL OBRIGATÓRIA
Antes de gerar o XML, identifique se a página é:
- Uma capa ou página de guarda (pode estar em branco ou apenas amarelada).
- Uma página de texto denso (mesmo que degradado ou com scripts complexos como Siriaco/Grego).
- Uma página com gravuras ou tabelas.

### ETAPA 2: TRANSCRIÇÃO ESTRUTURADA (XML)
Se a página estiver REALMENTE em branco (apenas papel), use: <pagina estado="vazio" tipo="capa_ou_guarda" />
Caso contrário, siga o formato abaixo.

Valores permitidos para script: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto, desconhecido.
Valores permitidos para tipo: cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal, outro.

REGRAS CRÍTICAS CONTRA OMISSÃO E ERROS DO OCR:
1. PROIBIÇÃO DE NEGATIVA: É terminantemente proibido ignorar blocos de texto ou afirmar que a página está em branco se houver qualquer vestígio de tinta. Se o texto estiver difícil, transcreva o que for possível; NUNCA desista de um bloco.
2. INTEGRIDADE: Cada nota de rodapé e aparato crítico deve ser mapeado. A omissão de blocos será considerada falha grave de processamento.
3. ESTADO DA PÁGINA: A tag raiz <pagina> deve conter o atributo 'estado' ("com_texto" ou "vazio").
4. USE O RASCUNHO COMO PISTA, NÃO COMO FONTE CONFIÁVEL: só aproveite palavras/trechos do <rascunho_ocr> que você confirma visualmente na imagem; corrija erros e descarte alucinações de caracteres, palavras, etc.
5. COERÊNCIA VISUAL: se o rascunho tiver linhas ausentes ou extras, siga SEMPRE o que está na imagem.

Formato de saída:
<pagina estado="com_texto">
  <bloco tipo="..." script="...">
    transcrição literal preservando quebras de linha
  </bloco>
  <notas>
    Explique aqui se houve scripts complexos identificados (ex: Siriaco Estrangelo) ou correções relevantes feitas sobre o rascunho do Tesseract.
  </notas>
</pagina>

MAIS REGRAS:
- Preserve a ordem visual (cima para baixo).
- Não traduza, não normalize, não invente texto.
- Use [ilegivel] apenas para palavras específicas, não para blocos inteiros.
- Retorne APENAS o XML.
""".strip()


PROMPT_LLM_JUDGE = """
Você é um especialista em paleografia e transcrição de documentos históricos (Patrologia Orientalis).
Compare a imagem com a transcrição na tag <ocr> e avalie a fidelidade.

# Critérios de avaliação

**Fidelidade** — quão bem a transcrição reflete o que está na imagem:
- alta: texto principal correto, erros mínimos ou apenas em scripts difíceis
- media: erros parciais, omissões menores, mas estrutura preservada
- baixa: erros significativos, blocos omitidos, confusão de scripts
- descartar: transcrição irreconhecível ou completamente incorreta

**Usabilidade** — se o texto é aproveitável para produção de resumos:
- alta: semântica preservada, termos principais identificáveis
- media: compreensível com esforço, perdas pontuais de sentido
- baixa: sentido comprometido por erros acumulados
- descartar: inutilizável

# Notas importantes
- Para blocos em scripts não-latinos (armênio, siríaco, grego), avalie apenas:
  (a) se o bloco está presente na transcrição
  (b) se a extensão aproximada parece compatível com a imagem
  (c) se não há confusão óbvia de script (ex: caracteres árabes no meio de armênio)
  Não avalie a correção caractere a caractere nesses scripts.
- Para blocos em francês/latim/inglês, avalie semântica e fidelidade completas.

Formato de saída esperado (retorne apenas o XML válido preenchido de acordo com o julgamento da imagem):
<avaliacao>
  <comentario>
    Comentário específico por bloco: o que está correto, o que está errado ou omitido.
  </comentario>
  <idiomas_identificados>
    <idioma>armênio</idioma>
    <!-- outros idiomas em PT-BR, cite o idioma identificado no texto, exemplo: "francês" -->
  </idiomas_identificados>
  <julgamento>
    <fidelidade>alta|media|baixa|descartar</fidelidade>
    <usabilidade>alta|media|baixa|descartar</usabilidade>
  </julgamento>
</avaliacao>

<ocr>
{llm_ocr}
</ocr>
""".strip()


def judge_img(
    img_path: Path,
    algorithm: str = "ollama",
    llm_model: str = main2.DEFAULT_LLM_MODEL,
    ollama_url: str = main2.DEFAULT_OLLAMA_URL,
    openai_base_url: str = main2.DEFAULT_OPENAI_BASE_URL,
    openai_api_key: str = os.getenv("OPENAI_API_KEY"),
    reprocess: bool = False,
    lang: str = "fra+lat+grc+ell+syr",
):
    tesseract_db = main2.open_tesseract_cache_db()
    main2.init_tesseract_cache(tesseract_db)

    tesseractres = main2.strip_bidi_markers(
        main2.run_tesseract_cached(tesseract_db, img_path, lang=lang)
    ).strip()

    base_out = img_path.parent.parent
    images_dir = base_out / "images"
    text_dir = base_out / "text"

    print(f"Processing {img_path.name} {images_dir} {text_dir}...")

    page_txt_path = text_dir / (img_path.stem + ".txt")
    if page_txt_path.exists():
        txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")

    # txt = main2.clean_text_oriental(txt).strip()

    # PROMPT_LLM_JUDGE
    prompt_llm_judge = PROMPT_LLM_JUDGE.format(llm_ocr=txt)

    print(f"PROMPT_LLM_JUDGE  {prompt_llm_judge}")

    t4 = time.time()
    txt = main2.llm_process_chat_retry(
        img_path,
        provider=algorithm,
        model=llm_model,
        url=ollama_url,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        prompt=prompt_llm_judge,
        reprocess=reprocess,
    )
    t5 = time.time()
    print(
        f"[{time.strftime('%H:%M:%S')}] {img_path.name} — LLM ({algorithm}) OCR: {t5 - t4:.3f}s"
    )

    print(f"PROMPT_LLM_JUDGE  {prompt_llm_judge}\nSAÍDA DA LLM:\n{txt}")


def process_img(
    img_path: Path,
    algorithm: str = "ollama",
    llm_model: str = main2.DEFAULT_LLM_MODEL,
    ollama_url: str = main2.DEFAULT_OLLAMA_URL,
    openai_base_url: str = main2.DEFAULT_OPENAI_BASE_URL,
    openai_api_key: str = os.getenv("OPENAI_API_KEY"),
    reprocess: bool = False,
    lang: str = "fra+lat+grc+ell+syr",
):
    tesseract_db = main2.open_tesseract_cache_db()
    main2.init_tesseract_cache(tesseract_db)

    tesseractres = main2.strip_bidi_markers(
        main2.run_tesseract_cached(tesseract_db, img_path, lang=lang)
    ).strip()

    base_out = img_path.parent.parent
    images_dir = base_out / "images"
    text_dir = base_out / "text"

    print(f"Processing {img_path.name} {images_dir} {text_dir}...")

    page_txt_path = text_dir / (img_path.stem + ".txt")
    if page_txt_path.exists():
        txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")

    txt = main2.clean_text_oriental(txt).strip()

    # PROMPT_REWRITE
    prompt_llm_judge = PROMPT_REWRITE.format(tesseract_text=tesseractres, llm_ocr=txt)

    print(f"PROMPT_REWRITE  {prompt_llm_judge}")

    t4 = time.time()
    txt = main2.llm_process_image_autoretry(
        img_path,
        provider=algorithm,
        model=llm_model,
        url=ollama_url,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        prompt=prompt_llm_judge,
        reprocess=reprocess,
    )
    t5 = time.time()
    print(
        f"[{time.strftime('%H:%M:%S')}] {img_path.name} — LLM ({algorithm}) OCR: {t5 - t4:.3f}s"
    )

    print(f"PROMPT_REWRITE  {prompt_llm_judge}\n{txt}")


def main():
    img_path = Path("teste/PO021/images/d306e9e5-aa17-41bf-9b1c-80816e85278c-728.png")
    judge_img(
        img_path,
        # llm_model="qwen3.5:27b"
        llm_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        algorithm="openai",
    )


if __name__ == "__main__":
    main()
