from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_team_audit_cases_cover_three_projects_and_expected_clusters() -> None:
    payload = json.loads((ROOT / "data" / "review_cases" / "team_audit_cases.json").read_text(encoding="utf-8"))

    cases = payload["cases"]
    assert len(cases) == 3
    assert len({case["project_code"] for case in cases}) == 3
    assert all(case["manuscript"]["content_source"] in {"INTERNAL_UPLOAD", "AI_GENERATED"} for case in cases)
    assert all(case["brief"]["ai_review_focus"] for case in cases)
    assert any("品牌名错误" in case["expected_review"]["issue_clusters"] for case in cases)
    assert any(case["expected_review"]["should_pass"] is True for case in cases)
    assert any(case["manuscript"]["content_source"] == "AI_GENERATED" for case in cases)


def test_training_scripts_have_error_and_correct_examples() -> None:
    errors = _read_jsonl(ROOT / "data" / "training" / "basic_content_error_script.jsonl")
    correct = _read_jsonl(ROOT / "data" / "training" / "basic_content_correct_script.jsonl")

    assert len(errors) >= 6
    assert len(correct) >= 4
    assert all(sample["labels"] for sample in errors)
    assert all(sample["labels"] == [] for sample in correct)
    assert {"品牌名错误", "错别字", "重复表达", "未确认产品能力"} <= {
        label for sample in errors for label in sample["labels"]
    }
    assert all(sample["dimension_focus"] for sample in errors + correct)
