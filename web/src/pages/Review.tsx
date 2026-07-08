import { useEffect, useState } from "react";
import { api, type Config, type Row } from "../api";

const COL = {
  id: "内容编号", title: "标题", body: "正文", media: "图片/视频",
  status: "内容状态", problem: "问题原因", suggestion: "修改建议", platform: "平台",
};

export default function Review() {
  const [tab, setTab] = useState<"all" | "human">("all");
  const [rows, setRows] = useState<Row[]>([]);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [msg, setMsg] = useState<{ t: "ok" | "err"; s: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const data = tab === "human" ? await api.humanQueue() : await api.rows();
    setRows(data.rows);
  };
  useEffect(() => { load(); }, [tab]);
  useEffect(() => { api.config().then(setCfg); }, []);

  const saveCfg = async (patch: Partial<Config>) => {
    const c = await api.saveConfig(patch);
    setCfg(c);
  };

  const run = async () => {
    setBusy(true); setMsg(null);
    try {
      const s = await api.runBatch();
      setMsg({ t: "ok", s: `审核完成：读取 ${s.read} · 审核 ${s.review} · 自动改写 ${s.modify}；状态分布 ${JSON.stringify(s.status_dist)}` });
      await load();
    } catch (e: any) {
      setMsg({ t: "err", s: e.message });
    } finally { setBusy(false); }
  };

  const decide = async (id: string, decision: string) => {
    const reason = decision === "approved" ? "" : prompt("请填写理由/修改建议") || "";
    if (decision !== "approved" && !reason) return;
    try {
      await api.human(id, { decision, reason });
      await load();
    } catch (e: any) {
      setMsg({ t: "err", s: e.message });
    }
  };

  return (
    <div>
      <h2>审核台</h2>

      <div className="card">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div className="field" style={{ width: 150 }}>
            <label>模型后端</label>
            <select value={cfg?.reviewer || "heuristic"} onChange={(e) => saveCfg({ reviewer: e.target.value })}>
              <option value="heuristic">离线(仅确定性)</option>
              <option value="oneapi">OneAPI</option>
              <option value="ernie">文心</option>
            </select>
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>模型名 (model)</label>
            <input type="text" value={cfg?.model || ""} placeholder="如 gpt-5.5"
              onChange={(e) => setCfg(cfg ? { ...cfg, model: e.target.value } : cfg)}
              onBlur={(e) => saveCfg({ model: e.target.value })} />
          </div>
          <div className="field" style={{ width: 160 }}>
            <label>项目 (project)</label>
            <input type="text" value={cfg?.project || ""} placeholder="如 五一KOL"
              onChange={(e) => setCfg(cfg ? { ...cfg, project: e.target.value } : cfg)}
              onBlur={(e) => saveCfg({ project: e.target.value })} />
          </div>
          <div className="field">
            <button className="btn btn-primary" onClick={run} disabled={busy}>{busy ? "审核中…" : "一键跑审核"}</button>
          </div>
        </div>
        {cfg && !cfg.key_set && cfg.reviewer === "oneapi" && (
          <div className="small" style={{ color: "var(--reject)" }}>
            未检测到 ONEAPI_KEY 环境变量，OneAPI 审核会失败。请在启动后端前 export ONEAPI_KEY。
          </div>
        )}
        {msg && <div className={`msg ${msg.t}`}>{msg.s}</div>}
      </div>

      <div className="tabs">
        <button className={`tab ${tab === "all" ? "active" : ""}`} onClick={() => setTab("all")}>全部内容</button>
        <button className={`tab ${tab === "human" ? "active" : ""}`} onClick={() => setTab("human")}>待人工审核</button>
      </div>

      {rows.length === 0 && <p className="small">暂无内容</p>}
      <div className="grid">
        {rows.map((r) => (
          <div className="item" key={r[COL.id]}>
            <div className="id">{r[COL.id]} · {r[COL.platform]}</div>
            <h4>{r[COL.title]}</h4>
            {r[COL.media] && <img src={`/media/${r[COL.id]}`} alt="" onError={(e) => ((e.target as HTMLImageElement).style.display = "none")} />}
            <p>{r[COL.body]}</p>
            <div><span className={`badge ${r[COL.status]}`}>{r[COL.status]}</span></div>
            {r[COL.problem] && <p className="small">{r[COL.problem].split("；").map((c, i) => <span className="risk" key={i}>{c}</span>)}</p>}
            {r[COL.suggestion] && <p className="small">建议：{r[COL.suggestion]}</p>}
            {r[COL.status] === "待人工审核" && (
              <div className="btn-row">
                <button className="btn btn-pass" onClick={() => decide(r[COL.id], "approved")}>通过</button>
                <button className="btn btn-warn" onClick={() => decide(r[COL.id], "need_modify")}>需修改</button>
                <button className="btn btn-danger" onClick={() => decide(r[COL.id], "deleted")}>删除</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
