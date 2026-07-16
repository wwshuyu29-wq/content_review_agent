import { useEffect, useMemo, useState } from "react";
import { api, type Batch, type ContentTableRow, type Project, type ReportData } from "../api";
import { categoryLabel, decisionLabel, severityLabel, statusLabel } from "../reviewLabels";

function Distribution({ title, values, label }: { title: string; values: Record<string, number>; label: (value: string) => string }) {
  const entries = Object.entries(values).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  return (
    <section className="report-panel">
      <h3>{title}</h3>
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

const passStatuses = new Set(["PASSED", "PASSED_WITH_SUGGESTIONS"]);
const pct = (value: number) => Number.isFinite(value) ? `${Math.round(value * 100)}%` : "—";

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
    const passed = rows.filter((row) => passStatuses.has(row.review_status)).length;
    const human = rows.filter((row) => row.open_task_count > 0 || row.review_status === "HUMAN_REVIEW_REQUIRED").length;
    const dimensions: Record<string, number> = {};
    const issueExamples = rows
      .flatMap((row) => row.issues.map((issue) => ({ row, issue })))
      .sort((a, b) => b.issue.confidence - a.issue.confidence)
      .slice(0, 6);
    rows.flatMap((row) => row.agents).forEach((agent) => {
      if (agent.status !== "NOT_RUN" && agent.decision) dimensions[agent.decision] = (dimensions[agent.decision] || 0) + 1;
    });
    return {
      passed,
      passRate: rows.length ? passed / rows.length : 0,
      humanRate: rows.length ? human / rows.length : 0,
      dimensions,
      issueExamples,
    };
  }, [rows]);

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
            <Metric title="审核总稿量" value={String(report.totals.contents)} detail="当前筛选范围" />
            <Metric title="内容通过率" value={pct(metrics.passRate)} detail={`${metrics.passed}/${rows.length} 篇通过`} />
            <Metric title="人工介入率" value={pct(metrics.humanRate)} detail="待处理或需人工确认稿件" />
            <Metric title="高频问题数" value={String(report.historical_totals.issues)} detail="用于培训和错题本沉淀" />
          </section>
          <div className="report-grid">
            <Distribution title="维度评分结论分布" values={metrics.dimensions} label={decisionLabel} />
            <Distribution title="审核状态" values={report.status_counts} label={statusLabel} />
            <Distribution title="高频问题类别" values={report.category_counts} label={categoryLabel} />
            <Distribution title="命中规则排行" values={report.rule_counts} label={(value) => value} />
          </div>
          <section className="report-panel mistake-book">
            <div className="section-heading"><div><h3>问题沉淀 / 错题本</h3><p className="small">优先展示置信度较高的问题，用于后续培训和规则优化。</p></div></div>
            {metrics.issueExamples.length === 0 ? <p className="empty">当前筛选范围暂无问题。</p> : metrics.issueExamples.map(({ row, issue }) => (
              <article key={`${row.id}-${issue.id}`}>
                <div><b>{row.final_title || row.supplier_external_id}</b><span>{categoryLabel(issue.category)} · {severityLabel(issue.severity)}</span></div>
                <p>{issue.reason}</p>
                <small>{issue.suggestion || "暂无建议"}</small>
              </article>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
