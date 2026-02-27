import base64
import json
from pathlib import Path
import requests

image_path = "teste/PO002/images/d0d8ff89-f30e-4dc9-bf09-0bf404b4d336-239.png"

prompt = """
Você é um transcritor de documentos históricos.

Analise a página da imagem e identifique os blocos visuais de texto.

Para cada bloco produza um elemento XML contendo:
- o script principal do bloco
- o bounding box aproximado da região
- a transcrição literal do texto visível

Use apenas estas tags:

<pagina>
<bloco tipo="..." script="..." bbox="x1,y1,x2,y2">
<notas>

Cada bloco textual deve seguir este formato:

<bloco script="SCRIPT" bbox="x1,y1,x2,y2">
transcrição literal
</bloco>

Valores permitidos para script:
- latino
- grego
- siriaco
- cirilico
- ethiopico
- misto
- desconhecido

Valores permitidos para tipo:
- cabecalho
- texto_principal
- aparato_critico
- rodape
- nota
- nota_marginal
- outro

Regras importantes:

1. bbox deve ser x1,y1,x2,y2 com valores inteiros de 0 a 1000 relativos à imagem.
2. Preserve a ordem visual dos blocos na página, de cima para baixo.
3. Um bloco textual deve corresponder a uma região visual coerente, normalmente contendo
   várias palavras, uma linha completa, ou várias linhas contíguas do mesmo trecho.
4. Não crie um bloco separado para elementos isolados como:
   - um único número
   - um único caractere
   - um único símbolo tipográfico
   - marcadores críticos isolados
   - números de linha ou de página isolados
5. Elementos pequenos e isolados devem ser incorporados ao bloco textual mais próximo,
   quando fizer sentido visualmente.
6. Prefira blocos maiores e coerentes em vez de muitos blocos pequenos.
7. Preserve as quebras de linha do texto.
8. Não traduza.
9. Não normalize ortografia.
10. Não translitere entre alfabetos.
11. Não invente texto.
12. Quando algo estiver ilegível, use [ilegivel].
13. Use script="misto" apenas quando houver duas ou mais escritas visivelmente relevantes no mesmo bloco.
   Se houver apenas palavras isoladas de outro script dentro de um bloco majoritário, use o script majoritário.
14. Se o script não puder ser identificado com confiança, use script="desconhecido".
15. Use <notas> apenas para explicar ambiguidades importantes de segmentação,
    leitura ou identificação de script.
16. Retorne apenas o XML, sem markdown e sem comentários fora das tags.

Formato de saída:
<pagina>
  <bloco script="..." bbox="x1,y1,x2,y2">
    ...
  </bloco>
  <bloco script="..." bbox="x1,y1,x2,y2">
    ...
  </bloco>
  <notas>
    ...
  </notas>
</pagina>
""".strip()

img_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("utf-8")

payload = {
    "model": "qwen3.5:397b-cloud",
    "messages": [
        {
            "role": "user",
            "content": prompt,
            "images": [img_b64]
        }
    ],
    "stream": False
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

