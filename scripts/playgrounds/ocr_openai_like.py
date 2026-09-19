import base64
import json
import os
from pathlib import Path
import requests

image_path = "teste/PG001/images/PG001-018.png"

DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
DEFAULT_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


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

Layout note: Letters A, B, C, D placed vertically in the centre gutter are nota_marginal paragraph identifiers.

Output format:
<pagina estado="com_texto">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    literal transcription
  </bloco>
  <notas>complex scripts or important issues</notas>
</pagina>

Return ONLY the XML.
""".strip()

img_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")


def openai_process_image(
    image_path: Path,
    model: str = DEFAULT_OPENAI_MODEL,
    base_url: str = DEFAULT_OPENAI_BASE_URL,
    api_key: str | None = None,
    current_try: int = 1,
    system_prompt: str = PROMPT,
    user_prompt: str = "Proceda conforme instruções do system.",
    mime: str = "image/png",
    reprocess: bool = False,
):
    """
    Faz OCR via endpoint de chat da OpenAI usando apenas requests.
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não encontrado no ambiente.")

    if current_try > 4:
        system_prompt += (
            f"\nEsta é uma tentativa de recuperação, número {current_try}\n"
        )

    print(
        f"{len(img_b64) / 1024:.2f} KB de imagem para {image_path.name}; try {current_try} (openai)"
    )

    image_url = {"url": f"data:{mime};base64,{img_b64}"}

    if "gpt-5" in model:
        image_url["detail"] = "high"

    if ("gpt-5.4" in model) or "gpt-5.6" in model:
        # A partir do 5.4 original rende a melhor qualidade disponível
        image_url["detail"] = "original"

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": user_prompt,
                },
                {"type": "image_url", "image_url": image_url},
            ],
        },
    ]

    payload = {
        "model": model,
        "messages": messages,
        "top_p": 1.0,
        "presence_penalty": 0.0,
        "frequency_penalty": 0.0,
    }

    timeout = 400

    if "gpt-5" not in model:
        payload["temperature"] = 0.05
    else:
        if reprocess:
            payload["reasoning_effort"] = "medium"
            timeout = 1200
        else:
            payload["reasoning_effort"] = "medium"

        payload["service_tier"] = "flex"

    url = base_url.rstrip("/") + "/chat/completions"

    r = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        data=json.dumps(payload),
        timeout=timeout,
    )

    if r.status_code != 200:
        print(r.text)
    print(r.headers)
    r.raise_for_status()
    data = r.json()

    response = data["choices"][0]["message"]["content"]
    response = (
        response.replace("<ilegivel>", "[ilegivel]")
        .replace("</ilegivel>", "")
        .replace("<ilegivel/>", "[ilegivel]")
    )

    return response


LLM_For_PG001_018 = """<pagina estado="com_texto">
  <bloco tipo="cabecalho" script="latino" bbox="100,10,960,30">
35 AD S. CLEMENTEM I PAPAM 30
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="100,30,960,155">
quisquam transire ad nos; quos et ipsos mundos appellavit, cum ait: Oceanus intransmeabilis est homi-
nibus, et hi qui trans ipsum sunt mundi, qui his eisdem dominatoris Dei dispositionibus gubernantur. Pau-
cisque interjectis...: Ex his tamen quæ Clemens visus est indicare cum dicit : Oceanus qui intransmea-
bilis est hominibus, et hi mundi qui post ipsum sunt : qui post ipsum sunt mundos pluraliter nominans,
quos et eadem Dei providentia agi regique significat, semina quædam nobis hujusmodi intelligentiæ vi-
detur aspergere, quo putetur omnis quidem universitas eorum quæ sunt atque subsistunt, cœlestium et
supercoelestium, terrenorum infernorumque, unus et perfectus mundus generaliter dici : intra quem vel
a quo (si qui illi sunt) putandi sunt contineri. Epist. 1, cap. 20.
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="310,155,750,180">
IDEM, in Ezechiel., cap. 8, tom. III. pag. 422, A.
  </bloco>
  <bloco tipo="texto_principal" script="grego" bbox="100,180,500,225">
Φησι δὲ ὁ Κλήμης· Ὠκεανὸς ἀπέρατος ἀνθρώποις,
καὶ οἱ μετ' αὐτὸν κόσμοι τοσαύταις διαταγαῖς τοῦ
Δεσπότου διοικοῦνται.
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="510,180,960,225">
Dicit quoque Clemens : Oceanus impermeabilis
hominibus, et qui post eum mundi, tantis Domini or-
dinationibus gubernantur. Epist. 1, cap. 20.
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="200,225,860,245">
IDEM, in Joan., tom. IX. part. II, pag. 145, edit. Huet. Colon., 1685.
  </bloco>
  <bloco tipo="texto_principal" script="grego" bbox="100,245,500,335">
Μεμαρτύρηται δὲ καὶ παρὰ τοῖς Ἔθνεσιν, ὅτι πολ-
λοὶ τινες λοιμικῶν ἐνακψάντων νοσημάτων ἑαυτοὺς
πάδνια ὑπὲρ τοῦ κοινοῦ παραδεδώκασι· καὶ παραδέ-
χεται ταῦθ' οὕτως γεγονέναι οὐκ ἀλόγως πιστεύσας
ταῖς ἱστορίαις ὁ πιστὸς Κλήμης, ὑπὸ Παύλου μαρτυ-
ρούμενος, λέγοντος· Μετὰ Κλήμεντος, κ. τ. λ.
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="510,245,960,360">
Relatum autem atque testatum apud gentiles est,
multos cum pestilentes morbi grassarentur, se ip-
sos ut victimas pro patria tradidisse : atque hæc
ita evenisse admittit non sine causa, historiis cre-
dens Clemens ille fidelis, cui testimonium perhibet
Paulus dicens : Cum Clemente, etc. Epist. 1,
cap. 55.
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="450,365,550,385">
IV.
  </bloco>
  <bloco tipo="cabecalho" script="latino" bbox="130,385,860,405">
Rufino interprete. EUSEBIUS, Hist. eccl., lib. III, cap. 16 Valesio interprete.
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="100,405,330,625">
Hujus Clementis epistola habe-
tur ad Corinthios scripta, præci-
pua plane et valde mirabilis :
quam velut ex persona Romanæ
Ecclesiæ dictavit, cum dissensio
apud Corinthios fuisset exorta.
Quam epistolam in plurimis Ec-
clesiis publice legi, et veterum et
nostris etiam temporibus constat.
Verum de seditione facta apud
Corinthios ac dissensione plebis,
testis valde fidelis Hegesippus
indicat.
  </bloco>
  <bloco tipo="texto_principal" script="grego" bbox="355,405,635,630">
Τούτου δὴ οὖν τοῦ Κλήμεντος
ὁμολογουμένη μία ἐπιστολὴ φέρε-
ται, μεγάλη τε καὶ θαυμασία, ἣν
ὡς ἀπὸ τῆς Ῥωμαίων Ἐκκλησίας
τῇ Κορινθίων διετυπώσατο, στά-
σεως τηνικάδε κατὰ τὴν Κόρινθον
γενομένης. Ταύτην δὲ καὶ ἐν πλεί-
σταις Ἐκκλησίαις ἐπὶ τοῦ κοινοῦ δε-
δημοσιευμένην πάλαι τε καὶ καθ'
ἡμᾶς αὐτοὺς ἔγνωμεν. Καὶ ὅτι γε
κατὰ τὸν δηλούμενον, τὰ τῆς Κο-
ρινθίων κεκίνητο στάσεως, ἀξιό-
χρεως μάρτυς ὁ Ἡγήσιππος. Lege
NICEPHORUM, lib. III, cap. 16.
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="645,405,960,630">
Hujus igitur Clementis exstat
epistola uno consensu recepta,
eximia prorsus atque mirabilis,
quam nomine Ecclesiæ Romanæ
ad Corinthiorum Ecclesiam scrip-
sit, cum apud eos gravis esset
exorta dissensio. Hanc in pleris-
que Ecclesiis et nostra et supe-
riori memoria palam recitari con-
suevisse comperimus. Porro su-
pradicti Clementis tempore sedi-
tionem inter Corinthios esse com-
motam locupletissimus testis est
Hegesippus.
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="100,635,335,935">
Rufino interprete.
Clemens in epistola quam Co-
rinthiis scribit, meminit Episto-
læ Pauli ad Hebræos, et utitur
ejus testimoniis. Unde constat,
quod Apostolus tanquam Hebræis
mittendam patrio eam sermone
conscripserit, et ut quidam tra-
dunt, Lucam evangelistam, alii
autem hunc ipsum Clementem in-
terpretatum esse. Quod et magis
verum est, quia et stylus ipse
epistolæ Clementis cum hac con-
cordat, et sensus nimirum utrius-
que Scripturæ plurimam similitu-
dinem ferunt. Dicitur tamen esse
et alia Clementis epistola, cujus
nos notitiam non accepimus.
  </bloco>
  <bloco tipo="texto_principal" script="grego" bbox="355,635,635,965">
IDEM, ibid., lib. III, cap. 38.
Τοῦ Κλήμεντος ἐν τῇ ἀνωμολο-
γημένῃ παρὰ πᾶσιν, ἣν ἐκ προσώ-
που τῆς Ῥωμαίων Ἐκκλησίας τῇ
Κορινθίων διετυπώσατο. Ἐν ᾗ τῆς
πρὸς Ἑβραίους πολλὰ νοήματα πα-
ραθεὶς, ἧς δὲ καὶ αὐτολέξει ῥητοῖς
τισὶν ἐξ αὐτῆς χρησάμενος, σαφέ-
στατα παρίστησιν ὅτι μὴ νέον ὑπάρ-
χει τὸ σύγγραμμα. Ὅθεν εἰκότως
ἔδοξεν, αὐτὸ τοῖς λοιποῖς ἐγκατα-
λεχθῆναι γράμμασι τοῦ Ἀποστό-
λου. Ἑβραίοις γὰρ διὰ τῆς πατρίου
γλώσσης ἐγγράφως ὡμιληκότος τοῦ
Παύλου, οἱ μὲν τὸν εὐαγγελιστὴν
Λουκᾶν, οἱ δὲ τὸν Κλήμεντα τοῦτον
αὐτὸν, ἑρμηνεῦσαι λέγουσι τὴν γρα-
φήν. Ὃ καὶ μᾶλλον εἴη ἂν ἀληθὲς,
τῷ τὸν ὅμοιον τῆς φράσεως χαρα-
κτῆρα τήν τε τοῦ Κλήμεντος ἐπι-
στολὴν, καὶ τὴν πρὸς Ἑβραίους
  </bloco>
  <bloco tipo="texto_principal" script="latino" bbox="645,635,960,965">
Valesio interprete.
Clementis illa (epistola) ab omni
bus recepta, quam Ecclesiæ Ro-
manæ nomine ad Corinthiorum
Ecclesiam scripsit. In qua cum
multas inserat sententias desump-
tas ex Epistola ad Hebræos, iis-
demque interdum verbis utatur,
satis indicat opus illud nequa-
quam recens esse. Quamobrem
cum reliquis Apostoli scriptis non
sine causa hoc etiam recensitum
videtur. Cum enim ad Hebræos
patrio sermone scripsisset Paulus,
alii Lucam evangelistam, alii
hunc de quo loquimur Clementem,
Epistolam illam esse interpreta-
tum ferunt. Quod quidem verius
videtur, cum præsertim et Cle-
mentis, et illa ad Hebræos Epistola
eumdem habeant stylum, nec
  </bloco>
  <notas>Pagina de texto patristico trilíngue em colunas: parte superior em largura total (latim), seguida de grego-esquerda / latim-direita; parte inferior em três colunas (Rufino-latino, Eusébio-grego, Valesio-latino). Grego com tipografia do séc. XVII, acentuação densa e ligaduras; algumas palavras de leitura incerta por empastamento ([ilegivel] pontual não aplicado, transcrição diplomática aproximada). Texto inferior truncado no pé da página, continua na página seguinte.</notas>
</pagina>
""".strip()


Tesseract_for_PG001_018 = """
53 AD S. CLEMENTEM 11 PAPAM 56

quisquam transire ad nos ; quos et ipsos mundos appellavit, cum ait: Oceanus intransmeabilis est homi-
nibus, et hi qui trans ipsum sunt mundi, qui his eisdem dominatoris Dei dispositionibus gubernantur. Pau-
cisque interjectis... . Ex his tamen quæ Clemens visus est indicare cum dicit : Oceanus qui intransmea.
bilis est hominibus, et hi mundi qui post ipsum sunt: qui post ipsum sunt mundos pluraliter nominans,
quos et eadem Dei providentia agi regique significat, semina quædam nobis hujusmodi intelligentiæ vi-
detur aspergere, quo putetur omnis quidem universitas eorum quæ sunt atque subsistunt, cœlestium et
supercœlestium, terrenorum infernorumque, unus et perfectus mundus generaliter dici : intra quem vel

a quo (si qui illi sunt) putandi sunt contineri. Epist. 1, cap. 20.
or ix, in Ezechiel., cap. 8, tom. III, pag. 422, A.

φΦησὶ δὲ ὁΚλήμγmς· Ὁκεανὐὺς ἀπέρατυς ἀνθρώποις,
καὶ cἰ μετ' αὐτὸν κόσμοειτοσαύταις διαταγαῖς τοῦ

Αεσπότου ὃιοικοῦνται.

Dicit quoque Clemens: Oceanus impermeabilis
hominibus, et qui post eum mundi, tantis Domini or-

dinationibus qubernantur. Epist. 1, cap. 20.

mx, in Joan., tm. IX. part. u, pag. 143, edit. Huet. Colon., 1685.

" Μεμαρτύρηται δὲ καὶ παρὰ τοῖς ἔθνεσιν, ὅτι πολ-
λοί τινες λοιμικῶν ἐνσκηψάντων νοσημάτων ἑαυτοὺς
αφὸὰγια ὐὑπὲρ τοῦ κοινοῦ παραδεδώλκασι · καὶ παρσδέ-
χεται ταῦθ' οὕτως γεγονέναι οὐκ ἀλόγς πιστεύσας
ταῖς ἰστορίαις ὁ πιστὸς Κλήμης, ὑπὸ Παύλου μαρτυ-
ρούμενος, λέγοντος · Μετὰ Κήμεντοσς, κχ. τ. λ.

Rufino interprete.

Hujus Clementis epistola habe-
tur ad Corinthios scripta, preci-
pua plane et valde mirabilis:
quam velut ex persona Romanæ
Ecclesiæ dctavit, cum dissensio
apud Corinthios fuisset exorta.
Quam epistolam in plurimis Ec-
clesiis publice legi, et veterum et
nostris etiam temperibus constat.
Verum de seditione facta apud
Corinthios ac dissensione plebis,
testis valde fidelis Hegesippus
indicat.

Rufino interprete.

Clemens in epistola quam Co-
rinthiis scribit, meminit Episto-
ec Pauli ad cbeos, et utitur
ejus testimoniis,. nde constat ,
quud Apostolus taunquam Hrbr huis
mittendam patrio eam sermone
conscripserit, et ut quidam tra-
dunt, Lucam evangelistam , alii
autem hunc ipsum Clementem in-
terpretaliim esse. Quod et magis
verum est, quia et stylus ipse
epistolæ Clementis cum hac con-
cordat , et sensus nimirum utrius-
que Scripturæ plurimam similitu-
dinem ferunt. Dicitur tamen esse
et alia lementis epistola, cujus
nos notitiam non accepimus.

cap. 55.

» 1INs

Ecsr xius, Hist, ei., lib. m, cap. 16

Τούτου δὴ οὖν τοῦ Κλήμεντος
ὁμολογουμέυη μία ἐπιστολὴ φέρε-
ται, μεγάλη τε καὶ θαυμασία, ἣν
ὡς ἀπὸ τῆς Ῥωώμαίων Ἐκκλτσίας
τῆ Κορινθίων διετυπώσατο, στά-
σεως τηνικάδε κατὰ τὴν Κόρινθον
γενομένυης. Ταύτην δὲ καὶ ἐν πλεί-
σταιςἘκκλησίαις ἐπὶ τοῦ κοινοῦ δε-
δημοσιευμένην πάλαι τε καὶ καθ'
ἡμᾶς αὐτοὺς ἔγνωμεν. Καὶ ὅτι γε
κατὰ τὸν δηλούμενον, τὰ τῆς Κο-
ρινθίων κεκίνητο στάσεως, ἀξέ-
κρεως μάρτυς ὡ ἨἌγήσεππος. Lege
Niceruonvox, lib. in, cap. 16.

rx, ibid., lib. m, cap. a3.

Τοῦ Κλήμεντος ἐν τῇ ἀνωμολο-
ημένῃ παρὰ πᾶσιν, ἡν ἐκ προσύ-
που τῆς Γωμαίων Ἐκκλτσίας τῆ
Κοριηίων ὑιετυπώσατο. Ἐνἦ τῆς
πρὸς Ἐραίους πολλὰ νοήματα πα-
ρθεὶς, ἔδη δὲ καὶ αὐτολεξεὶ ῥητοῖς
τισιν ἐξ αὐτῆς χρσάμενος, σαφέ-
στατα παρίστησιν ὅὄτι μὴ νέον ὑπάρ-
χει τὸ σύγγραμμα. Ὅθεν εἰκότως
ἐἔδοξεν, αὐτὸ τοῖς λοιπαῖς ἐγκατα-
λεχθῆναι γράμμασι τοῦ Ἀποστέ-
λου. ἘἙθραίοις γὰρδιὰ τῆς πατρίου
γλώττης ἐγγράφως ὡμιληκότος τοῦ
Παυλου, οἱ μὲν τὶλν εὐαγγελιστὴν
Λουκᾶν, οἱ δὲ τὸν Κλήμεντα τοῦτον
αὐτὸν, ἐρμηνεῦσαι λέγουσι τὴν γρα-
φν. Ὃ καὶ μᾶλλον εἴη ἂν ἀλτθὲς,
τῷ τὸν ὅμοιον τῆς φράσεως χαρα-
κτῆρα τῆν τε τοῦ Κλήμεντος ἐπι-
στολην, καὶ τὴν πὲὸὺς Ἑραίους

Relatum autem atque testatum apud gentiles est,
multos cum pestilentes morbi grassarentur, se ip-
sos ut victimas pro patria tradidisse: atque hæec
ita evenisse admittit non sine causa, historiis cre-
dens Clemens ille fidelis, cui testimonium perhibet
Paulus dicens :

Cum Clemente , etc. Epist. 1,

Vutesio iterprete.

Hujus igiur Clementis exstat
epistola uo conscnsu recepta ,
eximia prorsus atque mirabilis ,
quam nomine Ecclesiæ tomame
ad Curinthiorum Ecclesiam scrip-
sit, cum apud eos gravis esset
exorla dissensio. Ilanc in pleris-
que Ecclesiis et nostra et supe-
riori memoria palam recitari con-
suevisse comperimus. Porro su-
pradicti Clementis tempore sedi-
tionem inter Corinthios esse com-
molam locupletissimus testis est
Hegesippus.

Valesio mterpr.ete.

Clementis illa tepistoia] ab omni
bus recep, quam Ecelesiæ Ro-
manæ nomine ad Corintniorum
Ecclesiam scripsit. In qua cum
multus inserat sententias desump-
tas ex Epistola ad Hebr os, iis-
demque interdum verbis utatur,
satis indicat opus illud nequa-
quam recens esse. Quamobrem
cum reliquis Apostoli scriptis non
sine causa hoc etiam recensitum
vidctur. Cum enim ad lebraus
patrio sermone scripsisset Paulus,
alii Lucam evangelistam , alii
hunc de quo loquimur Clementem,
Epistolam iltlam esse interpreta-
tum ferunt. Quod quidem verius
videtur. cum ppraæsertim et Cle-
mentis, et illa ad Heræos Epistom
cumdem habeat stylum, nec

""".strip()


prompt_llm_judge = PROMPT_CORRECAO_LLM_VS_TESSERACT
user_prompt = USER_PROMPT_CORRECAO_LLM_VS_TESSERACT.format(
    tesseract_text=Tesseract_for_PG001_018, llm_ocr=LLM_For_PG001_018
)

res = openai_process_image(
    Path(image_path), system_prompt=prompt_llm_judge, user_prompt=user_prompt
)
print(res)
