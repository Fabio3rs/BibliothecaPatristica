#!/usr/bin/env python3
"""Classifica páginas escaneadas como capa, vazia ou com texto.

Heurística baseada em:
- proporção de tinta (binarização adaptativa);
- densidade de bordas (Canny);
- variabilidade de cor (útil para capas ilustradas).

Requer OpenCV (`pip install opencv-python`).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np


@dataclass
class PageStats:
    path: Path
    shape: Tuple[int, int, int]
    mean_gray: float
    std_gray: float
    ink_ratio: float
    edge_ratio: float
    sat_std: float
    delta_e_border_center: float
    hue_hist_corr: float
    classification: str


def read_image(path: Path, max_dim: int) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(path)
    h, w = img.shape[:2]
    scale = max_dim / max(h, w) if max(h, w) > max_dim else 1.0
    if scale != 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def compute_stats(
    img: np.ndarray, border_frac: float, center_frac: float
) -> Tuple[float, float, float, float, float, float, float]:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    std = float(gray.std())

    # "tinta": porcentagem de pixels pretos após threshold adaptativo inverso
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10
    )
    ink_ratio = float(binary.mean() / 255)

    # densidade de bordas
    edges = cv2.Canny(gray, 50, 150)
    edge_ratio = float(edges.mean() / 255)

    # variabilidade de cor para achar capas/ilustrações
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    sat_std = float(hsv[:, :, 1].std())

    # borda vs centro
    h, w = img.shape[:2]
    bw = int(min(h, w) * border_frac)
    ch = int(h * center_frac)
    cw = int(w * center_frac)
    y0 = (h - ch) // 2
    x0 = (w - cw) // 2
    center_mask = np.zeros((h, w), dtype=np.uint8)
    center_mask[y0 : y0 + ch, x0 : x0 + cw] = 1

    border_mask = 1 - center_mask
    # erode border to avoid overlap bleed
    if bw >= 3:
        kernel = np.ones((3, 3), np.uint8)
        border_mask = cv2.erode(border_mask, kernel, iterations=bw // 3)
        center_mask = 1 - border_mask

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    border_lab = lab[border_mask == 1]
    center_lab = lab[center_mask == 1]
    border_mean = border_lab.mean(axis=0)
    center_mean = center_lab.mean(axis=0)
    diff = border_mean.astype(np.float32) - center_mean.astype(np.float32)
    delta_e = float(np.sqrt((diff**2).sum()))

    border_h = hsv[:, :, 0][border_mask == 1]
    center_h = hsv[:, :, 0][center_mask == 1]
    hist_border, _ = np.histogram(border_h, bins=30, range=(0, 180), density=True)
    hist_center, _ = np.histogram(center_h, bins=30, range=(0, 180), density=True)
    hue_corr = float(np.corrcoef(hist_border, hist_center)[0, 1])

    return mean, std, ink_ratio, edge_ratio, sat_std, delta_e, hue_corr


def classify(
    ink_ratio: float,
    edge_ratio: float,
    sat_std: float,
    mean_gray: float,
    delta_e: float,
    hue_corr: float,
) -> str:
    """Heurística ajustável para capa/vazia/texto.

    - vazia: quase nenhuma tinta nem borda
    - capa: grande variação de cor (sat_std alta) e pouca tinta; não depende de ser clara
    - texto: caso padrão
    """
    if ink_ratio < 0.003 and edge_ratio < 0.004 and hue_corr > 0.95 and delta_e < 8:
        return "vazia"

    # Capas podem ser claras ou escuras; a condição chave é variedade de cor,
    # pouca tinta real e bordas relativamente baixas.
    if sat_std > 25 and ink_ratio < 0.08 and edge_ratio < 0.05 and hue_corr > 0.9 and delta_e < 12:
        return "capa"

    return "texto"


def process(path: Path, max_dim: int, border_frac: float, center_frac: float) -> PageStats:
    img = read_image(path, max_dim)
    mean_gray, std_gray, ink_ratio, edge_ratio, sat_std, delta_e, hue_corr = compute_stats(
        img, border_frac, center_frac
    )
    return PageStats(
        path=path,
        shape=img.shape,
        mean_gray=mean_gray,
        std_gray=std_gray,
        ink_ratio=ink_ratio,
        edge_ratio=edge_ratio,
        sat_std=sat_std,
        delta_e_border_center=delta_e,
        hue_hist_corr=hue_corr,
        classification=classify(ink_ratio, edge_ratio, sat_std, mean_gray, delta_e, hue_corr),
    )


def run(args: argparse.Namespace) -> None:
    stats: List[PageStats] = []
    for p in args.images:
        try:
            stats.append(process(Path(p), args.max_dim, args.border_frac, args.center_frac))
        except Exception as e:
            print(f"[erro] {p}: {e}")

    if not stats:
        return

    header = f"{'classe':8s} {'ink%':>7s} {'edge%':>7s} {'sat_std':>7s} {'ΔEbc':>7s} {'hCorr':>6s} {'mean':>6s}  caminho"
    print(header)
    for s in stats:
        line = (
            f"{s.classification:8s} "
            f"{s.ink_ratio*100:6.2f} "
            f"{s.edge_ratio*100:6.2f} "
            f"{s.sat_std:7.2f} "
            f"{s.delta_e_border_center:7.2f} "
            f"{s.hue_hist_corr:6.3f} "
            f"{s.mean_gray:6.1f}  "
            f"{s.path}"
        )
        print(line)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", help="Arquivos de imagem a classificar")
    parser.add_argument("--max-dim", type=int, default=2000, help="Maior lado para amostragem (default 2000px)")
    parser.add_argument(
        "--border-frac",
        type=float,
        default=0.08,
        help="Largura da borda como fração do menor lado (default 0.08)",
    )
    parser.add_argument(
        "--center-frac",
        type=float,
        default=0.5,
        help="Tamanho do centro como fração (default 0.5)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run(args)


if __name__ == "__main__":
    main()
