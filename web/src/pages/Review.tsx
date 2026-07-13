import { useEffect, useMemo, useState } from "react";
import { api, type Batch, type Config, type ContentDetail, type ContentSummary, type Project, type ReviewStatus, type ReviewTask } from "../api";
import DiffView from "../components/DiffView";

const STATUS_LABELS: Record<string, string> = { NOT_STARTED: "未开始", AI_REVIEWING: "AI 审核中", MANUAL_REQUIRED: "需人工", FIX_PROPOSED: "待确认建议", APPROVED: "已通过", REJECTED: "已拒绝" };
const FILTER_STATUSES: ReviewStatus[] = ["NOT_STARTED", "AI_REVIEWING", "MANUAL_REQUIRED", "FIX_PROPOSED", "APPROVED", "REJECTED"];

export default function Review() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [contents, setContents] = useState<ContentSummary[]>([]);
  const [tasks, setTasks] = useState<ReviewTask[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [batchId, setBatchId] = useState(0);
  const [status, setStatus] = useState("");
  const [selectedId, setSelectedId] = useState(0);
  const [detail, setDetail] = useState<ContentDetail | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [reviewer, setReviewer] = useState("reviewer@example.com");
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [editedTitle, setEditedTitle] = useState("");
  const [editedBody, setEditedBody] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const loadContents = async (preferredId?: number) => {
    if (!projectId) return;
    const [items, openTasks] = await Promise.all([
      api.contents({ project_id: projectId, batch_id: batchId || undefined, review_status: status || undefined }),
      api.reviewTasks({ status: "OPEN", project_id: projectId, batch_id: batchId || undefined }),
    ]);
    setContents(items); setTasks(openTasks);
    const nextId = preferredId && items.some((item) => item.id === preferredId) ? preferredId : items[0]?.id || 0;
    setSelectedId(nextId);
    if (!nextId) setDetail(null);
    return nextId;
  };
  const loadDetail = async (id: number) => {
    const data = await api.content(id);
    setDetail(data);
    const proposed = data.versions.find((version) => version.source === "AI_PROPOSED");
    if (proposed) { setEditedTitle(proposed.title); setEditedBody(proposed.body); }
    setEditing(false); setNote("");
  };

  useEffect(() => {
    Promise.all([api.projects(), api.config()]).then(([projectData, configData]) => { setProjects(projectData); setConfig(configData); if (projectData[0]) setProjectId(projectData[0].id); }).catch((error: Error) => setMessage({ type: "err", text: error.message }));
  }, []);
  useEffect(() => { if (!projectId) return; setBatchId(0); api.batches(projectId).then(setBatches).catch((error: Error) => setMessage({ type: "err", text: error.message })); }, [projectId]);
  useEffect(() => { loadContents().catch((error: Error) => setMessage({ type: "err", text: error.message })); }, [projectId, batchId, status]);
  useEffect(() => { if (selectedId) loadDetail(selectedId).catch((error: Error) => setMessage({ type: "err", text: error.message })); }, [selectedId]);

  const taskByContent = useMemo(() => tasks.reduce<Record<number, number>>((counts, task) => ({ ...counts, [task.content_item_id]: (counts[task.content_item_id] || 0) + 1 }), {}), [tasks]);
  const original = detail?.versions.find((version) => version.source === "SUPPLIER") || detail?.versions[0];
  const proposed = detail?.versions.find((version) => version.source === "AI_PROPOSED");

  const act = async (label: string, action: () => Promise<unknown>, success: string) => {
    setBusy(label); setMessage(null);
    try { await action(); setMessage({ type: "ok", text: success }); const nextId = await loadContents(selectedId); if (nextId) await loadDetail(nextId); }
    catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "操作失败" }); }
    finally { setBusy(""); }
  };
  const auditBatch = () => { if (batchId) act("batch", () => api.auditBatch(batchId), "批次审核已完成"); };
  const auditContent = (id: number) => act(`content-${id}`, () => api.auditContent(id), "内容审核已完成");
  const resolve = (task: ReviewTask, decision: string, payload: Record<string, unknown> = {}) => {
    if (!reviewer.trim()) return setMessage({ type: "err", text: "请填写审核人" });
    return act(`task-${task.id}`, () => api.resolveTask(task.id, { decision, reviewer: reviewer.trim(), note: note.trim() || undefined, payload }), "人工任务已处理");
  };
  const saveConfig = async (patch: Partial<Config>) => {
    try { const saved = await api.saveConfig(patch); setConfig(saved); }
    catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "配置保存失败" }); }
  };

  return <div>
    <div className="page-heading"><div><h2>审核台</h2><p>筛选内容、触发审核并处理开放人工任务。</p></div><div className="header-actions">{batchId ? <button className="btn btn-primary" onClick={auditBatch} disabled={!!busy}>{busy === "batch" ? "审核中..." : "审核当前批次"}</button> : <span className="small">选择批次后可批量审核</span>}</div></div>
    <section className="filter-bar card">
      <div className="field"><label htmlFor="review-project">项目</label><select id="review-project" value={projectId} onChange={(event) => setProjectId(Number(event.target.value))}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></div>
      <div className="field"><label htmlFor="review-batch">批次</label><select id="review-batch" value={batchId} onChange={(event) => setBatchId(Number(event.target.value))}><option value={0}>全部批次</option>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}</select></div>
      <div className="field"><label htmlFor="review-status">审核状态</label><select id="review-status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option>{FILTER_STATUSES.map((value) => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}</select></div>
      <div className="field"><label htmlFor="review-backend">审核后端</label><select id="review-backend" value={config?.reviewer || "offline"} onChange={(event) => saveConfig({ reviewer: event.target.value })}><option value="offline">离线规则</option><option value="oneapi">OneAPI</option><option value="multi-agent">多 Agent</option></select></div>
      <div className="field grow"><label htmlFor="review-model">模型</label><input id="review-model" type="text" value={config?.model || ""} onChange={(event) => setConfig(config ? { ...config, model: event.target.value } : null)} onBlur={(event) => saveConfig({ model: event.target.value })} placeholder="留空使用后端默认值" /></div>
    </section>
    {config && !config.key_set && config.reviewer !== "offline" && <div className="msg err">后端未检测到 ONEAPI_KEY，在线模型审核会失败。</div>}
    {message && <div className={`msg ${message.type}`}>{message.text}</div>}
    <div className="review-layout">
      <section className="content-list card"><div className="section-heading"><h3>内容</h3><span className="count">{contents.length}</span></div>{contents.length === 0 && <p className="empty">当前筛选下暂无内容</p>}<div className="stack-list">{contents.map((content) => <button type="button" className={`content-row ${selectedId === content.id ? "active" : ""}`} key={content.id} onClick={() => setSelectedId(content.id)}><div><strong>{content.title}</strong><small>{content.external_id} · 批次 #{content.batch_id}</small></div><div className="content-status"><span className={`badge status-${content.review_status.toLowerCase()}`}>{STATUS_LABELS[content.review_status]}</span>{taskByContent[content.id] ? <span className="task-count">{taskByContent[content.id]} 任务</span> : null}</div></button>)}</div></section>
      <section className="review-detail">
        {!detail && <div className="card empty">选择一条内容查看审核详情</div>}
        {detail && <>
          <div className="card"><div className="section-heading"><div><div className="meta-line">{detail.external_id} · 内容 #{detail.id}</div><h3>{detail.title}</h3></div><div className="badge-group"><span className={`badge status-${detail.format_status.toLowerCase()}`}>{detail.format_status}</span><span className={`badge status-${detail.review_status.toLowerCase()}`}>{STATUS_LABELS[detail.review_status]}</span><span className={`badge status-${detail.publish_status.toLowerCase()}`}>{detail.publish_status}</span></div></div><div className="content-copy">{detail.versions[detail.versions.length - 1]?.body}</div>{detail.versions[detail.versions.length - 1]?.payload.media ? <img className="content-media" src={`/api/media/${detail.id}`} alt="内容素材" /> : null}<div className="btn-row"><button className="btn btn-ghost" disabled={!!busy || detail.format_status !== "PASSED"} onClick={() => auditContent(detail.id)}>{busy === `content-${detail.id}` ? "审核中..." : "重新审核此内容"}</button></div></div>
          {detail.latest_audit && <section className="card"><div className="section-heading"><h3>最近审核运行</h3><span className="meta-line">#{detail.latest_audit.id} · {detail.latest_audit.model} · 规则 V{detail.latest_audit.rule_version_id} · {detail.latest_audit.prompt_version}</span></div><h4 className="subheading">结构化问题</h4>{detail.latest_audit.issues.length === 0 ? <p className="empty">未发现问题</p> : <div className="issue-list">{detail.latest_audit.issues.map((issue) => <article className={`issue severity-${issue.severity.toLowerCase()}`} key={issue.id}><div className="issue-head"><strong>{issue.rule_id}</strong><span>{issue.category} · {issue.field}</span><span className="risk-label">{issue.severity}</span><span>{Math.round(issue.confidence * 100)}%</span></div>{issue.evidence_quote && <blockquote>{issue.evidence_quote}</blockquote>}<p>{issue.reason}</p><p className="suggestion"><b>建议：</b>{issue.suggestion}</p><div className="tag-row">{issue.auto_fixable && <span>可自动修复</span>}{issue.human_required && <span>需人工</span>}</div></article>)}</div>}<details className="agent-evidence"><summary>Agent 原始证据（{detail.latest_audit.agent_results.length}）</summary>{detail.latest_audit.agent_results.map((result) => <div key={result.id}><div className="meta-line">{result.agent_name} · {result.status}</div><pre>{JSON.stringify(result.raw_result, null, 2)}</pre></div>)}</details></section>}
          {proposed && original && detail.open_tasks.some((task) => task.task_type === "REVIEW_FIX_PROPOSAL") && <section className="card"><div className="section-heading"><h3>AI 修改建议确认</h3><button className="text-button" type="button" onClick={() => setEditing(!editing)}>{editing ? "查看建议稿" : "编辑后接受"}</button></div><DiffView original={original} proposed={proposed} editable={editing} title={editedTitle} body={editedBody} onTitleChange={setEditedTitle} onBodyChange={setEditedBody} /></section>}
          {detail.open_tasks.length > 0 && <section className="card"><h3>开放人工任务</h3><div className="reviewer-fields"><div className="field"><label htmlFor="reviewer">审核人</label><input id="reviewer" type="text" value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></div><div className="field grow"><label htmlFor="task-note">处理备注</label><input id="task-note" type="text" value={note} onChange={(event) => setNote(event.target.value)} /></div></div>{detail.open_tasks.map((task) => <article className="task" key={task.id}><div><strong>#{task.id} · {task.task_type === "REVIEW_FIX_PROPOSAL" ? "建议稿确认" : "风险审核"}</strong><span className="meta-line">{task.issue_id ? `关联问题 #${task.issue_id}` : "内容级任务"}</span></div><div className="btn-row">{task.task_type === "REVIEW_FIX_PROPOSAL" ? <><button className="btn btn-pass" disabled={!!busy} onClick={() => resolve(task, "ACCEPT_SUGGESTION")}>接受建议</button><button className="btn btn-primary" disabled={!!busy || !editedTitle.trim()} onClick={() => resolve(task, "ACCEPT_EDITED", { title: editedTitle, body: editedBody })}>编辑后接受</button><button className="btn btn-danger" disabled={!!busy} onClick={() => resolve(task, "REJECT_SUGGESTION")}>拒绝建议</button></> : <><button className="btn btn-pass" disabled={!!busy} onClick={() => resolve(task, "APPROVE_RISK")}>批准风险</button><button className="btn btn-danger" disabled={!!busy} onClick={() => resolve(task, "REJECT_RISK")}>拒绝内容</button></>}</div></article>)}</section>}
          <section className="card"><h3>版本历史</h3><div className="version-timeline">{[...detail.versions].reverse().map((version) => <div key={version.id}><b>V{version.version}</b><span>{version.source}</span><span>{version.title}</span><time>{new Date(version.created_at).toLocaleString()}</time></div>)}</div></section>
        </>}
      </section>
    </div>
  </div>;
}
