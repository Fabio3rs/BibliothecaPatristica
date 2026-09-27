"""Raster clones from original pixels and stored evidence; never rewrites inputs."""
import io
from pathlib import Path

from .store import digest

LAYERS = {"blocks", "anchors", "lines", "words", "differences", "corrections"}


def render(result, revision, layers=None, opacity=.4):
    from PIL import Image, ImageDraw, ImageFont

    layers = LAYERS if layers is None else set(layers)
    if layers - LAYERS or not 0 <= opacity <= 1:
        raise ValueError("Camadas ou opacidade inválidas")
    image_bytes = Path(result["image_path"]).read_bytes()
    if digest(image_bytes) != result["image_hash"]:
        raise ValueError("Imagem alterada desde a ingestão")
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", base.size)
    draw = ImageDraw.Draw(overlay)
    width, height = base.size
    stroke = max(1, width // 1000)

    def box(coords, color):
        if coords is None:
            return
        x1, y1, x2, y2 = coords
        draw.rectangle((x1 * width / 1000, y1 * height / 1000, x2 * width / 1000, y2 * height / 1000),
                       outline=(*color, round(255 * opacity)), fill=(*color, round(40 * opacity)), width=stroke)

    if "blocks" in layers:
        for b in result["llm"]["blocks"]:
            box(b["bbox"], (20, 104, 186))
    if "anchors" in layers:
        for ev in result["alignment"]["region_evidence"].values():
            box(ev["anchor_bbox"], (5, 129, 106))
    for node in result["tesseract"]["nodes"]:
        if "lines" in layers and node["level"] == 4:
            box(node["bbox"], (108, 85, 167))
        if "words" in layers and node["level"] == 5:
            box(node["bbox"], (38, 119, 96))
    tokens = {t["id"]: t for t in result["alignment"]["tesseract_tokens"]}
    for link in result["alignment"]["links"]:
        color = None
        if "differences" in layers and (link["uncertain"] or link["status"] not in ("equal", "normalized_equal")):
            color = (195, 58, 49)
        if "corrections" in layers and any(t in revision["words"] for t in link["llm_ids"]):
            color = (181, 91, 0)
        if color:
            for tid in link["tesseract_ids"]:
                for piece in tokens[tid]["pieces"]:
                    box(piece["bbox"], color)
    if "corrections" in layers:
        for coords in revision["boxes"].values():
            box(coords, (181, 91, 0))
    base = Image.alpha_composite(base, overlay).convert("RGB")
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = ImageFont.truetype(str(font_path), max(14, width // 100)) if font_path.exists() else ImageFont.load_default()
    line_height = max(22, width // 65)
    notes = [f"OCR offline | {result['label']} | revisão {revision['id']} | {result['key'][:12]}",
             "Azul: bbox LLM | Verde: aproximação | Vermelho: diferença | Laranja: ajuste"]
    if "corrections" in layers:
        left = {t["id"]: t["text"] for t in result["alignment"]["llm_tokens"]}
        for tid, text in revision["words"].items():
            value = f"{tid}: {left[tid]} → {text}"
            chunk = max(20, width // max(8, width // 160))
            notes.extend(value[i:i + chunk] for i in range(0, len(value), chunk))
        for bid, coords in revision["boxes"].items():
            notes.append(f"{bid}: bbox {coords}")
    clone = Image.new("RGB", (width, height + line_height * (len(notes) + 1)), "white")
    clone.paste(base)
    annotation = ImageDraw.Draw(clone)
    for i, text in enumerate(notes):
        annotation.text((12, height + i * line_height + 8), text, fill="#243237", font=font)
    output = io.BytesIO()
    clone.save(output, format="PNG")
    return output.getvalue()
