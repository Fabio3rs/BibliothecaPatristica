#!/usr/bin/env python3
"""
ocr_to_ascii.py — Reconstrói layout ASCII a partir de saída do Tesseract OCR.

Suporta dois formatos de entrada:
  - TSV (saída do tesseract --ocronly -c tessedit_create_tsv=1)
  - JSON (dicionário de colunas, como gerado por pandas/pytesseract)

Uso:
  python ocr_to_ascii.py out.txt          # TSV embutido em JSON
  python ocr_to_ascii.py out.json         # JSON de colunas
  python ocr_to_ascii.py out.txt --cols 120 --conf 30 --output resultado.txt
"""

import json
import sys
import argparse
from pathlib import Path


# ──────────────────────────────────────────────
# 1. Leitura dos dados
# ──────────────────────────────────────────────

def load_records(path: Path) -> list[dict]:
    """Carrega registros (level=5 = palavras) de TSV ou JSON."""
    raw = path.read_text(encoding="utf-8")

    # Detecta se é JSON
    if raw.lstrip().startswith("{"):
        data = json.loads(raw)

        # Caso: {"returncode":0, "stdout": "<tsv>", ...}
        if "stdout" in data and isinstance(data["stdout"], str):
            return _parse_tsv(data["stdout"])

        # Caso: dicionário de colunas (pandas-style)
        if "level" in data:
            return _dict_of_lists_to_records(data)

        raise ValueError("JSON não reconhecido.")
    else:
        return _parse_tsv(raw)


def _parse_tsv(text: str) -> list[dict]:
    lines = text.strip().splitlines()
    if not lines:
        return []
    headers = lines[0].split("\t")
    records = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) != len(headers):
            continue
        rec = {}
        for h, v in zip(headers, parts):
            try:
                rec[h] = int(v)
            except ValueError:
                try:
                    rec[h] = float(v)
                except ValueError:
                    rec[h] = v
        records.append(rec)
    return records


def _dict_of_lists_to_records(d: dict) -> list[dict]:
    keys = list(d.keys())
    n = len(d[keys[0]])
    records = []
    for i in range(n):
        records.append({k: d[k][i] for k in keys})
    return records


# ──────────────────────────────────────────────
# 2. Filtragem e normalização
# ──────────────────────────────────────────────

def filter_words(records: list[dict], min_conf: float) -> list[dict]:
    """Mantém apenas words (level=5) com confiança ≥ min_conf e texto não vazio."""
    words = []
    for r in records:
        if r.get("level") != 5:
            continue
        conf = r.get("conf", -1)
        text = str(r.get("text", "")).strip()
        if not text:
            continue
        if conf < 0:        # conf==-1 → linha/bloco sem confiança; pula
            continue
        if conf < min_conf:
            continue
        words.append(r)
    return words


# ──────────────────────────────────────────────
# 3. Agrupamento de palavras em linhas lógicas
# ──────────────────────────────────────────────

def cluster_into_lines(words: list[dict]) -> list[list[dict]]:
    """
    Agrupa palavras em linhas lógicas por proximidade vertical.

    Palavras cujo centro vertical (top + height/2) difere menos que
    `gap_threshold` pixels são consideradas da mesma linha.
    O threshold é 55% da altura mediana das palavras.
    """
    if not words:
        return []

    # Altura mediana como unidade de referência
    heights = sorted(w.get("height", 0) for w in words if w.get("height", 0) > 4)
    median_h = heights[len(heights) // 2] if heights else 20
    gap_threshold = median_h * 0.55   # palavras a menos de 55% da altura → mesma linha

    # Ordena por centro vertical
    def center_y(w):
        return w["top"] + w.get("height", 0) / 2

    sorted_words = sorted(words, key=center_y)

    lines: list[list[dict]] = []
    current_line: list[dict] = [sorted_words[0]]
    current_center = center_y(sorted_words[0])

    for w in sorted_words[1:]:
        cy = center_y(w)
        if abs(cy - current_center) <= gap_threshold:
            current_line.append(w)
            # Atualiza centro como média do grupo
            current_center = sum(center_y(x) for x in current_line) / len(current_line)
        else:
            lines.append(current_line)
            current_line = [w]
            current_center = cy

    lines.append(current_line)

    # Dentro de cada linha, ordena por left (esquerda → direita)
    for line in lines:
        line.sort(key=lambda w: w["left"])

    return lines


# ──────────────────────────────────────────────
# 4. Mapeamento pixel → char column
# ──────────────────────────────────────────────

def build_ascii_grid(words: list[dict], page_width_px: int, ascii_cols: int) -> dict[tuple[int, int], str]:
    """
    Agrupa palavras em linhas lógicas, mapeia cada grupo para
    uma linha ASCII, e posiciona as palavras horizontalmente.
    """
    if not words:
        return {}

    px_per_col = page_width_px / ascii_cols

    lines = cluster_into_lines(words)

    # Altura mediana para escala vertical entre linhas
    heights = sorted(w.get("height", 0) for w in words if w.get("height", 0) > 4)
    median_h = heights[len(heights) // 2] if heights else 20
    px_per_row = max(median_h * 0.75, 8)

    grid: dict[tuple[int, int], str] = {}

    for line_words in lines:
        # Linha ASCII baseada no top médio do grupo
        avg_top = sum(w["top"] for w in line_words) / len(line_words)
        row = int(round(avg_top / px_per_row))

        for w in line_words:
            col = int(round(w["left"] / px_per_col))
            col = max(0, min(col, ascii_cols - 1))
            text = str(w["text"]).strip()

            # Resolve colisão deslocando para a direita
            while (row, col) in grid:
                col += 1

            grid[(row, col)] = text

    return grid


# ──────────────────────────────────────────────
# 4. Renderização
# ──────────────────────────────────────────────

def render_grid(grid: dict[tuple[int, int], str], ascii_cols: int) -> str:
    if not grid:
        return "(nenhum texto encontrado com os critérios escolhidos)"

    max_row = max(r for r, _ in grid)
    lines = []

    for row in range(max_row + 1):
        # Coleta todas as palavras desta linha
        row_words = [(col, text) for (r, col), text in grid.items() if r == row]
        if not row_words:
            lines.append("")
            continue

        row_words.sort()
        buf = [" "] * ascii_cols

        for col, text in row_words:
            for i, ch in enumerate(text):
                pos = col + i
                if pos < ascii_cols:
                    buf[pos] = ch

        lines.append("".join(buf).rstrip())

    # Remove linhas em branco consecutivas no final
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 5. CLI
# ──────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Tesseract OCR output → ASCII layout")
    ap.add_argument("input", help="Arquivo de entrada (.txt TSV ou .json)")
    ap.add_argument("--cols", type=int, default=160,
                    help="Largura da saída ASCII em colunas (padrão: 160)")
    ap.add_argument("--conf", type=float, default=20.0,
                    help="Confiança mínima do Tesseract 0-100 (padrão: 20)")
    ap.add_argument("--output", "-o", default=None,
                    help="Arquivo de saída (padrão: stdout)")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"Erro: arquivo não encontrado: {path}", file=sys.stderr)
        sys.exit(1)

    print(f"Carregando {path} …", file=sys.stderr)
    records = load_records(path)
    print(f"  {len(records)} registros carregados.", file=sys.stderr)

    words = filter_words(records, min_conf=args.conf)
    print(f"  {len(words)} palavras com conf ≥ {args.conf}.", file=sys.stderr)

    # Largura da página em pixels (usa o campo 'width' do registro level=1)
    page_width_px = 2580   # fallback
    for r in records:
        if r.get("level") == 1:
            page_width_px = r.get("width", page_width_px)
            break

    print(f"  Largura da página: {page_width_px}px → {args.cols} colunas ASCII.", file=sys.stderr)

    grid = build_ascii_grid(words, page_width_px, args.cols)
    result = render_grid(grid, args.cols)

    result = result.replace("\n\n", "\n")

    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
        print(f"Saída gravada em: {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
