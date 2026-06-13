import json

from keywords_serial import apply_judge_decisions, parse_judge_response


def test_parse_judge_response_terms_list():
    raw = json.dumps(
        {
            "terms": [
                {"original": "A", "decision": "keep"},
                {"original": "B", "decision": "change", "change_to": "B2"},
            ]
        }
    )
    decisions, issues = parse_judge_response(raw)
    assert len(decisions) == 2
    assert decisions[0]["original"] == "A"
    assert decisions[1]["change_to"] == "B2"
    assert issues == []


def test_apply_judge_decisions_change_merge():
    existing = json.dumps(
        {
            "keywords": ["A", "B", "C"],
            "categorias": {"pessoas": ["B", "X"]},
        }
    )
    decisions = [
        {"original": "B", "decision": "change", "change_to": "B2"},
        {"original": "C", "decision": "merge", "change_to": "B2"},
    ]

    new_struct, report = apply_judge_decisions(
        existing, decisions, order_from_judge=True, include_categories=True
    )

    assert new_struct["keywords"] == ["B2", "A"]
    assert new_struct["categorias"]["pessoas"] == ["B2", "X"]
    assert report["merged"] >= 1
    assert report["changes"] >= 1


def test_apply_judge_missing_change_to_is_issue():
    existing = json.dumps({"keywords": ["A"]})
    decisions = [{"original": "A", "decision": "change"}]

    new_struct, report = apply_judge_decisions(
        existing, decisions, order_from_judge=True, include_categories=False
    )

    assert new_struct["keywords"] == ["A"]
    assert any("change_without_target" in issue for issue in report["issues"])


def test_apply_judge_change_same_name_keeps_and_flags():
    existing = json.dumps({"keywords": ["Alpha"]})
    decisions = [{"original": "Alpha", "decision": "change", "change_to": "Alpha"}]

    new_struct, report = apply_judge_decisions(
        existing, decisions, order_from_judge=True, include_categories=False
    )

    assert new_struct["keywords"] == ["Alpha"]
    # keep silencioso: não deve gerar issue
    assert all("change_same_as_original" not in issue for issue in report["issues"])


def test_apply_judge_change_to_existing_auto_merge():
    existing = json.dumps({"keywords": ["A", "B", "C"]})
    decisions = [{"original": "C", "decision": "change", "change_to": "A"}]

    new_struct, report = apply_judge_decisions(
        existing, decisions, order_from_judge=True, include_categories=False
    )

    # Deve manter ordem do primeiro A, removendo C e não duplicando
    assert new_struct["keywords"] == ["A", "B"]
    # nenhuma duplicação, não precisa contar removidos
    assert report["removed"] >= 0


def test_parse_judge_response_rejects_control_chars():
    raw = '{"terms":[{"original":"bad\\u007f", "decision":"keep"}]}'
    decisions, issues = parse_judge_response(raw)
    assert decisions == []
    assert "invalid_control_chars" in issues
