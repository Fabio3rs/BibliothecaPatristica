#!/usr/bin/env python3
"""
bg_binarize_playground.py
Playground OpenCV para identificar o fundo pelas margens e comparar
estratégias de binarização para o OCR da BibliothecaPatristica.

Uso: python3 bg_playground.py <input.png> [output_prefix]
"""

import sys
import cv2
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════════════
# 1. AMOSTRAGEM E ESTIMATIVA DE FUNDO
# ══════════════════════════════════════════════════════════════════════════════

def sample_margins(gray: np.ndarray, margin_px: int = 60) -> dict:
    """Coleta pixels de cada uma das 4 margens separadamente."""
    h, w = gray.shape
    mh = min(margin_px, h // 8)
    mw = min(margin_px, w // 8)
    return {
        'top':    gray[:mh, :].ravel(),
        'bottom': gray[-mh:, :].ravel(),
        'left':   gray[:, :mw].ravel(),
        'right':  gray[:, -mw:].ravel(),
    }


def estimate_background(
    margin_dict: dict,
    pct: float = 85.0,
    outlier_sigma: float = 1.5,
) -> tuple[float, dict, bool]:
    """
    Estima a cor global de fundo a partir dos percentis das margens.

    Detecta outliers (ex: spine escuro em scans de livros abertos) e os descarta.
    Retorna (bg_global, bg_por_margem, houve_outlier).
    """
    per_margin = {k: float(np.percentile(v, pct)) for k, v in margin_dict.items()}
    vals = np.array(list(per_margin.values()))
    mean_v, std_v = vals.mean(), vals.std()

    # Descarta margens que destoam mais de outlier_sigma * std da média
    threshold_dev = max(8.0, outlier_sigma * std_v)
    valid_vals = [v for v in vals if abs(v - mean_v) <= threshold_dev]
    outlier = len(valid_vals) < len(vals)

    bg_global = float(np.mean(valid_vals)) if valid_vals else mean_v
    return bg_global, per_margin, outlier


def background_from_corners(gray: np.ndarray, corner_px: int = 80, pct: float = 90.0) -> float:
    """
    Alternativa: só usa os 4 cantos (menos suscetível a texto de margem).
    """
    h, w = gray.shape
    c = min(corner_px, h // 8, w // 8)
    corners = np.concatenate([
        gray[:c, :c].ravel(),
        gray[:c, -c:].ravel(),
        gray[-c:, :c].ravel(),
        gray[-c:, -c:].ravel(),
    ])
    return float(np.percentile(corners, pct))


# ══════════════════════════════════════════════════════════════════════════════
# 2. MODELOS DE FUNDO (para iluminação não-uniforme)
# ══════════════════════════════════════════════════════════════════════════════

def bg_morph_open(gray: np.ndarray, ksize: int = 71) -> np.ndarray:
    """Estimativa de fundo via abertura morfológica (remove texto, mantém fundo)."""
    k = ksize | 1  # garante ímpar
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.morphologyEx(gray, cv2.MORPH_OPEN, ker)


def bg_blur_gaussian(gray: np.ndarray, ksize: int = 101) -> np.ndarray:
    """Estimativa simples via blur grande."""
    k = ksize | 1
    return cv2.GaussianBlur(gray, (k, k), 0)


def bg_bilateral(gray: np.ndarray) -> np.ndarray:
    """
    Bilateral: preserva bordas fortes (texto nítido) mas suaviza fundo.
    Útil para páginas com muita textura de papel.
    """
    # Iterado 3x para efeito mais forte
    out = gray.copy()
    for _ in range(3):
        out = cv2.bilateralFilter(out, d=15, sigmaColor=40, sigmaSpace=40)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 3. NORMALIZAÇÃO POR MODELO DE FUNDO
# ══════════════════════════════════════════════════════════════════════════════

def normalize_scalar(gray: np.ndarray, bg_value: float) -> np.ndarray:
    """
    Normalização global: escala linear para que bg_value → 255.
    Funciona bem quando a iluminação é uniforme.
    """
    scale = 255.0 / max(bg_value, 1.0)
    return np.clip(gray.astype(np.float32) * scale, 0, 255).astype(np.uint8)


def normalize_divide(gray: np.ndarray, bg_model: np.ndarray) -> np.ndarray:
    """
    Divisão pixel-a-pixel pelo modelo de fundo.
    Compensa iluminação não-uniforme (vinheta, etc.).
    """
    g = gray.astype(np.float32)
    b = np.clip(bg_model.astype(np.float32), 1.0, None)
    return np.clip(g / b * 255.0, 0, 255).astype(np.uint8)


def normalize_subtract(gray: np.ndarray, bg_model: np.ndarray) -> np.ndarray:
    """
    Subtração do modelo de fundo + stretch ao full range.
    Abordagem alternativa que preserva a forma linear do contraste.
    """
    g = gray.astype(np.float32)
    b = bg_model.astype(np.float32)
    diff = b - g  # > 0 onde há tinta
    diff_clipped = np.clip(diff, 0, None)
    # Inverte e normaliza: muito tinta → 0, sem tinta → 255
    if diff_clipped.max() > 0:
        normalized = 255.0 - (diff_clipped / diff_clipped.max() * 255.0)
    else:
        normalized = np.full_like(g, 255.0)
    return normalized.astype(np.uint8)


# ══════════════════════════════════════════════════════════════════════════════
# 4. BINARIZAÇÕES
# ══════════════════════════════════════════════════════════════════════════════

def binarize_otsu(gray: np.ndarray) -> tuple[np.ndarray, float]:
    thresh, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary, float(thresh)


def binarize_adaptive(gray: np.ndarray, block_size: int = 31, c: int = 4) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.adaptiveThreshold(
        enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, c
    )


def binarize_sauvola(gray: np.ndarray, win: int = 35, k: float = 0.25) -> np.ndarray:
    """Sauvola thresholding (via boxFilter, rápido)."""
    f = gray.astype(np.float32)
    m = cv2.boxFilter(f, -1, (win, win))
    m2 = cv2.boxFilter(f * f, -1, (win, win))
    std = np.sqrt(np.clip(m2 - m * m, 0.0, None))
    R = 128.0
    thr = m * (1.0 + k * (std / R - 1.0))
    return np.where(f < thr, 0, 255).astype(np.uint8)


def binarize_combined(gray: np.ndarray, bg_model: np.ndarray,
                      bg_global: float, block_size: int = 31, c: int = 4) -> np.ndarray:
    """
    Estratégia combinada:
    1. Divide pelo modelo local de fundo
    2. CLAHE suave
    3. Adaptativo
    4. Remove ruído de fundo (máscara de bg > threshold)
    """
    norm = normalize_divide(gray, bg_model)
    clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(norm)
    binary = cv2.adaptiveThreshold(
        enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, c
    )
    # Onde o fundo original é muito claro (> bg_global * 0.92), força branco
    bg_mask = (gray > bg_global * 0.92).astype(np.uint8)
    # Dilata um pouco o bg_mask para não cortar bordas de letras
    bg_mask_dilated = cv2.dilate(bg_mask, cv2.getStructuringElement(cv2.MORPH_RECT, (3,3)))
    # Intersecção: mantém pixel preto só se a binarização E o modelo concordam que não é fundo
    text_mask = (binary == 0).astype(np.uint8)
    text_mask_clean = text_mask & ~bg_mask_dilated
    result = np.full_like(binary, 255)
    result[text_mask_clean == 1] = 0
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 5. VISUALIZAÇÃO
# ══════════════════════════════════════════════════════════════════════════════

def mark_margin_zone(gray: np.ndarray, margin_px: int) -> np.ndarray:
    """Cria uma versão do gray com as zonas de margem marcadas em azul (para display RGB)."""
    h, w = gray.shape
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    mh = min(margin_px, h // 8)
    mw = min(margin_px, w // 8)
    # Overlay azul semi-transparente nas margens
    overlay = vis.copy()
    overlay[:mh, :] = (220, 150, 100)
    overlay[-mh:, :] = (220, 150, 100)
    overlay[:, :mw] = (220, 150, 100)
    overlay[:, -mw:] = (220, 150, 100)
    vis = cv2.addWeighted(vis, 0.6, overlay, 0.4, 0)
    return vis


def plot_margin_histograms(margin_dict: dict, per_margin_bg: dict, bg_global: float,
                           bg_corners: float, output_path: str):
    """Figura 1: histogramas por margem."""
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.suptitle(
        f"Distribuição de intensidade por margem  —  "
        f"fundo global estimado: {bg_global:.1f}  |  cantos: {bg_corners:.1f}",
        fontsize=12, fontweight='bold'
    )
    colors = dict(zip(['top','bottom','left','right'],
                      ['steelblue','tomato','forestgreen','goldenrod']))

    for ax, (name, pixels) in zip(axes.ravel(), margin_dict.items()):
        n, bins, _ = ax.hist(pixels, bins=80, color=colors[name], alpha=0.75, density=True)
        ax.axvline(per_margin_bg[name], color='black', ls='--', lw=2.0,
                   label=f'P85 desta margem = {per_margin_bg[name]:.0f}')
        ax.axvline(bg_global, color='red', ls=':', lw=1.8,
                   label=f'bg global = {bg_global:.0f}')
        ax.set_title(f'Margem {name}', fontsize=11, fontweight='bold')
        ax.set_xlabel('Intensidade (0=preto → 255=branco)')
        ax.set_ylabel('Densidade')
        ax.set_xlim(50, 260)
        ax.legend(fontsize=9)
        ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=110, bbox_inches='tight')
    plt.close()
    print(f"  → {output_path}")


def plot_model_comparison(gray, bg_morph, bg_gauss, margin_vis,
                          bg_global, output_path, crop=None):
    """Figura 2: modelos de fundo e normalizações."""
    if crop is not None:
        y0, y1, x0, x1 = crop
        gray = gray[y0:y1, x0:x1]
        bg_morph = bg_morph[y0:y1, x0:x1]
        bg_gauss = bg_gauss[y0:y1, x0:x1]
        margin_vis_crop = margin_vis[y0:y1, x0:x1]
    else:
        margin_vis_crop = margin_vis

    norm_scalar = normalize_scalar(gray, bg_global)
    norm_div_morph = normalize_divide(gray, bg_morph)
    norm_div_gauss = normalize_divide(gray, bg_gauss)
    norm_sub_morph = normalize_subtract(gray, bg_morph)

    fig, axes = plt.subplots(2, 4, figsize=(22, 11))
    fig.suptitle("Modelos de fundo e normalizações", fontsize=12, fontweight='bold')

    items = [
        (margin_vis_crop, "Zonas de margem amostradas\n(azul = pixels usados)", True),
        (bg_morph,        "Modelo morfológico\n(abertura kernel=71)", False),
        (bg_gauss,        "Modelo gaussiano\n(blur kernel=101)", False),
        (gray,            "Original (crop)", False),
        (norm_scalar,     f"Norm escalar\n(bg={bg_global:.0f}→255)", False),
        (norm_div_morph,  "Norm ÷ morfológico\n(compensa vinheta)", False),
        (norm_div_gauss,  "Norm ÷ gaussiano", False),
        (norm_sub_morph,  "Norm subtração\nmorfológica", False),
    ]

    for ax, (im, title, is_rgb) in zip(axes.ravel(), items):
        if is_rgb:
            ax.imshow(cv2.cvtColor(im, cv2.COLOR_BGR2RGB))
        else:
            ax.imshow(im, cmap='gray', vmin=0, vmax=255)
        ax.set_title(title, fontsize=9)
        ax.axis('off')

    plt.tight_layout()
    plt.savefig(output_path, dpi=110, bbox_inches='tight')
    plt.close()
    print(f"  → {output_path}")


def plot_binarization_comparison(gray, bg_morph, bg_gauss,
                                 bg_global, output_path, crop=None):
    """Figura 3: comparação de binarizações."""
    if crop is not None:
        y0, y1, x0, x1 = crop
        gray = gray[y0:y1, x0:x1]
        bg_morph = bg_morph[y0:y1, x0:x1]
        bg_gauss = bg_gauss[y0:y1, x0:x1]

    norm_div_morph = normalize_divide(gray, bg_morph)
    norm_scalar = normalize_scalar(gray, bg_global)

    # --- Binarizações ---
    baseline_otsu, thr_raw = binarize_otsu(gray)
    baseline_adapt = binarize_adaptive(gray, block_size=31, c=4)

    _, thr_norm_scalar = binarize_otsu(norm_scalar)
    norm_scalar_otsu = binarize_otsu(norm_scalar)[0]
    norm_scalar_adapt = binarize_adaptive(norm_scalar, block_size=31, c=4)

    norm_morph_otsu, thr_morph = binarize_otsu(norm_div_morph)
    norm_morph_adapt = binarize_adaptive(norm_div_morph, block_size=31, c=4)
    norm_morph_sauvola = binarize_sauvola(norm_div_morph, win=35, k=0.25)
    combined = binarize_combined(gray, bg_morph, bg_global, block_size=31, c=4)

    fig, axes = plt.subplots(3, 4, figsize=(22, 16))
    fig.suptitle(
        f"Comparação de binarizações  |  bg_global={bg_global:.0f}  "
        f"|  Otsu raw={thr_raw:.0f}  Otsu norm_morph={thr_morph:.0f}",
        fontsize=11, fontweight='bold'
    )

    items = [
        (gray,              "Original gray"),
        (norm_scalar,       f"Norm escalar\n(bg={bg_global:.0f}→255)"),
        (norm_div_morph,    "Norm ÷ morfológica"),
        (normalize_divide(gray, bg_gauss), "Norm ÷ gaussiana"),

        (baseline_otsu,    f"[baseline] Otsu raw\nthr={thr_raw:.0f}"),
        (baseline_adapt,    "[baseline] CLAHE\n+ adaptativo"),
        (norm_scalar_otsu,  f"Scalar norm\n+ Otsu  thr={thr_norm_scalar:.0f}"),
        (norm_scalar_adapt, "Scalar norm\n+ CLAHE + adapt"),

        (norm_morph_otsu,   f"÷morph + Otsu\nthr={thr_morph:.0f}"),
        (norm_morph_adapt,  "÷morph + CLAHE\n+ adaptativo"),
        (norm_morph_sauvola,"÷morph + Sauvola\n(win=35, k=0.25)"),
        (combined,          "Combinado\n÷morph + bg_mask"),
    ]

    for ax, (im, title) in zip(axes.ravel(), items):
        ax.imshow(im, cmap='gray', vmin=0, vmax=255)
        ax.set_title(title, fontsize=9)
        ax.axis('off')

    plt.tight_layout()
    plt.savefig(output_path, dpi=110, bbox_inches='tight')
    plt.close()
    print(f"  → {output_path}")


def plot_detail_zoom(gray, bg_morph, bg_global, output_path, crops: list):
    """Figura 4: zoom em regiões específicas para avaliar qualidade de borda."""
    n_crops = len(crops)
    fig, axes = plt.subplots(n_crops, 4, figsize=(20, 5 * n_crops))
    fig.suptitle("Detalhe em zooms: gray | ÷morph | adapt | ÷morph+adapt",
                 fontsize=11, fontweight='bold')

    for row, (y0, y1, x0, x1, label) in enumerate(crops):
        crop_gray = gray[y0:y1, x0:x1]
        crop_bg = bg_morph[y0:y1, x0:x1]

        norm = normalize_divide(crop_gray, crop_bg)
        adapt_raw = binarize_adaptive(crop_gray, 31, 4)
        adapt_norm = binarize_adaptive(norm, 31, 4)

        row_items = [
            (crop_gray,  f"{label}\noriginal gray"),
            (norm,       "norm ÷ morph"),
            (adapt_raw,  "adapt (raw)"),
            (adapt_norm, "adapt (÷morph)"),
        ]

        axrow = axes[row] if n_crops > 1 else axes
        for ax, (im, title) in zip(axrow, row_items):
            ax.imshow(im, cmap='gray', vmin=0, vmax=255)
            ax.set_title(title, fontsize=9)
            ax.axis('off')

    plt.tight_layout()
    plt.savefig(output_path, dpi=130, bbox_inches='tight')
    plt.close()
    print(f"  → {output_path}")


# ══════════════════════════════════════════════════════════════════════════════
# 6. MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run(img_path: str, out_prefix: str = "bg_playground", margin_px: int = 70):
    print(f"\n{'='*60}")
    print(f"  Binarization Playground  —  {img_path}")
    print(f"{'='*60}\n")

    img = cv2.imread(img_path)
    if img is None:
        raise FileNotFoundError(f"Não encontrei: {img_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    print(f"[img]  {w}×{h} px")

    # ── Análise de margens ──────────────────────────────────────────────────
    print(f"[step] Amostrando margens ({margin_px}px cada lado)…")
    margin_dict = sample_margins(gray, margin_px)
    bg_global, per_margin_bg, had_outlier = estimate_background(margin_dict, pct=85.0)
    bg_corners = background_from_corners(gray, corner_px=80, pct=90.0)

    print(f"       Por margem (P85): { {k: f'{v:.1f}' for k,v in per_margin_bg.items()} }")
    print(f"       Fundo global estimado : {bg_global:.1f}")
    print(f"       Fundo pelos cantos     : {bg_corners:.1f}")
    if had_outlier:
        print("  ⚠️  Pelo menos uma margem destoou — possível spine ou artefato.")

    # ── Modelos de fundo ────────────────────────────────────────────────────
    print("[step] Calculando modelos de fundo…")
    bg_morph_model = bg_morph_open(gray, ksize=71)
    bg_gauss_model = bg_blur_gaussian(gray, ksize=101)

    # ── Região de visualização (crop central representativo) ────────────────
    crop_y0 = h // 8
    crop_y1 = h // 8 + min(1200, h // 2)
    crop_x0 = 0
    crop_x1 = w
    main_crop = (crop_y0, crop_y1, crop_x0, crop_x1)

    # Zooms para avaliação de borda
    zoom_crops = [
        (200,  500,  80,  900, "Topo-esq (cabeçalho + texto)"),
        (700, 1000, 900, 1750, "Centro-dir (coluna B)"),
        (1800, 2100, 50, 870,  "Meio baixo (col A, Grego)"),
        (2600, 2900, 50, 1800, "Rodapé"),
    ]

    margin_vis = mark_margin_zone(gray, margin_px)

    # ── Figuras ─────────────────────────────────────────────────────────────
    print("[step] Gerando figuras…")
    plot_margin_histograms(
        margin_dict, per_margin_bg, bg_global, bg_corners,
        f"{out_prefix}_1_histogramas.png"
    )
    plot_model_comparison(
        gray, bg_morph_model, bg_gauss_model, margin_vis,
        bg_global, f"{out_prefix}_2_modelos.png", crop=main_crop
    )
    plot_binarization_comparison(
        gray, bg_morph_model, bg_gauss_model, bg_global,
        f"{out_prefix}_3_binarizacoes.png", crop=main_crop
    )
    plot_detail_zoom(
        gray, bg_morph_model, bg_global,
        f"{out_prefix}_4_zoom_detalhes.png", zoom_crops
    )

    print(f"\n[done] bg_global={bg_global:.1f}  bg_corners={bg_corners:.1f}")
    print("       Abra os 4 PNGs para comparar as estratégias.\n")
    return bg_global, bg_corners, per_margin_bg


if __name__ == "__main__":
    img_path = sys.argv[1] if len(sys.argv) > 1 else "input.png"
    prefix   = sys.argv[2] if len(sys.argv) > 2 else "bg_playground"
    run(img_path, prefix)
