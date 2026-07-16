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
    return {
      clearRows,
      passRate: rows.length ? clearRows / rows.length : 0,
      attentionRows: attentionRows.length,
      issueDimensionCount,
      resultCounts: { CLEAR: clearRows, ATTENTION: attentionRows.length },
      progressCounts: { ANALYZED: analyzedRows, PENDING: Math.max(0, rows.length - analyzedRows) },
      issueExamples,
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
            <div className="section-heading"><div><h3>供应商问题沉淀 / 错题本</h3><p className="small">结合脚本内容、命中问题、证据片段和改稿建议，沉淀给供应商复盘的高频问题。</p></div></div>
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
