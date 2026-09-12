#!/usr/bin/env python3
"""Build PO014 alphabetical-index payload.

Usage: python scripts/pipeline_index_extraction/build_po014_alphabetical_payload.py
The script reads PO014 OCR through the project OCR reader, segments the tail
index/closure pages, and writes data/alphabetical_index_payloads/PO014_alphabetical_indices.json.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/homessddata/Projects/pdfocr")
sys.path.insert(0, str(ROOT))

from tools.corpus_utils import page_number, page_sort_key
from tools.ocr_xml_utils import read_ocr_page


SOURCE_ROOT = ROOT / "teste/PO014/text"
OUT = ROOT / "data/alphabetical_index_payloads/PO014_alphabetical_indices.json"
HELPER_REQUEST = ROOT / "data/alphabetical_index_payloads/PO014_helper_request.json"
HELPER_OUTPUT = ROOT / "data/alphabetical_index_payloads/PO014_helper_output.json"
INTERMEDIATE = ROOT / "data/intermediate_payloads/PO014"


def norm_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sort_text(value: str | None) -> str | None:
    normalized = norm_text(value)
    if normalized is None:
        return None
    return re.sub(r"[^a-z0-9α-ωἀ-῾]+", " ", normalized).strip()


def raw_block_text(block: object) -> str:
    raw = getattr(block, "content_raw", "") or getattr(block, "content_clean", "")
    lines = [line.strip() for line in raw.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def logical_join(lines: list[str]) -> str:
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-"):
            if len(out) >= 2 and out[-2].isdigit() and line[:1].isdigit():
                out = out + line
            else:
                out = out[:-1] + line
        else:
            out += " " + line
    return re.sub(r"\s+", " ", out).strip()


def clean_entry_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return logical_join(lines)


def body_blocks(seq: int) -> list[str]:
    path = next(SOURCE_ROOT.glob(f"*-{seq:03d}.txt"))
    page = read_ocr_page(path)
    return [raw_block_text(block) for block in page.blocks if getattr(block, "tipo", "") == "texto_principal"]


def ocr_file(seq: int) -> str:
    return str(next(SOURCE_ROOT.glob(f"*-{seq:03d}.txt")))


def page_map() -> dict[int, str]:
    """Best-effort local map for page refs in this multi-fascicle volume."""
    mapping: dict[int, str] = {}
    fallback: dict[int, str] = {}
    for path in sorted(SOURCE_ROOT.glob("*.txt"), key=page_sort_key):
        seq = page_number(path)
        if 1 <= seq <= 900:
            fallback.setdefault(seq, str(path))
        page = read_ocr_page(path)
        candidates = re.findall(r"(?<!\[)\b([1-8]?\d{1,2})\b(?!\])", page.header_text or "")
        for raw in candidates:
            num = int(raw)
            if 1 <= num <= 900:
                mapping.setdefault(num, str(path))
    for num, path in fallback.items():
        mapping.setdefault(num, path)
    return mapping


PAGE_MAP = page_map()


def extract_refs(entry_key: str, entry_raw: str, section_file: str, anchor_file: str) -> list[dict]:
    refs: list[dict] = []
    # Skip leading editorial ordinal in contents entries.
    text = re.sub(r"^\s*\d+\s*(?:\(\d+\))?\.\s*", "", entry_raw)
    matches = list(re.finditer(r"(?<![A-Za-z])(\d{1,3})(?:\s*-\s*(\d{1,3}))?(?:\s*\(\?\))?", text))
    seen: set[tuple[str, str | None]] = set()
    for match in matches:
        raw_start, raw_end = match.group(1), match.group(2)
        start = int(raw_start)
        if start > 900:
            continue
        key = (raw_start, raw_end)
        if key in seen:
            continue
        seen.add(key)
        ref_raw = match.group(0).strip()
        target = PAGE_MAP.get(start)
        refs.append(
            {
                "entry_key": entry_key,
                "ref_order": len(refs) + 1,
                "ref_kind": "editorial_range" if raw_end else "editorial_page",
                "ref_raw": ref_raw,
                "page_ref_raw": raw_start,
                "page_ref_int": start,
                "page_ref_col": None,
                "line_ref_raw": None,
                "range_start_raw": raw_start,
                "range_end_raw": raw_end,
                "target_file": target,
                "target_file_probability": 0.72 if target else None,
                "section_start_file": section_file,
                "editorial_anchor_file": anchor_file,
                "confidence": 0.86 if target else 0.62,
                "raw_json": {
                    "locator_method": "local_page_map",
                    "page_map_note": "Mapped cited page to the same-number OCR file or a header-derived page signal inside PO014.",
                },
            }
        )
    return refs


def lemma_from_entry(entry_raw: str) -> str:
    text = re.sub(r"^\s*\d+\s*(?:\(\d+\))?\.\s*", "", entry_raw).strip()
    text = re.split(r",\s*\d|;\s*\d|\.\s*\d|\. \.|\s\d{1,3}(?:\.|,|;|$)", text, maxsplit=1)[0]
    text = text.rstrip(" .,;:")
    if text.startswith("—"):
        text = text[1:].strip(" .,;:")
    return text[:180] or entry_raw[:180]


def split_index_entries(seq: int) -> list[str]:
    entries: list[str] = []
    for block in body_blocks(seq):
        current: list[str] = []
        for line in [ln.strip() for ln in block.splitlines() if ln.strip()]:
            if line.startswith("PATR. OR."):
                continue
            starts_continuation = bool(re.match(r"^(\d|[a-z]|\(|of,|cited,|fide\)|[0-9]+;)", line))
            previous_complete = bool(current and current[-1].rstrip().endswith("."))
            starts_new = not starts_continuation and (not current or previous_complete)
            if starts_new and current:
                entries.append(logical_join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            entries.append(logical_join(current))
    return entries


def split_dotted_entries(seq: int) -> list[str]:
    entries: list[str] = []
    current: list[str] = []
    dotted_page = re.compile(r"(?:\.\s*){3,}\d+\.?$")
    for block in body_blocks(seq):
        for line in [ln.strip() for ln in block.splitlines() if ln.strip()]:
            if line in {"APPENDIX", "Letters of Severus :", "DU TOME XIV"}:
                continue
            if dotted_page.search(line):
                current.append(line)
                entries.append(logical_join(current))
                current = []
            elif re.match(r"^\d+\s*(?:\(\d+\))?\.\s+", line) and current:
                entries.append(logical_join(current))
                current = [line]
            else:
                current.append(line)
    if current and re.search(r"\d", " ".join(current)):
        entries.append(logical_join(current))
    return [e for e in entries if re.search(r"\d|[IVXLCDM]$", e)]


def split_addenda_entries(seq: int) -> list[str]:
    entries: list[str] = []
    current: list[str] = []
    for block in body_blocks(seq):
        for line in [ln.strip() for ln in block.splitlines() if ln.strip()]:
            if line.startswith("P. ") or line.startswith("— "):
                if current:
                    entries.append(logical_join(current))
                current = [line]
            else:
                current.append(line)
    if current:
        entries.append(logical_join(current))
    return entries


def add_section(sections: list[dict], key: str, order: int, kind: str, heading: str, start: int, end: int, work: str | None, page_start: int | None, page_end: int | None, note: str) -> dict:
    section = {
        "section_key": key,
        "volume_id": "PO014",
        "work_key": work,
        "section_order": order,
        "section_kind": kind,
        "heading_raw": heading,
        "heading_norm": norm_text(heading),
        "heading_letter": None,
        "page_start": page_start,
        "page_end": page_end,
        "file_start": ocr_file(start),
        "file_end": ocr_file(end),
        "confidence": 0.98,
        "raw_json": {
            "section_kind_reason": note,
            "section_files": [ocr_file(seq) for seq in range(start, end + 1)],
        },
    }
    sections.append(section)
    return section


def main() -> None:
    INTERMEDIATE.mkdir(parents=True, exist_ok=True)
    sections: list[dict] = []
    nodes: list[dict] = []
    entries: list[dict] = []
    refs: list[dict] = []

    section1 = add_section(
        sections,
        "PO014:alpha:analytic_subject:001",
        1,
        "analytic_subject",
        "TABLE ANALYTIQUE DES MATIÈRES",
        853,
        856,
        "LES MIRACLES DE JÉSUS",
        842,
        844,
        "Analytical contents table for the closing fascicle Les Miracles de Jésus.",
    )
    section1["raw_json"]["helper_request"] = str(HELPER_REQUEST)
    section1["raw_json"]["helper_output"] = str(HELPER_OUTPUT)

    previous = json.loads(OUT.read_text(encoding="utf-8"))
    previous_entries = {item["entry_key"]: item for item in previous["entries"]}
    previous_refs = {item["entry_key"]: item for item in previous["refs"]}
    analytic_context = {
        2: "1. Une génisse ayant été volée, Jésus est choisi comme juge à la fois par le propriétaire et par le voleur. — 2. Il ordonne à la génisse de parler, laquelle déclare qu'elle appartient au vieillard Kèmèmour, fils de Nàzer, et qu'elle a été volée à Césarée. — 3. Il pardonne au voleur qui se repent. — 4. Le voleur demande à Jésus de devenir son disciple.",
        3: "1. Joseph, en allant avec Jésus de Tibériade à Jérusalem, est épouvanté, sur la route de Galilée, par la vue d'un lion en embuscade. — 2. Jésus le rassure. — 3. Arrivé auprès du lion, il lui ordonne de parler : le lion confesse la divinité de Jésus. — 4. Jésus déclare à Joseph émerveillé de ce prodige qu'il verra de plus grands miracles que celui-ci. — 5. Il le fait monter au sommet du Thabor et lui montre sa puissance.",
        4: "1. Un habitant de Naplouse ruiné par l'inondation de son champ vient demander secours à Jésus qui se trouvait alors sur la route de Nazareth. — 2. Jésus l'accompagne, dessèche le champ, fait lever la semence et mûrir les épis immédiatement. — 3. Il fait constater le miracle. — 4. Les témoins de la scène ainsi que les habitants de Naplouse croient en la divinité de Jésus. — 5. Le propriétaire du champ devient disciple de Jésus et convertit beaucoup de Samaritains et de Juifs.",
        5: "1. Les Juifs vantent à Jésus la grandeur et la beauté du temple de Jérusalem. — 2. Comme ils le traitent de fou, parce qu'il a déclaré pouvoir rebâtir le temple en trois jours, Jésus propose un miracle comme preuve de sa divinité. — 3. Il choisit l'image de la vision d'Ézéchiel. — 4. Il donnera la vie aux quatre animaux (face d'homme, face de lion, face de bœuf, face d'aigle, Ézéch., 1, 10) représentés sur l'image.",
        6: "1. Les Juifs amènent à Jésus, afin qu'il la juge, une femme surprise en adultère. — 2. Jésus écrit à terre les péchés communs à tous les Juifs; il demande ensuite qu'on conduise la pécheresse au lieu du supplice, afin qu'elle soit lapidée en sa présence. — 3. Il écrit à terre les péchés individuels de chacun des Juifs qui sont présents; voyant ceci, les Juifs partent tous. — 4. Il pardonne à la femme adultère. — 5. Celle-ci raconte son histoire et convertit beaucoup de gens.",
        7: "I. DIALOGUE ENTRE JÉSUS ET LA SAMARITAINE. — 1. Au sujet de l'eau du puits de Jacob et de l'eau de la grâce. — 2. Au sujet des cinq maris de la Samaritaine nommément désignés par Jésus. — 3. Au sujet de ses père et mère également nommés par Jésus. — 4. Au sujet du Christ attendu (Jésus déclare qu'il est le Christ).",
        8: "1. VOCATION DE SIMON ET D'ANDRÉ. — 1. Sur le bord du lac de Tibériade, a Magdala, Jésus appelle à sa suite Simon et André, fils de Jonas. — 2. Confession de Simon. — II. BAPTÊME DE SIMON ET D'ANDRÉ. — 4. Jésus conduit au Jourdain Simon et André, afin qu'ils soient baptisés par Jean.",
        9: "1. Un homme aveugle, sourd et muet demande à Jésus, qui traverse Jérusalem, de le guérir. — 2. Jésus déclare à Simon-Pierre que la guérison qu'il va opérer doit servir à montrer aux Juifs sa puissance. — 3. D'abord Jésus rend la parole à cet infirme qui confesse aussitôt, devant Simon-Pierre et André, la divinité du Christ.",
        10: "1. En traversant Naïm, Jésus rencontre le convoi funèbre de Yonâs, de souche prophétique, fils unique de la veuve Bar'e'â. — 2. Il ordonne aux porteurs de déposer la civière. — 3. Il ressuscite Yonâs qui confesse sa divinité et devient son disciple.",
        11: "1. Venu à Jérusalem pour la fête des Tabernacles, Jésus voit un Israélite qui pleure son frère mort le jour même et implore la venue du Messie. — 2. Il lui déclare qu'il est le Christ. — 3. Il lui propose, comme preuve de sa divinité, de ressusciter son frère.",
    }
    for idx in range(1, 12):
        old_key = f"PO014:entry:{idx:03d}"
        item = dict(previous_entries[old_key])
        analytic_summary = analytic_context.get(idx)
        item["context_raw"] = None
        if analytic_summary and analytic_summary not in item["entry_raw"]:
            item["entry_raw"] = item["entry_raw"] + "\n" + analytic_summary
        item["raw_json"]["checkpoint_reuse"] = "Verified against OCR reader output for files 853-856; context expanded where the table prints synopsis lines belonging to the same logical entry."
        entries.append(item)
        ref = dict(previous_refs[old_key])
        refs.append(ref)

    section2 = add_section(
        sections,
        "PO014:alpha:onomastic_mixed:002",
        2,
        "onomastic_mixed",
        "INDEX TO THE LETTERS OF SEVERUS AND APPENDIX T. XII, FASC. 2 AND T. XIV, FASC. 1",
        857,
        860,
        "LETTERS OF SEVERUS AND APPENDIX",
        845,
        848,
        "Alphabetical index of persons, places, subjects, authors cited, and Greek terms.",
    )
    previous_lemma: str | None = None
    for seq in range(857, 861):
        for raw in split_index_entries(seq):
            lemma = lemma_from_entry(raw)
            inherited = False
            if raw.startswith("—") and previous_lemma:
                lemma = f"{previous_lemma}, {lemma}"
                inherited = True
            elif not raw.startswith("—"):
                previous_lemma = lemma.split(",")[0]
            key = f"PO014:entry:{len(entries) + 1:03d}"
            entry = {
                "entry_key": key,
                "section_key": section2["section_key"],
                "parent_node_key": None,
                "entry_order": len(entries) + 1,
                "entry_kind": "cross_reference" if re.search(r"\bSee\b", raw) and not re.search(r"\d", raw) else "lemma",
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": norm_text(lemma),
                "lemma_sort": sort_text(lemma),
                "entry_raw": raw,
                "context_raw": None,
                "heading_letter": lemma[:1].upper() if lemma else None,
                "inferred_printed_page": None,
                "section_start_file": section2["file_start"],
                "editorial_anchor_file": ocr_file(seq),
                "target_file_best": None,
                "confidence": 0.91,
                "raw_json": {
                    "source_file_seq": seq,
                    "segmentation": "line entry with continuations joined from index columns",
                    "inherited_dash": inherited,
                },
            }
            entry_refs = extract_refs(key, raw, section2["file_start"], ocr_file(seq))
            if entry_refs:
                entry["inferred_printed_page"] = entry_refs[0]["page_ref_int"]
                entry["target_file_best"] = entry_refs[0]["target_file"]
            entries.append(entry)
            refs.extend(entry_refs)

    section3 = add_section(
        sections,
        "PO014:alpha:editorial_closure:003",
        3,
        "editorial_closure",
        "ADDENDA AND CORRIGENDA TO T. XII, FASC. 2 AND T. XIV, FASC. 1",
        861,
        863,
        "T. XII, FASC. 2 AND T. XIV, FASC. 1",
        849,
        851,
        "Editorial addenda and corrigenda captured in the filtered tail window.",
    )
    last_addenda_ref: dict | None = None
    for seq in range(861, 864):
        for raw in split_addenda_entries(seq):
            if not raw.startswith(("P. ", "— ")):
                continue
            key = f"PO014:entry:{len(entries) + 1:03d}"
            lemma = raw.split(". ", 1)[0].strip(".")
            entry = {
                "entry_key": key,
                "section_key": section3["section_key"],
                "parent_node_key": None,
                "entry_order": len(entries) + 1,
                "entry_kind": "editorial_note",
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": norm_text(lemma),
                "lemma_sort": sort_text(lemma),
                "entry_raw": raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": None,
                "section_start_file": section3["file_start"],
                "editorial_anchor_file": ocr_file(seq),
                "target_file_best": None,
                "confidence": 0.86,
                "raw_json": {"source_file_seq": seq, "section_role": "addenda_corrigenda"},
            }
            entry_refs = extract_refs(key, raw, section3["file_start"], ocr_file(seq))
            if not entry_refs and raw.startswith("—") and last_addenda_ref:
                inherited_ref = dict(last_addenda_ref)
                inherited_ref["entry_key"] = key
                inherited_ref["ref_order"] = 1
                inherited_ref["ref_raw"] = inherited_ref["ref_raw"]
                inherited_ref["confidence"] = min(inherited_ref["confidence"], 0.72)
                inherited_ref["target_file_probability"] = min(inherited_ref["target_file_probability"] or 0.72, 0.72)
                inherited_ref["raw_json"] = {
                    **inherited_ref.get("raw_json", {}),
                    "inherited_dash_locator": True,
                    "inherited_from_previous_addenda_entry": True,
                }
                entry_refs = [inherited_ref]
            if entry_refs:
                entry["inferred_printed_page"] = entry_refs[0]["page_ref_int"]
                entry["target_file_best"] = entry_refs[0]["target_file"]
                if not raw.startswith("—"):
                    last_addenda_ref = entry_refs[0]
            entries.append(entry)
            refs.extend(entry_refs)

    section4 = add_section(
        sections,
        "PO014:alpha:ordo_rerum:004",
        4,
        "ordo_rerum",
        "CONTENTS OF T. XII, FASC. 2 AND T. XIV, FASC. 1",
        864,
        867,
        "T. XII, FASC. 2 AND T. XIV, FASC. 1",
        852,
        855,
        "Contents section with page-bearing line items, not an alphabetical index.",
    )
    for seq in range(864, 868):
        for raw in split_dotted_entries(seq):
            key = f"PO014:entry:{len(entries) + 1:03d}"
            lemma = lemma_from_entry(raw)
            entry = {
                "entry_key": key,
                "section_key": section4["section_key"],
                "parent_node_key": None,
                "entry_order": len(entries) + 1,
                "entry_kind": "heading_group" if not re.match(r"^\d", raw) else "lemma",
                "lemma_raw": lemma,
                "lemma_display": lemma,
                "lemma_norm": norm_text(lemma),
                "lemma_sort": sort_text(lemma),
                "entry_raw": raw,
                "context_raw": None,
                "heading_letter": None,
                "inferred_printed_page": None,
                "section_start_file": section4["file_start"],
                "editorial_anchor_file": ocr_file(seq),
                "target_file_best": None,
                "confidence": 0.88,
                "raw_json": {"source_file_seq": seq, "section_role": "contents"},
            }
            entry_refs = extract_refs(key, raw, section4["file_start"], ocr_file(seq))
            if entry_refs:
                entry["inferred_printed_page"] = entry_refs[0]["page_ref_int"]
                entry["target_file_best"] = entry_refs[0]["target_file"]
            entries.append(entry)
            refs.extend(entry_refs)

    section5 = add_section(
        sections,
        "PO014:alpha:ordo_rerum:005",
        5,
        "ordo_rerum",
        "TABLE DES MATIÈRES DU TOME XIV",
        868,
        868,
        "TOME XIV",
        None,
        None,
        "General table of contents for tome XIV; included because the filtered tail window contains it after the alphabetical index.",
    )
    for raw in split_dotted_entries(868):
        key = f"PO014:entry:{len(entries) + 1:03d}"
        lemma = lemma_from_entry(raw)
        entry = {
            "entry_key": key,
            "section_key": section5["section_key"],
            "parent_node_key": None,
            "entry_order": len(entries) + 1,
            "entry_kind": "heading_group",
            "lemma_raw": lemma,
            "lemma_display": lemma,
            "lemma_norm": norm_text(lemma),
            "lemma_sort": sort_text(lemma),
            "entry_raw": raw,
            "context_raw": None,
            "heading_letter": None,
            "inferred_printed_page": None,
            "section_start_file": section5["file_start"],
            "editorial_anchor_file": ocr_file(868),
            "target_file_best": None,
            "confidence": 0.9,
            "raw_json": {"source_file_seq": 868, "section_role": "table_des_matieres"},
        }
        entry_refs = extract_refs(key, raw, section5["file_start"], ocr_file(868))
        if entry_refs:
            entry["inferred_printed_page"] = entry_refs[0]["page_ref_int"]
            entry["target_file_best"] = entry_refs[0]["target_file"]
        entries.append(entry)
        refs.extend(entry_refs)

    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "volume": {
            "volume_id": "PO014",
            "collection": "PO",
            "source_root": str(SOURCE_ROOT),
            "volume_label": "PO014",
            "notes": [
                "Rebuilt from the previous checkpoint after verifying OCR files 853-868.",
                "The previous payload omitted the English alphabetical index in OCR files 857-860 and the filtered tail closure/contents pages.",
                "The OCR literal 140-048 is retained for the Seizième Miracle range; it likely represents 140-148.",
            ],
        },
        "sections": sections,
        "nodes": nodes,
        "entries": entries,
        "refs": refs,
        "scripture_refs": [],
        "coverage": {
            "entries_status": "extracted",
            "entries_status_reason": "Recovered the analytical table, the omitted English alphabetical index, addenda/corrigenda, and tail contents sections from OCR files 853-868; empty guard/library pages 869-876 were inspected and excluded.",
            "evidence_files": [ocr_file(seq) for seq in range(853, 869)],
        },
        "notes": [
            "Files 857-860 contain a true alphabetical mixed index, not merely trailing matter.",
            "Files 861-863 are editorial closure addenda/corrigenda; files 864-868 are contents/table sections rather than alphabetical entries.",
            "Reference target files for newly extracted sections use a conservative local page map; helper output is preserved for the original miracle-table locators.",
        ],
    }

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (INTERMEDIATE / "todo.json").write_text(
        json.dumps(
            {
                "volume_id": "PO014",
                "updated_at": payload["generated_at"],
                "current_focus": "Payload rebuilt and ready for validation",
                "completed": [
                    "verified OCR files 853-868",
                    "added omitted English alphabetical index",
                    "added filtered tail closure and contents sections",
                    "assembled final payload",
                ],
                "pending": [],
                "blocked": [],
                "notes": [
                    "Files 869-876 were empty or binding/library material and excluded.",
                    "Local page-map locators are intentionally lower-confidence than directly verified helper locators.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
