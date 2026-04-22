import os
import layoutparser as lp
import cv2
import matplotlib.pyplot as plt
from PIL import ImageFont
import numpy as np

# --- Monkey-patch para compatibilidade Pillow 10+ ---
# getsize() foi removido; substituir por getbbox()
_orig_truetype = ImageFont.truetype

def _patched_truetype(*args, **kwargs):
    font = _orig_truetype(*args, **kwargs)
    if not hasattr(font, 'getsize'):
        font.getsize = lambda text: (
            font.getbbox(text)[2] - font.getbbox(text)[0],
            font.getbbox(text)[3] - font.getbbox(text)[1]
        )
    return font

ImageFont.truetype = _patched_truetype

# Forçar o DEFAULT_FONT_OBJECT a também ter getsize
import layoutparser.visualization as lpviz
if not hasattr(lpviz.DEFAULT_FONT_OBJECT, 'getsize'):
    lpviz.DEFAULT_FONT_OBJECT.getsize = lambda text: (
        lpviz.DEFAULT_FONT_OBJECT.getbbox(text)[2] - lpviz.DEFAULT_FONT_OBJECT.getbbox(text)[0],
        lpviz.DEFAULT_FONT_OBJECT.getbbox(text)[3] - lpviz.DEFAULT_FONT_OBJECT.getbbox(text)[1]
    )

from layoutparser.visualization import draw_box
# --- fim do patch ---

config_path = os.path.expanduser('~/models/publaynet/config.yml')
model_path  = os.path.expanduser('~/models/publaynet/model_final.pth')

model = lp.Detectron2LayoutModel(
    config_path = config_path,
    model_path  = model_path,
    label_map   = {0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"},
    extra_config=["MODEL.ROI_HEADS.SCORE_THRESH_TEST", 0.6]
)



image_path = "teste/PL011/images/PL011-088.png"
image = cv2.imread(image_path)
image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

layout = model.detect(image)

viz = draw_box(image, layout, show_element_type=True)
plt.imshow(viz)
plt.axis('off')
plt.savefig("debug_layout.png", bbox_inches='tight', dpi=150)
plt.close()
print("Debug visual salvo em debug_layout.png")

text_blocks = lp.Layout([b for b in layout if b.type in ['Text', 'Title']])
text_blocks = text_blocks.sort(key=lambda b: (b.block.x_1, b.block.y_1))

ocr_agent = lp.TesseractAgent(languages='lat+grc', config='--psm 6')

print(f"\n--- Iniciando OCR de {image_path} ---\n")

for i, block in enumerate(text_blocks):
    segment_image = block.pad(left=10, right=10, top=10, bottom=10).crop_image(image)

    gray = cv2.cvtColor(segment_image, cv2.COLOR_RGB2GRAY)
    binary_img = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)

    width = block.block.x_2 - block.block.x_1
    block_type = "NOTA_MARGINAL" if width < 60 else "COLUNA_TEXTO"

    res = ocr_agent.detect(binary_img)

    print(f"Bloco {i} [{block_type}] (x={block.block.x_1}):")
    print(res)
    print("-" * 30)
