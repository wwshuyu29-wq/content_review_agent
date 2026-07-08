import { useEffect, useState } from "react";
import { api } from "../api";

export default function Report() {
  const [rep, setRep] = useState<any>(null);
  const [proposals, setProposals] = useState<any>(null);
  const [msg, setMsg] = useState("");

  const load = async () => setRep(await api.report());
  useEffect(() => { load(); }, []);

  const preview = async () => {
    const r = await api.distill(false);
    setProposals(r.proposals);
    setMsg("");
  };
  const apply = async () => {
    const r = await api.distill(true);
    setMsg(`已写入规则库：${JSON.stringify(r.applied)}`);
  };

  if (!rep) return <p className="small">加载中…</p>;
  const g = rep["审核成果"];
  const q = rep["问题汇总"];

  return (
    <div>
      <h2>审核报告（流程六）</h2>

      <div className="card">
        <h3>一、审核成果</h3>
        <span className="stat"><b>{g["内容总数"]}</b>内容总数</span>
        <span className="stat"><b>{g["通过"]}</b>通过（{Math.round(g["通过率"] * 100)}%）</span>
        <span className="stat"><b>{g["待人工审核"]}</b>待人工审核</span>
        <span className="stat"><b>{g["需修改"]}</b>需修改</span>
        <span className="stat"><b>{g["待供应商补充"]}</b>待供应商补充</span>
        <span className="stat"><b>{g["已驳回"]}</b>已驳回</span>
      </div>

      <div className="card">
        <h3>二、问题汇总</h3>
        <h4 className="small">问题类别分布</h4>
        <ul>
          {Object.entries(q["问题类别分布"]).map(([k, v]) => <li key={k}>{k}：{v as number}</li>)}
        </ul>
        <h4 className="small">高频问题 Top10</h4>
        <ul>
          {q["高频问题Top10"].map((it: [string, number], i: number) => <li key={i}>（{it[1]}次）{it[0]}</li>)}
        </ul>
      </div>

      <div className="card">
        <h3>规则沉淀（流程七）</h3>
        <div className="btn-row">
          <button className="btn btn-ghost" onClick={preview}>生成建议</button>
          <button className="btn btn-primary" onClick={apply} disabled={!proposals}>确认写入规则库</button>
        </div>
        {msg && <div className="msg ok">{msg}</div>}
        {proposals && <pre>{JSON.stringify(proposals, null, 2)}</pre>}
      </div>
    </div>
  );
}
