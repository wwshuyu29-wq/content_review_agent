import { useEffect, useState } from "react";
import { api, type Project, type ProjectDetail, type RuleVersion, type RuleVersionInput } from "../api";

type Draft = { dimension_standards: string; project_facts: string; structured_rules: string; prompt_version: string };
const emptyDraft: Draft = { dimension_standards: "{}", project_facts: "{}", structured_rules: "{}", prompt_version: "prompt-v1" };

function draftFrom(version: RuleVersion | null): Draft {
  if (!version) return emptyDraft;
  return {
    dimension_standards: JSON.stringify(version.dimension_standards, null, 2),
    project_facts: JSON.stringify(version.project_facts, null, 2),
    structured_rules: JSON.stringify(version.structured_rules, null, 2),
    prompt_version: version.prompt_version,
  };
}

function parseObject(label: string, value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(`${label}必须是 JSON 对象`);
  return parsed as Record<string, unknown>;
}

export default function Standards() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState(0);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [newProject, setNewProject] = useState({ name: "", description: "" });
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const loadProjects = async (preferredId?: number) => {
    const data = await api.projects();
    setProjects(data);
    const next = preferredId || projectId || data[0]?.id || 0;
    setProjectId(next);
  };
  const loadDetail = async (id: number, preferredVersionId?: number) => {
    const data = await api.project(id);
    setDetail(data);
    const selected = data.rule_versions.find((version) => version.id === preferredVersionId)
      || data.current_rule_version || data.rule_versions[data.rule_versions.length - 1] || null;
    setSelectedVersionId(selected?.id || 0);
    setDraft(draftFrom(selected));
  };

  useEffect(() => { loadProjects().catch((error: Error) => setMessage({ type: "err", text: error.message })); }, []);
  useEffect(() => { if (projectId) loadDetail(projectId).catch((error: Error) => setMessage({ type: "err", text: error.message })); }, [projectId]);

  const selectVersion = (id: number) => {
    setSelectedVersionId(id);
    setDraft(draftFrom(detail?.rule_versions.find((version) => version.id === id) || null));
    setMessage(null);
  };

  const createProject = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!newProject.name.trim()) return setMessage({ type: "err", text: "项目名称不能为空" });
    setBusy(true);
    try {
      const created = await api.createProject({ name: newProject.name.trim(), description: newProject.description.trim() || undefined });
      setNewProject({ name: "", description: "" });
      await loadProjects(created.id);
      setMessage({ type: "ok", text: `项目「${created.name}」已创建，请创建首个规则版本` });
    } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "创建失败" }); }
    finally { setBusy(false); }
  };

  const createVersion = async () => {
    if (!projectId || !draft.prompt_version.trim()) return setMessage({ type: "err", text: "Prompt 版本不能为空" });
    setBusy(true);
    try {
      const payload: RuleVersionInput = {
        dimension_standards: parseObject("分维度标准", draft.dimension_standards),
        project_facts: parseObject("项目事实", draft.project_facts),
        structured_rules: parseObject("结构化规则", draft.structured_rules),
        prompt_version: draft.prompt_version.trim(),
      };
      const version = await api.createRuleVersion(projectId, payload);
      await loadDetail(projectId, version.id);
      setMessage({ type: "ok", text: `规则 V${version.version} 已创建，发布前不会用于审核` });
    } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "版本创建失败" }); }
    finally { setBusy(false); }
  };

  const publish = async (version: RuleVersion) => {
    setBusy(true);
    try {
      await api.publishRuleVersion(projectId, version.id);
      await loadDetail(projectId, version.id);
      await loadProjects(projectId);
      setMessage({ type: "ok", text: `规则 V${version.version} 已发布为当前版本` });
    } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "发布失败" }); }
    finally { setBusy(false); }
  };

  const selected = detail?.rule_versions.find((version) => version.id === selectedVersionId) || null;
  return <div>
    <div className="page-heading"><div><h2>标准管理</h2><p>历史版本保持不可变，编辑内容将创建新版本。</p></div></div>
    <div className="standards-layout">
      <aside>
        <form className="card compact-card" onSubmit={createProject}><h3>创建项目</h3><div className="field"><label htmlFor="project-name">项目名称</label><input id="project-name" type="text" value={newProject.name} onChange={(event) => setNewProject({ ...newProject, name: event.target.value })} /></div><div className="field"><label htmlFor="project-description">说明</label><textarea id="project-description" rows={3} value={newProject.description} onChange={(event) => setNewProject({ ...newProject, description: event.target.value })} /></div><button className="btn btn-primary" disabled={busy}>创建</button></form>
        <section className="card compact-card"><h3>项目</h3><div className="stack-list">{projects.map((project) => <button type="button" className={`list-button ${project.id === projectId ? "active" : ""}`} key={project.id} onClick={() => setProjectId(project.id)}><span>{project.name}</span><small>{project.current_rule_version_id ? "已发布规则" : "未发布规则"}</small></button>)}</div></section>
      </aside>
      <div className="standards-main">
        {message && <div className={`msg ${message.type}`}>{message.text}</div>}
        {detail && <>
          <section className="card"><div className="section-heading"><div><h3>{detail.name}</h3><p className="small">{detail.description || "无项目说明"}</p></div>{detail.current_rule_version && <span className="badge status-approved">当前 V{detail.current_rule_version.version}</span>}</div><div className="version-strip">{detail.rule_versions.map((version) => <button type="button" className={`version-chip ${selectedVersionId === version.id ? "active" : ""}`} key={version.id} onClick={() => selectVersion(version.id)}>V{version.version}{detail.current_rule_version_id === version.id ? " · 当前" : ""}</button>)}{detail.rule_versions.length === 0 && <span className="empty">尚无规则版本</span>}</div></section>
          <section className="card"><div className="section-heading"><div><h3>{selected ? `基于 V${selected.version} 创建新版本` : "创建首个版本"}</h3><p className="small">编辑区是新版本草稿，不会修改所选历史版本。</p></div>{selected && detail.current_rule_version_id !== selected.id && <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => publish(selected)}>发布所选 V{selected.version}</button>}</div><div className="field"><label htmlFor="prompt-version">Prompt 版本</label><input id="prompt-version" type="text" value={draft.prompt_version} onChange={(event) => setDraft({ ...draft, prompt_version: event.target.value })} /></div><div className="json-grid"><div className="field"><label htmlFor="dimension-standards">分维度标准 JSON</label><textarea id="dimension-standards" rows={16} value={draft.dimension_standards} onChange={(event) => setDraft({ ...draft, dimension_standards: event.target.value })} /></div><div className="field"><label htmlFor="project-facts">项目事实 JSON</label><textarea id="project-facts" rows={16} value={draft.project_facts} onChange={(event) => setDraft({ ...draft, project_facts: event.target.value })} /></div><div className="field span-2"><label htmlFor="structured-rules">结构化规则 JSON</label><textarea id="structured-rules" rows={12} value={draft.structured_rules} onChange={(event) => setDraft({ ...draft, structured_rules: event.target.value })} /></div></div><div className="btn-row"><button className="btn btn-primary" type="button" disabled={busy} onClick={createVersion}>{busy ? "处理中..." : "创建不可变新版本"}</button></div></section>
          <section className="card"><h3>版本历史</h3><div className="table-wrap"><table><thead><tr><th>版本</th><th>Prompt</th><th>创建时间</th><th>状态</th><th>操作</th></tr></thead><tbody>{[...detail.rule_versions].reverse().map((version) => <tr key={version.id}><td>V{version.version}</td><td>{version.prompt_version}</td><td>{new Date(version.created_at).toLocaleString()}</td><td>{detail.current_rule_version_id === version.id ? "当前版本" : "历史版本"}</td><td><button className="text-button" type="button" onClick={() => selectVersion(version.id)}>查看 / 克隆</button>{detail.current_rule_version_id !== version.id && <button className="text-button" type="button" disabled={busy} onClick={() => publish(version)}>发布</button>}</td></tr>)}</tbody></table></div></section>
        </>}
      </div>
    </div>
  </div>;
}
