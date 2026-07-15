import type { TestCase } from "../api";

type Props = { cases: TestCase[]; loading?: boolean; error?: string };

export default function TestEvidencePanel({ cases, loading = false, error = "" }: Props) {
  return <section className="workspace-panel" aria-labelledby="test-evidence-title">
    <div className="panel-heading"><h3 id="test-evidence-title">测试用例与证据</h3>{!loading && !error && <span className="count">{cases.length} 用例</span>}</div>
    {loading && <p className="panel-state" role="status">正在加载测试记录...</p>}
    {error && <div className="panel-state error" role="alert">测试记录加载失败：{error}</div>}
    {!loading && !error && cases.length === 0 && <p className="panel-state">未录入测试用例，也没有可展示的测试证据。</p>}
    {!loading && !error && cases.map((test) => <article className="test-case" key={test.id}>
      <div className="test-case-head"><b>{test.external_test_case_id}</b><span className={`badge ${test.evidence.length ? "status-passed" : "status-incomplete"}`}>{test.evidence.length ? `${test.evidence.length} 份证据` : "无证据"}</span></div>
      <dl className="compact-dl"><div><dt>主张</dt><dd>{test.claim}</dd></div><div><dt>操作</dt><dd>{test.command}</dd></div><div><dt>观察结果</dt><dd>{test.observed_result}</dd></div></dl>
      <div className="test-meta">{[test.city, test.tested_at, test.app_version, test.device, test.operating_system, test.network_environment].filter(Boolean).join(" · ") || "未提供测试环境信息"}</div>
      {test.evidence.length > 0 && <ul className="evidence-list">{test.evidence.map(({ id, asset }) => <li key={id}><b>{asset.filename}</b><span>{asset.kind} · {asset.mime_type || "未知类型"}{asset.size_bytes === null ? "" : ` · ${asset.size_bytes} B`}</span></li>)}</ul>}
    </article>)}
  </section>;
}
