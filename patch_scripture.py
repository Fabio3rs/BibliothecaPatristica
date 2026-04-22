import re

with open("scripture_ref_normalizer.py", "r", encoding="utf-8") as f:
    content = f.read()

# Instead of injecting a class, just replace `_extract_explicit_citations` with logic that pre-filters `_REF_SPECS`.

new_func = """
def _get_spec_words(pattern_str: str) -> list[str]:
    m = re.search(r'\\(\\?\\:(.*?)\\)', pattern_str)
    if not m:
        return []
    frag = m.group(1).lower().replace(r'\\ ', ' ').replace(r'\\s+', ' ')
    frag = re.sub(r'\\[.*?\\]', '', frag)
    frag = re.sub(r'\\(.*?\\)', '', frag)
    frag = frag.replace('(?:', '').replace(')?', '').replace(')', '')
    frag = re.sub(r'(?<!\\\\)\\.', '', frag)
    frag = frag.replace(r'\\.', '.')
    return [w for w in frag.split() if len(w)>2 and not w.isdigit()]

_REF_SPECS_WORDS = [(spec, _get_spec_words(spec["pattern"].pattern)) for spec in _REF_SPECS]

def _extract_explicit_citations(
    text: str,
    *,
    source_kind: str,
    source_path: str,
) -> list[dict]:
    found: list[tuple[int, dict]] = []
    seen_spans: set[tuple[int, int, int | None]] = set()
    text_low = text.lower()
    
    # Filter specs fast
    valid_specs = []
    for spec, words in _REF_SPECS_WORDS:
        if not words:
            valid_specs.append(spec)
            continue
        ok = True
        for w in words:
            if w not in text_low:
                ok = False
                break
        if ok:
            valid_specs.append(spec)

    for spec in valid_specs:
        idx = spec["idx"]
        for match in spec["pattern"].finditer(text):
            main = _parse_ref_number(match.group("main"))
            if main is None:
                continue
            verse = match.groupdict().get("verse")
            if verse and not _VERSE_RE.fullmatch(verse.strip()):
                verse = None
            alt = _parse_ref_number(match.groupdict().get("alt"))
            span_key = (idx, match.start(), main)
            if span_key in seen_spans:
                continue
            record = _make_citation_record(
                idx,
                match.group("raw"),
                source_kind,
                source_path,
                text,
                main=main,
                verse=verse.strip() if verse else None,
                alt=alt,
            )
            if record:
                found.append((match.start(), record))
                seen_spans.add(span_key)
    found.sort(key=lambda item: item[0])
    return [record for _, record in found]
"""

content = re.sub(r"def _get_spec_words.*?return \[record for _, record in found\]", new_func.strip(), content, flags=re.DOTALL)

with open("scripture_ref_normalizer.py", "w", encoding="utf-8") as f:
    f.write(content)
