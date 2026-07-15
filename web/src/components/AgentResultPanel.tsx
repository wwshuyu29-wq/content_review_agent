import { AGENT_ORDER, type AgentId, type AgentResult } from "../api";

const LABELS: Record<AgentId, string> = {
  COMPLIANCE: "合规", BRAND: "品牌", PRODUCT_ACCURACY: "产品准确性", TEST_CREDIBILITY: "测试可信度", CONTENT_QUALITY: "内容质量", CAMPAIGN_EFFECTIVENESS: "传播效果",
};

type Props = { results: AgentResult[]; loading?: boolean; error?: string; auditExists?: boolean };

export default function AgentResultPanel({ results, loading = false, error = "", auditExists = false }: Props) {
  const byId = new Map(results.map((result) => [result.agent_id || result.agent_name, result]));
  return <section className="workspace-panel" aria-labelledby="agent-results-title">
    <div className="panel-heading"><h3 id="agent-results-title">六维 Agent 结果</h3>{auditExists && !loading && !error && <span className="count">{results.length}/6 已返回</span>}</div>
    {loading && <p className="panel-state" role="status">正在加载 Agent 结果...</p>}
    {error && <div className="panel-state error" role="alert">Agent 结果加载失败：{error}</div>}
    {!loading && !error && !auditExists && <p className="panel-state">尚未运行审核，六维 Agent 均无结果。</p>}
    {!loading && !error && auditExists && <div className="agent-list">{AGENT_ORDER.map((agentId) => {
      const result = byId.get(agentId);
      return <article className="agent-result" key={agentId}>
        <div className="agent-result-head"><div><b>{LABELS[agentId]}</b><span>{agentId}</span></div><span className={`badge status-${(result?.status || "not_run").toLowerCase()}`}>{result?.status || "NOT_RUN"}</span></div>
        {!result ? <p className="panel-state compact">该 Agent 未返回结果。</p> : <>
          <div className="agent-decision"><strong>{result.decision || "未给出决策"}</strong>{result.score === null ? <span>无评分</span> : <span>{result.score} 分</span>}</div>
          <p>{result.summary || "未提供摘要"}</p>
          {Object.keys(result.raw_result).length > 0 && <details><summary>查看原始结果</summary><pre>{JSON.stringify(result.raw_result, null, 2)}</pre></details>}
        </>}
      </article>;
    })}</div>}
  </section>;
}
