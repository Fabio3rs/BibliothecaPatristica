#!/usr/bin/env python3
"""Playground para testar combinações de operações do OpenCV.

Uso típico:
    python tools/opencv_playground.py caminho/para/pg_0001.jpg --show
    python tools/opencv_playground.py imagem.png --chains "gray,clahe,adaptive" \
        --chains "gray,blur3,otsu" --save-grid /tmp/opencv_grid.png

O script roda cadeias de operações (chains) definidas por nomes simples,
gera uma grade com o resultado de cada cadeia e opcionalmente abre uma
janela temporária para inspeção rápida.
"""

from __future__ import annotations

import argparse
from math import ceil
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

import cv2
import numpy as np


Operation = Callable[[np.ndarray, argparse.Namespace], np.ndarray]


def ensure_gray(img: np.ndarray) -> np.ndarray:
    """Garante escala de cinza."""
    return img if len(img.shape) == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def read_image(path: Path, max_dim: int) -> np.ndarray:
    """Lê imagem e reduz o maior lado para max_dim, preservando proporção."""
    img = cv2.imread(str(path))
    if img is None:
        raise FileNotFoundError(f"Não consegui ler {path}")

    h, w = img.shape[:2]
    scale = max_dim / max(h, w) if max(h, w) > max_dim else 1.0
    if scale != 1.0:
        img = cv2.resize(
            img,
            (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    return img


def op_identity(img: np.ndarray, _: argparse.Namespace) -> np.ndarray:
    return img


def op_gray(img: np.ndarray, _: argparse.Namespace) -> np.ndarray:
    return ensure_gray(img)


def op_clahe(img: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    gray = ensure_gray(img)
    clahe = cv2.createCLAHE(
        clipLimit=args.clahe_clip,
        tileGridSize=(args.clahe_grid, args.clahe_grid),
    )
    return clahe.apply(gray)


def op_blur3(img: np.ndarray, _: argparse.Namespace) -> np.ndarray:
    return cv2.GaussianBlur(img, (3, 3), 0)


def op_bilateral(img: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    return cv2.bilateralFilter(img, d=args.bilateral_d, sigmaColor=50, sigmaSpace=50)


def op_adaptive(img: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    gray = ensure_gray(img)
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        args.block_size,
        args.c_value,
    )


def op_adaptive_strict(img: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    gray = ensure_gray(img)
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        15,
        10,
    )


def op_otsu(img: np.ndarray, _: argparse.Namespace) -> np.ndarray:
    gray = ensure_gray(img)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def _ensure_binary(img: np.ndarray) -> np.ndarray:
    return img if len(img.shape) == 2 else ensure_gray(img)


def op_open2(img: np.ndarray, _: argparse.Namespace) -> np.ndarray:
    binary = _ensure_binary(img)
    kernel = np.ones((2, 2), np.uint8)
    return cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)


def op_close3(img: np.ndarray, _: argparse.Namespace) -> np.ndarray:
    binary = _ensure_binary(img)
    kernel = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)


def op_invert(img: np.ndarray, _: argparse.Namespace) -> np.ndarray:
    return cv2.bitwise_not(img)


def op_canny(img: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    gray = ensure_gray(img)
    return cv2.Canny(gray, args.canny_low, args.canny_high)


OPERATIONS: Dict[str, Operation] = {
    "id": op_identity,
    "gray": op_gray,
    "clahe": op_clahe,
    "blur3": op_blur3,
    "bilateral": op_bilateral,
    "adaptive": op_adaptive,
    "adaptive_strict": op_adaptive_strict,
    "otsu": op_otsu,
    "open2": op_open2,
    "close3": op_close3,
    "invert": op_invert,
    "canny": op_canny,
}


DEFAULT_CHAINS = [
    "id",
    "gray",
    "gray,clahe",
    "gray,clahe,adaptive",
    "gray,clahe,adaptive_strict,open2",
    "gray,blur3,otsu",
    "gray,bilateral,adaptive",
    "gray,clahe,adaptive,open2,close3",
    "gray,canny",
]


def parse_chain(chain: str) -> List[str]:
    parts = [p.strip() for p in chain.split(",") if p.strip()]
    if not parts:
        raise ValueError(f"Cadeia vazia: {chain}")
    for p in parts:
        if p not in OPERATIONS:
            raise ValueError(f"Operação desconhecida: {p}")
    return parts


def apply_chain(img: np.ndarray, chain: Sequence[str], args: argparse.Namespace) -> np.ndarray:
    out = img
    for op_name in chain:
        out = OPERATIONS[op_name](out, args)
    return out


def label_image(img: np.ndarray, text: str) -> np.ndarray:
    """Adiciona label no canto superior esquerdo."""
    if len(img.shape) == 2:
        labeled = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    else:
        labeled = img.copy()
    cv2.rectangle(labeled, (0, 0), (labeled.shape[1], 35), (0, 0, 0), -1)
    cv2.putText(
        labeled,
        text,
        (10, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
        cv2.LINE_AA,
    )
    return labeled


def build_grid(images: List[np.ndarray], cols: int) -> np.ndarray:
    """Empilha imagens rotuladas em uma grade."""
    if not images:
        raise ValueError("Nenhuma imagem para montar a grade.")
    h, w = images[0].shape[:2]
    rows = ceil(len(images) / cols)
    blank = np.zeros((h, w, 3), dtype=np.uint8)
    padded = images + [blank] * (rows * cols - len(images))

    grid_rows = []
    for r in range(rows):
        start = r * cols
        row_imgs = padded[start : start + cols]
        grid_rows.append(cv2.hconcat(row_imgs))
    return cv2.vconcat(grid_rows)


def save_images(results: List[Tuple[str, np.ndarray]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, img in results:
        cv2.imwrite(str(out_dir / f"{name}.png"), img)


def run(args: argparse.Namespace) -> None:
    img = read_image(Path(args.image), args.max_dim)

    chains = DEFAULT_CHAINS if not args.chains else args.chains
    parsed_chains = [parse_chain(c) for c in chains]

    results: List[Tuple[str, np.ndarray]] = []
    for chain_tokens in parsed_chains:
        label = "+".join(chain_tokens)
        processed = apply_chain(img, chain_tokens, args)
        results.append((label, label_image(processed, label)))

    grid = build_grid([im for _, im in results], cols=args.cols)

    if args.save_grid:
        Path(args.save_grid).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.save_grid), grid)
        print(f"Grade salva em {args.save_grid}")

    if args.save_each:
        save_images(results, Path(args.save_each))
        print(f"Imagens individuais em {args.save_each}")

    if args.show:
        cv2.imshow("OpenCV Playground (ESC para fechar)", grid)
        key = cv2.waitKey(0)
        if key == 27:  # ESC
            cv2.destroyAllWindows()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Caminho da imagem a ser testada")
    parser.add_argument(
        "--chains",
        nargs="+",
        help="Cadeias de operações separadas por vírgula. Ex.: gray,clahe,adaptive",
    )
    parser.add_argument("--max-dim", type=int, default=1600, help="Maior lado da imagem (default: 1600px)")
    parser.add_argument("--cols", type=int, default=3, help="Colunas da grade")
    parser.add_argument("--show", action="store_true", help="Abre janela temporária com a grade")
    parser.add_argument("--save-grid", help="Salva a grade em PNG")
    parser.add_argument("--save-each", help="Diretório para salvar cada resultado individual")
    parser.add_argument("--list-ops", action="store_true", help="Lista operações disponíveis e sai")

    # Parâmetros finos
    parser.add_argument("--block-size", type=int, default=15, help="Block size para adaptiveThreshold")
    parser.add_argument("--c-value", type=int, default=10, help="C para adaptiveThreshold")
    parser.add_argument("--clahe-clip", type=float, default=3.0, help="ClipLimit do CLAHE")
    parser.add_argument("--clahe-grid", type=int, default=8, help="Grid do CLAHE (tileGridSize)")
    parser.add_argument("--bilateral-d", type=int, default=5, help="Diâmetro do bilateral filter")
    parser.add_argument("--canny-low", type=int, default=50, help="Limite inferior do Canny")
    parser.add_argument("--canny-high", type=int, default=150, help="Limite superior do Canny")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_ops:
        print("Operações disponíveis:")
        for name in sorted(OPERATIONS):
            print(f" - {name}")
        return

    run(args)


if __name__ == "__main__":
    main()
