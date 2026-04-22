import torch
from PIL import Image, ImageDraw, ImageFont
from transformers import LayoutLMv3Processor, LayoutLMv3ForTokenClassification
import numpy as np

# 1. Configuração do Modelo e Processador
# O processador 'apply_ocr=True' usa o Tesseract internamente para extrair palavras e caixas
model_id = "nielsr/layoutlmv3-finetuned-funsd" 
processor = LayoutLMv3Processor.from_pretrained("microsoft/layoutlmv3-base", apply_ocr=True)
model = LayoutLMv3ForTokenClassification.from_pretrained(model_id)

# 2. Carregamento da Imagem
image_path = "teste/PL011/images/PL011-088.png"
image = Image.open(image_path).convert("RGB")

# 3. Processamento (Geração de tensores para o modelo)
encoding = processor(
    image, 
    return_tensors="pt", 
    max_length=512, 
    truncation=True, 
    stride=128, 
    return_overflowing_tokens=True, 
    padding="max_length"
)

# Remove o campo que o modelo não espera
overflow_to_sample_mapping = encoding.pop("overflow_to_sample_mapping")

# Inferência em batch (processa todas as janelas da página)
with torch.no_grad():
    outputs = model(**encoding)

# 5. Recuperação de predições e rótulos
logits = outputs.logits
predictions = logits.argmax(-1).squeeze().tolist()
labels = model.config.id2label

# 6. Mapeamento de Coordenadas (Denormalização)
# O LayoutLMv3 trabalha com coordenadas de 0 a 1000
width, height = image.size
boxes = encoding.bbox.squeeze().tolist()

def unnormalize_box(bbox, width, height):
    return [
        width * (bbox[0] / 1000),
        height * (bbox[1] / 1000),
        width * (bbox[2] / 1000),
        height * (bbox[3] / 1000),
    ]

# 7. Visualização dos Resultados
draw = ImageDraw.Draw(image)
# Cores para diferentes classes (Header, Question, Answer, Other)
label_color_map = {
    "HEADER": "red",
    "QUESTION": "blue",
    "ANSWER": "green",
    "OTHER": "orange"
}

for prediction, box in zip(predictions, boxes):
    predicted_label = labels[prediction].upper()
    if box == [0, 0, 0, 0]: continue # Pula tokens de preenchimento
    
    real_box = unnormalize_box(box, width, height)
    color = label_color_map.get(predicted_label, "black")
    
    draw.rectangle(real_box, outline=color, width=2)
    # Opcional: draw.text((real_box[0], real_box[1]-10), predicted_label, fill=color)

# Salva o novo debug para comparar com o do Detectron2
image.save("debug_layout_lmv3.png")
print("Visualização salva em debug_layout_lmv3.png")
