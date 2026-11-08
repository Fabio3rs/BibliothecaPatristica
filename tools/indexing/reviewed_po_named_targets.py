"""Scoped, reproducible page candidates for PO008 canons and PO018 documents.

Inputs are current OCR and literal extracted reference metadata. No database is
opened or modified. The reviewed scopes select editions, never target offsets.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import re

from tools.indexing.scoped_body_numbering import page_list

VERSION = "reviewed-po-named-targets-v2"
BLOCK = re.compile(r'<bloco\b[^>]*tipo="([^"]+)"[^>]*>(.*?)</bloco>', re.S)
PO008_SECTIONS = {"po008-apostles-canons-subjects", "po008-apostles-canons-scripture"}
CHAPTERS = {
    "amulette": (409, 433, "AMULETTES CHRÉTIENNES"),
    "liturgie": (434, 460, "FRAGMENTS LITURGIQUES ET PRIÈRES"),
    "livres canoniques": (461, 477, "FRAGMENTS DE LIVRES CANONIQUES"),
    "textes divers": (478, 509, "TEXTES DIVERS DE LA LITTÉRATURE CHRÉTIENNE"),
}
NUMBER_TITLE = re.compile(
    r"(?m)^\s*(\d{1,2})[).]?\s+(?:Évangile|Épître|Ire?\s+[Éé]pître|Première\s+[Éé]pître|"
    r"Apocalypse|Papyrus|Fragments?|Liturgie|Adoration|Prières?|Deux|Ancienne|Réflexions|"
    r"Le Trisagion|Bénédiction|Acta|Entretien|Anecdotes|Un prétendu|Brouillon|Vieil)\b[^\n]*"
)
LETTER_TITLE = re.compile(r"(?m)^\s*([A-Q])\.\s+(?:Amulette|Papyrus|Tablette)\b[^\n]*")


def _files(root, volume, first, last):
    found = {}
    for path in sorted((root / "teste" / volume / "text").glob("*.txt")):
        match = re.search(r"-(\d+)\.txt$", path.name)
        if match and first <= int(match[1]) <= last:
            physical = int(match[1])
            if physical in found:
                raise ValueError(f"Duplicate physical file: {volume}/{physical}")
            found[physical] = path.resolve()
    return found


def _evidence(path, raw, start, end, **extra):
    return {"source": str(path), "span": [start, end], "raw": raw[start:end], **extra}


def _headers(path):
    raw = path.read_text(encoding="utf-8")
    parts = []
    for block in BLOCK.finditer(raw):
        if block[1] != "cabecalho":
            break
        parts.append(block)
    if not parts:
        return None
    text = " ".join(b[2] for b in parts)
    if not re.search(r"INTRODUCTION|CANONS DES AP[OÔ]TRES", text):
        return None
    bracketed = re.findall(r"\[\s*(\d{1,3})\s*\]", text)
    numbers = [int(x) for x in re.findall(r"(?<!\w)\d+(?!\w)", text)]
    if bracketed:
        number = int(bracketed[0])
    elif len(numbers) >= 2:
        number = min(numbers)
    else:
        return None
    if not 1 <= number <= 150:
        return None
    return {"page": number, "evidence": _evidence(path, raw, parts[0].start(2), parts[-1].end(2))}


def page_sequence(files):
    """Validate headers locally; interpolate only between concordant anchors.

    A corrupted printed number is not authoritative over both neighbors. We
    retain its literal observation but use the locally agreed sequence, with an
    uncertainty flag. A titled introduction immediately before two agreeing
    headers can additionally supply a lower-confidence opening-page candidate.
    """
    observed = {p: h for p, path in sorted(files.items()) if (h := _headers(path))}
    valid = {}
    for physical, header in observed.items():
        offset = physical - header["page"]
        nearby = [p - h["page"] for p, h in observed.items() if 0 < abs(p - physical) <= 4]
        if Counter(nearby)[offset] >= 2:
            valid[physical] = header
    result = {}
    for physical, path in sorted(files.items()):
        header = valid.get(physical)
        uncertainty = []
        if header:
            page = header["page"]
            evidence = [header["evidence"]]
            method = "printed_fascicle_header"
        else:
            left = [p for p in valid if 0 < physical - p <= 4]
            right = [p for p in valid if 0 < p - physical <= 4]
            pairs = [(a, b) for a in left for b in right
                     if a - valid[a]["page"] == b - valid[b]["page"]]
            if not pairs:
                next_headers = [p for p in sorted(valid) if physical < p <= physical + 2]
                raw = path.read_text(encoding="utf-8")
                first_block = BLOCK.search(raw)
                if (len(next_headers) == 2 and first_block
                        and first_block[1] == "cabecalho" and first_block[2].strip() == "INTRODUCTION"
                        and len({p - valid[p]["page"] for p in next_headers}) == 1):
                    page = valid[next_headers[0]]["page"] - (next_headers[0] - physical)
                    result.setdefault(str(page), []).append({
                        "file": str(path), "physical": physical, "editorial_page": page,
                        "score": 0.75, "method": "titled_opening_preceding_concordant_headers",
                        "evidence": [_evidence(path, raw, first_block.start(2), first_block.end(2))]
                                    + [valid[p]["evidence"] for p in next_headers],
                        "uncertainties": ["unprinted_opening_page_inferred"],
                        "namespace": "po008-apostles-fascicle-page", "certified": False,
                    })
                continue
            a, b = min(pairs, key=lambda pair: (pair[1] - pair[0], pair))
            page = valid[a]["page"] + physical - a
            evidence = [valid[a]["evidence"], valid[b]["evidence"]]
            method = "bracketed_header_sequence"
            uncertainty = ["header_missing_or_corrupted_interpolated"]
            if physical in observed:
                evidence.append(observed[physical]["evidence"])
                uncertainty.append("contradictory_ocr_header")
        result.setdefault(str(page), []).append({
            "file": str(path), "physical": physical, "editorial_page": page,
            "score": 0.94 if header else 0.86, "method": method,
            "evidence": evidence, "uncertainties": uncertainty,
            "namespace": "po008-apostles-fascicle-page", "certified": False,
        })
    return result


def document_headings(files, division):
    pattern = LETTER_TITLE if division == "amulette" else NUMBER_TITLE
    opening = files[min(files)].read_text(encoding="utf-8")
    enumerated = [int(m[1]) for m in NUMBER_TITLE.finditer(opening)]
    max_code = max(enumerated) if enumerated else None
    headings = []
    for physical, path in sorted(files.items()):
        raw = path.read_text(encoding="utf-8")
        for block in BLOCK.finditer(raw):
            if block[1] in {"aparato_critico"}:
                continue
            matches = list(pattern.finditer(block[2]))
            # Introductory enumerations reproduce all document labels. Only
            # independent titles start documents; the actual first title is
            # repeated after the enumeration on the same physical page.
            if len(matches) != 1:
                continue
            match = matches[0]
            if division != "amulette" and (max_code is None or int(match[1]) > max_code):
                continue
            if block[2][:match.start()].strip():
                continue
            a, b = block.start(2) + match.start(), block.start(2) + match.end()
            headings.append({"physical": physical, "code": match[1],
                             "evidence": _evidence(path, raw, a, b)})
    # A fresh heading closes the preceding document inside a physical page.
    # Include that page for both documents: page navigation is the objective.
    result = {}
    for i, heading in enumerate(headings):
        stop = headings[i + 1]["physical"] if i + 1 < len(headings) else max(files)
        evidence = [heading["evidence"]]
        if i + 1 < len(headings):
            evidence.append(headings[i + 1]["evidence"])
        for physical, path in sorted(files.items()):
            if heading["physical"] <= physical <= stop:
                result.setdefault(heading["code"], []).append({
                    "file": str(path), "physical": physical,
                    "score": 0.9 if physical == heading["physical"] else 0.82,
                    "method": "named_document_heading_interval", "evidence": evidence,
                    "namespace": "po018:" + division, "document_code": heading["code"],
                    "uncertainties": ["passage_position_within_document_unverified"],
                    "certified": False,
                })
    return result


def build_map(root: Path) -> dict:
    root = Path(root).resolve()
    # The reviewed opening at563 identifies this fascicle. Headers before the
    # alphabetical index at706 belong to its own pagination, not volume pages.
    files = _files(root, "PO008", 563, 705)
    opener = files.get(563)
    if opener is None or "127 CANONS DES APOTRES" not in opener.read_text(encoding="utf-8"):
        raise ValueError("PO008 fascicle opening no longer matches reviewed scope")
    result = {"version": VERSION, "po008_pages": page_sequence(files), "po018_documents": {}}
    for division, (first, last, title) in CHAPTERS.items():
        files = _files(root, "PO018", first, last)
        if first not in files or title not in files[first].read_text(encoding="utf-8"):
            raise ValueError(f"PO018 chapter opening missing: {division}")
        result["po018_documents"][division] = document_headings(files, division)
    return result


def locate(entry: dict, mapping: dict) -> list:
    candidates = []
    if entry.get("section_id") in PO008_SECTIONS:
        if entry.get("reference_namespace") == "book_canon_page":
            tokens = [t for loc in entry.get("editorial_locators", []) for t in loc.get("page_tokens", [])]
        elif entry.get("section_id") == "po008-apostles-canons-scripture":
            raw = entry.get("reference_raw", "")
            # Only the isolated reference side, never chapter/verse or canon.
            if not re.fullmatch(r"[\d\s,;.\-–età]+", raw):
                return []
            base = entry.get("reference_span", [0])[0]
            # Preserve string length so every parsed span still addresses the
            # original OCR, including list conjunctions and range interiors.
            prepared = re.sub(r"\bet\b", ", ", raw)
            parsed = page_list(prepared, base)
            tokens = [{**loc, "raw": raw[loc["char_start"] - base:loc["char_end"] - base],
                       "span": [loc["char_start"], loc["char_end"]]}
                      for loc in parsed["locators"]]
        else:
            return []
        for token in tokens:
            page = token.get("page", token["raw"])
            if not str(page).isdigit():
                continue
            for target in mapping["po008_pages"].get(str(int(page)), []):
                candidates.append({**target, "reference_token": token,
                                   "uncertainties": target["uncertainties"] + entry.get("flags", [])})
    elif entry.get("section_id") == "po018-nubian-scripture":
        locator = entry.get("named_locator", {})
        division = locator.get("division_raw", "").lower().rstrip("s")
        division = {"livres canonique": "livres canoniques", "textes diver": "textes divers"}.get(division, division)
        for code in locator.get("codes", []):
            for target in mapping["po018_documents"].get(division, {}).get(code["raw"], []):
                candidates.append({**target, "reference_token": code})
    by_file = {}
    for candidate in candidates:
        key = candidate["file"]
        localization = {name: candidate[name] for name in
                        ("document_code", "reference_token", "namespace", "method", "evidence")
                        if name in candidate}
        if key not in by_file:
            by_file[key] = {**candidate, "reference_tokens": [candidate["reference_token"]],
                            "evidence": list(candidate.get("evidence", [])),
                            "uncertainties": list(candidate.get("uncertainties", [])),
                            "localizations": [localization]}
        else:
            by_file[key]["reference_tokens"].append(candidate["reference_token"])
            by_file[key]["score"] = max(by_file[key]["score"], candidate["score"])
            by_file[key]["localizations"].append(localization)
            for name in ("evidence", "uncertainties"):
                for value in candidate.get(name, []):
                    if value not in by_file[key][name]:
                        by_file[key][name].append(value)
    return sorted(by_file.values(), key=lambda row: (-row["score"], row["physical"], row["file"]))
