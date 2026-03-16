#!/usr/bin/env python3
"""Visualiza bounding boxes em uma página de imagem.

Uso rápido (caso desta issue):
    python tools/bbox_playground.py \
        teste/PO009/images/9c7ed59b-6f68-40f0-bd7e-862cebeb2e47-349.png \
        --preset po009 --norm-max 1000 --out /tmp/po009_bboxes.png

Também aceita boxes manuais:
    python tools/bbox_playground.py img.png \
        --box cabecalho:50,30,950,60 --box texto:80,70,900,150 --norm-max 1000

Gera uma imagem anotada e (opcionalmente) abre uma janela para inspeção.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import cv2


Box = Tuple[str, Tuple[int, int, int, int]]


def parse_box(box_str: str) -> Box:
    """Converte "label:x1,y1,x2,y2" em tupla."""
    if ":" in box_str:
        label, coords = box_str.split(":", 1)
    else:
        label, coords = "box", box_str

    parts = [p.strip() for p in coords.split(",") if p.strip()]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"Formato esperado: label:x1,y1,x2,y2 (recebido: {box_str})")
    try:
        x1, y1, x2, y2 = map(int, parts)
    except ValueError as exc:  # pragma: no cover - erro claro para o usuário
        raise argparse.ArgumentTypeError(f"Coordenadas devem ser inteiras: {box_str}") from exc
    return label, (x1, y1, x2, y2)


def preset_boxes(name: str) -> List[Box]:
    presets: Dict[str, List[Box]] = {
        "po009": [
            ("cabecalho", (50, 30, 950, 60)),
            ("texto_principal", (80, 70, 900, 150)),
            ("texto_principal", (80, 160, 900, 260)),
            ("texto_principal", (80, 270, 900, 370)),
            ("texto_principal", (80, 380, 900, 410)),
            ("aparato_critico", (80, 420, 900, 480)),
        ],
    }
    if name not in presets:
        raise argparse.ArgumentTypeError(f"Preset desconhecido: {name}")
    return presets[name]


def draw_boxes(
    img_path: Path,
    boxes: Iterable[Box],
    out_path: Path,
    show: bool,
    norm_max: int | None,
) -> None:
    img = cv2.imread(str(img_path))
    if img is None:
        raise FileNotFoundError(f"Não consegui ler {img_path}")

    h, w = img.shape[:2]

    def to_pixels(coord: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        """Converte coordenadas normalizadas (0..norm_max) para pixels se necessário."""
        if norm_max is None:
            return coord
        x1, y1, x2, y2 = coord
        fx = w / norm_max
        fy = h / norm_max
        x1p = int(round(x1 * fx))
        y1p = int(round(y1 * fy))
        x2p = int(round(x2 * fx))
        y2p = int(round(y2 * fy))
        # Garante dentro da imagem
        return (
            max(0, min(w - 1, x1p)),
            max(0, min(h - 1, y1p)),
            max(0, min(w - 1, x2p)),
            max(0, min(h - 1, y2p)),
        )

    colors = {
        "cabecalho": (0, 255, 255),  # amarelo
        "texto_principal": (0, 200, 0),
        "aparato_critico": (0, 0, 255),
    }

    for label, (x1, y1, x2, y2) in boxes:
        x1, y1, x2, y2 = to_pixels((x1, y1, x2, y2))
        color = colors.get(label, (255, 0, 0))
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            img,
            label,
            (x1 + 4, y1 + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    print(f"Imagem anotada salva em {out_path}")

    if show:
        cv2.imshow("BBoxes", img)
        key = cv2.waitKey(0)
        if key == 27:  # ESC
            cv2.destroyAllWindows()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Caminho da imagem")
    parser.add_argument("--out", default="/tmp/bboxes.png", help="PNG de saída (default: /tmp/bboxes.png)")
    parser.add_argument("--box", action="append", type=parse_box, help="Box no formato label:x1,y1,x2,y2; pode repetir")
    parser.add_argument("--preset", help="Nome de preset de boxes (por enquanto: po009)")
    parser.add_argument(
        "--norm-max",
        type=int,
        help="Interpreta coordenadas no intervalo 0..N (ex.: 1000) e escala para pixels da imagem",
    )
    parser.add_argument("--show", action="store_true", help="Abre janela com prévia (ESC para fechar)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    boxes: List[Box] = []
    if args.preset:
        boxes.extend(preset_boxes(args.preset))
    if args.box:
        boxes.extend(args.box)

    if not boxes:
        parser.error("Forneça pelo menos uma bounding box via --preset ou --box")

    draw_boxes(Path(args.image), boxes, Path(args.out), args.show, args.norm_max)


if __name__ == "__main__":
    main()
