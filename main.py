from pdf2image import convert_from_path
import pytesseract
from PIL import Image
import cv2
import os
import numpy as np
import cv2
import pytesseract
import pyarrow
import pandas as pd


def preprocess_image(img):
    # converter para tons de cinza
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # binarização simples (threshold) — ajustar conforme qualidade
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    # talvez aplicar denoise ou suavização
    # thresh = cv2.medianBlur(thresh, 3)
    return thresh


def ocr_pdf(path_pdf, dpi=600, lang="lat"):
    # converter páginas para imagens
    print("Convertendo PDF para imagens...")
    pages = convert_from_path(
        path_pdf,
        dpi=dpi,
        thread_count=max(4, os.cpu_count() - 1),
        use_pdftocairo=True,  # opcional
    )
    texts = []
    for i, page in enumerate(pages):
        print(f"Processando página {i+1}/{len(pages)}...")
        # converter PIL Image para formato compatível com OpenCV
        page_cv = cv2.cvtColor(np.array(page), cv2.COLOR_RGB2BGR)
        pre = preprocess_image(page_cv)
        # PIL de volta para OCR
        pil_pre = Image.fromarray(pre)
        txt = pytesseract.image_to_string(pil_pre, lang=lang)
        texts.append(txt)
    return texts


if __name__ == "__main__":
    pdf_file = "PriscianiInstitutionumGrammaticarumLibriI-xiihertz.1855.pdf"
    texts = ocr_pdf(
        pdf_file, dpi=600, lang="lat"
    )  # usar "lat" se tiver dicionário latino
    all_text = "\n\n".join(texts)
    # gravar em arquivo
    with open("texto_extraido.txt", "w", encoding="utf-8") as f:
        f.write(all_text)
