#!/usr/bin/env python3
"""
ocr_overlay.py — Desenha bounding boxes do Tesseract sobre a imagem original.

Detecta automaticamente:
  - Coluna esquerda (texto Latin/principal)
  - Coluna direita (texto Grego/paralelo)
  - Gutter central (zona entre colunas)
  - Títulos centralizados que interrompem o gutter
  - Parágrafos (level 3) como blocos maiores
  - Linhas (level 4) em tom intermediário
  - Palavras (level 5) individuais

Uso:
  python ocr_overlay.py imagem.jpg out.txt [opções]

Opções:
  --tsv PATH        Arquivo TSV (padrão: detectado automaticamente)
  --output PATH     Imagem de saída (padrão: overlay.png)
  --conf FLOAT      Confiança mínima para palavras (padrão: 20)
  --level {3,4,5}   Nível mínimo a desenhar (padrão: 4)
  --alpha FLOAT     Transparência dos fills 0-1 (padrão: 0.25)
  --scale FLOAT     Escala de saída para não explodir a memória (padrão: 1.0)
  --no-labels       Omite rótulos de texto nas caixas
"""

import argparse
import io
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np


# ─────────────────────────────────────────────────────
# Cores BGR
# ─────────────────────────────────────────────────────
C_PAR_LEFT   = (180,  80,  30)   # azul escuro  → parágrafos coluna esquerda
C_PAR_RIGHT  = ( 30, 120,  30)   # verde escuro → parágrafos coluna direita
C_PAR_CENTER = (160,  30, 160)   # roxo         → bloco centralizado (título/gutter)
C_LINE_LEFT  = (230, 140,  60)   # azul médio
C_LINE_RIGHT = ( 60, 200,  60)   # verde médio
C_LINE_CTR   = (220,  80, 220)   # roxo médio
C_WORD_LEFT  = (255, 200, 100)   # azul claro
C_WORD_RIGHT = (100, 255, 100)   # verde claro
C_WORD_CTR   = (255, 140, 255)   # lilás
C_GUTTER     = ( 30, 200, 230)   # amarelo → linha do gutter


# ─────────────────────────────────────────────────────
# Leitura do TSV / JSON
# ─────────────────────────────────────────────────────

def load_records(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    if raw.lstrip().startswith("{"):
        obj = json.loads(raw)
        if "stdout" in obj:
            return _parse_tsv(obj["stdout"])
        if "level" in obj:
            return _dict_of_lists_to_records(obj)
        raise ValueError("JSON não reconhecido")
    return _parse_tsv(raw)


def _parse_tsv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text.strip()), delimiter="\t")
    rows = []
    for r in reader:
        rec = {}
        for k, v in r.items():
            try:    rec[k] = int(v)
            except ValueError:
                try: rec[k] = float(v)
                except ValueError: rec[k] = v
        rows.append(rec)
    return rows


def _dict_of_lists_to_records(d: dict) -> list[dict]:
    keys = list(d.keys())
    n = len(d[keys[0]])
    return [{k: d[k][i] for k in keys} for i in range(n)]


# ─────────────────────────────────────────────────────
# Detecção de gutter
# ─────────────────────────────────────────────────────

def detect_gutter(records: list[dict], page_w: int, page_h: int) -> tuple[int, int]:
    """
    Retorna (gutter_left, gutter_right) em pixels.
    Estratégia: histograma horizontal de densidade de palavras.
    O gutter é o vale mais profundo na região central (30%–70% da largura).
    """
    words = [r for r in records if r.get("level") == 5
             and str(r.get("text", "")).strip()
             and float(r.get("conf", -1)) > 10]

    # Histograma de presença horizontal (resolução: 1% da largura)
    bins = 200
    hist = np.zeros(bins, dtype=float)
    for w in words:
        l = int(w["left"])
        r = l + int(w["width"])
        b_l = max(0, int(l / page_w * bins))
        b_r = min(bins - 1, int(r / page_w * bins))
        hist[b_l:b_r + 1] += 1

    # Suaviza
    kernel = np.ones(5) / 5
    hist_smooth = np.convolve(hist, kernel, mode="same")

    # Procura o mínimo na zona central (30%–70%)
    z_l = int(bins * 0.30)
    z_r = int(bins * 0.70)
    central = hist_smooth[z_l:z_r]
    rel_min = int(np.argmin(central))
    min_bin = z_l + rel_min

    # Expande ao redor do mínimo enquanto densidade < 20% do pico
    threshold = hist_smooth.max() * 0.20
    gl = min_bin
    gr = min_bin
    while gl > 0 and hist_smooth[gl - 1] < threshold:
        gl -= 1
    while gr < bins - 1 and hist_smooth[gr + 1] < threshold:
        gr += 1

    gutter_left  = int(gl / bins * page_w)
    gutter_right = int(gr / bins * page_w)
    # Garante largura mínima de gutter
    if gutter_right - gutter_left < page_w * 0.03:
        mid = (gutter_left + gutter_right) // 2
        gutter_left  = mid - int(page_w * 0.02)
        gutter_right = mid + int(page_w * 0.02)

    return gutter_left, gutter_right


def classify_box(left: int, width: int, gl: int, gr: int) -> str:
    """Classifica caixa como 'left', 'right' ou 'center'."""
    cx = left + width // 2
    # Centro da caixa está na zona esquerda?
    if cx < gl:
        return "left"
    # Centro na zona direita?
    if cx > gr:
        return "right"
    # No gutter: verifica se a caixa É centralizada (abrange ambos os lados)
    if left < gl and (left + width) > gr:
        return "center"   # título que cruza o gutter
    return "center"


# ─────────────────────────────────────────────────────
# Desenho com fill transparente
# ─────────────────────────────────────────────────────

def draw_box(img: np.ndarray, overlay: np.ndarray,
             x: int, y: int, w: int, h: int,
             color: tuple, alpha: float,
             label: str = "", font_scale: float = 0.4,
             thickness: int = 1):
    x2, y2 = x + w, y + h
    # Fill transparente no overlay
    cv2.rectangle(overlay, (x, y), (x2, y2), color, -1)
    # Borda sólida na imagem
    cv2.rectangle(img, (x, y), (x2, y2), color, thickness)
    # Rótulo
    if label:
        ly = max(y - 3, 10)
        cv2.putText(img, label, (x + 2, ly),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, 1,
                    cv2.LINE_AA)


# ─────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="Imagem original (jpg/png/tif)")
    ap.add_argument("tsv",   help="Arquivo TSV/JSON do Tesseract")
    ap.add_argument("--output",    "-o", default="overlay.png")
    ap.add_argument("--conf",      type=float, default=20.0)
    ap.add_argument("--level",     type=int, default=4, choices=[3, 4, 5])
    ap.add_argument("--alpha",     type=float, default=0.20)
    ap.add_argument("--scale",     type=float, default=1.0)
    ap.add_argument("--no-labels", action="store_true")
    args = ap.parse_args()

    # ── Carrega imagem ──
    img_path = Path(args.image)
    if not img_path.exists():
        sys.exit(f"Imagem não encontrada: {img_path}")
    img_orig = cv2.imread(str(img_path))
    if img_orig is None:
        sys.exit(f"Não consegui abrir a imagem: {img_path}")

    if args.scale != 1.0:
        h, w = img_orig.shape[:2]
        img_orig = cv2.resize(img_orig, (int(w * args.scale), int(h * args.scale)))

    img    = img_orig.copy()
    overlay = img_orig.copy()

    # ── Carrega OCR ──
    records = load_records(Path(args.tsv))

    # Dimensões da página conforme OCR
    page_recs = [r for r in records if r.get("level") == 1]
    if page_recs:
        page_w = int(page_recs[0]["width"])
        page_h = int(page_recs[0]["height"])
    else:
        page_h, page_w = img_orig.shape[:2]

    # Fator de escala OCR→imagem (caso a imagem tenha sido redimensionada
    # ou o OCR foi feito em resolução diferente)
    img_h, img_w = img_orig.shape[:2]
    sx = img_w / page_w
    sy = img_h / page_h

    print(f"Página OCR: {page_w}x{page_h}  Imagem: {img_w}x{img_h}  Escala: {sx:.3f}x{sy:.3f}")

    # ── Detecta gutter ──
    gl_px, gr_px = detect_gutter(records, page_w, page_h)
    print(f"Gutter detectado: x={gl_px}–{gr_px}px  ({gl_px/page_w*100:.1f}%–{gr_px/page_w*100:.1f}%)")

    # Escala gutter para coordenadas da imagem
    gl_img = int(gl_px * sx)
    gr_img = int(gr_px * sx)

    # Desenha linha do gutter
    cv2.line(img, (gl_img, 0), (gl_img, img_h), C_GUTTER, 1)
    cv2.line(img, (gr_img, 0), (gr_img, img_h), C_GUTTER, 1)
    # Zona do gutter com fill suave
    gutter_zone = overlay[0:img_h, gl_img:gr_img]
    gutter_zone[:] = (int(C_GUTTER[0]*0.3), int(C_GUTTER[1]*0.3), int(C_GUTTER[2]*0.3))

    # ── Parágrafos (level 3) ──
    if args.level <= 3:
        pars = [r for r in records if r.get("level") == 3]
        for p in pars:
            x = int(int(p["left"])   * sx)
            y = int(int(p["top"])    * sy)
            w = int(int(p["width"])  * sx)
            h = int(int(p["height"]) * sy)
            zone = classify_box(int(p["left"]), int(p["width"]), gl_px, gr_px)
            color = {"left": C_PAR_LEFT, "right": C_PAR_RIGHT, "center": C_PAR_CENTER}[zone]
            lbl = "" if args.no_labels else f"P{p['par_num']}"
            draw_box(img, overlay, x, y, w, h, color, args.alpha,
                     label=lbl, font_scale=0.5, thickness=2)

    # ── Linhas (level 4) ──
    if args.level <= 4:
        lines = [r for r in records if r.get("level") == 4]
        for ln in lines:
            x = int(int(ln["left"])   * sx)
            y = int(int(ln["top"])    * sy)
            w = int(int(ln["width"])  * sx)
            h = int(int(ln["height"]) * sy)
            zone = classify_box(int(ln["left"]), int(ln["width"]), gl_px, gr_px)
            color = {"left": C_LINE_LEFT, "right": C_LINE_RIGHT, "center": C_LINE_CTR}[zone]
            lbl = "" if args.no_labels else f"L{ln['line_num']}"
            draw_box(img, overlay, x, y, w, h, color, args.alpha,
                     label=lbl, font_scale=0.35, thickness=1)

    # ── Palavras (level 5) ──
    if args.level <= 5:
        words = [r for r in records if r.get("level") == 5
                 and str(r.get("text", "")).strip()
                 and float(r.get("conf", -1)) >= args.conf]
        for wrd in words:
            x = int(int(wrd["left"])   * sx)
            y = int(int(wrd["top"])    * sy)
            w = int(int(wrd["width"])  * sx)
            h = int(int(wrd["height"]) * sy)
            zone = classify_box(int(wrd["left"]), int(wrd["width"]), gl_px, gr_px)
            color = {"left": C_WORD_LEFT, "right": C_WORD_RIGHT, "center": C_WORD_CTR}[zone]
            lbl = "" if args.no_labels else str(wrd["text"])
            draw_box(img, overlay, x, y, w, h, color, args.alpha * 0.5,
                     label=lbl, font_scale=0.30, thickness=1)

    # ── Composição final ──
    result = cv2.addWeighted(overlay, args.alpha, img, 1 - args.alpha, 0)

    out_path = Path(args.output)
    cv2.imwrite(str(out_path), result)
    print(f"Salvo em: {out_path}  ({img_w}x{img_h}px)")

    # ── Legenda no terminal ──
    print("\nLegenda:")
    print("  🟦 Azul   → coluna esquerda (Latin)")
    print("  🟩 Verde  → coluna direita  (Grego)")
    print("  🟪 Roxo   → bloco central  (título/gutter interrupt)")
    print("  🟨 Amarelo → zona do gutter")


if __name__ == "__main__":
    main()
