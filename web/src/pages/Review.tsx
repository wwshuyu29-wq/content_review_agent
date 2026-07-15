import { useEffect, useMemo, useState } from "react";
import { api, type Batch, type Config, type ContentDetail, type ContentTableRow, type Project, type ReviewStatus, type ReviewTask, type TestCase } from "../api";
import AgentResultPanel from "../components/AgentResultPanel";
import TestEvidencePanel from "../components/TestEvidencePanel";

const STATUS_LABELS: Record<string, string> = { NOT_STARTED: "未开始", AI_REVIEWING: "AI 审核中", HUMAN_REVIEW_REQUIRED: "需人工", SUPPLIER_REVISION_REQUIRED: "供应商修改", AUTO_FIX_PENDING: "待确认修复", PASSED: "已通过", PASSED_WITH_SUGGESTIONS: "通过·有建议", BLOCKED: "已阻断", REJECTED: "已拒绝" };
const STATUSES: ReviewStatus[] = ["NOT_STARTED", "AI_REVIEWING", "HUMAN_REVIEW_REQUIRED", "SUPPLIER_REVISION_REQUIRED", "AUTO_FIX_PENDING", "PASSED", "PASSED_WITH_SUGGESTIONS", "BLOCKED", "REJECTED"];
const label = (value: string | null | undefined) => value ? STATUS_LABELS[value] || value : "—";

type Message = { type: "ok" | "err"; text: string };

export default function Review() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [rows, setRows] = useState<ContentTableRow[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [batchId, setBatchId] = useState(0);
  const [status, setStatus] = useState<ReviewStatus | "">("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(0);
  const [detail, setDetail] = useState<ContentDetail | null>(null);
  const [tests, setTests] = useState<TestCase[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [message, setMessage] = useState<Message | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [reviewer, setReviewer] = useState("reviewer@example.com");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");

  const loadTable = async (preferredId?: number) => {
    if (!projectId) return;
    setLoading(true);
    try {
      const data = await api.contentTable({ project_id: projectId, batch_id: batchId || undefined, review_status: status || undefined });
      setRows(data);
      const nextId = preferredId && data.some((row) => row.id === preferredId) ? preferredId : data[0]?.id || 0;
      setSelectedId(nextId);
    } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "内容表加载失败" }); }
    finally { setLoading(false); }
  };
  const loadDetail = async (id: number) => {
    if (!id) { setDetail(null); setTests([]); return; }
    setDetailLoading(true); setDetailError("");
    try { const [content, cases] = await Promise.all([api.content(id), api.contentTestCases(id)]); setDetail(content); setTests(cases); }
    catch (error) { setDetailError(error instanceof Error ? error.message : "详情加载失败"); }
    finally { setDetailLoading(false); }
  };
  useEffect(() => { Promise.all([api.projects(), api.config()]).then(([projectData, configData]) => { const tech = projectData.filter((project) => project.content_type === "TECH_MEDIA_REVIEW"); setProjects(tech); setConfig(configData); setProjectId(tech[0]?.id || 0); }).catch((error: Error) => setMessage({ type: "err", text: error.message })); }, []);
  useEffect(() => { if (!projectId) return; setBatchId(0); api.batches(projectId).then(setBatches).catch((error: Error) => setMessage({ type: "err", text: error.message })); }, [projectId]);
  useEffect(() => { loadTable().catch(() => undefined); }, [projectId, batchId, status]);
  useEffect(() => { loadDetail(selectedId).catch(() => undefined); }, [selectedId]);

  const filteredRows = useMemo(() => { const needle = search.trim().toLowerCase(); return needle ? rows.filter((row) => [row.supplier_external_id, row.account_name, row.platform, row.final_title, row.final_body].filter(Boolean).join(" ").toLowerCase().includes(needle)) : rows; }, [rows, search]);
  const selectedRow = rows.find((row) => row.id === selectedId) || null;
  const act = async (name: string, action: () => Promise<unknown>, success: string) => { setBusy(name); setMessage(null); try { await action(); setMessage({ type: "ok", text: success }); await loadTable(selectedId); await loadDetail(selectedId); } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "操作失败" }); } finally { setBusy(""); } };
  const resolve = (task: ReviewTask, decision: string) => { if (!reviewer.trim()) { setMessage({ type: "err", text: "请填写审核人" }); return; } return act(`task-${task.id}`, () => api.resolveTask(task.id, { decision, reviewer: reviewer.trim(), note: note.trim() || undefined }), "人工任务已处理"); };
  const saveConfig = async (patch: Partial<Pick<Config, "reviewer" | "model">>) => { try { const saved = await api.saveConfig(patch); setConfig(saved); } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "配置保存失败" }); } };

  return <div>
    <div className="page-heading"><div><h2>审核工作区</h2><p>按状态、风险、证据与账号定位内容；选择行查看三列处置工作区。</p></div></div>
    <section className="filter-bar card"><div className="field"><label htmlFor="review-project">项目</label><select id="review-project" value={projectId} onChange={(e) => setProjectId(Number(e.target.value))}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></div><div className="field"><label htmlFor="review-batch">批次</label><select id="review-batch" value={batchId} onChange={(e) => setBatchId(Number(e.target.value))}><option value={0}>全部批次</option>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}</select></div><div className="field"><label htmlFor="review-status">审核状态</label><select id="review-status" value={status} onChange={(e) => setStatus(e.target.value as ReviewStatus | "")}><option value="">全部状态</option>{STATUSES.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select></div><div className="field grow"><label htmlFor="review-search">搜索内容 / 账号 / 平台</label><input id="review-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="供应商编号、标题、正文、账号" /></div><div className="field"><label htmlFor="review-backend">审核后端</label><select id="review-backend" value={config?.reviewer || "heuristic"} onChange={(e) => saveConfig({ reviewer: e.target.value })}><option value="heuristic">启发式规则</option><option value="oneapi">OneAPI</option><option value="ernie">文心一言</option></select></div></section>
    {message && <div className={`msg ${message.type}`} role="status">{message.text}</div>}
    {loading && <p className="empty">正在加载内容表...</p>}
    {!loading && filteredRows.length === 0 && <div className="card empty">当前筛选下暂无内容。</div>}
    {!loading && filteredRows.length > 0 && <section className="card table-card"><div className="section-heading"><h3>内容表</h3><span className="count">{filteredRows.length} 行</span></div><div className="table-wrap"><table className="ops-table"><thead><tr><th>内容 / 版本</th><th>平台 / 账号</th><th>状态</th><th>风险</th><th>证据</th><th>任务</th></tr></thead><tbody>{filteredRows.map((row) => <tr key={row.id} className={selectedId === row.id ? "selected-row" : ""} onClick={() => setSelectedId(row.id)} tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") setSelectedId(row.id); }}><td><b>{row.final_title || row.supplier_external_id}</b><div className="cell-subline">{row.supplier_external_id} · #{row.id}{row.row_number ? ` · Excel 行 ${row.row_number}` : ""}</div></td><td>{row.platform || "—"}<div className="cell-subline">{row.account_name || "未提供账号"}</div></td><td><span className={`badge status-${row.review_status.toLowerCase()}`}>{label(row.review_status)}</span><div className="cell-subline">发布：{label(row.publish_status)}</div></td><td>{row.issue_count ? <><span className={`risk-label severity-${(row.highest_severity || "unknown").toLowerCase()}`}>{row.highest_severity}</span><div className="cell-subline">{row.categories.join("、") || "未分类"}</div></> : <span className="muted">无命中</span>}</td><td><span className={`badge status-${row.evidence_status.toLowerCase()}`}>{row.evidence_status === "PRESENT" ? "已提供" : row.evidence_status === "MISSING" ? "缺失" : "无测试"}</span><div className="cell-subline">{row.test_count} 测试 · {row.evidence_count} 文件</div></td><td>{row.open_task_count ? <span className="task-count">{row.open_task_count} 个开放任务</span> : <span className="muted">—</span>}</td></tr>)}</tbody></table></div></section>}
    <div className="workspace-layout">
      <section className="card content-workspace"><div className="panel-heading"><h3>内容与发现</h3>{selectedRow && <span className="small">#{selectedRow.id} · {selectedRow.supplier_external_id}</span>}</div>{detailLoading && <p className="panel-state">正在加载详情...</p>}{detailError && <div className="panel-state error">{detailError}</div>}{detail && !detailLoading && <><div className="badge-group"><span className={`badge status-${detail.review_status.toLowerCase()}`}>{label(detail.review_status)}</span><span className={`badge status-${detail.format_status.toLowerCase()}`}>{detail.format_status}</span><span className={`badge status-${detail.publish_status.toLowerCase()}`}>{detail.publish_status}</span></div><h4>{detail.versions[detail.versions.length - 1]?.title || detail.title}</h4><div className="content-copy">{detail.versions[detail.versions.length - 1]?.body || "无正文"}</div><div className="meta-line">版本：V{detail.versions[detail.versions.length - 1]?.version || "—"} · 来源：{detail.versions[detail.versions.length - 1]?.source || "—"}</div>{detail.latest_audit && <><h4 className="subheading">结构化发现（{detail.latest_audit.issues.length}）</h4>{detail.latest_audit.issues.length === 0 ? <p className="panel-state">最近审核未返回结构化问题。</p> : <div className="issue-list">{detail.latest_audit.issues.map((issue) => <article className={`issue severity-${issue.severity.toLowerCase()}`} key={issue.id}><div className="issue-head"><b>{issue.rule_id}</b><span>{issue.category} · {issue.field}</span><strong>{issue.severity}</strong></div>{issue.evidence_quote && <blockquote>{issue.evidence_quote}</blockquote>}<p>{issue.reason}</p><p className="suggestion"><b>建议：</b>{issue.suggestion || "未提供"}</p></article>)}</div>}</>}</>}</section>
      <TestEvidencePanel cases={tests} loading={detailLoading} error={detailError} />
      <div className="workspace-side"><AgentResultPanel results={detail?.latest_audit?.agent_results || []} loading={detailLoading} error={detailError} auditExists={Boolean(detail?.latest_audit)} />{detail && <section className="workspace-panel"><div className="panel-heading"><h3>人工 / 任务动作</h3><span className="count">{detail.open_tasks.length}</span></div>{detail.open_tasks.length === 0 ? <p className="panel-state">当前没有开放任务。</p> : <><div className="reviewer-fields"><div className="field"><label htmlFor="reviewer">审核人</label><input id="reviewer" value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></div><div className="field"><label htmlFor="task-note">备注</label><input id="task-note" value={note} onChange={(e) => setNote(e.target.value)} /></div></div>{detail.open_tasks.map((task) => <div className="task" key={task.id}><div><b>#{task.id} · {task.task_type}</b><span className="meta-line">审核 #{task.audit_run_id} · 问题 {task.issue_ids.length || "—"}</span></div><div className="btn-row"><button className="btn btn-pass" disabled={!!busy} onClick={() => resolve(task, task.task_type.includes("RISK") ? "APPROVE_RISK" : "ACCEPT_SUGGESTION")}>通过</button><button className="btn btn-danger" disabled={!!busy} onClick={() => resolve(task, task.task_type.includes("RISK") ? "REJECT_RISK" : "REJECT_SUGGESTION")}>退回</button></div></div>)}</>}</section>}</div>
    </div>
  </div>;
}
