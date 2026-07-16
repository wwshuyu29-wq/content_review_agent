from pathlib import Path


ROOT = Path(__file__).parents[1]
WEB = ROOT / "web" / "src"


def test_business_api_errors_use_shared_chinese_normalizer() -> None:
    source = (WEB / "api.ts").read_text(encoding="utf-8")
    assert "normalizeApiError" in source
    assert "网络连接异常，请检查网络后重试。" in source
    assert "请求失败，请稍后重试。" in source
    assert 'error.name === "AbortError"' in source


def test_login_success_uses_router_navigation_without_hard_reload() -> None:
    source = (WEB / "pages" / "Login.tsx").read_text(encoding="utf-8")
    assert "useNavigate" in source
    assert 'navigate("/dashboard", { replace: true })' in source
    assert "window.location.assign" not in source


def test_authenticated_shell_uses_baidu_maps_sidebar_navigation() -> None:
    app = (WEB / "App.tsx").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")

    assert "app-shell" in app
    assert "sidebar" in app
    assert "lucide-react" in app
    assert "BarChart3" in app
    assert "ShieldCheck" in app
    assert "baidu-map-logo.png" in app
    assert "brand-logo" in app
    assert "百度地图" in app
    assert "工作台" in app
    assert 'label: "概览"' in app
    assert 'label: "看板"' not in app
    assert 'icon: "⌘"' not in app
    assert "Baidu Maps Review Ops" not in app
    assert "workspace-topbar" not in app
    assert "header className=\"top\"" not in app
    assert ".sidebar" in styles
    assert ".brand-logo" in styles
    assert ".content-shell" in styles


def test_login_screen_has_baidu_brand_and_refined_panel_structure() -> None:
    login = (WEB / "pages" / "Login.tsx").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")

    assert "login-brand" in login
    assert "百度地图" in login
    assert "login-panel" in login
    assert "baidu-map-logo.png" in login
    assert "Baidu Maps Review Ops" not in login
    assert ".login-brand" in styles
    assert ".login-panel" in styles
    assert "backdrop-filter" in styles


def test_visual_system_uses_frontend_design_palette_tokens() -> None:
    styles = (WEB / "styles.css").read_text(encoding="utf-8")

    for token in (
        "--map-ink: #111827",
        "--map-paper: #f7f8fb",
        "--map-surface: #ffffff",
        "--map-line: #e6eaf0",
        "--map-blue: #1f5eff",
        "--map-red: #e94235",
    ):
        assert token in styles
    assert "final design pass" in styles
    assert ".sidebar-nav a.active" in styles
    assert ".review-filter-bar" in styles
    assert "--primary: var(--map-blue)" in styles
    assert "--primary: #2468f2" not in styles


def test_review_filters_are_compact_without_search_or_blue_top_rule() -> None:
    review = (WEB / "pages" / "Review.tsx").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")

    assert "review-search-field" not in review
    assert "review-search" not in review
    assert "搜索内容 / 账号 / 平台" not in review
    assert "内容编号、标题、正文、账号" not in review
    assert "border-top: 2px solid rgba(31, 94, 255, .88)" not in styles
    assert "grid-template-columns: minmax(280px, 1.2fr) minmax(180px, .72fr) minmax(180px, .72fr) 184px" in styles


def test_dashboard_grid_removes_dead_middle_air() -> None:
    styles = (WEB / "styles.css").read_text(encoding="utf-8")

    assert "dashboard-grid-compact-pass" in styles
    assert "grid-template-columns: minmax(0, 1.3fr) minmax(340px, .7fr)" in styles
    assert "grid-auto-flow: dense" in styles
    assert "min-height: 0" in styles


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
    assert '<Distribution title="维度评分结论分布"' in report
    assert "label={decisionLabel}" in report
    assert "label={categoryLabel}" in report


def test_audit_progress_summary_announces_updates_to_screen_readers() -> None:
    progress = (WEB / "components" / "AuditProgressPanel.tsx").read_text(encoding="utf-8")
    assert 'aria-live="polite"' in progress
    assert "audit-progress-summary-line" in progress
    assert "audit-progress-counts" in progress
