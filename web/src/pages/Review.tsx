import { type ReactNode, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AGENT_ORDER,
  api,
  type AuditJobProgress,
  type Batch,
  type ContentDetail,
  type ContentTableAgent,
  type ContentTableRow,
  type Issue,
  type Project,
  type ReviewStatus,
  type ReviewTask,
} from "../api";
import AgentResultPanel from "../components/AgentResultPanel";
import AuditProgressPanel from "../components/AuditProgressPanel";
import {
  agentLabel,
  categoryLabel,
  contentSourceLabel,
  decisionLabel,
  fieldLabel,
  formatStatusLabel,
  publishStatusLabel,
  severityLabel,
  statusLabel,
  taskTypeLabel,
} from "../reviewLabels";

const STATUSES: ReviewStatus[] = [
  "NOT_STARTED",
  "AI_REVIEWING",
  "HUMAN_REVIEW_REQUIRED",
  "SUPPLIER_REVISION_REQUIRED",
  "AUTO_FIX_PENDING",
  "PASSED",
  "PASSED_WITH_SUGGESTIONS",
  "BLOCKED",
  "REJECTED",
];
const TERMINAL_AUDIT_JOB_STATUSES = new Set(["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "INTERRUPTED"]);
const DEPRECATED_PRESENTATION_RULE_IDS = new Set([
  "TEST-COUNT-001",
  "TEST-EVIDENCE-001",
]);
const label = (value: string | null | undefined) => value ? statusLabel(value) : "—";
type Message = { type: "ok" | "err"; text: string };

const formatSkipReason = (row: Pick<ContentTableRow, "format_status" | "format_errors">) => (
  row.format_status === "INCOMPLETE"
    ? `信息不完整：${row.format_errors.length ? row.format_errors.join("；") : "字段信息不完整"}，未进入自动审核`
    : ""
);

function visibleIssue(issue: Issue): boolean {
  return !["system", "system_suggestion"].includes(issue.category) && !DEPRECATED_PRESENTATION_RULE_IDS.has(issue.rule_id);
}

function dimensionFromIssue(issue: Issue): string {
  const rawCategory = issue.category || "";
  if (["CONTENT_QUALITY", "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "CAMPAIGN_EFFECTIVENESS"].includes(rawCategory)) return rawCategory;
  const searchable = [issue.rule_id, rawCategory, issue.reason, issue.suggestion, issue.field, issue.evidence_quote].join(" ");
  if (/^BRAND|品牌|官方名称|产品名|卖点口径/.test(searchable)) return "BRAND";
  if (/^CLAIM|合规|绝对|保证|承诺|夸大|广告法/.test(searchable)) return "COMPLIANCE";
  if (/功能|能力|路线|规划|导航|产品准确|事实错误|讲错/.test(searchable)) return "PRODUCT_ACCURACY";
  if (/传播|卖点|转化|受众|场景|标题吸引/.test(searchable)) return "CAMPAIGN_EFFECTIVENESS";
  return "CONTENT_QUALITY";
}

function issueDimension(issue: Issue, detail: ContentDetail): string {
  const agent = detail.latest_audit?.agent_results.find((result) => result.id === issue.agent_result_id);
  if (agent?.agent_id) return categoryLabel(agent.agent_id);
  return categoryLabel(dimensionFromIssue(issue));
}

function locatedIssues(body: string, issues: Issue[]): Issue[] {
  const used = new Set<string>();
  return issues
    .filter((issue) => visibleIssue(issue) && issue.evidence_quote && body.includes(issue.evidence_quote))
    .sort((a, b) => body.indexOf(a.evidence_quote) - body.indexOf(b.evidence_quote))
    .filter((issue) => {
      const key = `${issue.evidence_quote}-${issue.rule_id}`;
      if (used.has(key)) return false;
      used.add(key);
      return true;
    });
}

function AnnotatedBody({ body, issues }: { body: string; issues: Issue[] }) {
  const markers = locatedIssues(body, issues);
  if (!markers.length) return <div className="review-body-text">{body || "无正文"}</div>;
  const nodes: ReactNode[] = [];
  let cursor = 0;
  markers.forEach((issue, index) => {
    const start = body.indexOf(issue.evidence_quote, cursor);
    if (start < 0) return;
    if (start > cursor) nodes.push(<span key={`text-${issue.id}`}>{body.slice(cursor, start)}</span>);
    nodes.push(
      <mark className={`body-annotation severity-${issue.severity.toLowerCase()}`} key={`mark-${issue.id}`}>
        {body.slice(start, start + issue.evidence_quote.length)}
        <sup>{index + 1}</sup>
      </mark>,
    );
    cursor = start + issue.evidence_quote.length;
  });
  if (cursor < body.length) nodes.push(<span key="text-end">{body.slice(cursor)}</span>);
  return <div className="review-body-text">{nodes}</div>;
}

function IssueNotes({ detail, body, issues }: { detail?: ContentDetail; body: string; issues: Issue[] }) {
  const allIssues = issues.filter(visibleIssue);
  const pinned = locatedIssues(body, allIssues);
  const pinnedIds = new Set(pinned.map((issue) => issue.id));
  const unpinned = allIssues.filter((issue) => !pinnedIds.has(issue.id));
  const dimension = (issue: Issue) => detail ? issueDimension(issue, detail) : categoryLabel(dimensionFromIssue(issue));
  if (!allIssues.length) {
    return <aside className="annotation-rail"><p className="panel-state">当前没有可展示的问题批注。</p></aside>;
  }
  return (
    <aside className="annotation-rail" aria-label="问题批注">
      {pinned.map((issue, index) => (
        <article className={`annotation-note severity-${issue.severity.toLowerCase()}`} key={issue.id}>
          <div className="annotation-note-head"><span>{index + 1}</span><b>{dimension(issue)}</b><strong>{severityLabel(issue.severity)}</strong></div>
          <p>{issue.reason}</p>
          <p className="suggestion"><b>建议：</b>{issue.suggestion || "未提供"}</p>
        </article>
      ))}
      {unpinned.length > 0 && (
        <details className="annotation-overflow" open={pinned.length === 0}>
          <summary>未定位问题（{unpinned.length}）</summary>
          <div className="issue-list compact-issues">
            {unpinned.map((issue) => (
              <article className={`issue severity-${issue.severity.toLowerCase()}`} key={issue.id}>
                <div className="issue-head"><b>{dimension(issue)}</b><span>{fieldLabel(issue.field)}</span><strong>{severityLabel(issue.severity)}</strong></div>
                <p>{issue.reason}</p>
                <p className="suggestion"><b>建议：</b>{issue.suggestion || "未提供"}</p>
              </article>
            ))}
          </div>
        </details>
      )}
    </aside>
  );
}

function DimensionSnapshot({ agents }: { agents: ContentTableAgent[] }) {
  const byId = new Map(agents.map((agent) => [agent.agent_id, agent]));
  return (
    <div className="dimension-snapshot dimension-full-card-grid" aria-label="五维度评分">
      {AGENT_ORDER.map((agentId) => {
        const agent = byId.get(agentId);
        return (
          <details className="dimension-card" key={agentId}>
            <summary>
              <span>{agentLabel(agentId)}</span>
              <b>{agent?.score === null || agent?.score === undefined ? "未评分" : `${agent.score} 分`}</b>
            </summary>
            <strong>{decisionLabel(agent?.decision)}</strong>
            <p>{agent?.summary || "待评分"}</p>
            <dl>
              <div><dt>评分逻辑</dt><dd>90-100 无发布问题；80-89 轻微建议；70-79 需修改；60-69 需人工确认；60 以下明显不可发布。</dd></div>
            </dl>
          </details>
        );
      })}
    </div>
  );
}

function LightweightAnnotationPreview({
  selectedRow,
  detailLoading,
  onLoadDetail,
}: {
  selectedRow: ContentTableRow;
  detailLoading: boolean;
  onLoadDetail: () => void;
}) {
  const issues = selectedRow.issues || [];
  const body = selectedRow.final_body || selectedRow.body_summary || "";
  const reviewing = selectedRow.review_status === "AI_REVIEWING";
  const skipReason = formatSkipReason(selectedRow);
  return (
    <>
      <div className="badge-group annotation-preview-status">
        <span className="badge status-pending">预检批注</span>
        <span className={`badge status-${selectedRow.review_status.toLowerCase()}`}>{label(selectedRow.review_status)}</span>
        <span className={`badge status-${selectedRow.publish_status.toLowerCase()}`}>发布：{publishStatusLabel(selectedRow.publish_status)}</span>
        {selectedRow.issue_count > 0 && (
          <span className={`risk-label severity-${(selectedRow.highest_severity || "unknown").toLowerCase()}`}>
            {selectedRow.issue_count} 个问题 · {severityLabel(selectedRow.highest_severity)}
          </span>
        )}
        <button type="button" className="btn btn-ghost btn-sm" onClick={onLoadDetail} disabled={detailLoading}>
          {detailLoading ? "正在加载完整详情..." : "加载完整详情"}
        </button>
      </div>
      <p className="panel-state compact">
        {skipReason || (reviewing ? "AI 正在判断，预检结论会随审核进度刷新；完整批注生成后可继续查看明细。" : "已先展示表格风险和可定位批注，完整 Agent 判断可按需加载。")}
      </p>
      <article className="manuscript-review annotation-preview">
        <header>
          <h4>{selectedRow.final_title || selectedRow.supplier_external_id}</h4>
          <p className="meta-line">{selectedRow.supplier_external_id}{selectedRow.row_number ? ` · Excel 行 ${selectedRow.row_number}` : ""}</p>
        </header>
        <div className="document-annotation-grid">
          <AnnotatedBody body={body} issues={issues} />
          <IssueNotes body={body} issues={issues} />
        </div>
        <DimensionSnapshot agents={selectedRow.agents} />
      </article>
    </>
  );
}

function TaskAction({ task, busy, onResolve }: { task: ReviewTask; busy: boolean; onResolve: (task: ReviewTask, decision: string) => void }) {
  if (task.task_type === "AUTO_FIX_PROPOSAL") {
    return <>
      <button className="btn btn-pass" disabled={busy} onClick={() => onResolve(task, "ACCEPT_AUTO_FIX")}>接受修复</button>
      <button className="btn btn-danger" disabled={busy} onClick={() => onResolve(task, "REJECT_AUTO_FIX")}>退回修改</button>
    </>;
  }
  if (task.task_type === "HUMAN_REVIEW" || task.task_type === "BLOCK_REVIEW") {
    return <>
      <button className="btn btn-pass" disabled={busy} onClick={() => onResolve(task, "HUMAN_APPROVE")}>人工通过</button>
      <button className="btn btn-danger" disabled={busy} onClick={() => onResolve(task, "HUMAN_REJECT")}>人工拒绝</button>
    </>;
  }
  if (task.task_type === "SUPPLIER_REVISION") {
    return <p className="panel-state task-next-action">等待供应商提交修订稿；当前页面不代替原稿创建新版本。</p>;
  }
  return <p className="panel-state">任务类型 {taskTypeLabel(task.task_type)} 暂无可用前端动作。</p>;
}

function DetailWorkspace({
  detail,
  detailLoading,
  detailError,
  selectedRow,
  reviewer,
  setReviewer,
  note,
  setNote,
  busy,
  resolve,
  reauditSelected,
  reauditBusy,
  loadDetail,
}: {
  detail: ContentDetail | null;
  detailLoading: boolean;
  detailError: string;
  selectedRow: ContentTableRow | null;
  reviewer: string;
  setReviewer: (value: string) => void;
  note: string;
  setNote: (value: string) => void;
  busy: string;
  resolve: (task: ReviewTask, decision: string) => void;
  reauditSelected: () => void;
  reauditBusy: boolean;
  loadDetail: () => void;
}) {
  const latest = detail?.versions[detail.versions.length - 1];
  const body = latest?.body || "";
  const issues = detail?.latest_audit?.issues || [];
  const hasDetail = Boolean(detail && selectedRow && detail.id === selectedRow.id);
  return (
    <div className={`review-detail-layout ${hasDetail ? "" : "preview-only"}`}>
      <section className="card content-workspace review-document-card">
        <div className="panel-heading">
          <h3>稿件批注</h3>
          {selectedRow && <span className="small">{selectedRow.supplier_external_id}</span>}
        </div>
        {!selectedRow && <p className="panel-state">选择一条内容查看预检批注。</p>}
        {selectedRow && !hasDetail && (
          <>
            <LightweightAnnotationPreview selectedRow={selectedRow} detailLoading={detailLoading} onLoadDetail={loadDetail} />
            {detailError && <div className="panel-state error annotation-load-error" role="alert">详情加载失败：{detailError}</div>}
          </>
        )}
        {hasDetail && detail && (
          <>
            <div className="badge-group">
              <span className={`badge status-${detail.review_status.toLowerCase()}`}>{label(detail.review_status)}</span>
              <span className={`badge status-${detail.format_status.toLowerCase()}`}>{formatStatusLabel(detail.format_status)}</span>
              <span className={`badge status-${detail.publish_status.toLowerCase()}`}>{publishStatusLabel(detail.publish_status)}</span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={reauditSelected} disabled={reauditBusy} aria-label="重新审核此条内容">{reauditBusy ? "审核中..." : "重新审核"}</button>
            </div>
            <article className="manuscript-review">
              <header>
                <h4>{latest?.title || detail.title}</h4>
                <p className="meta-line">版本：V{latest?.version || "—"} · 来源：{contentSourceLabel(latest?.source)}</p>
              </header>
              <div className="document-annotation-grid">
                <AnnotatedBody body={body} issues={issues} />
                <IssueNotes detail={detail} body={body} issues={issues} />
              </div>
            </article>
            <AgentResultPanel
              results={detail.latest_audit?.agent_results || []}
              issues={detail.latest_audit?.issues || []}
              loading={detailLoading}
              error={detailError}
              auditExists={Boolean(detail.latest_audit)}
            />
          </>
        )}
      </section>
      {hasDetail && detail && (
        <section className="workspace-panel task-panel">
          <div className="panel-heading"><h3>人工动作</h3><span className="count">{detail.open_tasks.length}</span></div>
          {detail.open_tasks.length === 0 ? <p className="panel-state">当前没有开放任务。</p> : (
            <>
              <div className="reviewer-fields">
                <div className="field"><label htmlFor="reviewer">审核人</label><input id="reviewer" value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></div>
                <div className="field"><label htmlFor="task-note">备注</label><input id="task-note" value={note} onChange={(event) => setNote(event.target.value)} /></div>
              </div>
              {detail.open_tasks.map((task) => (
                <div className="task" key={task.id}>
                  <div><b>{taskTypeLabel(task.task_type)}</b><span className="meta-line">关联问题：{task.issue_ids.length || "—"}</span></div>
                  <div className="btn-row"><TaskAction task={task} busy={!!busy} onResolve={resolve} /></div>
                </div>
              ))}
            </>
          )}
        </section>
      )}
    </div>
  );
}

export default function Review() {
  const [searchParams] = useSearchParams();
  const [projects, setProjects] = useState<Project[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [rows, setRows] = useState<ContentTableRow[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [batchId, setBatchId] = useState(0);
  const [status, setStatus] = useState<ReviewStatus | "">("");
  const [selectedId, setSelectedId] = useState(0);
  const [detailRequestId, setDetailRequestId] = useState(0);
  const [detail, setDetail] = useState<ContentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [message, setMessage] = useState<Message | null>(null);
  const [reviewer, setReviewer] = useState("reviewer@example.com");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [auditBusy, setAuditBusy] = useState(false);
  const [reauditBusy, setReauditBusy] = useState(false);
  const [auditJob, setAuditJob] = useState<AuditJobProgress | null>(null);
  const [auditJobLoading, setAuditJobLoading] = useState(false);
  const pendingBatchParamRef = useRef<string | null>(searchParams.get("batch_id"));

  useEffect(() => {
    const controller = new AbortController();
    api.projects().then((data) => {
      if (controller.signal.aborted) return;
      const tech = data.filter((project) => project.content_type === "TECH_MEDIA_REVIEW");
      setProjects(tech);
      setProjectId(tech[0]?.id || 0);
    }).catch((error: Error) => {
      if (error.name !== "AbortError") setMessage({ type: "err", text: error.message });
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    setBatchId(0);
    setSelectedId(0);
    setDetailRequestId(0);
    setDetail(null);
    setDetailError("");
    if (!projectId) {
      setBatches([]);
      return;
    }
    const controller = new AbortController();
    api.batches(projectId, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setBatches(data);
      const pending = pendingBatchParamRef.current;
      const targetId = pending ? Number(pending) : 0;
      const selectedBatchId = data.some((batch) => batch.id === targetId) ? targetId : data.length === 1 ? data[0].id : 0;
      pendingBatchParamRef.current = null;
      if (selectedBatchId) setBatchId(selectedBatchId);
    }).catch((error: Error) => {
      if (error.name !== "AbortError") setMessage({ type: "err", text: error.message });
    });
    return () => controller.abort();
  }, [projectId]);

  useEffect(() => {
    if (!projectId) {
      setRows([]);
      setSelectedId(0);
      setDetailRequestId(0);
      setDetail(null);
      setDetailError("");
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    api.contentTable({ project_id: projectId, batch_id: batchId || undefined, review_status: status || undefined }, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setRows(data);
        setSelectedId((current) => data.some((row) => row.id === current) ? current : 0);
        setDetailRequestId((current) => data.some((row) => row.id === current) ? current : 0);
        setDetail((current) => current && data.some((row) => row.id === current.id) ? current : null);
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted) {
          setRows([]);
          setMessage({ type: "err", text: error.message });
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [projectId, batchId, status, refreshKey]);

  useEffect(() => {
    setAuditJob(null);
    if (!batchId) {
      setAuditJobLoading(false);
      return;
    }
    const controller = new AbortController();
    setAuditJobLoading(true);
    api.batchAuditJob(batchId, controller.signal)
      .then((job) => { if (!controller.signal.aborted) setAuditJob(job); })
      .catch((error: Error) => {
        if (!controller.signal.aborted && error.name !== "AbortError") setMessage({ type: "err", text: `审核进度加载失败：${error.message}` });
      })
      .finally(() => { if (!controller.signal.aborted) setAuditJobLoading(false); });
    return () => controller.abort();
  }, [batchId]);

  const auditJobActive = Boolean(auditJob && !TERMINAL_AUDIT_JOB_STATUSES.has(auditJob.status));
  useEffect(() => {
    if (!auditJob || !auditJobActive) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api.auditJob(auditJob.id, controller.signal)
        .then((job) => {
          if (controller.signal.aborted) return;
          setAuditJob(job);
          if (TERMINAL_AUDIT_JOB_STATUSES.has(job.status)) setRefreshKey((key) => key + 1);
        })
        .catch((error: Error) => {
          if (!controller.signal.aborted && error.name !== "AbortError") setMessage({ type: "err", text: `审核进度更新失败：${error.message}` });
        });
    }, 2000);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [auditJob, auditJobActive]);

  const hasReviewing = rows.some((row) => row.review_status === "AI_REVIEWING");
  useEffect(() => {
    if (!hasReviewing) return;
    const timer = setTimeout(() => { setRefreshKey((key) => key + 1); }, 3000);
    return () => clearTimeout(timer);
  }, [hasReviewing, refreshKey]);

  useEffect(() => {
    setDetail(null);
    setDetailError("");
    if (!detailRequestId) {
      setDetailLoading(false);
      return;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    api.content(detailRequestId, controller.signal)
      .then((content) => { if (!controller.signal.aborted) setDetail(content); })
      .catch((error: Error) => {
        if (!controller.signal.aborted) {
          setDetail(null);
          setDetailError(error.message);
        }
      })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [detailRequestId, refreshKey]);

  const selectedRow = rows.find((row) => row.id === selectedId) || null;
  const selectRow = (id: number) => {
    setSelectedId(id);
    setDetailRequestId(0);
    setDetail(null);
    setDetailError("");
    setDetailLoading(false);
  };
  const loadSelectedDetail = () => {
    if (!selectedId) return;
    setDetailRequestId(selectedId);
  };
  const act = async (name: string, action: () => Promise<unknown>, success: string, contentId: number, taskId: number) => {
    if (selectedId !== contentId || detail?.id !== contentId || !detail.open_tasks.some((task) => task.id === taskId)) return;
    setBusy(name);
    setMessage(null);
    try {
      await action();
      if (selectedId === contentId) {
        setMessage({ type: "ok", text: success });
        setRefreshKey((value) => value + 1);
      }
    } catch (error) {
      if (selectedId === contentId) setMessage({ type: "err", text: error instanceof Error ? error.message : "操作失败" });
    } finally {
      setBusy("");
    }
  };
  const resolve = (task: ReviewTask, decision: string) => {
    if (!reviewer.trim()) {
      setMessage({ type: "err", text: "请填写审核人" });
      return;
    }
    if (task.task_type === "SUPPLIER_REVISION") return;
    return act(
      `task-${task.id}`,
      () => api.resolveTask(task.id, { decision, reviewer: reviewer.trim(), note: note.trim() || undefined }),
      "人工任务已处理",
      task.content_item_id,
      task.id,
    );
  };
  const auditCurrentBatch = async () => {
    if (auditJobActive) {
      document.getElementById("audit-progress-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (!batchId) {
      setMessage({ type: "err", text: "请先选择批次" });
      return;
    }
    setAuditBusy(true);
    setMessage(null);
    try {
      const started = await api.startAuditJob(batchId);
      const job = await api.auditJob(started.job_id);
      setAuditJob(job);
      setMessage({ type: "ok", text: "批次审核已启动，正在显示实时进度。" });
    } catch (error) {
      setMessage({ type: "err", text: `审核启动失败：${error instanceof Error ? error.message : "未知错误"}` });
    } finally {
      setAuditBusy(false);
    }
  };
  const reauditSelected = async () => {
    if (!selectedId) return;
    setReauditBusy(true);
    setMessage(null);
    try {
      await api.auditContent(selectedId);
      setMessage({ type: "ok", text: `内容 #${selectedId} 重新审核已启动` });
      setRefreshKey((key) => key + 1);
    } catch (error) {
      setMessage({ type: "err", text: `重新审核失败：${error instanceof Error ? error.message : "未知错误"}` });
    } finally {
      setReauditBusy(false);
    }
  };

  return (
    <div>
      <div className="page-heading">
        <div><h2>审核工作区</h2><p>选择稿件查看正文批注、维度结论和人工动作。</p></div>
      </div>
      <section className="filter-bar card review-filter-bar">
        <div className="field">
          <label htmlFor="review-project">项目</label>
          <select id="review-project" value={projectId} onChange={(event) => setProjectId(Number(event.target.value))}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select>
        </div>
        <div className="field">
          <label htmlFor="review-batch">批次</label>
          <select id="review-batch" value={batchId} onChange={(event) => setBatchId(Number(event.target.value))}><option value={0}>全部批次</option>{batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.name}</option>)}</select>
        </div>
        <div className="field">
          <label htmlFor="review-status">审核状态</label>
          <select id="review-status" value={status} onChange={(event) => setStatus(event.target.value as ReviewStatus | "")}><option value="">全部状态</option>{STATUSES.map((value) => <option key={value} value={value}>{label(value)}</option>)}</select>
        </div>
        <div className="field audit-start-field">
          <label aria-hidden="true">&nbsp;</label>
          <button type="button" className="btn btn-primary" onClick={auditCurrentBatch} disabled={auditBusy || auditJobLoading} aria-describedby="audit-start-help">
            {auditBusy ? "启动中..." : auditJobActive ? "查看审核进度" : "开始审核"}
          </button>
          <p id="audit-start-help" className="field-help">{!batchId ? "请选择一个批次后开始审核。" : auditJobActive ? "当前批次已有审核任务，不能重复启动。" : "按当前 AI 评分维度处理当前批次。"}</p>
        </div>
      </section>

      {message && <div className={`msg ${message.type}`} role="status">{message.text}</div>}
      {auditJob && <AuditProgressPanel job={auditJob} />}
      {loading && <p className="empty">正在加载内容表...</p>}
      {!loading && rows.length === 0 && <div className="card empty">当前筛选下暂无内容。</div>}
      {!loading && rows.length > 0 && (
        <section className="card table-card">
          <div className="section-heading"><h3>内容表</h3><span className="count">{rows.length} 行</span></div>
          <div className="table-wrap">
            <table className="ops-table">
              <thead><tr><th>内容 / 版本</th><th>平台 / 账号</th><th>状态</th><th>风险</th><th>任务</th></tr></thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className={selectedId === row.id ? "selected-row" : ""}>
                    <td><button type="button" className="row-select-button" aria-label={`选择内容 ${row.final_title || row.supplier_external_id}`} onClick={() => selectRow(row.id)}><b>{row.final_title || row.supplier_external_id}</b><span className="cell-subline">{row.supplier_external_id}{row.row_number ? ` · Excel 行 ${row.row_number}` : ""}</span></button></td>
                    <td>{row.platform || "—"}<div className="cell-subline">{row.account_name || "未提供账号"}</div></td>
                    <td><span className={`badge status-${row.review_status.toLowerCase()}`}>{label(row.review_status)}</span><div className="cell-subline">{formatSkipReason(row) || `发布：${publishStatusLabel(row.publish_status)}`}</div></td>
                    <td>{row.issue_count ? <><span className={`risk-label severity-${(row.highest_severity || "unknown").toLowerCase()}`}>{severityLabel(row.highest_severity)}</span><div className="cell-subline">{row.categories.map(categoryLabel).join("、") || "未分类"}</div></> : <span className="muted">无命中</span>}</td>
                    <td>{row.open_task_count ? <span className="task-count">{row.open_task_count} 个开放任务</span> : <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <DetailWorkspace
        detail={detail}
        detailLoading={detailLoading}
        detailError={detailError}
        selectedRow={selectedRow}
        reviewer={reviewer}
        setReviewer={setReviewer}
        note={note}
        setNote={setNote}
        busy={busy}
        resolve={resolve}
        reauditSelected={reauditSelected}
        reauditBusy={reauditBusy}
        loadDetail={loadSelectedDetail}
      />
    </div>
  );
}
