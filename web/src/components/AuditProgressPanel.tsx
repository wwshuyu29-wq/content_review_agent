import type { AuditJobProgress } from "../api";
import { AGENT_ORDER } from "../api";
import { agentLabel, decisionLabel, statusLabel } from "../reviewLabels";

type Props = { job: AuditJobProgress };
const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "INTERRUPTED"]);
const age = (value: string) => { const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000)); return seconds < 60 ? `${seconds} 秒前` : `${Math.floor(seconds / 60)} 分钟前`; };
const elapsed = (start: string | null, end: string | null) => { if (!start) return "未开始"; const seconds = Math.max(0, Math.floor(((end ? new Date(end) : new Date()).getTime() - new Date(start).getTime()) / 1000)); return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; };
const cls = (prefix: string, status: string) => `${prefix} ${prefix}-${status.toLowerCase()}`;

export default function AuditProgressPanel({ job }: Props) {
  const processed = job.completed_count + job.failed_count + job.skipped_count;
  const percentage = job.total_count ? Math.min(100, Math.round(processed / job.total_count * 100)) : 0;
  const current = job.manuscripts.find((item) => item.content_item_id === job.current_content_item_id);
  const agents = new Map(job.current_agents.map((agent) => [agent.agent_id, agent]));
  const terminal = TERMINAL.has(job.status);
  return <section className="card audit-progress-panel" id="audit-progress-panel" aria-labelledby="audit-progress-heading">
    <div className="section-heading"><div><h3 id="audit-progress-heading">批次审核进度</h3><p className="small">任务 #{job.id} · {statusLabel(job.status)} · 模型：{job.model}</p></div><span className="badge" aria-label={`审核进度 ${percentage}%`}>{percentage}%</span></div>
    <progress className="audit-progress-bar" value={processed} max={Math.max(job.total_count, 1)} aria-label={`批次审核进度 ${percentage}%`} />
    <div className="audit-progress-summary-line"><strong>{processed} / {job.total_count} 篇已处理</strong><span>{terminal ? "任务已停止轮询" : "每 2 秒自动更新"}</span></div>
    <div className="audit-progress-counts" aria-label="审核结果统计"><span>成功 {job.completed_count}</span><span>失败 {job.failed_count}</span><span>跳过 {job.skipped_count}</span><span>等待 {job.pending_count}</span></div>
    <dl className="audit-progress-meta"><div><dt>当前稿件</dt><dd>{current ? `稿件 ${current.position}` : terminal ? "无" : "准备中"}</dd></div><div><dt>已用时间</dt><dd>{elapsed(job.started_at, job.completed_at)}</dd></div><div><dt>最近心跳</dt><dd>{age(job.heartbeat_at)}</dd></div></dl>
    {job.error_summary && <p className="panel-state error" role="alert">{job.error_summary}</p>}
    <div className="audit-progress-agents" aria-label="当前稿件六个审核 Agent 状态"><h4>当前稿件审核维度</h4><div className="audit-agent-list">{AGENT_ORDER.map((agentId) => { const agent = agents.get(agentId); const state = agent?.status || "PENDING"; return <div className="audit-agent-row" key={agentId}><span className="audit-agent-name">{agentLabel(agentId)}</span><span className={cls("audit-agent-status", state)}>{statusLabel(state)}</span><span className="audit-agent-detail">{agent?.decision ? `结论：${decisionLabel(agent.decision)}` : agent?.attempt_count ? `第 ${agent.attempt_count} 次尝试` : "等待中"}</span></div>; })}</div></div>
    <div className="audit-manuscripts"><h4>稿件处理列表</h4><div className="audit-manuscript-list">{job.manuscripts.map((manuscript) => <div className="audit-manuscript-row" key={manuscript.id}><span>稿件 {manuscript.position}</span><span className={cls("audit-manuscript-status", manuscript.status)}>{statusLabel(manuscript.status)}</span></div>)}</div></div>
  </section>;
}
