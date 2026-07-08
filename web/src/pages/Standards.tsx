import { useEffect, useState } from "react";
import { api } from "../api";

interface Dim { key: string; name: string; content: string; }
interface Rules { deny_words: string[]; recommended: Record<string, string>; must_human_keywords: string[]; required_tags: string[]; }

export default function Standards() {
  const [dims, setDims] = useState<Dim[]>([]);
  const [projects, setProjects] = useState<string[]>([]);
  const [rules, setRules] = useState<Rules | null>(null);
  const [active, setActive] = useState<string>("");
  const [projName, setProjName] = useState("");
  const [projContent, setProjContent] = useState("");
  const [msg, setMsg] = useState("");

  const load = async () => {
    const s = await api.standards();
    setDims(s.dimensions);
    setProjects(s.projects);
    setRules(s.rules);
    if (!active && s.dimensions[0]) setActive(s.dimensions[0].key);
  };
  useEffect(() => { load(); }, []);

  const cur = dims.find((d) => d.key === active);

  const saveDim = async () => {
    if (!cur) return;
    await api.saveDim(cur.key, cur.content);
    setMsg(`已保存「${cur.name}」标准`);
  };

  const loadProject = async (name: string) => {
    setProjName(name);
    const p = await api.project(name);
    setProjContent(p.content);
  };
  const saveProject = async () => {
    if (!projName) { setMsg("请填写项目名"); return; }
    await api.saveProject(projName, projContent);
    setMsg(`已保存项目「${projName}」标准`);
    load();
  };

  const saveRules = async () => {
    if (!rules) return;
    await api.saveRules(rules as any);
    setMsg("已保存规则库");
  };

  return (
    <div>
      <h2>标准管理（流程一）</h2>
      {msg && <div className="msg ok">{msg}</div>}

      <div className="card">
        <h3>全局标准（分维度）</h3>
        <div className="tabs">
          {dims.map((d) => (
            <button key={d.key} className={`tab ${active === d.key ? "active" : ""}`} onClick={() => setActive(d.key)}>{d.name}</button>
          ))}
        </div>
        {cur && (
          <>
            <textarea rows={12} value={cur.content}
              onChange={(e) => setDims(dims.map((d) => (d.key === cur.key ? { ...d, content: e.target.value } : d)))} />
            <div className="btn-row"><button className="btn btn-primary" onClick={saveDim}>保存该维度</button></div>
          </>
        )}
      </div>

      <div className="card">
        <h3>项目补充标准</h3>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div className="field" style={{ flex: 1 }}>
            <label>项目名</label>
            <input type="text" value={projName} onChange={(e) => setProjName(e.target.value)} placeholder="如 五一KOL" list="projs" />
            <datalist id="projs">{projects.map((p) => <option key={p} value={p} />)}</datalist>
          </div>
          <div className="field">
            <button className="btn btn-ghost" onClick={() => projName && loadProject(projName)}>加载</button>
          </div>
        </div>
        <textarea rows={10} value={projContent} onChange={(e) => setProjContent(e.target.value)} placeholder="卖点 / 活动规则 / 优惠 / 必带标签 / 授权明星IP清单…" />
        <div className="btn-row"><button className="btn btn-primary" onClick={saveProject}>保存项目标准</button></div>
      </div>

      {rules && (
        <div className="card">
          <h3>规则库（确定性精确匹配）</h3>
          <div className="field">
            <label>禁用词（换行分隔，命中即高风险）</label>
            <textarea rows={4} value={rules.deny_words.join("\n")}
              onChange={(e) => setRules({ ...rules, deny_words: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} />
          </div>
          <div className="field">
            <label>必须人工确认关键词（明星/IP/第三方）</label>
            <textarea rows={2} value={rules.must_human_keywords.join("\n")}
              onChange={(e) => setRules({ ...rules, must_human_keywords: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} />
          </div>
          <div className="field">
            <label>必带标签（缺少即中风险）</label>
            <textarea rows={2} value={rules.required_tags.join("\n")}
              onChange={(e) => setRules({ ...rules, required_tags: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} />
          </div>
          <div className="btn-row"><button className="btn btn-primary" onClick={saveRules}>保存规则库</button></div>
        </div>
      )}
    </div>
  );
}
