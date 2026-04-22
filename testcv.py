# ...existing code...
import cv2
import numpy as np
from pathlib import Path

imagepath = Path("teste/PL004/images/PL004-432.png")

image = cv2.imread(str(imagepath))
if image is None:
    raise SystemExit(f"Imagem não encontrada: {imagepath}")

gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
_, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
kernel = np.ones((5, 5), np.uint8)
dilation = cv2.dilate(thresh, kernel, iterations=1)

contours, _ = cv2.findContours(dilation, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
for cnt in contours:
    x, y, w, h = cv2.boundingRect(cnt)
    # Aqui você tem sua caixa de texto aproximada
    print((x, y, w, h))
    cv2.rectangle(image, (x, y), (x + w, y + h), (0, 255, 0), 2)

# Mostrar janelas (exige servidor gráfico / DISPLAY configurado)
cv2.namedWindow("image", cv2.WINDOW_NORMAL)
cv2.imshow("image", image)
cv2.namedWindow("thresh", cv2.WINDOW_NORMAL)
cv2.imshow("thresh", thresh)
cv2.namedWindow("dilation", cv2.WINDOW_NORMAL)
cv2.imshow("dilation", dilation)

print("Pressione qualquer tecla na janela para fechar.")
cv2.waitKey(0)
cv2.destroyAllWindows()
# ...existing code...
