#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.corpus_utils import page_number, page_sort_key
from tools.ocr_xml_utils import read_ocr_page


def parse_pages_spec(spec: str) -> set[int]:
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left)
            end = int(right)
            pages.update(range(min(start, end), max(start, end) + 1))
        else:
            pages.add(int(part))
    return pages


def files_for_args(args: argparse.Namespace) -> list[Path]:
    files = [Path(item) for item in args.files]
    if args.volume:
        text_root = args.root / args.volume / "text"
        if not text_root.exists():
            raise SystemExit(f"Text directory not found: {text_root}")
        volume_files = sorted(text_root.glob("*.txt"), key=page_sort_key)
        if args.pages:
            wanted = parse_pages_spec(args.pages)
            volume_files = [path for path in volume_files if page_number(path) in wanted]
        files.extend(volume_files)
    if not files:
        raise SystemExit("Provide file paths or --volume.")
    return files


def render_text(page: object, view: str) -> str:
    if view == "xml":
        return page.to_clean_xml()
    if view == "all":
        return page.all_text
    if view == "body":
        return page.body_text
    if view == "header":
        return page.header_text
    if view == "footer":
        return page.footer_text
    if view == "notes":
        return page.notes_text
    if view == "blocks":
        lines: list[str] = []
        for idx, block in enumerate(page.blocks, start=1):
            label = f"[{idx}] tag={block.tag_name} tipo={block.tipo or '-'} script={block.script or '-'} bbox={block.bbox or '-'}"
            lines.append(label)
            lines.append(block.content_clean)
        return "\n\n".join(lines).strip()
    raise SystemExit(f"Unknown view: {view}")


def render_pages(paths: Iterable[Path], args: argparse.Namespace) -> str:
    paths = list(paths)
    if args.json:
        pages: list[dict[str, Any]] = []
        for path in paths:
            page = read_ocr_page(path)
            data = page.to_dict(include_raw=args.raw)
            data["file"] = str(path)
            data["file_seq"] = page_number(path)
            pages.append(data)
        payload: dict[str, Any] | list[dict[str, Any]] = pages[0] if len(pages) == 1 else pages
        return json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None)

    rendered: list[str] = []
    for path in paths:
        page = read_ocr_page(path)
        text = path.read_text(encoding="utf-8", errors="replace") if args.raw else render_text(page, args.view)
        if args.show_source:
            rendered.append(f"<!-- file={path} file_seq={page_number(path)} parse_ok={page.parse_ok} -->")
        rendered.append(text)
    return "\n\n".join(part for part in rendered if part)


def main() -> None:
    ap = argparse.ArgumentParser(description="Read OCR page .txt files through the conservative XML cleaner.")
    ap.add_argument("files", nargs="*", help="OCR .txt files to read")
    ap.add_argument("--root", type=Path, default=Path("teste"), help="Corpus root containing volume folders")
    ap.add_argument("--volume", help="Volume id, e.g. PO025")
    ap.add_argument("--pages", help="Physical page numbers/ranges, e.g. 177-183,485,487-495")
    ap.add_argument("--view", choices=("xml", "all", "body", "header", "footer", "notes", "blocks"), default="xml")
    ap.add_argument("--json", action="store_true", help="Emit structured JSON instead of text/XML")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    ap.add_argument("--show-source", action="store_true", help="Add source comments before text/XML output")
    ap.add_argument("--raw", action="store_true", help="Emit raw OCR content; intended only for parser debugging")
    args = ap.parse_args()

    print(render_pages(files_for_args(args), args))


if __name__ == "__main__":
    main()
