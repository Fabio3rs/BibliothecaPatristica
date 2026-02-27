#!/usr/bin/env python3
import argparse
import re
from pathlib import Path

NUM_RE = re.compile(r"-(\d+)\.txt$", re.IGNORECASE)

def page_number(p: Path) -> int | None:
    m = NUM_RE.search(p.name)
    if m:
        return int(m.group(1))
    # fallback: último bloco numérico antes da extensão
    m2 = re.search(r"(\d+)(?=\.[^.]+$)", p.name)
    return int(m2.group(1)) if m2 else None

def find_text_files(text_dir: Path) -> list[Path]:
    # pega só .txt (não recursivo por padrão; mude para rglob se quiser)
    files = [p for p in text_dir.glob("*.txt")]
    # filtra os que têm número detectável
    files = [p for p in files if page_number(p) is not None]
    # ordena por número; em empate, por nome (estável)
    files.sort(key=lambda p: (page_number(p), p.name))
    return files

def concat(files: list[Path], out_path: Path, with_headers: bool = True, encoding="utf-8"):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_chars = 0
    with out_path.open("w", encoding=encoding, newline="\n") as out:
        for i, f in enumerate(files, 1):
            n = page_number(f)
            if with_headers:
                out.write(f"\n\n=== PAGE {n} ({f.name}) ===\n\n")
            text = f.read_text(encoding=encoding, errors="replace")
            out.write(text.rstrip() + "\n")
            total_chars += len(text)
    return len(files), total_chars

def main():
    ap = argparse.ArgumentParser(description="Concatena .txt ordenando pelo número final do nome do arquivo (…-NNN.txt).")
    ap.add_argument("--text-dir", type=Path, default=Path("text"),
                    help="Diretório com os .txt (padrão: ./text)")
    ap.add_argument("--out", type=Path, default=Path("concatenado.txt"),
                    help="Arquivo de saída (padrão: concatenado.txt)")
    ap.add_argument("--no-headers", action="store_true",
                    help="Não inserir '=== PAGE N ===' entre páginas")
    ap.add_argument("--recursive", action="store_true",
                    help="Buscar .txt recursivamente (usa rglob)")
    args = ap.parse_args()

    text_dir = args.text_dir
    if not text_dir.exists():
        raise SystemExit(f"Diretório não existe: {text_dir}")

    if args.recursive:
        # versão recursiva (se preferir): use rglob
        files = [p for p in text_dir.rglob("*.txt") if page_number(p) is not None]
        files.sort(key=lambda p: (page_number(p), p.name))
    else:
        files = find_text_files(text_dir)

    if not files:
        raise SystemExit("Nenhum .txt com sufixo numérico encontrado.")

    count, total_chars = concat(files, args.out, with_headers=not args.no_headers)
    print(f"✔ Concatenei {count} arquivos em: {args.out}  (≈{total_chars} chars)")

if __name__ == "__main__":
    main()
