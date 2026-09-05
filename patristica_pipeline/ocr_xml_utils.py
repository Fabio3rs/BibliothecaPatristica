from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BLOCK_TAGS = {"bloco", "cabecalho", "rodape"}
TEXT_BLOCK_TYPES = {"texto_principal", ""}
NOTE_BLOCK_TYPES = {"nota", "nota_marginal"}

_BLOCK_RE = re.compile(
    r"<(?P<tag>bloco|cabecalho|rodape)(?P<attrs>[^>]*)>(?P<content>.*?)</(?P=tag)>",
    flags=re.DOTALL | re.IGNORECASE,
)
_PAGE_ATTR_RE = re.compile(r"<pagina(?P<attrs>[^>]*)>", flags=re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:.-]*)="([^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t]+")
_LINEBREAK_HYPHEN_RE = re.compile(r"(?<=[^\W_])[-\u2010]\s*\n\s*(?=[^\W_])", re.UNICODE)
_NON_XML_AMPERSAND_RE = re.compile(
    r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9A-Fa-f]+);)"
)


@dataclass(slots=True)
class OcrBlock:
    tag_name: str
    tipo: str = ""
    script: str = ""
    bbox: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    content_raw: str = ""
    content_clean: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag_name": self.tag_name,
            "tipo": self.tipo,
            "script": self.script,
            "bbox": self.bbox,
            "attrs": self.attrs,
            "content_raw": self.content_raw,
            "content_clean": self.content_clean,
        }


@dataclass(slots=True)
class OcrPage:
    estado: str = ""
    tipo: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    blocks: list[OcrBlock] = field(default_factory=list)
    is_xml: bool = False
    parse_ok: bool = False
    repairs: dict[str, int] = field(default_factory=dict)
    raw_text: str = ""

    @property
    def header_text(self) -> str:
        return _join_blocks(block.content_clean for block in self.blocks if _is_header_block(block))

    @property
    def body_text(self) -> str:
        bodies = [block.content_clean for block in self.blocks if _is_body_block(block)]
        if bodies:
            return _join_blocks(bodies)
        return _join_blocks(block.content_clean for block in self.blocks if not _is_header_block(block) and not _is_footer_block(block))

    @property
    def footer_text(self) -> str:
        return _join_blocks(block.content_clean for block in self.blocks if _is_footer_block(block))

    @property
    def notes_text(self) -> str:
        return _join_blocks(block.content_clean for block in self.blocks if _is_note_block(block))

    @property
    def all_text(self) -> str:
        return _join_blocks([self.header_text, self.body_text, self.footer_text, self.notes_text])

    def to_legacy_dict(self) -> dict[str, str]:
        return {
            "header_text": self.header_text,
            "body_text": self.body_text,
            "footer_text": self.footer_text,
            "notes_text": self.notes_text,
            "all_text": self.all_text,
        }

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "estado": self.estado,
            "tipo": self.tipo,
            "attrs": self.attrs,
            "is_xml": self.is_xml,
            "parse_ok": self.parse_ok,
            "repairs": self.repairs,
            "header_text": self.header_text,
            "body_text": self.body_text,
            "footer_text": self.footer_text,
            "notes_text": self.notes_text,
            "all_text": self.all_text,
            "blocks": [block.to_dict() for block in self.blocks],
        }
        if include_raw:
            data["raw_text"] = self.raw_text
        return data

    def to_clean_xml(self) -> str:
        if not self.is_xml:
            return self.all_text
        page_attrs = dict(self.attrs)
        if self.estado and "estado" not in page_attrs:
            page_attrs["estado"] = self.estado
        if self.tipo and "tipo" not in page_attrs:
            page_attrs["tipo"] = self.tipo
        lines = [f"<pagina{_format_attrs(page_attrs)}>"]
        for block in self.blocks:
            attrs = dict(block.attrs)
            if block.tipo and "tipo" not in attrs:
                attrs["tipo"] = block.tipo
            if block.script and "script" not in attrs:
                attrs["script"] = block.script
            if block.bbox and "bbox" not in attrs:
                attrs["bbox"] = block.bbox
            tag_name = block.tag_name or "bloco"
            body = html.escape(block.content_clean, quote=False)
            if "\n" in block.content_clean:
                lines.append(f"  <{tag_name}{_format_attrs(attrs)}>")
                lines.extend(f"    {html.escape(line, quote=False)}" for line in block.content_clean.splitlines())
                lines.append(f"  </{tag_name}>")
            else:
                lines.append(f"  <{tag_name}{_format_attrs(attrs)}>{body}</{tag_name}>")
        lines.append("</pagina>")
        return "\n".join(lines)


def normalize_visible_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").replace("\u2007", " ").replace("\u202f", " ")
    return text


def join_linebreak_hyphenation(text: str) -> str:
    return _LINEBREAK_HYPHEN_RE.sub("", text or "")


def clean_visible_text(text: str) -> str:
    text = normalize_visible_text(text)
    text = _TAG_RE.sub(" ", text)
    text = join_linebreak_hyphenation(text)
    lines = [_SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return text.strip()


def parse_ocr_xml_page(
    raw: str,
    *,
    repair_non_xml_ampersands: bool = False,
) -> OcrPage:
    raw = raw or ""
    stripped = raw.strip()
    if not stripped.startswith("<pagina"):
        clean = clean_visible_text(stripped)
        return OcrPage(
            blocks=[
                OcrBlock(
                    tag_name="bloco",
                    tipo="texto_principal",
                    content_raw=stripped,
                    content_clean=clean,
                )
            ]
            if clean
            else [],
            is_xml=False,
            parse_ok=True,
            raw_text=raw,
        )

    try:
        return _parse_with_element_tree(stripped, raw)
    except ET.ParseError:
        if repair_non_xml_ampersands:
            repaired, repair_count = _escape_non_xml_ampersands(stripped)
            if repair_count:
                try:
                    return _parse_with_element_tree(
                        repaired,
                        raw,
                        repairs={"escaped_non_xml_ampersands": repair_count},
                    )
                except ET.ParseError:
                    pass
        return _parse_with_regex(stripped, raw)


def read_ocr_page(path: Path) -> OcrPage:
    return parse_ocr_xml_page(path.read_text(encoding="utf-8", errors="replace"))


def parse_ocr_page_xml_dict(raw: str) -> dict[str, str]:
    return parse_ocr_xml_page(raw).to_legacy_dict()


def _parse_with_element_tree(
    stripped: str,
    raw: str,
    *,
    repairs: dict[str, int] | None = None,
) -> OcrPage:
    root = ET.fromstring(stripped)
    page_attrs = dict(root.attrib)
    blocks: list[OcrBlock] = []
    for node in list(root):
        tag_name = _strip_namespace(node.tag).lower()
        if tag_name not in BLOCK_TAGS:
            continue
        attrs = dict(node.attrib)
        content = "".join(node.itertext())
        block = OcrBlock(
            tag_name=tag_name,
            tipo=(attrs.get("tipo") or "").strip().lower(),
            script=(attrs.get("script") or "").strip().lower(),
            bbox=(attrs.get("bbox") or "").strip(),
            attrs=attrs,
            content_raw=content,
            content_clean=clean_visible_text(content),
        )
        blocks.append(block)
    return OcrPage(
        estado=(page_attrs.get("estado") or "").strip().lower(),
        tipo=(page_attrs.get("tipo") or "").strip().lower(),
        attrs=page_attrs,
        blocks=[block for block in blocks if block.content_clean],
        is_xml=True,
        parse_ok=True,
        repairs=dict(repairs or {}),
        raw_text=raw,
    )


def _parse_with_regex(stripped: str, raw: str) -> OcrPage:
    page_attrs: dict[str, str] = {}
    page_match = _PAGE_ATTR_RE.search(stripped)
    if page_match:
        page_attrs = _parse_attrs(page_match.group("attrs"))
    blocks: list[OcrBlock] = []
    for match in _BLOCK_RE.finditer(stripped):
        attrs = _parse_attrs(match.group("attrs"))
        content = match.group("content") or ""
        block = OcrBlock(
            tag_name=match.group("tag").lower(),
            tipo=(attrs.get("tipo") or "").strip().lower(),
            script=(attrs.get("script") or "").strip().lower(),
            bbox=(attrs.get("bbox") or "").strip(),
            attrs=attrs,
            content_raw=content,
            content_clean=clean_visible_text(content),
        )
        if block.content_clean:
            blocks.append(block)
    return OcrPage(
        estado=(page_attrs.get("estado") or "").strip().lower(),
        tipo=(page_attrs.get("tipo") or "").strip().lower(),
        attrs=page_attrs,
        blocks=blocks,
        is_xml=True,
        parse_ok=False,
        raw_text=raw,
    )


def _parse_attrs(attr_blob: str) -> dict[str, str]:
    return {match.group(1): html.unescape(match.group(2)) for match in _ATTR_RE.finditer(attr_blob or "")}


def _escape_non_xml_ampersands(text: str) -> tuple[str, int]:
    """Escapa apenas ``&`` que não iniciam uma entidade válida em XML."""
    return _NON_XML_AMPERSAND_RE.subn("&amp;", text or "")


def _format_attrs(attrs: dict[str, str]) -> str:
    if not attrs:
        return ""
    return "".join(f' {key}="{html.escape(str(value), quote=True)}"' for key, value in attrs.items())


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _is_header_block(block: OcrBlock) -> bool:
    return block.tag_name == "cabecalho" or block.tipo == "cabecalho"


def _is_footer_block(block: OcrBlock) -> bool:
    return block.tag_name == "rodape" or block.tipo == "rodape"


def _is_note_block(block: OcrBlock) -> bool:
    return block.tipo in NOTE_BLOCK_TYPES


def _is_body_block(block: OcrBlock) -> bool:
    return block.tipo in TEXT_BLOCK_TYPES and not _is_header_block(block) and not _is_footer_block(block)


def _join_blocks(parts: Any) -> str:
    return "\n".join(str(part).strip() for part in parts if str(part).strip()).strip()
