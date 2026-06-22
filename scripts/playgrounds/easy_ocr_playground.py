import easyocr
import time
import cv2

# O 'gpu=True' agora vai usar o ROCm automaticamente
reader = easyocr.Reader(['la'], gpu=True) 

start = time.time()
result = reader.readtext('teste/PL004/images/PL004-432.png')
end = time.time()

print(f"Tempo de processamento: {end - start:.4f}s")
for (bbox, text, prob) in result:
    print(f"[{prob:.2f}] {text}")
