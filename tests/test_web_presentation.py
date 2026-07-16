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


def test_review_annotations_render_table_preview_before_full_detail() -> None:
    review = (WEB / "pages" / "Review.tsx").read_text(encoding="utf-8")
    agent_panel = (WEB / "components" / "AgentResultPanel.tsx").read_text(encoding="utf-8")

    assert "LightweightAnnotationPreview" in review
    assert "DimensionSnapshot" in review
    assert "selectedRow.agents" in review
    assert "dimension-full-card-grid" in review
    assert "className=\"dimension-card\"" in review
    assert "<summary>" in review
    assert "评分逻辑" in review
    assert "preview-only" in review
    assert "dimensionFromIssue" in review
    assert "预检批注" in review
    assert "加载完整详情" in review
    assert "正在加载完整详情" in review
    assert "detailRequestId" in review
    assert "api.content(detailRequestId" in review
    assert "selectedRow.issue_count" in review
    assert 'issue.category === "deterministic"' not in review
    assert "<dt>证据</dt>" not in agent_panel
    assert "<dt>原文片段</dt>" in agent_panel
    styles = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".review-detail-layout.preview-only" in styles
    dimension_styles = styles[styles.index(".dimension-snapshot {"):styles.index(".dimension-card {")]
    assert "grid-template-columns: repeat(2, minmax(320px, 1fr))" in dimension_styles
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" not in dimension_styles
    assert "setSelectedId(data[0]?.id || 0)" not in review


def test_dashboard_grid_removes_dead_middle_air() -> None:
    dashboard = (WEB / "pages" / "Dashboard.tsx").read_text(encoding="utf-8")
    styles = (WEB / "styles.css").read_text(encoding="utf-8")

    assert "dashboard-main-column" in dashboard
    assert "dashboard-side-column" in dashboard
    assert "dashboard-grid-compact-pass" in styles
    assert "grid-template-columns: minmax(0, 1.3fr) minmax(340px, .7fr)" in styles
    assert ".dashboard-main-column" in styles
    assert ".dashboard-side-column" in styles
    assert "min-height: 0" in styles


def test_dashboard_prioritizes_monthly_reviews_supplier_quality_and_clusters() -> None:
    dashboard = (WEB / "pages" / "Dashboard.tsx").read_text(encoding="utf-8")
    cluster_panel = (WEB / "components" / "dashboard" / "IssueClusterPanel.tsx").read_text(encoding="utf-8")
    api = (WEB / "api.ts").read_text(encoding="utf-8")

    assert "MonthlyReviewChart" in dashboard
    assert "SupplierQualityPanel" in dashboard
    assert "WorkloadChart" not in dashboard
    assert "团队月度工作量" not in dashboard
    assert "monthly_reviews" in api
    assert "supplier_quality" in api
    assert "供应商质量" in dashboard
    assert "聚类问题" in dashboard
    assert "问题稿件数" in dashboard
    assert "按五个维度去重统计" in dashboard
    assert "new Set" in dashboard
    assert "主要原因" in cluster_panel
    assert "高风险 {cluster.high_count}" not in cluster_panel
    labels = (WEB / "reviewLabels.ts").read_text(encoding="utf-8")
    assert 'COMPLIANCE: "合规审核"' in labels
    assert 'CONTENT_QUALITY: "基础内容校对"' in labels
    assert 'deterministic: "规则提示"' not in labels
    assert "cluster-manuscripts" not in cluster_panel


def test_api_setup_allows_team_members_to_choose_or_type_models() -> None:
    setup = (WEB / "components" / "dashboard" / "ApiSetupCard.tsx").read_text(encoding="utf-8")

    assert "MODEL_OPTIONS" in setup
    assert 'list="dashboard-model-options"' in setup
    assert '<datalist id="dashboard-model-options">' in setup
    assert "可选择常用模型，也可以直接输入团队可用的模型名" in setup
    assert "团队成员各自保存自己的 key 和模型" in setup
    assert "GPT 5.6 SOL" in setup
    assert "gpt-5.6-luna" in setup
    assert "<select" not in setup


def test_upload_flow_groups_brief_with_files_and_has_supplier_name() -> None:
    upload = (WEB / "pages" / "Upload.tsx").read_text(encoding="utf-8")

    assert "供应商名称 *" in upload
    assert "负责人 *" in upload
    assert "批次编号 *" in upload
    assert "Brief / Excel 文件" in upload
    assert "upload-main-card" in upload
    assert "supplierName" in upload
    assert "ownerName" in upload
    assert 'id="supplier-name"\n            type="text"' in upload
    assert 'id="owner-name"\n            type="text"' in upload
    assert 'id="batch-name"\n            type="text"' in upload
    assert 'data.append("supplier_id", supplierNameValue)' in upload
    assert 'data.append("owner_name", ownerNameValue)' in upload
    assert "项目 Brief / 审核标准" not in upload
    assert "本批次 Brief" not in upload
    assert "project-brief" not in upload
    assert "saveBrief" not in upload
    assert "publishStandard" not in upload
    assert "手工录入" not in upload
    assert "submitManual" not in upload


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


def test_review_and_report_use_the_central_label_adapters() -> None:
    review = (WEB / "pages" / "Review.tsx").read_text(encoding="utf-8")
    report = (WEB / "pages" / "Report.tsx").read_text(encoding="utf-8")
    assert "statusLabel" in review
    assert '<Distribution title="稿件初筛结果"' in report
    assert '<Distribution title="分析进度"' in report
    assert "label={mvpResultLabel}" in report
    assert "label={analysisProgressLabel}" in report
    assert "label={categoryLabel}" in report
    assert 'title="项目问题维度汇总"' in report
    assert "按当前项目/批次的稿件问题归类汇总" in report
    assert "高频问题类别" not in report
    assert "维度评分结论分布" not in report
    assert "人工介入率" not in report
    assert "高频问题数" not in report
    assert "需要人工确认" not in report


def test_report_removes_rule_ranking_and_adds_supplier_learning_prompt() -> None:
    report = (WEB / "pages" / "Report.tsx").read_text(encoding="utf-8")
    prompt = (ROOT / "prompts" / "供应商问题沉淀Prompt.md").read_text(encoding="utf-8")

    assert "命中规则排行" not in report
    assert "供应商问题沉淀" in report
    assert "错题本" in report
    assert "语义分析" in prompt
    assert "供应商改稿建议" in prompt
    assert "证据" in prompt


def test_audit_progress_summary_announces_updates_to_screen_readers() -> None:
    progress = (WEB / "components" / "AuditProgressPanel.tsx").read_text(encoding="utf-8")
    assert 'aria-live="polite"' in progress
    assert "audit-progress-summary-line" in progress
    assert "audit-progress-counts" in progress
