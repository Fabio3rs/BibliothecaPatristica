# Usage: python scripts/pipeline_index_extraction/repair_pg015_ordo_payload.py
"""Repair PG015 ORDO RERUM payload text against the cleaned OCR page 726."""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VOLUME_ID = "PG015"
SOURCE_ROOT = ROOT / "teste" / VOLUME_ID / "text"
SECTION_FILE = SOURCE_ROOT / "691e0abd-67e6-45da-b469-8352851f8674-726.txt"
PAYLOAD = ROOT / "data" / "alphabetical_index_payloads" / "PG015_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data" / "alphabetical_index_payloads" / "PG015_helper_request.json"
INTERMEDIATE_DIR = ROOT / "data" / "intermediate_payloads" / VOLUME_ID
TODO = INTERMEDIATE_DIR / "todo.json"


ENTRY_LINE_RE = re.compile(r"^(?P<lemma>.+?)\.?\s+(?P<page>\d+)$")
TAG_RE = re.compile(r"<[^>]+>")


def clean_norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def read_clean_ordo_lines() -> list[str]:
    result = subprocess.run(
        [
            "python",
            "scripts/read_ocr_page_text.py",
            "--view",
            "xml",
            "--show-source",
            str(SECTION_FILE),
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    lines: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = TAG_RE.sub("", raw_line).strip()
        if not line or line.startswith("<!--"):
            continue
        if line in {
            "ORDO RERUM",
            "QUÆ IN HOC TOMO CONTINENTUR.",
            "ORIGENES.",
            "HEXAPLORUM QUÆ SUPERSUNT.",
            "FINIS TOMI DECIMI QVINTI.",
            "Ex typis MIGNE, au Petit-Montrouge.",
            "Digitized by Google",
        }:
            continue
        if ENTRY_LINE_RE.match(line):
            lines.append(line)
    if len(lines) != 98:
        raise RuntimeError(f"Expected 98 ORDO RERUM entry lines, found {len(lines)}")
    return lines


def split_entry_line(line: str) -> tuple[str, int]:
    match = ENTRY_LINE_RE.match(line)
    if not match:
        raise ValueError(line)
    lemma = match.group("lemma").strip()
    page = int(match.group("page"))
    return lemma, page


def update_payload(lines: list[str]) -> dict[str, Any]:
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    if len(payload["entries"]) != len(lines) or len(payload["refs"]) != len(lines):
        raise RuntimeError("Payload entry/ref counts do not match OCR line count")

    changed_entries: list[int] = []
    entry_page_by_key: dict[str, int] = {}
    for idx, (entry, line) in enumerate(zip(payload["entries"], lines), start=1):
        lemma, page = split_entry_line(line)
        entry_raw = f"{lemma}. {page}" if not lemma.endswith(".") else f"{lemma} {page}"
        old = {
            "lemma_raw": entry.get("lemma_raw"),
            "entry_raw": entry.get("entry_raw"),
            "page": entry.get("inferred_printed_page"),
        }
        entry["lemma_raw"] = lemma
        entry["lemma_display"] = lemma
        entry["lemma_norm"] = clean_norm(lemma)
        entry["lemma_sort"] = clean_norm(lemma)
        entry["entry_raw"] = entry_raw
        entry["inferred_printed_page"] = page
        entry_page_by_key[entry["entry_key"]] = page
        if old != {
            "lemma_raw": entry.get("lemma_raw"),
            "entry_raw": entry.get("entry_raw"),
            "page": entry.get("inferred_printed_page"),
        }:
            changed_entries.append(idx)
            entry.setdefault("raw_json", {})["pg015_rerun_ocr_text_repaired"] = True

    for ref in payload["refs"]:
        page = entry_page_by_key[ref["entry_key"]]
        ref["ref_raw"] = str(page)
        ref["page_ref_raw"] = str(page)
        ref["page_ref_int"] = page

    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload.setdefault("coverage", {})["entries_status"] = "complete"
    payload["coverage"]["entries_status_reason"] = (
        "Rerun aligned all ORDO RERUM entries with the cleaned XML OCR for file 726, "
        "removing validation-blocking line-break hyphen artifacts while preserving helper material locators."
    )
    payload["coverage"]["evidence_files"] = [str(SECTION_FILE)]
    payload.setdefault("notes", []).append(
        "PG015 rerun repaired OCR line-break hyphen artifacts in ORDO RERUM entries against the cleaned OCR reader output for file 726."
    )
    payload.setdefault("notes", []).append(
        f"PG015 rerun updated {len(changed_entries)} entry text rows from the OCR page; refs keep one printed-page locator per entry."
    )

    PAYLOAD.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"changed_entries": changed_entries, "lines": lines}


def update_helper_request(lines: list[str]) -> None:
    helper = json.loads(HELPER_REQUEST.read_text(encoding="utf-8"))
    if len(helper.get("entries", [])) != len(lines):
        raise RuntimeError("Helper request entry count does not match OCR line count")
    for item, line in zip(helper["entries"], lines):
        lemma, page = split_entry_line(line)
        item["lemma_raw"] = lemma
        item["query_names"] = [lemma]
        if " — " in lemma:
            item["query_names"].append(lemma.split(" — ", 1)[1])
        item["page_hints"] = [str(page)]
        item["page_hint_ints"] = [page]
        item["context_raw"] = f"{lemma}."
    HELPER_REQUEST.write_text(json.dumps(helper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_todo(changed_count: int) -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "current_focus": "Payload repaired after PG015 line-break hyphen validation failure; ready for final validation.",
        "completed": [
            "Read PG015 validation failure and inspected the exact offending entries",
            "Verified ORDO RERUM in OCR file 726 with read_ocr_page_text.py --view xml --show-source",
            f"Aligned {changed_count} entry text rows and helper request rows with cleaned OCR literals",
        ],
        "pending": [
            "Run index_target_locator.py on the refreshed helper request",
            "Validate final payload with import_alphabetical_index_json.py --validate-only",
        ],
        "blocked": [],
        "notes": [
            "The section is a closing ORDO RERUM contents table, not a scripture or alphabetical subject index.",
            "Line-break hyphen artifacts were removed only where the cleaned OCR page joined the same wrapped word.",
        ],
    }
    TODO.write_text(json.dumps(todo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    lines = read_clean_ordo_lines()
    result = update_payload(lines)
    update_helper_request(lines)
    update_todo(len(result["changed_entries"]))
    print(json.dumps({"changed_entries": len(result["changed_entries"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
