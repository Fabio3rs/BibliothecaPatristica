from kraken import blla
from kraken.lib import models
from PIL import Image
import cv2
import os
import tempfile
import math


img = Image.open("teste/PL004/images/PL004-432.png")
segmentation = blla.segment(img)  # retorna linhas com baseline + bbox poligonal

print(segmentation)


def polygon_bbox(boundary):
	"""Return integer bbox (left, upper, right, lower) from polygon boundary."""
	xs = [p[0] for p in boundary]
	ys = [p[1] for p in boundary]
	left = int(math.floor(min(xs)))
	upper = int(math.floor(min(ys)))
	right = int(math.ceil(max(xs)))
	lower = int(math.ceil(max(ys)))
	return (left, upper, right, lower)


def save_line_crops(seg, src_image, out_dir=None, pad=2):
	"""Crop each detected line polygon and save to out_dir (created if None).

	Returns list of saved file paths.
	"""
	if out_dir is None:
		out_dir = tempfile.mkdtemp(prefix='pdfocr_crops_')
	os.makedirs(out_dir, exist_ok=True)

	saved = []
	for i, line in enumerate(seg.lines):
		if not getattr(line, 'boundary', None):
			continue
		bbox = polygon_bbox(line.boundary)
		# add small padding
		left = max(0, bbox[0] - pad)
		upper = max(0, bbox[1] - pad)
		right = min(src_image.width, bbox[2] + pad)
		lower = min(src_image.height, bbox[3] + pad)
		crop = src_image.crop((left, upper, right, lower))
		# create safe filename
		lid = getattr(line, 'id', f'{i}')
		fname = f'line_{i}_{lid}.png'
		path = os.path.join(out_dir, fname)
		crop.save(path)
		saved.append(path)
	return out_dir, saved


out_dir, files = save_line_crops(segmentation, img)
print(f"Saved {len(files)} line crops to: {out_dir}")
