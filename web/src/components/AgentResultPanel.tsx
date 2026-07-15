import { AGENT_ORDER, type AgentResult, type Issue } from "../api";
import { agentDetail, agentLabel, decisionLabel, statusLabel } from "../reviewLabels";

type Props = { results: AgentResult[]; issues?: Issue[]; loading?: boolean; error?: string; auditExists?: boolean };

export default function AgentResultPanel({ results, issues = [], loading = false, error = "", auditExists = false }: Props) {
  const byId = new Map(results.map((result) => [result.agent_id || result.agent_name, result]));
  return <section className="workspace-panel" aria-labelledby="agent-results-title">
    <div className="panel-heading"><h3 id="agent-results-title">六维审核结果</h3>{auditExists && !loading && !error && <span className="count">{results.length}/6 已返回</span>}</div>
    {loading && <p className="panel-state" role="status">正在加载审核结果...</p>}
    {error && <div className="panel-state error" role="alert">审核结果加载失败：{error}</div>}
    {!loading && !error && !auditExists && <p className="panel-state">尚未运行审核，六个审核维度均无结果。</p>}
    {!loading && !error && auditExists && <div className="agent-list">{AGENT_ORDER.map((agentId) => {
      const result = byId.get(agentId);
      const detail = result ? agentDetail(result, issues) : null;
      return <article className="agent-result" key={agentId}>
        <div className="agent-result-head"><div><b>{agentLabel(agentId)}</b></div><span className={`badge status-${(result?.status || "not_run").toLowerCase()}`}>{statusLabel(result?.status || "NOT_RUN")}</span></div>
        {!result ? <p className="panel-state compact">该审核维度未返回结果。</p> : <>
          <div className="agent-decision"><strong>{decisionLabel(result.decision)}</strong><span>{result.score === null || result.score === undefined ? "未评分" : `${result.score} 分`}</span></div>
          <p>{detail?.summary}</p>
          <details><summary>查看审核详情</summary><dl className="detail-list"><div><dt>证据</dt><dd>{detail?.evidence}</dd></div><div><dt>原因</dt><dd>{detail?.reason}</dd></div><div><dt>建议</dt><dd>{detail?.suggestion}</dd></div><div><dt>置信度</dt><dd>{detail?.confidence}</dd></div><div><dt>审核来源</dt><dd>{detail?.source}</dd></div></dl></details>
        </>}
      </article>;
    })}</div>}
  </section>;
}
