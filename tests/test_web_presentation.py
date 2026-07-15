from pathlib import Path


ROOT = Path(__file__).parents[1]
WEB = ROOT / "web" / "src"


def test_business_api_errors_use_shared_chinese_normalizer() -> None:
    source = (WEB / "api.ts").read_text(encoding="utf-8")
    assert "normalizeApiError" in source
    assert "网络连接异常，请检查网络后重试。" in source
    assert "请求失败，请稍后重试。" in source
    assert 'error.name === "AbortError"' in source


def test_business_types_do_not_carry_raw_result() -> None:
    source = (WEB / "api.ts").read_text(encoding="utf-8")
    assert "raw_result" not in source


def test_current_backend_enums_have_business_labels_and_no_raw_fallback() -> None:
    labels = (WEB / "reviewLabels.ts").read_text(encoding="utf-8")
    for value in (
        "INCOMPLETE", "INVALID", "PUBLISHED", "SCREENSHOT",
        "SCREEN_RECORDING", "TEST_LOG", "SKIPPED", "COMPLETED_WITH_ERRORS",
        "INTERRUPTED", "OPEN", "CLOSED",
    ):
        assert value in labels
    assert '|| value' not in labels


def test_evidence_and_report_use_the_central_label_adapters() -> None:
    evidence = (WEB / "components" / "TestEvidencePanel.tsx").read_text(encoding="utf-8")
    report = (WEB / "pages" / "Report.tsx").read_text(encoding="utf-8")
    assert "assetKindLabel" in evidence
    assert '<Distribution title="Agent 决策分布"' in report
    assert "label={decisionLabel}" in report
    assert "label={categoryLabel}" in report
