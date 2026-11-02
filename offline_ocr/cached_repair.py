"""Freeze a historical XML and the exact main2 Tesseract cache for manual inference."""
from contextlib import closing
import difflib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image

from . import ab_experiment as ab
from .selection import digest, now, read_only

VERSION = "ocr-cached-repair-v1"
DEFAULT_REASONING_EFFORT = "medium"
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")
SYSTEM_PROMPT = ab.SYSTEM_PROMPT + """
Reconcile the previous LLM transcription and cached Tesseract against the image. Neither draft is authoritative. Check the beginning and end of every column and the continuation of notes; restore visible omissions, remove duplicated text, and correct OCR errors only when supported by the image. Return the complete corrected page, not a patch or a summary."""
REQUIRED_FILES = {"system.txt", "cached_text.txt", "draft.xml", "tesseract_cached.txt"}


def prepare(image_path, experiment_dir, *, db_path, cache_db_path, volume, page,
            version_id=None, expect_model=None, lang="migne", preprocess="adaptive_soft",
            clahe_clip=2.0, block_size=31, c_value=4):
    """Read only two scoped SQLite records; never run Tesseract or a model."""
    image_path, experiment_dir = Path(image_path).resolve(), Path(experiment_dir).resolve()
    image_bytes = image_path.read_bytes()
    image_hash = digest(image_bytes)
    with Image.open(image_path) as image:
        if image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Use a single PNG, JPEG or WEBP image")
        size, mime = list(image.size), Image.MIME[image.format]
    query = """SELECT r.*, e.engine, e.model, e.prompt_key
               FROM ocr_results r JOIN ocr_engine_versions e ON e.id=r.engine_version_id
               WHERE r.volume_id=? AND r.page_num=? AND r.image_hash=?
                 AND lower(e.engine) != 'tesseract'"""
    args = [volume, page, image_hash]
    if version_id is not None:
        query += " AND r.id=?"
        args.append(version_id)
    query += " ORDER BY r.created_at DESC, r.id DESC LIMIT 1"
    with closing(read_only(db_path)) as con:
        row = con.execute(query, args).fetchone()
    if row is None:
        raise ValueError("No matching non-Tesseract version for this page/image/version ID")
    draft = row["text_content"]
    if digest(draft.encode("utf-8")) != row["text_hash"]:
        raise ValueError("Stored version text hash does not match its contents")
    if expect_model is not None and row["model"] != expect_model:
        raise ValueError(f"Expected source model {expect_model!r}, found {row['model']!r}")
    errors = ab.validate_xml(draft)
    if errors:
        raise ValueError("Invalid source XML: " + ", ".join(errors))
    key = f"{image_hash}:{lang}:{preprocess}:{clahe_clip}:{block_size}:{c_value}"
    with closing(read_only(cache_db_path)) as con:
        cached = con.execute("SELECT * FROM tesseract_cache WHERE image_hash=?", (key,)).fetchone()
    if cached is None or cached["lang"] != lang or not cached["result"].strip():
        raise ValueError("Exact Tesseract cache entry missing/empty; no OCR fallback: " + key)
    reference = cached["result"]
    user = ("Check both fallible drafts against the attached facsimile.\n"
            "BEGIN CACHED TESSERACT (original text order; no coordinates)\n" + reference +
            "\nEND CACHED TESSERACT\nBEGIN FALLIBLE PREVIOUS TRANSCRIPTION\n" + draft +
            "\nEND FALLIBLE PREVIOUS TRANSCRIPTION\nReturn only the complete corrected XML.")
    experiment_dir.mkdir(parents=True, exist_ok=False)
    contents = {"system.txt": SYSTEM_PROMPT, "cached_text.txt": user,
                "draft.xml": draft, "tesseract_cached.txt": reference}
    for name, text in contents.items():
        (experiment_dir / name).write_bytes(text.encode("utf-8"))
    manifest = {
        "version": VERSION, "created_at": now(), "mode": "cached_correction",
        "image": {"path": str(image_path), "sha256": image_hash, "size": size, "mime": mime},
        "source_version": {k: row[k] for k in ("id", "volume_id", "page_num", "engine", "model",
                                               "prompt_key", "text_hash", "is_current", "created_at")},
        "source_db": str(Path(db_path).resolve()),
        "cache": {"db": str(Path(cache_db_path).resolve()), "key": key,
                  "lang": lang, "created_at": cached["created_at"],
                  "text_sha256": digest(reference.encode("utf-8")), "characters": len(reference)},
        "files": {name: digest(text.encode("utf-8")) for name, text in contents.items()},
        "arms": [{"name": "cached_text", "prompt": "cached_text.txt"}],
        "inference_performed": False,
    }
    ab.write_json(experiment_dir / "experiment.json", manifest)
    return manifest


def load_experiment(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    if manifest["version"] != VERSION:
        raise ValueError("experiment_version_mismatch")
    if (manifest["arms"] != [{"name": "cached_text", "prompt": "cached_text.txt"}] or
            set(manifest["files"]) != REQUIRED_FILES):
        raise ValueError("invalid_cached_experiment_files")
    for name, expected in manifest["files"].items():
        if digest((directory / name).read_bytes()) != expected:
            raise ValueError("experiment_file_changed: " + name)
    return directory, manifest


def run(experiment_dir, results_dir, *, model="gpt-5.4-mini", base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY", image_detail="original", parameters=None, reasoning_effort=None,
        timeout=600, post=None):
    directory, manifest = load_experiment(experiment_dir)
    parameters = dict(parameters or {})
    if reasoning_effort is not None:
        parameters["reasoning_effort"] = reasoning_effort
    parameters.setdefault("reasoning_effort", DEFAULT_REASONING_EFFORT)
    if parameters["reasoning_effort"] not in REASONING_EFFORTS:
        raise ValueError("reasoning_effort must be one of: " + ", ".join(REASONING_EFFORTS))
    result = ab.run_prepared(directory, manifest, results_dir, provider="openai-like",
                             model=model, base_url=base_url, api_key_env=api_key_env,
                             image_detail=image_detail, parameters=parameters, timeout=timeout, post=post)
    result["reasoning_effort"] = parameters["reasoning_effort"]
    if result["counts"].get("ok"):
        result["inspection"] = inspect_result(directory, results_dir)
    return result


def inspect_result(experiment_dir, results_dir):
    """Offline syntax check and literal diff; does not grade transcription quality."""
    directory, _ = load_experiment(experiment_dir)
    results_dir = Path(results_dir)
    config = json.loads((results_dir / "run.json").read_text(encoding="utf-8"))
    if config["experiment_sha256"] != digest((directory / "experiment.json").read_bytes()):
        raise ValueError("run_experiment_mismatch")
    saved = json.loads((results_dir / "r001_cached_text.result.json").read_text(encoding="utf-8"))
    if saved["status"] != "ok":
        raise ValueError("No successful response to inspect")
    raw = (results_dir / "r001_cached_text.xml").read_bytes()
    if digest(raw) != saved["output_sha256"]:
        raise ValueError("response_changed")
    output = raw.decode("utf-8")
    before = (directory / "draft.xml").read_text(encoding="utf-8")
    errors = ab.validate_xml(output)
    report = {"xml_errors": errors, "finish_reason": saved.get("finish_reason"),
              "changed": output != before, "before_characters": len(before),
              "after_characters": len(output), "quality_verified": False}
    if not errors:
        report["blocks"] = len(ET.fromstring(output).findall("bloco"))
    ab.write_json(results_dir / "inspection.json", report)
    diff = difflib.unified_diff(before.splitlines(keepends=True), output.splitlines(keepends=True),
                                fromfile="draft.xml", tofile="r001_cached_text.xml")
    (results_dir / "changes.diff").write_text("".join(diff), encoding="utf-8")
    return report
