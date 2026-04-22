import base64
import json
from pathlib import Path
import requests

image_path = "teste/PL011/images/PL011-088.png"

prompt = """
Você é um especialista em paleografia e transcrição de documentos históricos (Patrologia Graeca, Latina et Orientalis).

Analise a imagem e produza uma transcrição XML fiel. Identifique primeiro o tipo de página (capa/guarda, texto, gravura).

Se a página estiver realmente em branco: <pagina estado="vazio" tipo="capa_ou_guarda" />

Scripts permitidos: latino, grego, copta, siriaco, cirilico, ethiopico, armenio, arabe, hebraico, misto, desconhecido.
Tipos permitidos: cabecalho, texto_principal, aparato_critico, rodape, nota, nota_marginal, outro.

REGRAS:
1. NUNCA afirme que a página está em branco se houver qualquer vestígio de tinta. Transcreva o que for possível.
2. Mapeie todos os blocos: rodapés, aparato crítico, notas marginais. Omissão é falha grave.
3. Tag raiz deve ter atributo estado="com_texto" ou estado="vazio".
4. BBOX: x1,y1,x2,y2 (escala 0-1000).
5. Em duas colunas: transcreva a coluna esquerda inteira, depois a direita. Cabeçalhos e rodapés span-completo ficam na posição visual que ocupam.
6. Não traduza, não normalize, não invente. Use [ilegivel] apenas por palavra, nunca por bloco.

Nota sobre layout: Letras A, B, C, D na vertical central são nota_marginal de identificação do parágrafo.

Formato de saída:
<pagina estado="com_texto">
  <bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
    transcrição literal
  </bloco>
  <notas>scripts complexos ou correções relevantes se foram realizadas</notas>
</pagina>

Retorne APENAS o XML.
""".strip()

img_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")

payload = {
    "model": "qwen3.5:27b",
    "messages": [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": "Proceda conforme instruções do system.",
            "images": [img_b64],
        },
    ],
    "stream": False,
}

r = requests.post(
    "http://localhost:11434/api/chat",
    headers={"Content-Type": "application/json"},
    data=json.dumps(payload),
    timeout=300,
)

r.raise_for_status()
data = r.json()
print(data["message"]["content"])
