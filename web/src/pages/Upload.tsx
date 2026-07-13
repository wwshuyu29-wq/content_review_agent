import { useEffect, useState } from "react";
import { api, type BatchDetail, type Project } from "../api";

const initialForm = {
  supplier_id: "",
  batch_name: "",
  external_id: "",
  platform: "微博",
  publish_time: "",
  title: "",
  body: "",
};

export default function Upload() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [form, setForm] = useState(initialForm);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<BatchDetail | null>(null);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.projects().then((data) => {
      setProjects(data);
      if (data[0]) setProjectId(data[0].id);
    }).catch((error: Error) => setMessage({ type: "err", text: error.message }));
  }, []);

  const setField = (key: keyof typeof initialForm) => (
    event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>,
  ) => setForm((current) => ({ ...current, [key]: event.target.value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!projectId || !form.supplier_id.trim() || !form.batch_name.trim() || !form.external_id.trim() || !form.title.trim() || !form.body.trim()) {
      setMessage({ type: "err", text: "项目、批次、供应商、内容编号、标题和正文均为必填项" });
      return;
    }
    setBusy(true);
    setMessage(null);
    setResult(null);
    try {
      const data = new FormData();
      data.append("project_id", String(projectId));
      data.append("supplier_id", form.supplier_id.trim());
      data.append("name", form.batch_name.trim());
      data.append("contents", JSON.stringify([{
        external_id: form.external_id.trim(),
        title: form.title,
        body: form.body,
        payload: { platform: form.platform, publish_time: form.publish_time },
      }]));
      if (file) data.append("files", file);
      const created = await api.createBatch(data);
      setResult(created);
      setMessage({ type: "ok", text: `批次 #${created.id} 已创建，共接收 ${created.content_count} 条内容` });
      setForm((current) => ({ ...initialForm, supplier_id: current.supplier_id, platform: current.platform }));
      setFile(null);
    } catch (error) {
      setMessage({ type: "err", text: error instanceof Error ? error.message : "上传失败" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="page-heading">
        <div><h2>供应商上传</h2><p>创建批次并生成不可变供应商原稿 V1。</p></div>
      </div>
      <form className="card form-card" onSubmit={submit}>
        <div className="form-grid">
          <div className="field span-2">
            <label htmlFor="upload-project">项目 *</label>
            <select id="upload-project" value={projectId} onChange={(event) => setProjectId(Number(event.target.value))}>
              {projects.length === 0 && <option value={0}>暂无项目</option>}
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </div>
          <div className="field"><label htmlFor="supplier">供应商 ID *</label><input id="supplier" type="text" value={form.supplier_id} onChange={setField("supplier_id")} /></div>
          <div className="field"><label htmlFor="batch-name">批次名称 *</label><input id="batch-name" type="text" value={form.batch_name} onChange={setField("batch_name")} /></div>
          <div className="field"><label htmlFor="external-id">内容编号 *</label><input id="external-id" type="text" value={form.external_id} onChange={setField("external_id")} /></div>
          <div className="field"><label htmlFor="platform">平台</label><select id="platform" value={form.platform} onChange={setField("platform")}><option>微博</option><option>抖音</option><option>小红书</option><option>B站</option><option>其他</option></select></div>
          <div className="field span-2"><label htmlFor="publish-time">计划发布时间</label><input id="publish-time" type="text" value={form.publish_time} onChange={setField("publish_time")} placeholder="2026-07-14 18:00" /></div>
          <div className="field span-2"><label htmlFor="title">标题 *</label><input id="title" type="text" value={form.title} onChange={setField("title")} /></div>
          <div className="field span-2"><label htmlFor="body">正文 / 脚本 *</label><textarea id="body" rows={7} value={form.body} onChange={setField("body")} /></div>
          <div className="field span-2"><label htmlFor="media">图片（JPG / PNG / WEBP）</label><input id="media" type="file" accept=".jpg,.jpeg,.png,.webp" onChange={(event) => setFile(event.target.files?.[0] || null)} /></div>
        </div>
        {message && <div className={`msg ${message.type}`}>{message.text}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy || projects.length === 0}>{busy ? "提交中..." : "创建批次"}</button>
      </form>

      {result && (
        <section className="card">
          <div className="section-heading"><h3>批次结果</h3><span className="badge neutral">{result.status}</span></div>
          <div className="meta-line">#{result.id} · {result.name} · 供应商 {result.supplier_id}</div>
          <div className="table-wrap"><table><thead><tr><th>内容编号</th><th>标题</th><th>格式</th><th>审核状态</th><th>版本</th></tr></thead><tbody>
            {result.contents.map((content) => <tr key={content.id}><td>{content.external_id}</td><td>{content.title}</td><td><span className={`badge status-${content.format_status.toLowerCase()}`}>{content.format_status}</span></td><td>{content.review_status}</td><td>V{content.versions[content.versions.length - 1]?.version ?? 1}</td></tr>)}
          </tbody></table></div>
        </section>
      )}
    </div>
  );
}
