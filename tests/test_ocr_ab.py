"""Offline A/B contracts, including a fake provider; no HTTP or inference calls."""
import json
from pathlib import Path
import sys

import pytest
from PIL import Image
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offline_ocr import ab_experiment as ab

FIXTURES = Path(__file__).parent / "fixtures/ocr_diff_v2"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not call HTTP")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)


@pytest.fixture
def experiment(tmp_path):
    # Raster supplies geometry only. The fake provider returns recorded XML.
    image = tmp_path / "page.png"
    Image.new("L", (2480, 3509), 255).save(image)
    directory = tmp_path / "experiment"
    ab.prepare(image, FIXTURES / "PG001_638_migne_raw.tsv", directory,
               draft_path=FIXTURES / "PG001_638_current.xml")
    return directory


class Response:
    def __init__(self, text, provider):
        self.text, self.provider = text, provider

    def raise_for_status(self):
        pass

    def json(self):
        if self.provider == "ollama":
            return {"message": {"content": self.text}, "done_reason": "stop", "eval_count": 80}
        return {"choices": [{"message": {"content": self.text}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 80}}


def fake_runner(experiment, output, provider="openai-like", repeats=1, responses=None):
    calls = []
    current = (FIXTURES / "PG001_638_current.xml").read_text()
    previous = (FIXTURES / "PG001_638_previous.xml").read_text()

    def post(url, *, json, headers, timeout):
        calls.append({"url": url, "body": json, "headers": headers, "timeout": timeout})
        user = json["messages"][1]["content"]
        if isinstance(user, list):
            user = user[0]["text"]
        text = current if user.startswith(ab.FORMAT_NOTES["raw_text"]) else previous
        return Response(responses[len(calls) - 1] if responses else text, provider)

    result = ab.run(experiment, output, provider=provider, model="fixture-model",
                    base_url="http://127.0.0.1:11434" if provider == "ollama" else "http://127.0.0.1:8000/v1",
                    repeats=repeats, parameters={"options": {"temperature": .1}} if provider == "ollama" else {"temperature": .1},
                    post=post)
    return calls, result


def test_prepare_is_offline_and_prompts_vary_only_in_hints(experiment):
    _, manifest = ab.load_experiment(experiment)
    assert manifest["inference_performed"] is False
    assert manifest["mode"] == "correction"
    assert manifest["layout_stats"]["assigned_words"] == 939
    assert manifest["system_characters"] < 1400
    draft = (experiment / "draft.xml").read_text()
    for arm in manifest["arms"]:
        user = (experiment / arm["prompt"]).read_text()
        assert user.endswith(draft + "\nEND FALLIBLE PREVIOUS TRANSCRIPTION")
        assert "adulteros" in user
        assert arm["hint_bytes_vs_full_tsv"] < .25
    assert not (experiment / "run.json").exists()


@pytest.mark.parametrize("provider", ["openai-like", "ollama"])
def test_runner_holds_model_image_prompt_and_parameters_constant(experiment, tmp_path, provider):
    output = tmp_path / "results"
    calls, result = fake_runner(experiment, output, provider, repeats=2)
    assert result["counts"] == {"ok": 10}
    assert len(calls) == 10
    assert len({c["body"]["model"] for c in calls}) == 1
    assert len({c["body"]["messages"][0]["content"] for c in calls}) == 1
    if provider == "ollama":
        assert len({c["body"]["messages"][1]["images"][0] for c in calls}) == 1
        assert all(c["body"]["options"] == {"temperature": .1} for c in calls)
    else:
        assert len({c["body"]["messages"][1]["content"][1]["image_url"]["url"] for c in calls}) == 1
        assert all(c["body"]["temperature"] == .1 for c in calls)
    config = json.loads((output / "run.json").read_text())
    assert all(len({j["arm"] for j in config["jobs"] if j["repeat"] == repeat}) == 5 for repeat in [1, 2])
    assert all(not c["body"]["stream"] for c in calls)
    assert all(c["headers"] == {} for c in calls)
    report = ab.evaluate(experiment, output, targets=["adulteros", "homicidas"])
    assert report["automatic_winner"] is None
    assert len(report["paired_comparisons"]) == 8
    for row in report["rows"]:
        assert row["xml_valid"]
        assert row["content_profile"]["reference_tokens"] > 0
        assert all(row["target_presence"].values()) == (row["arm"] != "raw_text")
    assert all(p["delta_vs_raw_text"]["missing_reference_tokens"] < 0 for p in report["paired_comparisons"])


def test_incomplete_errors_and_invalid_xml_are_not_scored_as_winners(experiment, tmp_path):
    output = tmp_path / "results"
    calls = []

    def failing_post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise requests.Timeout("fixture timeout")
        return Response("```xml\n<pagina/>\n```", "openai-like")

    result = ab.run(experiment, output, provider="openai-like", model="fixture", base_url="http://127.0.0.1/v1", post=failing_post)
    assert result["counts"] == {"error": 1, "ok": 4}
    config = json.loads((output / "run.json").read_text())
    (output / (config["jobs"][-1]["id"] + ".result.json")).unlink()
    report = ab.evaluate(experiment, output)
    assert len(calls) == 5
    assert {r["status"] for r in report["rows"]} == {"error", "pending", "invalid_xml"}
    assert report["paired_comparisons"] == []
    assert all(a["mean_profile"] is None for a in report["arms"].values())


@pytest.mark.parametrize("change", ["image", "prompt"])
def test_changed_inputs_prevent_inference(experiment, tmp_path, change):
    _, manifest = ab.load_experiment(experiment)
    path = Path(manifest["image"]["path"]) if change == "image" else experiment / "block_tsv.txt"
    path.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        fake_runner(experiment, tmp_path / "results")
    assert not (tmp_path / "results").exists()


def test_changed_output_rejected_by_evaluation(experiment, tmp_path):
    output = tmp_path / "results"
    fake_runner(experiment, output)
    next(output.glob("*.xml")).write_text("changed")
    with pytest.raises(ValueError, match="response_changed"):
        ab.evaluate(experiment, output)


def test_new_run_does_not_overwrite_prior_results(experiment, tmp_path):
    output = tmp_path / "results"
    fake_runner(experiment, output)
    with pytest.raises(FileExistsError):
        fake_runner(experiment, output)


@pytest.mark.parametrize("text,error", [
    ("<pagina estado='com_texto'/>", "text_page_without_blocks"),
    ("<pagina estado='vazio'><bloco/></pagina>", "blank_page_with_blocks"),
    ("<pagina estado='com_texto'><bloco tipo='texto_principal' script='latino' bbox='100,100,90,200'>abc</bloco></pagina>", "invalid_bbox"),
    ("<pagina estado='com_texto'>lost text<bloco/></pagina>", "text_outside_blocks"),
    ("not XML", "malformed_xml"),
])
def test_schema_validation(text, error):
    assert error in ab.validate_xml(text)


def test_payload_does_not_allow_parameters_to_change_experimental_controls():
    for field in ["model", "messages", "stream"]:
        with pytest.raises(ValueError, match="override"):
            ab.request_payload("ollama", "fixture", "system", "user", "base64", "image/png", {field: "bad"})
    payload = ab.request_payload("openai-like", "fixture", "system", "user", "base64", "image/png", image_detail="high")
    assert payload["messages"][1]["content"][1]["image_url"]["detail"] == "high"


def test_credentials_sent_only_as_header_not_saved(experiment, tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_AB_TEST_KEY", "secret-fixture-key")
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs["headers"])
        return Response("<pagina estado='vazio'/>", "openai-like")
    output = tmp_path / "results"
    ab.run(experiment, output, provider="openai-like", model="fixture", base_url="http://127.0.0.1/v1",
           api_key_env="OCR_AB_TEST_KEY", post=post)
    assert all(c["Authorization"] == "Bearer secret-fixture-key" for c in calls)
    assert all("secret-fixture-key" not in p.read_text() for p in output.iterdir() if p.is_file())


def test_transcription_mode_requires_no_previous_draft(tmp_path):
    image = tmp_path / "page.png"
    Image.new("L", (2480, 3509), 255).save(image)
    manifest = ab.prepare(image, FIXTURES / "PG001_638_migne_raw.tsv", tmp_path / "experiment", formats=["block_tsv"])
    assert manifest["mode"] == "transcription"
    assert "draft.xml" not in manifest["files"]
    assert "PREVIOUS TRANSCRIPTION" not in (tmp_path / "experiment/block_tsv.txt").read_text()


def real_outputs():
    root = Path(__file__).parent / "fixtures/ocr_ab_pg001_638"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        assert ab.digest((root / name).read_bytes()) == expected
    return root, manifest


def test_real_ordered_output_has_layout_failure_not_hundreds_of_missing_words():
    root, manifest = real_outputs()
    layout = json.loads((root / "layout.json").read_text())
    text = (root / "r001_ordered_text.xml").read_text()
    spatial = ab.quality_profile(ab.compare(text, reference_tsv=(FIXTURES / "PG001_638_migne_raw.tsv").read_text(),
                                           image_size=[2480, 3509]))
    content = ab.content_profile(ab.compare(text, ab.content_reference(layout)))
    assert spatial["missing_reference_tokens"] == 395
    assert content["missing_reference_tokens"] == 11
    assert "spatial_alignment_confounded_check_boxes_and_reading_order" in ab.diagnostic_flags(spatial, content)


def test_real_micro_xml_copies_reference_errors_and_loses_visible_markers():
    import xml.etree.ElementTree as ET
    root, _ = real_outputs()
    layout = json.loads((root / "layout.json").read_text())
    text = (root / "r001_micro_xml.xml").read_text()
    content = ab.content_profile(ab.compare(text, ab.content_reference(layout)))
    assert content["literal_reference_tokens"] == 877
    assert content["reference_tokens"] == 878
    assert "near_verbatim_tesseract_agreement_requires_image_review" in ab.diagnostic_flags({"missing_reference_tokens": 2}, content)
    assert "1375" in text and "1246" in text
    marginal = [b.text.strip() for b in ET.fromstring(text).findall('bloco') if b.get('tipo') == 'nota_marginal']
    assert marginal == ["A"]


def test_real_raw_text_recovers_target_words_but_still_omits_first_line():
    root, _ = real_outputs()
    layout = json.loads((root / "layout.json").read_text())
    text = (root / "r001_raw_text.xml").read_text()
    assert "adulteros" in text and "homicidas" in text
    result = ab.compare(text, ab.content_reference(layout))
    assert any(r["text"] == "ditor exigit etiam legem impleri Et Petrus Ergo" for r in result["omission_candidates"])


def test_content_reference_preserves_physical_hyphen_boundaries():
    reference = ab.content_reference({"blocks": [{"text": "con-"}, {"text": "ditor & salvat"}]})
    result = ab.compare("con ditor salvat", reference)
    assert [t["text"] for t in result["reference_tokens"]] == ["con", "ditor", "salvat"]


def test_diagnostic_flags_do_not_label_empty_reference_as_copying():
    assert ab.diagnostic_flags({"missing_reference_tokens": 0}, {"reference_tokens": 0,
                               "missing_reference_tokens": 0, "literal_reference_fraction": 0}) == []
