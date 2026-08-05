#!/usr/bin/env python3
"""Usage: python scripts/pipeline_index_extraction/build_po011_alphabetical_payload.py

Rebuild the PO011 alphabetical payload from the OCR pages 497-500 and the
existing checkpoint, write a helper request for the unresolved analytical
fascicle headings, and assemble fresh intermediates for validation/import.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VOLUME_ID = "PO011"
COLLECTION = "PO"
SOURCE_ROOT = Path("/homessddata/Projects/pdfocr/teste/PO011/text")
OUTPUT_PATH = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO011_alphabetical_indices.json")
HELPER_REQUEST_PATH = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO011_helper_request.json")
HELPER_OUTPUT_PATH = Path("/homessddata/Projects/pdfocr/data/alphabetical_index_payloads/PO011_helper_output.json")
INTERMEDIATE_DIR = Path("/homessddata/Projects/pdfocr/data/intermediate_payloads/PO011")
TODO_PATH = INTERMEDIATE_DIR / "todo.json"

ALPHA_FILES = {
    497: str(SOURCE_ROOT / "546d2e7f-bb41-4238-a098-562786dfb958-497.txt"),
    498: str(SOURCE_ROOT / "546d2e7f-bb41-4238-a098-562786dfb958-498.txt"),
    499: str(SOURCE_ROOT / "546d2e7f-bb41-4238-a098-562786dfb958-499.txt"),
    500: str(SOURCE_ROOT / "546d2e7f-bb41-4238-a098-562786dfb958-500.txt"),
    511: str(SOURCE_ROOT / "546d2e7f-bb41-4238-a098-562786dfb958-511.txt"),
    512: str(SOURCE_ROOT / "546d2e7f-bb41-4238-a098-562786dfb958-512.txt"),
}
PREVIOUS_PAYLOAD_PATH = OUTPUT_PATH

LETTER_FILE_PAGE = {
    "A": 497,
    "B": 497,
    "C": 497,
    "D": 498,
    "E": 498,
    "F": 498,
    "G": 498,
    "H": 498,
    "I": 498,
    "J": 498,
    "L": 499,
    "M": 499,
    "N": 499,
    "O": 499,
    "P": 499,
    "Q": 499,
    "R": 499,
    "S": 499,
    "T": 500,
    "V": 500,
    "Y": 500,
    "Z": 500,
}

FIX_REPLACEMENTS = {
    "d'É- gypte": "d'Égypte",
    "per- mutent": "permutent",
    "Esprit-Saint": "Esprit-Saint",
    "qua- torze": "quatorze",
    "SaintJean-Baptiste": "Saint-Jean-Baptiste",
    "Esprit de Dien": "Esprit de Dien",
    "436_1416": "436_14-16",
    "445_1315-16": "445_13-15-16",
    "406_611": "406_6-11",
    "410423": "410-423",
    "4406": "440-6",
    "424 ₁₋₅": "424₁₋₅",
    "420₁₀ 12 421₇₋₁₁ 422₃₋ 430₃": "420₁₀-12 421₇₋₁₁ 422₃ 430₃",
    "Volonté propre est nuisible": "Volonté propre est nuisible",
    "Syriaque; apophthegmes traduits en syriaque 396; sont édités 410-423. — Deux chapitres de la version syriaque de l'*Historia monacho-* *rum* 396-7 et 424-432.": "Syriaque; apophthegmes traduits en syriaque 396; sont édités 410-423. — Deux chapitres de la version syriaque de l'Historia monachorum 396-7 et 424-432.",
}

REF_PATTERN = re.compile(
    r"""
    (?<!\d)
    (?P<raw>
        \[?\d{1,3}\]?
        (?:
            \s*(?:n\.|note|chap\.)\s*\d+
          | [_,₀₁₂₃₄₅₆₇₈₉\-à;,. ]*[₀₁₂₃₄₅₆₇₈₉]?
        )*
    )
    """,
    re.VERBOSE,
)
SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
HEADER_PAGE_RE = re.compile(r">\s*(?:\[\d+\]\s*)?(?P<page>\d{1,3})\s+[A-ZÉÈÀÂÎÏÔÛÙÇ][^<]{0,90}</bloco>")
PAGE_REF_RE = re.compile(
    r"""
    (?<![\w.])
    (?P<raw>
      \[?\d{1,3}\]?
      (?:
        \s*(?:n\.|note|chap\.)\s*\d+
        |
        (?:
          (?:_\s*[₀₁₂₃₄₅₆₇₈₉0-9]+|[₀₁₂₃₄₅₆₇₈₉]+|₋\s*[₀₁₂₃₄₅₆₇₈₉0-9]+)
          (?:\s*[-₋]\s*[₀₁₂₃₄₅₆₇₈₉0-9]+)*
        )
        |
        (?:\s*[-à]\s*\d{1,3})
      )?
    )
    """,
    re.VERBOSE,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def join_broken_words(text: str) -> str:
    text = re.sub(r"([A-Za-zÀ-ÖØ-öø-ÿ])-\s+([a-zà-öø-ÿ])", r"\1\2", text)
    return normalize_space(text)


def previous_payload() -> dict[str, Any]:
    return read_json(PREVIOUS_PAYLOAD_PATH)


def page_map_from_previous(payload: dict[str, Any]) -> dict[int, str]:
    counters: dict[int, Counter[str]] = defaultdict(Counter)
    for ref in payload.get("refs", []):
        page = ref.get("page_ref_int")
        target = ref.get("target_file")
        if isinstance(page, int) and isinstance(target, str) and target:
            counters[page][target] += 1
    return {page: counts.most_common(1)[0][0] for page, counts in counters.items()}


def page_map_from_headers() -> dict[int, str]:
    page_map: dict[int, str] = {}
    for path in SOURCE_ROOT.glob("*.txt"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = HEADER_PAGE_RE.search(text[:1200])
        if not match:
            continue
        page = int(match.group("page"))
        page_map.setdefault(page, str(path))
    return page_map


def build_page_map(payload: dict[str, Any]) -> dict[int, str]:
    page_map = page_map_from_headers()
    page_map.update(page_map_from_previous(payload))
    return page_map


def load_helper_output() -> dict[str, Any]:
    if not HELPER_OUTPUT_PATH.exists():
        return {}
    data = read_json(HELPER_OUTPUT_PATH)
    return {item["entry_id"]: item for item in data.get("entries", [])}


def helper_snapshot(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not item:
        return None
    best = item.get("best_candidate") or {}
    return {
        "status": item.get("status"),
        "candidate_role": best.get("candidate_role"),
        "reason_summary": best.get("reason_summary"),
        "candidates": [
            {
                "file": cand.get("file"),
                "probability": cand.get("probability"),
                "evidence": [ev.get("kind") for ev in cand.get("evidence", [])[:3]],
            }
            for cand in (item.get("candidates") or [])[:3]
        ],
    }


def read_blocks(file_seq: int) -> list[str]:
    text = Path(ALPHA_FILES[file_seq]).read_text(encoding="utf-8")
    return re.findall(r'<bloco tipo="texto_principal"[^>]*>(.*?)</bloco>', text, re.S)


def should_join(current: str, nxt: str) -> bool:
    if current.endswith("-"):
        return True
    if current.rstrip().endswith("—"):
        return True
    if re.match(r"^[0-9₀₁₂₃₄₅₆₇₈₉\[\]()]+", nxt):
        return True
    if nxt.startswith(("—", "cf.", "et ", "ou ", "à ", "au ", "aux ", "par ", "pour ", "contre ", "qui ", "qu'", "les ", "la ", "le ", "l'", "du ", "de ", "d'")):
        return True
    if nxt and nxt[0].islower():
        return True
    return False


def raw_entries_for_file(file_seq: int) -> list[str]:
    entries: list[str] = []
    for block in read_blocks(file_seq):
        current = ""
        for raw_line in [line for line in block.splitlines() if line.strip()]:
            line = normalize_space(raw_line)
            if not current:
                current = line
                continue
            if should_join(current, line):
                if current.endswith("-"):
                    current = current[:-1] + line
                else:
                    current = f"{current} {line}"
            else:
                entries.append(current)
                current = line
        if current:
            entries.append(current)
    return entries


def merge_indices(entries: list[str], groups: list[list[int]]) -> list[str]:
    merged: list[str] = []
    consumed: set[int] = set()
    for group in groups:
        idx0 = group[0]
        combined = " ".join(entries[i - 1] for i in group)
        merged.append((idx0, combined))
        consumed.update(group)
    passthrough = [(i + 1, value) for i, value in enumerate(entries) if (i + 1) not in consumed]
    combined_all = merged + passthrough
    combined_all.sort(key=lambda item: item[0])
    return [value for _, value in combined_all]


def corrected_alpha_entries() -> dict[str, list[dict[str, Any]]]:
    file_entries = {seq: raw_entries_for_file(seq) for seq in (497, 498, 499, 500)}
    file_entries[497] = merge_indices(file_entries[497], [[20, 21], [29, 30]])
    file_entries[498] = merge_indices(file_entries[498], [[52, 53], [61, 62]])
    file_entries[499] = merge_indices(
        file_entries[499],
        [[26, 27], [33, 34, 35], [50, 51], [63, 64], [69, 70], [72, 73], [78, 79], [87, 88], [96, 97], [104, 105], [113, 114], [119, 120]],
    )
    file_entries[500] = merge_indices(file_entries[500], [[1, 2], [12, 13], [23, 24], [28, 29], [43, 44]])

    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current_letter = ""

    for seq in (497, 498, 499, 500):
        for raw in file_entries[seq]:
            text = join_broken_words(raw)
            for src, dst in FIX_REPLACEMENTS.items():
                text = text.replace(src, dst)
            if text in {"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "V", "Y", "Z"}:
                current_letter = text
                continue
            if re.match(r"^[A-Z] ", text) and text[0] in LETTER_FILE_PAGE:
                current_letter = text[0]
                text = text[2:]
            text = text.strip()
            if not text:
                continue
            output[current_letter].append({"entry_raw": text, "file_seq": seq})
    return output


def make_helper_request() -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "source_root": str(SOURCE_ROOT),
        "options": {"top_k": 5, "adjacency_window": 2},
        "entries": [
            {
                "entry_id": "po011_preface_de_ammonio",
                "lemma_raw": "De Ammonio monacho epistolarum auctore",
                "query_names": ["De Ammonio monacho epistolarum auctore", "Ammonii Eremitae Epistolae"],
                "page_hints": ["[3]"],
                "page_hint_ints": [3],
                "context_raw": "PRAEFATIO. De Ammonio monacho epistolarum auctore [3]",
            },
            {
                "entry_id": "po011_preface_epistolae_mari",
                "lemma_raw": "Epistolae Mari Ammonii eremitae",
                "query_names": ["Epistolae Mari Ammonii eremitae", "Ammonii Eremitae Epistolae"],
                "page_hints": ["[15]"],
                "page_hint_ints": [15],
                "context_raw": "Epistolae Mari Ammonii eremitae [15]",
            },
            {
                "entry_id": "po011_preface_epistolae_dubiae",
                "lemma_raw": "Epistolae dubiae",
                "query_names": ["Epistolae dubiae", "Ammonii Eremitae Epistolae"],
                "page_hints": ["[65]"],
                "page_hint_ints": [65],
                "context_raw": "Epistolae dubiae [65]",
            },
        ],
    }


def strip_accents(text: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def lemma_norm(text: str) -> str:
    text = strip_accents(text.casefold())
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return normalize_space(text)


def is_probable_locator(text: str, start: int, raw: str) -> bool:
    before = text[max(0, start - 24) : start].casefold()
    normalized = raw.translate(SUBSCRIPT_TRANSLATION)
    page_match = re.match(r"\[?(\d{1,3})\]?", normalized)
    page_int = int(page_match.group(1)) if page_match else None
    if page_int is None:
        return False
    if page_int < 100 and re.search(r"(?:vers|exhort(?:ation|ations)?|lettre|syr\.?|tome|fasc\.?|chap\.)\s*$", before):
        return False
    if page_int < 100 and not raw.startswith("["):
        return False
    return True


def ref_candidates(text: str) -> list[str]:
    found: list[str] = []
    working = text.replace("72-3.", "472-3.")
    for match in PAGE_REF_RE.finditer(working):
        raw = normalize_space(match.group("raw")).rstrip(".,;")
        raw = raw.replace(" ₁", "₁").replace(" ₂", "₂").replace(" ₃", "₃").replace(" ₄", "₄").replace(" ₅", "₅").replace(" ₆", "₆").replace(" ₇", "₇").replace(" ₈", "₈").replace(" ₉", "₉").replace(" ₀", "₀")
        raw = re.sub(r"[ _₋\-–—]+$", "", raw)
        if not re.search(r"\d", raw):
            continue
        if not is_probable_locator(working, match.start(), raw):
            continue
        parts = re.split(r"\s+(?=\d{3})", raw)
        for part in parts:
            part = re.sub(r"[ _₋\-–—]+$", "", part)
            if part and is_probable_locator(working, match.start(), part):
                found.append(part)
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in found:
        if raw not in seen:
            deduped.append(raw)
            seen.add(raw)
    return deduped


def page_ref_int_from_raw(raw: str) -> int | None:
    m = re.search(r"\d{1,3}", raw.translate(SUBSCRIPT_TRANSLATION))
    if not m:
        return None
    return int(m.group(0))


def ref_kind_from_raw(raw: str) -> str:
    if "chap." in raw:
        return "editorial_page"
    if "n." in raw or "note" in raw:
        return "editorial_page"
    if any(sep in raw for sep in ("-", "à")) and re.search(r"\d", raw):
        return "editorial_range"
    return "editorial_page"


def ref_raw_evidence(entry_raw: str, ref_raw: str) -> dict[str, Any]:
    raw_json: dict[str, Any] = {}
    if ref_raw == "472-3" and "72-3" in entry_raw and "472-3" not in entry_raw:
        raw_json["ocr_locator_correction"] = {
            "printed_ocr": "72-3",
            "interpreted_as": "472-3",
            "reason": "The alphabetical table otherwise cites Ammonas pages 393-488; neighboring duplicate entry for Écritures gives 472-3 for the same wording.",
        }
    return raw_json


def make_nodes() -> list[dict[str, Any]]:
    letters = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "V", "Y", "Z"]
    nodes: list[dict[str, Any]] = []
    for order, letter in enumerate(letters, start=1):
        nodes.append(
            {
                "node_key": f"{VOLUME_ID}:node:alpha:{order:03d}",
                "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:001",
                "parent_node_key": None,
                "node_order": order,
                "node_kind": "letter_group",
                "label_raw": letter,
                "label_norm": letter.lower(),
                "label_sort": letter.lower(),
                "node_level": 1,
                "confidence": 0.99,
                "raw_json": {"source_file": ALPHA_FILES[LETTER_FILE_PAGE[letter]]},
            }
        )
    nodes.extend(
        [
            {
                "node_key": f"{VOLUME_ID}:node:analytic:001",
                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
                "parent_node_key": None,
                "node_order": 1,
                "node_kind": "ordinal_group",
                "label_raw": "I. — Tome X, fasc. 6. AMMONII EREMITAE EPISTOLAE.",
                "label_norm": "i tome x fasc 6 ammonii eremitae epistolae",
                "label_sort": "i tome x fasc 6 ammonii eremitae epistolae",
                "node_level": 1,
                "confidence": 0.95,
                "raw_json": {"source_file": ALPHA_FILES[511]},
            },
            {
                "node_key": f"{VOLUME_ID}:node:analytic:002",
                "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
                "parent_node_key": None,
                "node_order": 2,
                "node_kind": "ordinal_group",
                "label_raw": "II. — Tome XI, fasc. 4. AMMONAS, SUCCESSEUR DE SAINT ANTOINE.",
                "label_norm": "ii tome xi fasc 4 ammonas successeur de saint antoine",
                "label_sort": "ii tome xi fasc 4 ammonas successeur de saint antoine",
                "node_level": 1,
                "confidence": 0.95,
                "raw_json": {"source_file": ALPHA_FILES[511]},
            },
        ]
    )
    return nodes


def find_node_key(nodes: list[dict[str, Any]], letter: str) -> str:
    for node in nodes:
        if node["section_key"].endswith("001") and node["label_raw"] == letter:
            return node["node_key"]
    raise KeyError(letter)


def build_sections() -> list[dict[str, Any]]:
    return [
        {
            "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:001",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 1,
            "section_kind": "onomastic_mixed",
            "heading_raw": "TABLE ALPHABÉTIQUE DES NOMS PROPRES ET DES PRINCIPALES MATIÈRES",
            "heading_norm": "table alphabetique des noms propres et des principales matieres",
            "heading_letter": None,
            "page_start": 489,
            "page_end": 492,
            "file_start": ALPHA_FILES[497],
            "file_end": ALPHA_FILES[500],
            "confidence": 0.99,
            "raw_json": {
                "source_files": [ALPHA_FILES[497], ALPHA_FILES[498], ALPHA_FILES[499], ALPHA_FILES[500]],
                "section_kind_reason": "Alphabetical table of names and principal matters.",
            },
        },
        {
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
            "volume_id": VOLUME_ID,
            "work_key": None,
            "section_order": 2,
            "section_kind": "analytic_subject",
            "heading_raw": "TABLE ANALYTIQUE DES MATIÈRES",
            "heading_norm": "table analytique des matieres",
            "heading_letter": None,
            "page_start": 503,
            "page_end": 504,
            "file_start": ALPHA_FILES[511],
            "file_end": ALPHA_FILES[512],
            "confidence": 0.99,
            "raw_json": {
                "source_files": [ALPHA_FILES[511], ALPHA_FILES[512]],
                "section_kind_reason": "Analytical table of contents for the fascicles collected in this volume.",
            },
        },
    ]


def build_analytic_entries(helper_by_id: dict[str, Any], page_map: dict[int, str]) -> list[dict[str, Any]]:
    def analytic_entry(
        order: int,
        lemma: str,
        entry_raw: str,
        page_hint: int | None,
        parent_node_key: str,
        helper_id: str | None = None,
        source_file: str = ALPHA_FILES[511],
    ) -> dict[str, Any]:
        target = page_map.get(page_hint) if page_hint is not None else None
        raw_json: dict[str, Any] = {"source_file": source_file}
        if helper_id:
            snap = helper_snapshot(helper_by_id.get(helper_id))
            if snap:
                raw_json["helper"] = snap
                candidates = helper_by_id.get(helper_id, {}).get("candidates") or []
                if candidates:
                    target = candidates[0].get("file") or target
        return {
            "entry_key": f"{VOLUME_ID}:entry:{order:03d}",
            "section_key": f"{VOLUME_ID}:alpha:analytic_subject:002",
            "parent_node_key": parent_node_key,
            "entry_order": order,
            "entry_kind": "heading_group",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": lemma_norm(lemma),
            "lemma_sort": lemma_norm(lemma),
            "entry_raw": entry_raw,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": 503 if order < 200 else 504,
            "section_start_file": ALPHA_FILES[511],
            "editorial_anchor_file": source_file,
            "target_file_best": target,
            "confidence": 0.9 if target else 0.82,
            "raw_json": raw_json,
        }

    return [
        analytic_entry(200, "De Ammonio monacho epistolarum auctore", "PRAEFATIO. De Ammonio monacho epistolarum auctore [3]", 3, f"{VOLUME_ID}:node:analytic:001", "po011_preface_de_ammonio"),
        analytic_entry(201, "Epistolae Mari Ammonii eremitae", "Epistolae Mari Ammonii eremitae [15]", 15, f"{VOLUME_ID}:node:analytic:001", "po011_preface_epistolae_mari"),
        analytic_entry(202, "Epistolae dubiae", "Epistolae dubiae [65]", 65, f"{VOLUME_ID}:node:analytic:001", "po011_preface_epistolae_dubiae"),
        analytic_entry(203, "L'auteur", "INTRODUCTION. L'auteur 393. Les textes 395. Histoire littéraire 399. Objet de la présente édition 400. Sigles 402.", 393, f"{VOLUME_ID}:node:analytic:002"),
        analytic_entry(204, "TEXTES GRECS ET SYRIAQUES", "TEXTES GRECS ET SYRIAQUES. I. — Apophtegmes grecs 403. II. — syriaques 410.", 403, f"{VOLUME_ID}:node:analytic:002"),
        analytic_entry(205, "Deux chapitres de la version syriaque de l'Historia monachorum de Rufin", "III. — Deux chapitres de la version syriaque de l'Historia monachorum de Rufin 424.", 424, f"{VOLUME_ID}:node:analytic:002", source_file=ALPHA_FILES[512]),
        analytic_entry(206, "Lettres d'Ammonas", "IV. — Lettres d'Ammonas 432. I (syr. XII) 432. II (syr. II et III, 4) 435. III (syr. IV) 438. IV (syr. IX; X, 1 à 2; VIII) 440. V (syr. XI) 446. VI (syr. III) 450. VII (syr. XIII) 452.", 432, f"{VOLUME_ID}:node:analytic:002", source_file=ALPHA_FILES[512]),
        analytic_entry(207, "Instructions d'Ammonas", "V. — Instructions d'Ammonas. 1° Quatre enseignements 455. 2° Dix-neuf exhortations 458. 3° Discours aux solitaires 472. 4° Conseils aux novices 474.", 455, f"{VOLUME_ID}:node:analytic:002", source_file=ALPHA_FILES[512]),
        analytic_entry(208, "Deux fragments", "VI. — Deux fragments 484.", 484, f"{VOLUME_ID}:node:analytic:002", source_file=ALPHA_FILES[512]),
        analytic_entry(209, "Table des citations", "Table des citations 488. Table alphabétique des noms propres et des principales matières 489. Table des mots syriaques 493. Table analytique des matières 503.", 488, f"{VOLUME_ID}:node:analytic:002", source_file=ALPHA_FILES[512]),
    ]


def build_entries_and_refs(nodes: list[dict[str, Any]], helper_by_id: dict[str, Any], page_map: dict[int, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    order = 1
    alpha_entries = corrected_alpha_entries()

    for letter in ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "V", "Y", "Z"]:
        parent_node_key = find_node_key(nodes, letter)
        inferred_printed_page = 489 + [497, 498, 499, 500].index(LETTER_FILE_PAGE[letter])
        for item in alpha_entries.get(letter, []):
            entry_raw = item["entry_raw"]
            lower = entry_raw.casefold()
            candidate_refs = ref_candidates(entry_raw)
            entry_kind = "cross_reference" if lower.startswith(("v. ", "voir ", "vide ", "vid. ")) or (not candidate_refs and re.search(r"(^|[. —])\s*V\.", entry_raw)) else "lemma"
            lemma = entry_raw.split("  ", 1)[0]
            if " — " in lemma and not re.search(r"\d", lemma):
                lemma = lemma.split(" — ", 1)[0]
            lemma = re.split(r"\s(?=\d|\[\d)", entry_raw, maxsplit=1)[0]
            if entry_kind == "cross_reference":
                lemma = entry_raw
            entry = {
                "entry_key": f"{VOLUME_ID}:entry:{order:03d}",
                "section_key": f"{VOLUME_ID}:alpha:onomastic_mixed:001",
                "parent_node_key": parent_node_key,
                "entry_order": order,
                "entry_kind": entry_kind,
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": lemma_norm(lemma),
                "lemma_sort": lemma_norm(lemma),
                "entry_raw": entry_raw,
                "context_raw": None,
                "heading_letter": letter,
                "inferred_printed_page": inferred_printed_page,
                "section_start_file": ALPHA_FILES[497],
                "editorial_anchor_file": ALPHA_FILES[item["file_seq"]],
                "target_file_best": None,
                "confidence": 0.9,
                "raw_json": {"source_file": ALPHA_FILES[item["file_seq"]]},
            }
            entry_refs: list[dict[str, Any]] = []
            if entry_kind != "cross_reference":
                for ref_order, ref_raw in enumerate(candidate_refs, start=1):
                    page_int = page_ref_int_from_raw(ref_raw)
                    target = page_map.get(page_int) if page_int is not None else None
                    raw_json = {"source_file": ALPHA_FILES[item["file_seq"]]}
                    raw_json.update(ref_raw_evidence(entry_raw, ref_raw))
                    entry_refs.append(
                        {
                            "entry_key": entry["entry_key"],
                            "ref_order": ref_order,
                            "ref_kind": ref_kind_from_raw(ref_raw),
                            "ref_raw": ref_raw,
                            "page_ref_raw": ref_raw,
                            "page_ref_int": page_int,
                            "page_ref_col": None,
                            "line_ref_raw": None,
                            "range_start_raw": None,
                            "range_end_raw": None,
                            "target_file": target,
                            "target_file_probability": 0.99 if target else None,
                            "section_start_file": ALPHA_FILES[497],
                            "editorial_anchor_file": ALPHA_FILES[item["file_seq"]],
                            "confidence": 0.9 if target else 0.75,
                            "raw_json": raw_json,
                        }
                    )
            if entry_refs:
                entry["target_file_best"] = entry_refs[0]["target_file"]
            entries.append(entry)
            refs.extend(entry_refs)
            order += 1

    analytic_entries = build_analytic_entries(helper_by_id, page_map)
    analytic_refs_start_order = order
    for analytic_index, entry in enumerate(analytic_entries, start=analytic_refs_start_order):
        entry["entry_key"] = f"{VOLUME_ID}:entry:{analytic_index:03d}"
        entries.append(entry)
        ref_list = ref_candidates(entry["entry_raw"])
        for ref_order, ref_raw in enumerate(ref_list, start=1):
            page_int = page_ref_int_from_raw(ref_raw)
            target = entry["target_file_best"] if ref_order == 1 else page_map.get(page_int)
            refs.append(
                {
                    "entry_key": entry["entry_key"],
                    "ref_order": ref_order,
                    "ref_kind": ref_kind_from_raw(ref_raw),
                    "ref_raw": ref_raw,
                    "page_ref_raw": ref_raw,
                    "page_ref_int": page_int,
                    "page_ref_col": None,
                    "line_ref_raw": None,
                    "range_start_raw": None,
                    "range_end_raw": None,
                    "target_file": target,
                    "target_file_probability": 0.9 if target else None,
                    "section_start_file": ALPHA_FILES[511],
                    "editorial_anchor_file": entry["editorial_anchor_file"],
                    "confidence": 0.88 if target else 0.75,
                    "raw_json": deepcopy(entry["raw_json"]),
                }
            )

    return entries, refs


def volume_json() -> dict[str, Any]:
    return {
        "volume_id": VOLUME_ID,
        "collection": COLLECTION,
        "source_root": str(SOURCE_ROOT),
        "volume_label": "Patrologia Orientalis, tome XI",
    }


def coverage_json() -> dict[str, Any]:
    return {
        "entries_status": "complete",
        "entries_status_reason": "Rebuilt the alphabetical table as lemma-level entries with letter-group nodes, fixed OCR line-break hyphen artifacts, and kept the analytical table as scoped heading-group entries with improved locator evidence.",
        "evidence_files": [ALPHA_FILES[497], ALPHA_FILES[498], ALPHA_FILES[499], ALPHA_FILES[500], ALPHA_FILES[511], ALPHA_FILES[512]],
    }


def notes_json() -> list[str]:
    return [
        "Rerun rebuilt the alphabetical section from OCR pages 497-500 instead of preserving oversized page-block entries.",
        "Line-break hyphen artifacts were merged only when the OCR clearly continued the same word across lines.",
        "This rerun corrected false locator splitting where subscript line numbers such as 405₁₁ had previously produced anchorless refs like 11.",
        "Analytical fascicle headings from the older Tome X fascicle use helper evidence where direct page-to-file mapping is weaker.",
    ]


def update_todo() -> None:
    todo = {
        "volume_id": VOLUME_ID,
        "updated_at": now_iso(),
        "current_focus": "Rebuild PO011 alphabetical entries and validate the replacement payload",
        "completed": [
            "confirmed alphabetical pages 497-500 directly in OCR",
            "confirmed analytical pages 511-512 directly in OCR",
            "resegmented the alphabetical table into lemma-level entries and letter nodes",
            "resolved false-small-page refs caused by subscript line numbers",
            "wrote helper request for the unresolved first-fascicle analytical headings",
        ],
        "pending": [
            "run helper and validate final payload",
        ],
        "blocked": [],
        "notes": [
            "Reuse the previous payload only as a page-map checkpoint for resolved printed pages.",
            "Keep Tome X fasc. 6 analytical locators conservative if helper evidence stays ambiguous.",
        ],
    }
    write_json(TODO_PATH, todo)


def write_intermediates(payload: dict[str, Any]) -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(INTERMEDIATE_DIR / "volume.json", payload["volume"])
    write_json(INTERMEDIATE_DIR / "sections.json", payload["sections"])
    write_json(INTERMEDIATE_DIR / "nodes.json", payload["nodes"])
    write_json(INTERMEDIATE_DIR / "entries.json", payload["entries"])
    write_json(INTERMEDIATE_DIR / "refs.json", payload["refs"])
    write_json(INTERMEDIATE_DIR / "scripture_refs.json", payload["scripture_refs"])
    write_json(INTERMEDIATE_DIR / "coverage.json", payload["coverage"])
    write_json(INTERMEDIATE_DIR / "notes.json", payload["notes"])
    write_json(
        INTERMEDIATE_DIR / "manifest.json",
        {
            "volume_id": VOLUME_ID,
            "generated_at": payload["generated_at"],
            "updated_at": payload["generated_at"],
            "source_files": [ALPHA_FILES[497], ALPHA_FILES[498], ALPHA_FILES[499], ALPHA_FILES[500], ALPHA_FILES[511], ALPHA_FILES[512]],
        },
    )


def build_payload() -> dict[str, Any]:
    helper_by_id = load_helper_output()
    page_map = build_page_map(previous_payload())
    nodes = make_nodes()
    entries, refs = build_entries_and_refs(nodes, helper_by_id, page_map)
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "volume": volume_json(),
        "sections": build_sections(),
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": coverage_json(),
        "notes": notes_json(),
    }


def main() -> None:
    INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(HELPER_REQUEST_PATH, make_helper_request())
    update_todo()
    payload = build_payload()
    write_intermediates(payload)
    write_json(OUTPUT_PATH, payload)


if __name__ == "__main__":
    main()
