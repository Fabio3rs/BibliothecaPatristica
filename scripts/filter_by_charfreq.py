#!/usr/bin/env python3
"""
Remove do dataset de treinamento todas as linhas que contenham caracteres
cuja frequência no charfreq seja menor que --min-count.

O charfreq é gerado por:
  LC_ALL=C.UTF-8 grep -P -o "\\X" migne.training_text | sort | uniq -c | sort -rn

Formato esperado do charfreq:
  <COUNT> <CHAR>   (com possível espaço inicial — saída do `uniq -c`)

Uso:
  python scripts/filter_by_charfreq.py \\
      --input  migne.training_text \\
      --charfreq charfreq \\
      --min-count 10 \\
      --output migne.training_text.filtered

  # Apenas inspecionar sem escrever:
  python scripts/filter_by_charfreq.py \\
      --input migne.training_text \\
      --charfreq charfreq \\
      --min-count 10 \\
      --dry-run
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

ALLOWED_SYMBOLS = {
    "†",
    "‡",
    "•",
    "¶",
    "§",
    "℣",
    "℟",
    "☧",
    "✠",
    "☩",
    "·",
    "«",
    "»",
    "’",
    "—",
    "◊",
    "*",
}


def load_charfreq(charfreq_path: Path) -> dict[str, int]:
    """Lê o arquivo charfreq e devolve {char: count}.

    Suporta o formato do `uniq -c`:  '   <N> <CHAR>'
    O char pode ser um grapheme cluster (base + combining), não apenas um
    codepoint — o grep -P -oX quebra por grapheme cluster.
    """
    freq: dict[str, int] = {}
    with open(charfreq_path, "rb") as f:
        for raw in f:
            line = raw.rstrip(b"\n").decode("utf-8", errors="replace").lstrip()
            if not line:
                continue
            # formato: "<COUNT> <CHAR>"  — separado pelo primeiro espaço
            idx = line.find(" ")
            if idx == -1:
                continue
            count_str = line[:idx]
            if not count_str.isdigit():
                continue
            count = int(count_str)
            char = line[idx + 1 :]  # pode ser string vazia se o char era espaço
            if char == "":
                char = " "
            freq[char] = count
    return freq


def build_banned_set(freq: dict[str, int], min_count: int) -> set[str]:
    """Retorna o set de chars (ou grapheme clusters) com contagem < min_count."""
    return {ch for ch, cnt in freq.items() if cnt < min_count}


def line_has_banned(line: str, banned: set[str]) -> bool:
    """Verifica se a linha contém algum char/cluster do set proibido.

    Itera codepoint a codepoint E também testa substrings de comprimento
    compatível com clusters do banned set (para cobrir o caso em que o
    charfreq foi gerado por grapheme clusters).
    """
    # checar codepoint a codepoint (caso mais comum)
    for ch in line:
        if ch in ALLOWED_SYMBOLS:
            continue
        if ch in banned:
            return True
    # checar clusters do banned set que têm mais de 1 codepoint
    multi = [b for b in banned if len(b) > 1]
    for cluster in multi:
        if cluster in line:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filtra linhas de um training_text por frequência de caracteres."
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Arquivo de entrada (ex: migne.training_text)",
    )
    parser.add_argument(
        "--charfreq", "-f",
        required=True,
        help="Arquivo charfreq gerado pelo grep/sort/uniq",
    )
    parser.add_argument(
        "--min-count", "-n",
        type=int,
        default=10,
        help="Threshold: remove linhas com chars de contagem < N (padrão: 10)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Arquivo de saída (padrão: <input>.filtered)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Não escreve arquivo; apenas exibe estatísticas e amostras",
    )
    parser.add_argument(
        "--show-banned-sample",
        type=int,
        default=30,
        metavar="N",
        help="Exibe os N chars proibidos mais frequentes (padrão: 30)",
    )
    parser.add_argument(
        "--preserve-liturgical-symbols",
        action="store_true",
        help="Não remove linhas apenas por conter símbolos litúrgicos como †, ℟, ℣",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    charfreq_path = Path(args.charfreq)

    if not input_path.exists():
        print(f"[erro] arquivo de entrada não encontrado: {input_path}", file=sys.stderr)
        sys.exit(1)
    if not charfreq_path.exists():
        print(f"[erro] charfreq não encontrado: {charfreq_path}", file=sys.stderr)
        sys.exit(1)

    # --- Carregar frequências ---
    print(f"Carregando charfreq: {charfreq_path} ...")
    freq = load_charfreq(charfreq_path)
    print(f"  {len(freq)} chars/clusters no charfreq")

    banned = build_banned_set(freq, args.min_count)
    print(f"  {len(banned)} chars proibidos (contagem < {args.min_count})")

    # Mostrar amostra dos proibidos (ordenados por contagem crescente)
    if args.show_banned_sample > 0 and banned:
        sample = sorted(banned, key=lambda c: freq.get(c, 0))
        sample = sample[: args.show_banned_sample]
        print(f"\n  Amostra de chars proibidos (os {len(sample)} de menor contagem):")
        for ch in sample:
            cnt = freq.get(ch, 0)
            if ch.strip():
                try:
                    name = unicodedata.name(ch[0])
                except Exception:
                    name = f"U+{ord(ch[0]):04X}"
            else:
                name = "SPACE/CONTROL"
            print(f"    {cnt:6}  {repr(ch):30}  {name}")

    # --- Filtrar linhas ---
    print(f"\nLendo: {input_path} ...")
    all_lines = input_path.read_text(encoding="utf-8").splitlines()
    total = len(all_lines)

    kept: list[str] = []
    removed: list[str] = []

    for line in all_lines:
        if args.preserve_liturgical_symbols:
            line_banned = line_has_banned(line, banned - ALLOWED_SYMBOLS)
        else:
            line_banned = line_has_banned(line, banned)
        if line_banned:
            removed.append(line)
        else:
            kept.append(line)

    removed_pct = len(removed) / total * 100 if total else 0
    print(f"  Total de linhas : {total}")
    print(f"  Mantidas        : {len(kept)}  ({100 - removed_pct:.1f}%)")
    print(f"  Removidas       : {len(removed)}  ({removed_pct:.1f}%)")

    # Mostrar amostra das linhas removidas
    if removed:
        print(f"\n  Amostra de linhas removidas (até 10):")
        for l in removed[:10]:
            print(f"    {l[:120]}")

    if args.dry_run:
        print("\n[dry-run] Nenhum arquivo escrito.")
        return

    out_path = Path(args.output) if args.output else input_path.with_suffix(
        input_path.suffix + ".filtered"
    )
    out_path.write_text("\n".join(kept), encoding="utf-8")
    print(f"\nArquivo escrito: {out_path}  ({len(kept)} linhas)")


if __name__ == "__main__":
    main()
