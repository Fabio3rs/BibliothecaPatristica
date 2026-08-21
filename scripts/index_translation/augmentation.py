"""Shared lexicon tools and guided-chat support for index translation."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ChatRequest = Callable[[dict[str, Any]], dict[str, Any]]
ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class GuidedChatResult:
    content: str
    tool_calls: tuple[dict[str, Any], ...]


def run_tool_guided_chat(
    *,
    request_chat: ChatRequest,
    payload: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    handlers: Mapping[str, ToolHandler],
    max_tool_rounds: int = 2,
) -> GuidedChatResult:
    """Run a Chat Completions tool loop without depending on one LLM SDK.

    ``request_chat`` owns transport and authentication. Tool handlers are local,
    deterministic callables, which keeps this orchestrator reusable by other
    translation pipelines in the project.
    """

    conversation = [dict(message) for message in messages]
    tool_specs = [dict(tool) for tool in tools]
    trace: list[dict[str, Any]] = []
    rounds = 0

    while True:
        request_payload = dict(payload)
        request_payload["messages"] = conversation
        if tool_specs and rounds < max(0, max_tool_rounds):
            request_payload["tools"] = tool_specs
            request_payload["tool_choice"] = "auto"

        body = request_chat(request_payload)
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("model returned no choices")
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        content = message.get("content")

        if not tool_calls:
            if isinstance(content, list):
                content = "".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                )
            return GuidedChatResult(content=str(content or ""), tool_calls=tuple(trace))

        if rounds >= max(0, max_tool_rounds):
            raise ValueError("model requested tools after the tool-round limit")

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        }
        conversation.append(assistant_message)

        for tool_call in tool_calls:
            call_id = str(tool_call.get("id") or "")
            function = tool_call.get("function") or {}
            name = str(function.get("name") or "")
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("tool arguments must be a JSON object")
                handler = handlers.get(name)
                if handler is None:
                    raise ValueError(f"unknown tool: {name}")
                result = handler(arguments)
                tool_content = json.dumps(result, ensure_ascii=False)
                trace.append(
                    {
                        "name": name,
                        "arguments": arguments,
                        "status": "ok",
                    }
                )
            except Exception as exc:
                tool_content = json.dumps(
                    {"status": "error", "error": str(exc)},
                    ensure_ascii=False,
                )
                trace.append(
                    {
                        "name": name,
                        "arguments": raw_arguments,
                        "status": "error",
                        "error": str(exc),
                    }
                )
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": tool_content,
                }
            )
        rounds += 1


def _lemma_sort(value: str) -> str:
    value = value.replace("æ", "ae").replace("Æ", "Ae")
    value = value.replace("œ", "oe").replace("Œ", "Oe")
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z]+", "", without_marks.casefold())


def _compact_text(value: Any, *, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


@dataclass(frozen=True)
class LatinLexiconTool:
    """Bounded, read-only access to the project's Latin lexicon collection."""

    dictionary_dir: Path
    max_lemmas: int = 10
    max_entries_per_source: int = 3
    max_entries_per_lemma: int = 12

    @property
    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "lookup_latin_lexicon",
                "description": (
                    "Consult local read-only Latin dictionaries for uncertain Latin words. "
                    "Pass likely dictionary lemmas, not every word. Results are evidence, "
                    "not mandatory translations."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lemmas": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": self.max_lemmas,
                            "description": "Likely Latin dictionary lemmas to consult.",
                        }
                    },
                    "required": ["lemmas"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }

    @property
    def available_sources(self) -> tuple[str, ...]:
        if (self.dictionary_dir / "superdb.sqlite").is_file():
            return ("superdb",)
        candidates = {
            "faria": self.dictionary_dir / "retificado_v2.db",
            "lewis_short": self.dictionary_dir / "ls_dict.db",
            "gaffiot": self.dictionary_dir / "gaffiot.db",
        }
        return tuple(name for name, path in candidates.items() if path.is_file())

    def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_lemmas = arguments.get("lemmas")
        if not isinstance(raw_lemmas, list):
            raise ValueError("lemmas must be an array")

        queries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for value in raw_lemmas:
            original = _compact_text(value, limit=80)
            key = _lemma_sort(original)
            if not key or key in seen:
                continue
            seen.add(key)
            queries.append((original, key))
            if len(queries) >= self.max_lemmas:
                break
        if not queries:
            raise ValueError("no usable Latin lemmas were supplied")

        sources = self.available_sources
        if not sources:
            raise FileNotFoundError(f"no supported dictionaries found in {self.dictionary_dir}")

        resolved_keys = self._resolve_forms([key for _, key in queries])
        results: list[dict[str, Any]] = []
        for original, key in queries:
            lookup_keys = [key]
            lookup_keys.extend(item for item in resolved_keys.get(key, []) if item != key)
            entries: list[dict[str, Any]] = []
            if "superdb" in sources:
                entries.extend(self._lookup_superdb(lookup_keys))
            elif "faria" in sources:
                entries.extend(self._lookup_faria(lookup_keys))
            if "lewis_short" in sources:
                entries.extend(self._lookup_lewis_short(lookup_keys))
            if "gaffiot" in sources:
                entries.extend(self._lookup_gaffiot(lookup_keys))
            results.append(
                {
                    "query": original,
                    "normalized": key,
                    "resolved_lemmas": lookup_keys,
                    "entries": entries,
                }
            )
        return {
            "status": "ok",
            "sources": list(sources),
            "results": results,
        }

    def _resolve_forms(self, keys: list[str]) -> dict[str, list[str]]:
        resolved = {key: [] for key in keys}
        superdb_path = self.dictionary_dir / "superdb.sqlite"
        if superdb_path.is_file() and keys:
            placeholders = ",".join("?" for _ in keys)
            with _connect_read_only(superdb_path) as con:
                rows = con.execute(
                    f"""
                    SELECT DISTINCT f.form_norm, e.lemma_norm
                    FROM entry_form f
                    JOIN entry e ON e.id = f.entry_id
                    WHERE f.form_norm IN ({placeholders})
                    ORDER BY f.form_norm, e.lemma_norm
                    """,
                    keys,
                ).fetchall()
            for row in rows:
                key = _lemma_sort(row["form_norm"])
                first_lemma = str(row["lemma_norm"] or "").strip().split(" ", 1)[0]
                lemma = _lemma_sort(first_lemma)
                if key in resolved and lemma and lemma not in resolved[key]:
                    resolved[key].append(lemma)
            return resolved

        path = self.dictionary_dir / "gaffiot.db"
        if not path.is_file() or not keys:
            return resolved
        placeholders = ",".join("?" for _ in keys)
        with _connect_read_only(path) as con:
            rows = con.execute(
                f"""
                SELECT DISTINCT f.form_norm, e.lemma_sort
                FROM entry_form f
                JOIN entry e ON e.entry_id = f.entry_id
                WHERE f.form_norm IN ({placeholders})
                ORDER BY f.form_norm, e.lemma_sort
                """,
                keys,
            ).fetchall()
        for row in rows:
            key = _lemma_sort(row["form_norm"])
            lemma = _lemma_sort(row["lemma_sort"])
            if key in resolved and lemma and lemma not in resolved[key]:
                resolved[key].append(lemma)
        return resolved

    def _lookup_superdb(self, keys: list[str]) -> list[dict[str, Any]]:
        path = self.dictionary_dir / "superdb.sqlite"
        placeholders = ",".join("?" for _ in keys)
        limit = self.max_entries_per_source * 10
        with _connect_read_only(path) as con:
            rows = con.execute(
                f"""
                SELECT e.id, e.lemma, e.lemma_norm, e.pos_std, e.pos_raw,
                       e.gender_std, e.gender_raw, e.morph_class_std,
                       e.morph_class_raw, e.head_raw, e.notes, e.needs_review,
                       src.name AS source_name,
                       (
                           SELECT GROUP_CONCAT(NULLIF(TRIM(s.gloss), ''), ' | ')
                           FROM sense s
                           WHERE s.entry_id = e.id
                       ) AS glosses,
                       (
                           SELECT GROUP_CONCAT(
                               NULLIF(TRIM(t.lang || ':' || t.gloss), ''), ' | '
                           )
                           FROM translation t
                           JOIN sense s ON s.id = t.sense_id
                           WHERE s.entry_id = e.id
                       ) AS translations
                FROM entry e
                JOIN source src ON src.id = e.source_id
                WHERE e.lemma_norm IN ({placeholders})
                ORDER BY
                    CASE src.name
                        WHEN 'retificado_v2' THEN 0
                        WHEN 'ls_dict' THEN 1
                        WHEN 'gaffiot' THEN 2
                        WHEN 'lewis_stardict' THEN 3
                        ELSE 4
                    END,
                    e.needs_review,
                    e.lemma
                LIMIT ?
                """,
                (*keys, limit),
            ).fetchall()

        source_counts: dict[str, int] = {}
        entries: list[dict[str, Any]] = []
        for row in rows:
            source = _compact_text(row["source_name"], limit=80)
            source_counts[source] = source_counts.get(source, 0) + 1
            if source_counts[source] > self.max_entries_per_source:
                continue
            entries.append(
                {
                    "source": source,
                    "lemma": _compact_text(row["lemma"], limit=120),
                    "lemma_norm": _compact_text(row["lemma_norm"], limit=120),
                    "part_of_speech": _compact_text(
                        row["pos_std"] or row["pos_raw"], limit=100
                    ),
                    "gender": _compact_text(
                        row["gender_std"] or row["gender_raw"], limit=80
                    ),
                    "morphology": _compact_text(
                        row["morph_class_std"] or row["morph_class_raw"], limit=180
                    ),
                    "headword": _compact_text(row["head_raw"], limit=260),
                    "definitions": _compact_text(row["glosses"]),
                    "translations": _compact_text(row["translations"]),
                    "notes": _compact_text(row["notes"], limit=260),
                    "needs_review": bool(row["needs_review"]),
                }
            )
            if len(entries) >= self.max_entries_per_lemma:
                break
        return entries

    def _lookup_faria(self, keys: list[str]) -> list[dict[str, Any]]:
        path = self.dictionary_dir / "retificado_v2.db"
        placeholders = ",".join("?" for _ in keys)
        with _connect_read_only(path) as con:
            rows = con.execute(
                f"""
                SELECT lemma, morph_render, definicao, notas, conf, needs_review
                FROM entry
                WHERE lemma_sort IN ({placeholders})
                ORDER BY CASE conf WHEN 'conf:high' THEN 0 WHEN 'high' THEN 0 ELSE 1 END,
                         needs_review, lemma
                LIMIT ?
                """,
                (*keys, self.max_entries_per_source),
            ).fetchall()
        return [
            {
                "source": "faria",
                "lemma": _compact_text(row["lemma"], limit=100),
                "morphology": _compact_text(row["morph_render"], limit=180),
                "definition_pt": _compact_text(row["definicao"]),
                "notes": _compact_text(row["notas"], limit=260),
                "confidence": _compact_text(row["conf"], limit=40),
                "needs_review": bool(row["needs_review"]),
            }
            for row in rows
        ]

    def _lookup_lewis_short(self, keys: list[str]) -> list[dict[str, Any]]:
        path = self.dictionary_dir / "ls_dict.db"
        placeholders = ",".join("?" for _ in keys)
        with _connect_read_only(path) as con:
            rows = con.execute(
                f"""
                SELECT lemma, pos, gen_text, tr_gloss_pt, tr_trad_pt, tr_notas
                FROM entry
                WHERE lemma_sort IN ({placeholders})
                ORDER BY CASE WHEN TRIM(COALESCE(tr_gloss_pt, '')) != '' THEN 0 ELSE 1 END,
                         lemma
                LIMIT ?
                """,
                (*keys, self.max_entries_per_source),
            ).fetchall()
        return [
            {
                "source": "lewis_short",
                "lemma": _compact_text(row["lemma"], limit=100),
                "part_of_speech": _compact_text(row["pos"], limit=80),
                "morphology": _compact_text(row["gen_text"], limit=180),
                "gloss_pt": _compact_text(row["tr_gloss_pt"], limit=350),
                "definition_pt": _compact_text(row["tr_trad_pt"]),
                "notes": _compact_text(row["tr_notas"], limit=260),
            }
            for row in rows
        ]

    def _lookup_gaffiot(self, keys: list[str]) -> list[dict[str, Any]]:
        path = self.dictionary_dir / "gaffiot.db"
        placeholders = ",".join("?" for _ in keys)
        with _connect_read_only(path) as con:
            rows = con.execute(
                f"""
                SELECT e.entry_id, e.lemma, e.pos, e.gen_text,
                       GROUP_CONCAT(NULLIF(TRIM(s.gloss_fr), ''), ' | ') AS gloss_fr
                FROM entry e
                LEFT JOIN sense s ON s.entry_id = e.entry_id
                WHERE e.lemma_sort IN ({placeholders})
                GROUP BY e.entry_id
                ORDER BY e.lemma
                LIMIT ?
                """,
                (*keys, self.max_entries_per_source),
            ).fetchall()
        return [
            {
                "source": "gaffiot",
                "lemma": _compact_text(row["lemma"], limit=100),
                "part_of_speech": _compact_text(row["pos"], limit=80),
                "morphology": _compact_text(row["gen_text"], limit=180),
                "definition_fr": _compact_text(row["gloss_fr"]),
            }
            for row in rows
        ]


def build_latin_lexicon_tools(
    dictionary_dir: Path | str,
) -> tuple[list[dict[str, Any]], dict[str, ToolHandler]]:
    tool = LatinLexiconTool(Path(dictionary_dir))
    if not tool.available_sources:
        return [], {}
    return [tool.definition], {"lookup_latin_lexicon": tool}
