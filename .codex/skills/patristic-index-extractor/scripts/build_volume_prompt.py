#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_todo_lines(prescan: dict, collection: str) -> str:
    hits = prescan.get("all_hits", [])
    if collection == "PO":
        lines = [
            "- [ ] Verify the volume title and tome-level table.",
            "- [ ] Verify which `FASC.` entries belong to the current tome.",
        ]
        heading_markers = (
            "TOMUS",
            "TABLE DES MATIÈRES",
            "FASC.",
            "AVERTISSEMENT",
            "INTRODUCTION",
            "PRÉFACE",
            "PREFACE",
            "PROLOGUE",
            "TABLE DES NOMS PROPRES",
            "INDEX DES NOMS PROPRES",
            "INDEX DES CITATIONS DES ÉCRITURES",
            "TABLE ALPHABÉTIQUE",
            "TABLE ANALYTIQUE DES MATIÈRES",
            "ADDENDA",
            "CORRIGENDA",
        )
    else:
        lines = ["- [ ] Verify the front index and record its scope."]
        heading_markers = (
            "ELENCHUS",
            "AUCTORUM ET OPERUM",
            "INDEX CAPITUM",
            "ORDO RERUM",
            "INDEX ANALYTICUS",
            "INDEX RERUM ET VERBORUM",
            "INDEX GRÆCITATIS",
            "INDEX GRAECITATIS",
            "ONOMASTICUM",
            "PROLEGOMENA",
        )
    seen: set[tuple[str, int | None]] = set()
    for hit in sorted(
        (h for h in hits if str(h.get("text", "")).strip()),
        key=lambda h: (h.get("page") if isinstance(h.get("page"), int) else 10**9, str(h.get("text", ""))),
    ):
        text = str(hit.get("text", "")).strip()
        page = hit.get("page")
        if page is None:
            continue
        if not text[0].isalpha():
            continue
        upper = text.upper()
        if len(text) > 80:
            continue
        if collection != "PO":
            letters = [c for c in text if c.isalpha()]
            upper_ratio = (sum(1 for c in letters if c.isupper()) / len(letters)) if letters else 0.0
            starts_like_heading = text.startswith(
                (
                    "Index capitum",
                    "Ordo rerum",
                    "Onomasticum",
                    "Elenchus",
                )
            )
            if not starts_like_heading and upper_ratio < 0.7:
                continue
        key = (upper, page)
        if key in seen:
            continue
        if any(marker in upper for marker in heading_markers):
            seen.add(key)
            lines.append(f"- [ ] Verify candidate index at page {page}: {text}")
    if collection == "PO":
        lines.extend([
            "- [ ] Build one TODO item per discovered fascicle or work entrypoint.",
            "- [ ] For each work TODO item, verify front matter, internal tables, and closing indexes.",
            "- [ ] Identify retrospective tables that list other tomes and keep them separate from the current tome.",
            "- [ ] Recheck uncertain page numbers, bracket pagination, and OCR digits before finalizing.",
        ])
    else:
        lines.extend([
            "- [ ] Build one TODO item per discovered work.",
            "- [ ] For each work TODO item, verify the opening pages and the work-level index.",
            "- [ ] Verify all closing indexes at the end of the volume.",
            "- [ ] Recheck uncertain page numbers, columns, and OCR digits before finalizing.",
        ])
    return "\n".join(lines)


def work_instructions(collection: str) -> str:
    base = [
        "- Start from the PRESCAN hints.",
        "- Verify the hinted files directly before deciding anything.",
        "- Search the whole volume if needed, but do not skip the hinted pages.",
        "- Keep OCR literals.",
        "- Apply the collection-specific taxonomy from `references/volume-taxonomy.md`.",
        "- If a number is uncertain, keep the raw literal and lower confidence.",
    ]
    if collection == "PO":
        base.extend(
            [
                "- Separate tome-level structures, fascicle inventory, work front matter, work indexes, editorial closure, and retrospective tables.",
                "- Distinguish `TABLE DES MATIÈRES` of the current tome from internal work tables and cumulative tables of other tomes.",
                "- Treat `FASC.` lines as candidate fascicle inventory, but verify whether they belong to the current tome.",
                "- Inspect the opening pages of each discovered fascicle before naming the work.",
                "- Inspect the closing pages for names indexes, scripture indexes, alphabetical tables, analytical tables, and addenda/corrigenda.",
            ]
        )
    else:
        base.extend(
            [
                "- Separate volume-front, work-front, and volume-end indexes.",
                "- For each work, inspect the opening pages and the work index before naming the work.",
                "- Inspect the closing pages for final indexes.",
            ]
        )
    return "\n".join(base)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Codex prompt envelope for one volume.")
    ap.add_argument("--skill", default="patristic-index-extractor", help="Skill name to invoke")
    ap.add_argument("--volume", required=True, help="Volume id, e.g. PG001, PL099, or PO025")
    ap.add_argument("--source-root", required=True, help="Text root, e.g. teste/PG001/text")
    ap.add_argument("--collection", required=True, help="Collection code, e.g. PG, PL, or PO")
    ap.add_argument("--prescan-json", type=Path, required=True, help="JSON produced by scan_volume.py --json")
    ap.add_argument("--output-dir", type=Path, default=Path("data/index_payloads"), help="Directory for the JSON payload artifact")
    args = ap.parse_args()

    prescan = load_json(args.prescan_json)
    output_file = args.output_dir / f"{args.volume}_indices.json"
    prompt = f"""${args.skill} volume {args.volume} localizado em {args.source_root}

TASK
You are extracting the index structure for one OCR volume.

VOLUME
- volume_id: {args.volume}
- source_root: {args.source_root}
- collection: {args.collection}

PRESCAN
{json.dumps(prescan, ensure_ascii=False, indent=2)}

WORK INSTRUCTIONS
{work_instructions(args.collection)}

TODO
{build_todo_lines(prescan, args.collection)}

OUTPUT FILE
Write the full JSON payload to:
{output_file}

The payload must follow `.codex/skills/patristic-index-extractor/references/output-format.md`.
Return a tiny JSON acknowledgment only.

FINAL RESPONSE
{{"status":"ok","volume_id":"{args.volume}","written_file":"{output_file}"}}
"""
    print(prompt.rstrip())


if __name__ == "__main__":
    main()
