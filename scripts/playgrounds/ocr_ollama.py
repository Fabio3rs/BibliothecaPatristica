import base64
import json
from pathlib import Path
import requests

image_path = "teste/PG001/images/PG001-018.png"

prompt = """
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

payload = {
    "model": "qwen3.5:397b-cloud",
    # "model": "gemma4:cloud",
    # "model": "kimi-k2.6:cloud",
    # "model": "qwen3.5:27b",
    "messages": [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": "Proceda conforme instruções do system. Analise a imagem e produza a transcrição XML.",
            "images": [img_b64],
        },
    ],
    "options": {"max_soft_tokens": 560},
    "stream": False,
}

r = requests.post(
    "http://localhost:11434/api/chat",
    headers={"Content-Type": "application/json"},
    data=json.dumps(payload),
    timeout=900,
)

r.raise_for_status()
data = r.json()
print(r.headers)
print("------------------------------")
print(data)
print(data["message"]["content"])
