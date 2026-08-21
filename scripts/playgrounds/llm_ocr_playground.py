import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os
import time
import main2



PROMPT = """
You are an expert in palaeography and transcription of historical documents (Patrologia Graeca, Latina et Orientalis).

Analyse the image and produce a faithful XML transcription. First identify the page type (cover/endpaper, text, illustration).

If the page is truly blank: <pagina estado="vazio" tipo="capa_ou_guarda" />

Allowed scripts: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto, desconhecido.
Allowed types: cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal, outro.

RULES:
1. NEVER state that the page is blank if there is any trace of ink. Transcribe whatever is possible.
2. Map every block: footnotes, critical apparatus, marginal notes. Omission is a serious failure.
3. The root tag must have the attribute estado="com_texto" or estado="vazio".
4. BBOX: x1,y1,x2,y2 (scale 0-1000).
5. Two-column layout: transcribe the entire left column first, then the right. Full-width headers and footers stay at the visual position they occupy.
6. Do not translate, normalise, or invent. Use [ilegivel] only for individual words, never for whole blocks.

Layout note: Letters A, B, C, D placed vertically in the centre gutter are nota_marginal section identifiers.

Output format:
<pagina estado="com_texto">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    literal transcription
  </bloco>
  <notas>complex scripts or relevant corrections if made</notas>
</pagina>

Return ONLY the XML.
""".strip()

PROMPT_VERIFY_TESSERACT = """
You are an expert in palaeography and transcription of historical documents (Patrologia Graeca, Latina et Orientalis).

Analyse the image and produce a faithful XML transcription. First identify the page type (cover/endpaper, text, illustration).

If the page is truly blank: <pagina estado="vazio" tipo="capa_ou_guarda" />

Allowed scripts: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto, desconhecido.
Allowed types: cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal, outro.

RULES:
1. NEVER state that the page is blank if there is any trace of ink. Transcribe whatever is possible.
2. Map every block: footnotes, critical apparatus, marginal notes. Omission is a serious failure.
3. The root tag must have the attribute estado="com_texto" or estado="vazio".
4. BBOX: x1,y1,x2,y2 (scale 0-1000).
5. The OCR draft is a hint — verify visually before using it. Tesseract makes mistakes with scripts, diacritics, and ligatures.
6. Two-column layout: transcribe the entire left column first, then the right. Full-width headers and footers stay at the visual position they occupy.
7. Do not translate, normalise, or invent. Use [ilegivel] only for individual words, never for whole blocks.

Layout note: Letters A, B, C, D placed vertically in the centre gutter are nota_marginal section identifiers.

Output format:
<pagina estado="com_texto">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    literal transcription
  </bloco>
  <notas>complex scripts or relevant corrections if made</notas>
</pagina>

Return ONLY the XML.
""".strip()

PROMPT_VERIFY_LLM_VS_TESSERACT = """
You are an expert in palaeography and transcription of historical documents (Patrologia Graeca, Latina et Orientalis).

Analyse the image and produce a faithful XML transcription. First identify the page type (cover/endpaper, text, illustration).

If the page is truly blank: <pagina estado="vazio" tipo="capa_ou_guarda" />

Allowed scripts: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto, desconhecido.
Allowed types: cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal, outro.

RULES:
1. NEVER state that the page is blank if there is any trace of ink. Transcribe whatever is possible.
2. Map every block: footnotes, critical apparatus, marginal notes. Omission is a serious failure.
3. The root tag must have the attribute estado="com_texto" or estado="vazio".
4. BBOX: x1,y1,x2,y2 (scale 0-1000).
5. The OCR drafts are hints — verify visually before using them. Tesseract makes mistakes with scripts, diacritics, and ligatures. The llm_ocr may hallucinate structure and content; the image is always the ground truth.
6. Two-column layout: transcribe the entire left column first, then the right. Full-width headers and footers stay at the visual position they occupy.
7. Do not translate, normalise, or invent. Use [ilegivel] only for individual words, never for whole blocks.

Layout note: Letters A, B, C, D placed vertically in the centre gutter are nota_marginal section identifiers.

Output format:
<pagina estado="com_texto">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    literal transcription
  </bloco>
  <notas>complex scripts or relevant corrections if made</notas>
</pagina>

Return ONLY the XML.
""".strip()


PROMPT_CORRECAO_LLM_VS_TESSERACT = """
Act as a palaeography expert (Patrologia). Transcribe the image to faithful XML, prioritising the image over the OCR drafts (Tesseract/LLM).

Guidelines:
1. State: Use `vazio` only if there is no ink; otherwise, `com_texto`.
2. Layout: Map every block (cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal). Central letters A, B, C, D are `nota_marginal`.
3. Flow: Transcribe the left column, then the right. BBOX on scale 0-1000.
4. Fidelity: Translation and normalisation are forbidden. Use `[ilegivel]` only for individual words.
5. Scripts: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto.

Output format (ONLY XML):
<pagina estado="com_texto/vazio" tipo="capa_ou_guarda/texto/gravura">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    literal transcription
  </bloco>
  <notas>technical details or corrections</notas>
</pagina>
""".strip()


USER_PROMPT_CORRECAO_LLM_VS_TESSERACT = """
<tesseract>
{tesseract_text}
</tesseract>

<llm_ocr>
{llm_ocr}
</llm_ocr>

Proceed as instructed in the system prompt. Return only the XML without markdown.
Pay attention to the columns and the gutter (if any); A, B, C, D identifiers must be in their own nota_marginal block. Warning: do NOT place section identifiers inside the column text.
""".strip()

USER_PROMPT_VERIFY_LLM_VS_TESSERACT = """
<rascunho_ocr>
{tesseract_text}
</rascunho_ocr>

<llm_ocr>
{llm_ocr}
</llm_ocr>

Proceed as instructed in the system prompt. Return only the XML without markdown.
Pay attention to the columns and the gutter (if any); A, B, C, D identifiers must be in their own nota_marginal block. Warning: do NOT place section identifiers inside the column text.
""".strip()

USER_PROMPT_VERIFY_TESSERACT = """
<rascunho_ocr>
{tesseract_text}
</rascunho_ocr>

Proceed as instructed in the system prompt. Return only the XML without markdown.
Pay attention to the columns and the gutter (if any); A, B, C, D identifiers must be in their own nota_marginal block. Warning: do NOT place section identifiers inside the column text.
""".strip()

PROMPT_LLM_JUDGE = """
You are an expert in palaeography and transcription of historical documents (Patrologia Graeca, Latina et Orientalis).
Compare the image with the transcription in the <ocr> tag and assess its fidelity.

# Evaluation criteria

**Fidelity** — how well the transcription reflects what is in the image:
- alta: main text correct, minimal errors or only in difficult scripts
- media: partial errors, minor omissions, but structure preserved
- baixa: significant errors, omitted blocks, script confusion
- descartar: transcription unrecognisable or completely incorrect

**Usability** — whether the text is usable for producing summaries:
- alta: semantics preserved, main terms identifiable
- media: understandable with effort, occasional loss of meaning
- baixa: meaning compromised by accumulated errors
- descartar: unusable

# Important notes
- For blocks in non-Latin scripts (Armenian, Syriac), assess only:
  (a) whether the block is present in the transcription
  (b) whether the approximate extent seems compatible with the image
  (c) whether there is no obvious script confusion (e.g. Arabic characters in the middle of Armenian)
  Do not evaluate character-level correctness for these scripts.
- For blocks in French/Latin/English/Greek, evaluate semantics and fidelity fully.

Expected output format (return only valid XML filled according to the image judgement):
<avaliacao>
  <comentario>
    Specific comment per block: what is correct, what is wrong or omitted.
  </comentario>
  <idiomas_identificados>
    <idioma>armenio</idioma>
    <!-- other languages in PT-BR, cite the language identified in the text, e.g. "francês" -->
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
        system_prompt=prompt_llm_judge,
        reprocess=reprocess,
    )
    t5 = time.time()
    print(
        f"[{time.strftime('%H:%M:%S')}] {img_path.name} — LLM ({algorithm}) OCR: {t5 - t4:.3f}s"
    )

    print(f"PROMPT_LLM_JUDGE  {prompt_llm_judge}\nSAÍDA DA LLM:\n{txt}")



def find_text_by_num(text_dir: Path, page_num: int) -> Optional[Path]:
    """
    Retorna um texto já existente para a página, seja com UUID ou já normalizada.
    Ex.: *-170.txt corresponde à página 170.
    """
    stable_name = f"*{page_num:03d}.txt"
    matches = sorted(text_dir.glob(stable_name))
    if matches:
        return matches[0]
    matches = sorted(text_dir.glob(f"*-{page_num}.txt"))
    return matches[0] if matches else None



def process_img(
    img_path: Path,
    algorithm: str = "ollama",
    llm_model: str = main2.DEFAULT_LLM_MODEL,
    ollama_url: str = main2.DEFAULT_OLLAMA_URL,
    openai_base_url: str = main2.DEFAULT_OPENAI_BASE_URL,
    openai_api_key: str = os.getenv("OPENAI_API_KEY"),
    reprocess: bool = False,
    lang: str = "migne",
):
    print(f"Provider: {algorithm}; Language: {lang}; Model: {llm_model}")
    tesseract_db = main2.open_tesseract_cache_db()
    main2.init_tesseract_cache(tesseract_db)

    tesseractres = main2.strip_bidi_markers(
        main2.run_tesseract_cached(tesseract_db, img_path, lang=lang)
    ).strip()

    base_out = img_path.parent.parent
    images_dir = base_out / "images"
    text_dir = base_out / "text"

    print(f"Processing {img_path.name} {images_dir} {text_dir}...")

    page_num = main2.parse_page_num_from_filename(img_path)
    page_txt_path = find_text_by_num(text_dir, page_num) if page_num is not None else text_dir / (img_path.stem + ".txt")
    txt = ""
    print(page_txt_path)
    if page_txt_path.exists():
        txt = page_txt_path.read_text(encoding="utf-8", errors="ignore")

    txt = main2.clean_text_oriental(txt).strip()

    prompt_llm_judge = USER_PROMPT_CORRECAO_LLM_VS_TESSERACT.format(tesseract_text=tesseractres, llm_ocr=txt)

    print(f"PROMPT_REWRITE  {prompt_llm_judge}")

    t4 = time.time()
    txt = main2.llm_process_image_autoretry(
        img_path,
        provider=algorithm,
        model=llm_model,
        url=ollama_url,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        system_prompt=PROMPT_CORRECAO_LLM_VS_TESSERACT,
        user_prompt=prompt_llm_judge,
        reprocess=reprocess,
    )
    t5 = time.time()
    print(
        f"[{time.strftime('%H:%M:%S')}] {img_path.name} — LLM ({algorithm}) OCR: {t5 - t4:.3f}s"
    )

    print(f"PROMPT_REWRITE  {prompt_llm_judge}\n{txt}")


def main():
    #img_path = Path("teste/PO021/images/d306e9e5-aa17-41bf-9b1c-80816e85278c-728.png")
    img_path = Path('teste/PG001/images/PG001-129.png')
    process_img(
        img_path,
        # llm_model="qwen3.5:27b"
        llm_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        algorithm="openai",
    )


if __name__ == "__main__":
    main()
