import { useEffect, useState } from "react";
import { api, type Batch, type Project, type ReportData } from "../api";

const STATUS_LABELS: Record<string, string> = {
  NOT_STARTED: "未开始", AI_REVIEWING: "AI 审核中", MANUAL_REQUIRED: "需人工", FIX_PROPOSED: "待确认建议", APPROVED: "已通过", REJECTED: "已拒绝",
};

function Distribution({ title, values }: { title: string; values: Record<string, number> }) {
  const entries = Object.entries(values).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  return <section className="card"><h3>{title}</h3>{entries.length === 0 ? <p className="empty">暂无数据</p> : <div className="metric-list">{entries.map(([label, value]) => <div className="metric-row" key={label}><span title={label}>{STATUS_LABELS[label] || label}</span><div className="metric-bar"><i style={{ width: `${(value / max) * 100}%` }} /></div><b>{value}</b></div>)}</div>}</section>;
}

export default function Report() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [batchId, setBatchId] = useState(0);
  const [report, setReport] = useState<ReportData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => { api.projects().then((data) => { setProjects(data); if (data[0]) setProjectId(data[0].id); }).catch((err: Error) => setError(err.message)); }, []);
  useEffect(() => { if (!projectId) return; setBatchId(0); api.batches(projectId).then(setBatches).catch((err: Error) => setError(err.message)); }, [projectId]);
  useEffect(() => {
    if (!projectId) return;
    setLoading(true); setError("");
    api.report(projectId, batchId || undefined).then(setReport).catch((err: Error) => setError(err.message)).finally(() => setLoading(false));
  }, [projectId, batchId]);

  return (
    <div>
      <div className="page-heading"><div><h2>审核报告</h2><p>按项目或批次查看审核结果与人工介入情况。</p></div></div>
      <div className="filter-bar card">
        <div className="field"><label htmlFor="report-project">项目</label><select id="report-project" value={projectId} onChange={(event) => setProjectId(Number(event.target.value))}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></div>
        <div className="field"><label htmlFor="report-batch">批次</label><select id="report-batch" value={batchId} onChange={(event) => setBatchId(Number(event.target.value))}><option value={0}>全部批次</option>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}</select></div>
      </div>
      {error && <div className="msg err">{error}</div>}
      {loading && <p className="empty">正在统计...</p>}
      {report && !loading && <>
        <div className="report-title"><h3>{report.project.name}</h3><span>{report.batch ? `批次：${report.batch.name}` : "全部批次"}</span></div>
        <div className="stats-grid">
          <div className="stat"><b>{report.totals.contents}</b><span>内容总数</span></div><div className="stat"><b>{report.totals.issues}</b><span>当前问题</span></div><div className="stat"><b>{report.totals.tasks}</b><span>开放任务</span></div><div className="stat accent"><b>{Math.round(report.manual_metrics.rate * 100)}%</b><span>当前人工介入率</span></div>
        </div>
        <div className="report-grid"><Distribution title="审核状态" values={report.status_counts} /><Distribution title="问题类别" values={report.category_counts} /><Distribution title="规则命中" values={report.rule_counts} /><section className="card"><h3>人工审核</h3><dl className="detail-list"><div><dt>涉及内容</dt><dd>{report.manual_metrics.contents}</dd></div><div><dt>风险任务</dt><dd>{report.manual_metrics.tasks}</dd></div><div><dt>介入占比</dt><dd>{(report.manual_metrics.rate * 100).toFixed(1)}%</dd></div><div><dt>历史问题</dt><dd>{report.historical_totals.issues}</dd></div><div><dt>历史任务</dt><dd>{report.historical_totals.tasks}</dd></div></dl></section></div>
      </>}
    </div>
  );
}
