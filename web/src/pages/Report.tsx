import { useEffect, useMemo, useState } from "react";
import { api, type Batch, type ContentTableRow, type Project, type ReportData } from "../api";
import { categoryLabel, severityLabel } from "../reviewLabels";

function Distribution({ title, values, label, detail }: { title: string; values: Record<string, number>; label: (value: string) => string; detail?: string }) {
  const entries = Object.entries(values).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  return (
    <section className="report-panel">
      <h3>{title}</h3>
      {detail && <p className="small">{detail}</p>}
      {entries.length === 0 ? <p className="empty">暂无数据</p> : (
        <div className="metric-list">
          {entries.map(([key, value]) => (
            <div className="metric-row" key={key}>
              <span title={label(key)}>{label(key)}</span>
              <div className="metric-bar"><i style={{ width: `${(value / max) * 100}%` }} /></div>
              <b>{value}</b>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Metric({ title, value, detail }: { title: string; value: string; detail: string }) {
  return <article className="dashboard-kpi"><span>{title}</span><b>{value}</b><small>{detail}</small></article>;
}

const pct = (value: number) => Number.isFinite(value) ? `${Math.round(value * 100)}%` : "—";
const mvpResultLabel = (value: string) => ({ CLEAR: "未发现明显问题", ATTENTION: "需关注稿件" }[value] || value);
const analysisProgressLabel = (value: string) => ({ ANALYZED: "已完成分析", PENDING: "待分析" }[value] || value);

type SupplierReportPoint = { label: string; count: number; reason: string; suggestion: string };
type SupplierReportExample = { title: string; label: string; severity: string; reason: string; suggestion: string };
type SupplierLearningReport = {
  summary: string;
  focus: SupplierReportPoint[];
  action: string;
  examples: SupplierReportExample[];
};

function shortText(value: string, max = 86): string {
  const text = value.trim();
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function buildSupplierLearningReport(rows: ContentTableRow[]): SupplierLearningReport {
  const issues = rows.flatMap((row) => row.issues.map((issue) => ({ row, issue })));
  const affectedIds = new Set(issues.map(({ row }) => row.id));
  const dimensionMap = new Map<string, { count: number; reasons: Map<string, number>; suggestions: Map<string, number> }>();
  for (const { issue } of issues) {
    const key = categoryLabel(issue.category);
    const group = dimensionMap.get(key) || { count: 0, reasons: new Map<string, number>(), suggestions: new Map<string, number>() };
    group.count += 1;
    if (issue.reason) group.reasons.set(issue.reason, (group.reasons.get(issue.reason) || 0) + 1);
    if (issue.suggestion) group.suggestions.set(issue.suggestion, (group.suggestions.get(issue.suggestion) || 0) + 1);
    dimensionMap.set(key, group);
  }
  const focus = Array.from(dimensionMap.entries())
    .map(([label, group]) => {
      const topReason = Array.from(group.reasons.entries()).sort((a, b) => b[1] - a[1])[0]?.[0] || "暂无明确原因";
      const topSuggestion = Array.from(group.suggestions.entries()).sort((a, b) => b[1] - a[1])[0]?.[0] || "下轮提交前按该维度复查。";
      return { label, count: group.count, reason: shortText(topReason, 96), suggestion: shortText(topSuggestion, 96) };
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 3);
  const summary = issues.length === 0
    ? `本次共分析 ${rows.length} 篇稿件，当前未发现需要沉淀给供应商复盘的中高风险问题。`
    : `本次共分析 ${rows.length} 篇稿件，其中 ${affectedIds.size} 篇存在需要关注的问题。问题主要集中在${focus.map((item) => item.label).join("、")}，说明供应商在产品能力边界、合规表达和基础校对上仍需要前置把关。`;
  const action = issues.length === 0
    ? "后续可继续沿用当前提交方式，并保持品牌名、功能边界和标题正文一致性的提交前自检。"
    : `建议供应商下轮先按“${focus[0]?.label || "重点维度"}”建立提交前检查清单：未经确认的产品能力不要写成确定功能，绝对化或保证式表达改成有边界的体验描述，发布前再做一次标题、正文和标签格式校对。`;
  const examples = issues
    .sort((a, b) => b.issue.confidence - a.issue.confidence)
    .slice(0, 3)
    .map(({ row, issue }) => ({
      title: row.final_title || row.supplier_external_id,
      label: categoryLabel(issue.category),
      severity: severityLabel(issue.severity),
      reason: shortText(issue.reason, 86),
      suggestion: shortText(issue.suggestion || "暂无建议", 86),
    }));
  return { summary, focus, action, examples };
}

export default function Report() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [batchId, setBatchId] = useState(0);
  const [report, setReport] = useState<ReportData | null>(null);
  const [rows, setRows] = useState<ContentTableRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [projectsLoading, setProjectsLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    api.projects()
      .then((data) => {
        if (controller.signal.aborted) return;
        const tech = data.filter((project) => project.content_type === "TECH_MEDIA_REVIEW");
        setProjects(tech);
        setProjectId(tech[0]?.id || 0);
      })
      .catch((e: Error) => { if (e.name !== "AbortError") setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setProjectsLoading(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!projectId) { setBatches([]); setReport(null); setRows([]); return; }
    const controller = new AbortController();
    setBatchId(0);
    api.batches(projectId, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setBatches(data); })
      .catch((e: Error) => { if (e.name !== "AbortError") setError(e.message); });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    Promise.all([
      api.report(projectId, batchId || undefined),
      api.contentTable({ project_id: projectId, batch_id: batchId || undefined }, controller.signal),
    ])
      .then(([summary, table]) => {
        if (!controller.signal.aborted) {
          setReport(summary);
          setRows(table);
        }
      })
      .catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, batchId]);

  const metrics = useMemo(() => {
    const attentionRows = rows.filter((row) => row.issue_count > 0);
    const clearRows = rows.length - attentionRows.length;
    const analyzedRows = rows.filter((row) => row.latest_audit_id).length;
    const issueDimensionCount = Object.values(report?.category_counts || {}).filter((value) => value > 0).length;
    const issueExamples = rows
      .flatMap((row) => row.issues.map((issue) => ({ row, issue })))
      .sort((a, b) => b.issue.confidence - a.issue.confidence)
      .slice(0, 6);
    const reportNarrative = buildSupplierLearningReport(rows);
    return {
      clearRows,
      passRate: rows.length ? clearRows / rows.length : 0,
      attentionRows: attentionRows.length,
      issueDimensionCount,
      resultCounts: { CLEAR: clearRows, ATTENTION: attentionRows.length },
      progressCounts: { ANALYZED: analyzedRows, PENDING: Math.max(0, rows.length - analyzedRows) },
      issueExamples,
      reportNarrative,
    };
  }, [report?.category_counts, rows]);

  if (projectsLoading) return <div><div className="page-heading"><div><h2>审核报告</h2><p>正在读取项目列表...</p></div></div><p className="empty">加载中...</p></div>;
  if (!projects.length) return <div><div className="page-heading"><div><h2>审核报告</h2><p>当前没有可统计的审核项目。</p></div></div>{error && <div className="msg err">{error}</div>}<div className="card empty">当前没有可统计的审核项目。</div></div>;

  return (
    <div className="report-page">
      <div className="page-heading">
        <div>
          <h2>审核报告</h2>
          <p>聚焦工作量、通过率和高频问题沉淀。</p>
        </div>
      </div>
      <div className="filter-bar card">
        <div className="field"><label htmlFor="report-project">项目</label><select id="report-project" value={projectId} onChange={(e) => setProjectId(Number(e.target.value))}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></div>
        <div className="field"><label htmlFor="report-batch">批次</label><select id="report-batch" value={batchId} onChange={(e) => setBatchId(Number(e.target.value))}><option value={0}>全部批次</option>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}</select></div>
      </div>
      {error && <div className="msg err">{error}</div>}
      {loading && <p className="empty">正在统计...</p>}
      {report && !loading && (
        <>
          <div className="report-title"><h3>{report.project.name}</h3><span>{report.batch ? `批次：${report.batch.name}` : "全部批次"}</span></div>
          <section className="dashboard-kpi-grid">
            <Metric title="分析稿量" value={String(report.totals.contents)} detail="当前筛选范围" />
            <Metric title="初筛通过率" value={pct(metrics.passRate)} detail={`${metrics.clearRows}/${rows.length} 篇未发现明显问题`} />
            <Metric title="需关注稿件" value={String(metrics.attentionRows)} detail="按稿件去重统计" />
            <Metric title="主要问题类型" value={String(metrics.issueDimensionCount)} detail="按五个维度聚合" />
          </section>
          <div className="report-grid">
            <Distribution title="稿件初筛结果" values={metrics.resultCounts} label={mvpResultLabel} />
            <Distribution title="分析进度" values={metrics.progressCounts} label={analysisProgressLabel} />
            <Distribution title="项目问题维度汇总" values={report.category_counts} label={categoryLabel} detail="按当前项目/批次的稿件问题归类汇总" />
          </div>
          <section className="report-panel mistake-book">
            <div className="section-heading"><div><h3>供应商问题沉淀 / 供应商复盘报告</h3><p className="small">根据当前项目/批次的全部命中问题，汇总成便于复盘和沟通的文字报告。</p></div></div>
            <div className="supplier-report-body">
              <section>
                <h4>整体判断</h4>
                <p>{metrics.reportNarrative.summary}</p>
              </section>
              {metrics.reportNarrative.focus.length > 0 && (
                <section>
                  <h4>主要问题</h4>
                  <div className="supplier-report-focus">
                    {metrics.reportNarrative.focus.map((item) => (
                      <article key={item.label}>
                        <b>{item.label}</b><span>{item.count} 个问题</span>
                        <p>{item.reason}</p>
                        <small>{item.suggestion}</small>
                      </article>
                    ))}
                  </div>
                </section>
              )}
              <section>
                <h4>整改重点</h4>
                <p>{metrics.reportNarrative.action}</p>
              </section>
              {metrics.reportNarrative.examples.length > 0 && (
                <section>
                  <h4>代表问题</h4>
                  <div className="supplier-report-examples">
                    {metrics.reportNarrative.examples.map((example) => (
                      <article key={`${example.title}-${example.label}-${example.reason}`}>
                        <div><b>{example.title}</b><span>{example.label} · {example.severity}</span></div>
                        <p>{example.reason}</p>
                        <small>{example.suggestion}</small>
                      </article>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
