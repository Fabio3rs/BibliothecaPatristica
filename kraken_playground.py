from kraken import blla
from kraken.lib import models
from PIL import Image

img = Image.open("teste/PL004/images/PL004-432.png")
segmentation = blla.segment(img)  # retorna linhas com baseline + bbox poligonal


print(segmentation)
