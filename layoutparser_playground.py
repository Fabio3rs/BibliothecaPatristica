import os
import layoutparser as lp
import cv2
import matplotlib.pyplot as plt
from PIL import ImageFont
import numpy as np
from pathlib import Path

try:
    # pdf2image é opcional — usamos somente quando a entrada for PDF
    from pdf2image import convert_from_path
except Exception:
    convert_from_path = None  # type: ignore


def read_input(path: Path, dpi: int, page: int | None = None) -> np.ndarray:
    """Lê uma imagem do disco; se for PDF, converte a página (pdf2image).

    Retorna imagem BGR (OpenCV).
    """
    if path.suffix.lower() in (".pdf",):
        if convert_from_path is None:
            raise RuntimeError(
                "pdf2image não está disponível; instale pdf2image para ler PDFs"
            )
        pages = convert_from_path(str(path), dpi=dpi)
        if not pages:
            raise FileNotFoundError(f"nenhuma página extraída de {path}")
        idx = page or 1
        if idx < 1 or idx > len(pages):
            raise IndexError(f"página {idx} fora do intervalo (1..{len(pages)})")
        pil = pages[idx - 1]
        arr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        return arr

    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"Não consegui ler imagem: {path}")
    return img


def ensure_gray(img: np.ndarray) -> np.ndarray:
    return img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def apply_erode(img: np.ndarray, ksize: int, iterations: int) -> np.ndarray:
    gray = ensure_gray(img)
    kernel = np.ones((ksize, ksize), np.uint8)
    return cv2.erode(gray, kernel, iterations=iterations)


def show_preview(before: np.ndarray, after: np.ndarray, title: str = "Preview") -> None:
    # converte para BGR 3-ch se necessário
    def to_bgr(i: np.ndarray) -> np.ndarray:
        return i if len(i.shape) == 3 else cv2.cvtColor(i, cv2.COLOR_GRAY2BGR)

    b = to_bgr(before)
    a = to_bgr(after)
    h = max(b.shape[0], a.shape[0])
    w = max(b.shape[1], a.shape[1])

    # pad
    def pad(img):
        ih, iw = img.shape[:2]
        canvas = np.zeros((h, w, 3), dtype=img.dtype)
        canvas[:ih, :iw] = img
        return canvas

    grid = cv2.hconcat([pad(b), pad(a)])
    cv2.putText(grid, "before", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.putText(
        grid, "after", (w + 10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2
    )
    cv2.imshow(title + " (ESC para fechar)", grid)
    key = cv2.waitKey(0)
    if key == 27:
        cv2.destroyAllWindows()


# --- Monkey-patch para compatibilidade Pillow 10+ ---
# getsize() foi removido; substituir por getbbox()
_orig_truetype = ImageFont.truetype


def _patched_truetype(*args, **kwargs):
    font = _orig_truetype(*args, **kwargs)
    if not hasattr(font, "getsize"):
        font.getsize = lambda text: (
            font.getbbox(text)[2] - font.getbbox(text)[0],
            font.getbbox(text)[3] - font.getbbox(text)[1],
        )
    return font


ImageFont.truetype = _patched_truetype

# Forçar o DEFAULT_FONT_OBJECT a também ter getsize
import layoutparser.visualization as lpviz

if not hasattr(lpviz.DEFAULT_FONT_OBJECT, "getsize"):
    lpviz.DEFAULT_FONT_OBJECT.getsize = lambda text: (
        lpviz.DEFAULT_FONT_OBJECT.getbbox(text)[2]
        - lpviz.DEFAULT_FONT_OBJECT.getbbox(text)[0],
        lpviz.DEFAULT_FONT_OBJECT.getbbox(text)[3]
        - lpviz.DEFAULT_FONT_OBJECT.getbbox(text)[1],
    )

from layoutparser.visualization import draw_box

# --- fim do patch ---

config_path = os.path.expanduser("~/models/PrimaLayout/config.yml")
model_path = os.path.expanduser("~/models/PrimaLayout/model_final.pth")

model = lp.Detectron2LayoutModel(
    config_path=config_path,
    model_path=model_path,
    label_map={
        1: "TextRegion",
        2: "ImageRegion",
        3: "TableRegion",
        4: "MathsRegion",
        5: "SeparatorRegion",
        6: "OtherRegion",
    },
    extra_config=[
        "MODEL.ROI_HEADS.SCORE_THRESH_TEST",
        0.5,  # Só aceita o que tiver 70% de certeza
        "MODEL.ROI_HEADS.NMS_THRESH_TEST",
        0.3,
        "MODEL.RPN.NMS_THRESH",
        0.3,
    ],
)


def rotate(img: np.ndarray) -> np.ndarray:
    arr = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

    thresh = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    # 1. REMOVER RUÍDO E JUNTAR LINHAS
    # Criamos um kernel largo para "derreter" as palavras em linhas horizontais
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
    dilate = cv2.dilate(thresh, kernel, iterations=2)

    # 2. ENCONTRAR CONTORNOS
    contours, _ = cv2.findContours(dilate, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    angles = []
    for cnt in contours:
        # Ignorar ruídos pequenos e as bordas gigantescas do papel
        area = cv2.contourArea(cnt)
        if 500 < area < 50000:  # Ajuste esses valores conforme necessário
            rect = cv2.minAreaRect(cnt)
            angle = rect[-1]
            rw, rh = rect[1]

            # Normalização do ângulo para OpenCV 4.5+
            # minAreaRect retorna o ângulo do eixo mais curto.
            # Para linhas de texto horizontais (largura >> altura), o eixo
            # curto é vertical → ângulo fica em torno de -90°.
            # Corrigimos para obter o ângulo real da linha (próximo de 0°).
            if rw < rh:
                angle = angle + 90  # roda 90° para alinhar com o eixo longo
            # Após normalização, descarta ângulos absurdos (>10°): provavelmente
            # contornos de elementos decorativos, linhas de margem etc.
            if abs(angle) <= 10:
                angles.append(angle)

    # 3. MÉDIA DOS ÂNGULOS
    # Usamos a mediana para evitar que um contorno doido puxe o valor
    if len(angles) > 0:
        median_angle = np.median(angles)
    else:
        median_angle = 0.0  # Sem inclinação detectada

    print(f"Ângulo real detectado: {median_angle}")

    # 4. ROTACIONAR
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )

    return rotated


image_path = "teste/PL011/images/PL011-088.png"
image = cv2.imread(image_path)
image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

image = rotate(image)
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

# 2. Binarização para limpar ruídos (Otsu)
_, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
_, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV) # Texto vira branco (255)

# 2. Histograma Vertical (Soma de pixels brancos por coluna)
# Isso vai mostrar claramente dois picos (as colunas) e um vale (o meio)
vertical_projection = np.sum(binary, axis=0)

# 3. Histograma Horizontal (Soma de pixels brancos por linha)
# Isso vai mostrar onde começa o cabeçalho e onde termina o rodapé
horizontal_projection = np.sum(binary, axis=1)


print(f"Projeção Vertical: {vertical_projection}")
print(f"Projeção Horizontal: {horizontal_projection}")

layout = model.detect(image)

viz = draw_box(image, layout, show_element_type=True)
plt.imshow(viz)
plt.axis("off")
plt.savefig("debug_layout.png", bbox_inches="tight", dpi=300)
plt.close()
print("Debug visual salvo em debug_layout.png")

text_blocks = lp.Layout([b for b in layout if b.type in ["Text", "Title"]])
text_blocks = text_blocks.sort(key=lambda b: (b.block.x_1, b.block.y_1))

ocr_agent = lp.TesseractAgent(languages="lat+grc", config="--psm 6")

print(f"\n--- Iniciando OCR de {image_path} ---\n")

for i, block in enumerate(text_blocks):
    segment_image = block.pad(left=10, right=10, top=10, bottom=10).crop_image(image)

    gray = cv2.cvtColor(segment_image, cv2.COLOR_RGB2GRAY)
    binary_img = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )

    width = block.block.x_2 - block.block.x_1
    block_type = "NOTA_MARGINAL" if width < 60 else "COLUNA_TEXTO"

    border_size = 50

    binary_img = cv2.copyMakeBorder(
        binary_img,
        top=border_size,
        bottom=border_size,
        left=border_size,
        right=border_size,
        borderType=cv2.BORDER_CONSTANT,
        value=[255, 255, 255],
    )

    res = ocr_agent.detect(binary_img)

    print(f"Bloco {i} [{block_type}] (x={block.block.x_1}):")
    print(res)
    print("-" * 30)
