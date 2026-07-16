import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, saveBlob, type BatchDetail, type ImportPreview, type Project, type ProjectDetail } from "../api";

const initialManual = { external_id: "", platform: "微博", publish_time: "", title: "", body: "" };

type Message = { type: "ok" | "err"; text: string };

export default function Upload() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [supplierId, setSupplierId] = useState("");
  const [batchName, setBatchName] = useState("");
  const [excel, setExcel] = useState<File | null>(null);
  const [evidenceZip, setEvidenceZip] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<BatchDetail | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const [busy, setBusy] = useState("");
  const [auditBusy, setAuditBusy] = useState(false);
  const [manual, setManual] = useState(initialManual);
  const [manualFile, setManualFile] = useState<File | null>(null);
  const [projectDetail, setProjectDetail] = useState<ProjectDetail | null>(null);
  const [brief, setBrief] = useState("");
  const [batchBrief, setBatchBrief] = useState("");
  const [briefFile, setBriefFile] = useState<File | null>(null);
  const [packageVersion, setPackageVersion] = useState("1.1");

  useEffect(() => {
    api.projects().then((data) => {
      const techProjects = data.filter((project) => project.content_type === "TECH_MEDIA_REVIEW");
      setProjects(techProjects);
      setProjectId(techProjects[0]?.id || 0);
    }).catch((error: Error) => setMessage({ type: "err", text: error.message }));
  }, []);
  useEffect(() => {
    if (!projectId) { setProjectDetail(null); setBrief(""); return; }
    let cancelled = false;
    api.project(projectId).then((detail) => {
      if (cancelled) return;
      setProjectDetail(detail);
      setBrief(detail.description || "");
      setBatchBrief((current) => current.trim() ? current : detail.description || "");
      setPackageVersion(detail.current_rule_version?.package_version || "1.1");
    }).catch((error: Error) => { if (!cancelled) setMessage({ type: "err", text: error.message }); });
    return () => { cancelled = true; };
  }, [projectId]);

  const identityReady = Boolean(projectId && supplierId.trim() && batchName.trim() && batchBrief.trim());
  const selectedProject = projects.find((project) => project.id === projectId) || null;
  const projectType = selectedProject?.name || "";
  const ownerName = supplierId.trim();
  const previewMatches = Boolean(
    preview
    && preview.project_id === projectId
    && preview.supplier_id === ownerName
    && preview.batch_name === batchName.trim()
    && preview.project_type === projectType
    && preview.owner_name === ownerName,
  );
  const canConfirm = Boolean(previewMatches && preview?.token && preview.error_count === 0 && preview.errors.length === 0 && preview.valid_count === preview.total_count && preview.total_count > 0);
  const step = result ? 5 : preview ? 4 : excel ? 3 : identityReady ? 2 : 1;

  const invalidatePreview = () => { setPreview(null); setResult(null); };
  const downloadTemplate = async () => {
    setBusy("template"); setMessage(null);
    try { saveBlob(await api.importTemplate(), "tech-media-import-template.xlsx"); }
    catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "模板下载失败" }); }
    finally { setBusy(""); }
  };
  const doPreview = async () => {
    if (!identityReady || !excel) return setMessage({ type: "err", text: "请先选择项目类型并填写负责人、批次编号、本批次 Brief 和 Excel 文件" });
    setBusy("preview"); setMessage(null); setResult(null);
    try {
      const data = new FormData();
      data.append("project_id", String(projectId));
      data.append("supplier_id", ownerName);
      data.append("batch_name", batchName.trim());
      data.append("project_type", projectType);
      data.append("owner_name", ownerName);
      data.append("review_brief", batchBrief.trim());
      data.append("excel_file", excel);
      if (evidenceZip) data.append("evidence_zip", evidenceZip);
      if (briefFile) data.append("brief_file", briefFile);
      setPreview(await api.previewImport(data));
    } catch (error) {
      setPreview(null);
      setMessage({ type: "err", text: error instanceof Error ? error.message : "预检失败" });
    } finally { setBusy(""); }
  };
  const confirm = async () => {
    if (!preview || !canConfirm) return setMessage({ type: "err", text: "预检结果无效或已过期，不能确认导入" });
    setBusy("confirm"); setMessage(null);
    try {
      const created = await api.confirmImport(preview.token, { project_id: projectId, supplier_id: ownerName, batch_name: batchName.trim(), project_type: projectType, owner_name: ownerName });
      setResult(created);
      setMessage({ type: "ok", text: `批次 #${created.id} 导入完成，共 ${created.content_count} 条内容` });
    } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "确认导入失败" }); }
    finally { setBusy(""); }
  };
  const exportResult = async () => {
    if (!result) return;
    setBusy("export");
    try { saveBlob(await api.exportBatch(result.id), `batch-${result.id}.xlsx`); }
    catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "导出失败" }); }
    finally { setBusy(""); }
  };
  const startAudit = async (batchId: number) => {
    setAuditBusy(true); setMessage(null);
    try {
      await api.auditBatch(batchId);
      navigate(`/review?batch_id=${batchId}`);
    } catch (error) {
      setMessage({ type: "err", text: `审核启动失败：${error instanceof Error ? error.message : "未知错误"}` });
    } finally {
      setAuditBusy(false);
    }
  };
  const submitManual = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!identityReady || !manual.external_id.trim() || !manual.title.trim() || !manual.body.trim() || !manualFile) {
      return setMessage({ type: "err", text: "请完整填写手工录入必填项" });
    }
    setBusy("manual"); setMessage(null);
    try {
      const data = new FormData();
      data.append("project_id", String(projectId)); data.append("supplier_id", ownerName); data.append("name", batchName.trim()); data.append("project_type", projectType); data.append("owner_name", ownerName);
      data.append("contents", JSON.stringify([{ external_id: manual.external_id.trim(), title: manual.title, body: manual.body, payload: { platform: manual.platform, publish_time: manual.publish_time } }]));
      data.append("files", manualFile);
      const created = await api.createBatch(data);
      setResult(created); setMessage({ type: "ok", text: `单条批次 #${created.id} 已创建` });
      setManual(initialManual); setManualFile(null);
    } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "创建失败" }); }
    finally { setBusy(""); }
  };
  const saveBrief = async () => {
    if (!projectId || !brief.trim()) return setMessage({ type: "err", text: "请填写项目 Brief" });
    setBusy("brief"); setMessage(null);
    try {
      await api.updateProjectBrief(projectId, brief.trim());
      const detail = await api.project(projectId);
      setProjectDetail(detail);
      setMessage({ type: "ok", text: "项目 Brief 已保存" });
    } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "保存 Brief 失败" }); }
    finally { setBusy(""); }
  };
  const publishStandard = async () => {
    if (!projectDetail?.code || !packageVersion.trim()) return setMessage({ type: "err", text: "当前项目缺少代码或标准包版本" });
    setBusy("standard"); setMessage(null);
    try {
      await api.publishPackage(projectId, { project_code: projectDetail.code, package_version: packageVersion.trim() });
      const detail = await api.project(projectId);
      setProjectDetail(detail);
      setMessage({ type: "ok", text: `标准包 ${packageVersion.trim()} 已发布` });
    } catch (error) { setMessage({ type: "err", text: error instanceof Error ? error.message : "发布标准包失败" }); }
    finally { setBusy(""); }
  };

  return <div>
    <div className="page-heading"><div><h2>上传</h2><p>上传 Excel 和对应 Word/Brief，填写项目类型、批次编号和负责人，预检通过后进入五个评分维度判断。</p></div></div>
    <section className="card upload-brief-panel">
      <div className="section-heading"><div><h3>项目 Brief / 审核标准</h3><p className="small">上传前明确产品功能、营销点、禁用表达与当前标准包。</p></div><span className="count">{projectDetail?.current_rule_version ? `V${projectDetail.current_rule_version.version} · ${projectDetail.current_rule_version.package_version || "未标记"}` : "未发布标准"}</span></div>
      <div className="form-grid">
        <div className="field span-2"><label htmlFor="project-brief">项目 Brief</label><textarea id="project-brief" rows={4} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="例如：产品核心功能、营销重点、必须使用/禁止使用的表达、特殊人工审核注意事项" /></div>
        <div className="field"><label htmlFor="package-version">标准包版本</label><input id="package-version" type="text" value={packageVersion} onChange={(e) => setPackageVersion(e.target.value)} /></div>
        <div className="field"><label>&nbsp;</label><div className="btn-row"><button type="button" className="btn btn-ghost" onClick={saveBrief} disabled={!!busy}>{busy === "brief" ? "保存中..." : "保存 Brief"}</button><button type="button" className="btn btn-primary" onClick={publishStandard} disabled={!!busy || !projectDetail?.code}>{busy === "standard" ? "发布中..." : "发布标准包"}</button></div></div>
      </div>
    </section>
    <ol className="stepper" aria-label="导入步骤">
      {["项目类型与批次", "下载模板", "选择文件", "预检确认", "完成"].map((label, index) => <li key={label} className={step > index + 1 ? "done" : step === index + 1 ? "current" : ""}><span>{index + 1}</span>{label}</li>)}
    </ol>
    {message && <div className={`msg ${message.type}`} role="status">{message.text}</div>}
    <section className="card import-shell">
      <div className="form-grid">
        <div className="field span-2"><label htmlFor="upload-project">项目类型 *</label><select id="upload-project" value={projectId} onChange={(event) => { setProjectId(Number(event.target.value)); setBatchBrief(""); invalidatePreview(); }}><option value={0}>请选择</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select>{projects.length === 0 && <p className="field-help">暂无可导入的项目类型</p>}</div>
        <div className="field"><label htmlFor="supplier">负责人 *</label><input id="supplier" value={supplierId} onChange={(event) => { setSupplierId(event.target.value); invalidatePreview(); }} placeholder="例如：张三 / 内容组 A" /></div>
        <div className="field"><label htmlFor="batch-name">批次编号 *</label><input id="batch-name" value={batchName} onChange={(event) => { setBatchName(event.target.value); invalidatePreview(); }} placeholder="例如：第 1 批 / 2026-春季-01" /></div>
        <div className="field span-2"><label htmlFor="batch-brief">本批次 Brief *</label><textarea id="batch-brief" rows={5} value={batchBrief} onChange={(event) => { setBatchBrief(event.target.value); invalidatePreview(); }} placeholder="填写这次上传稿件对应的产品功能、上线时期、营销点、禁止表达和特殊口径。预检和评分都会使用这份 Brief。" /><p className="field-help">三批不同时期稿件请分别填写各自 Brief；这里不会覆盖项目长期 Brief。</p></div>
      </div>
      <div className="import-actions">
        <button type="button" className="btn btn-ghost" onClick={downloadTemplate} disabled={busy === "template"} title="下载标准 Excel 模板" aria-label="下载标准 Excel 模板">↓ {busy === "template" ? "下载中" : "下载模板"}</button>
        <span className="small">内容表使用“标题、内容、类型、目标平台、作者、发布日期、图片/视频”七列；媒体 ZIP 可选。</span>
      </div>
      <div className="file-grid">
        <div className="file-field"><label htmlFor="excel-file">Excel 文件 *</label><input id="excel-file" type="file" accept=".xlsx" onChange={(event) => { setExcel(event.target.files?.[0] || null); invalidatePreview(); }} /><span>{excel?.name || "未选择 .xlsx 文件"}</span></div>
        <div className="file-field"><label htmlFor="brief-file">Word / Brief 文件（可选）</label><input id="brief-file" type="file" accept=".docx,.txt,.md" onChange={(event) => { setBriefFile(event.target.files?.[0] || null); invalidatePreview(); }} /><span>{briefFile?.name || "可上传 .docx / .txt / .md，系统会与文本 Brief 合并"}</span></div>
        <div className="file-field"><label htmlFor="evidence-zip">媒体 ZIP（可选）</label><input id="evidence-zip" type="file" accept=".zip" onChange={(event) => { setEvidenceZip(event.target.files?.[0] || null); invalidatePreview(); }} /><span>{evidenceZip?.name || "未选择 .zip 文件"}</span></div>
      </div>
      <div className="btn-row"><button type="button" className="btn btn-primary" onClick={doPreview} disabled={!identityReady || !excel || !!busy}>{busy === "preview" ? "预检中..." : "预检文件"}</button><button type="button" className="btn btn-pass" onClick={confirm} disabled={!canConfirm || !!busy} title={!canConfirm ? "仅无错误且身份未变化的预检可确认" : "确认导入"}>{busy === "confirm" ? "导入中..." : "确认导入"}</button></div>
    </section>

    {preview && <section className="card preview-section">
      <div className="section-heading"><div><h3>预检结果</h3><p className="small">项目类型 {preview.project_type} · 批次 {preview.batch_name} · 负责人 {preview.owner_name}</p><p className="small">标准包 {preview.package_version} · Token {preview.token.slice(0, 10)}…</p><p className="small">Brief：{preview.brief_summary}</p></div><div className="badge-group"><span className="badge status-passed">有效 {preview.valid_count}</span><span className={`badge ${preview.error_count ? "status-invalid" : "neutral"}`}>错误 {preview.error_count}</span></div></div>
      {(preview.errors.length > 0 || preview.warnings.length > 0) && <div className="validation-summary">{preview.errors.map((error) => <p className="validation-error" key={error}>错误：{error}</p>)}{preview.warnings.map((warning) => <p className="validation-warning" key={warning}>提示：{warning}</p>)}</div>}
      {preview.rows.length === 0 ? <p className="empty">文件中没有可预览的内容行</p> : <div className="table-wrap"><table><thead><tr><th>序号</th><th>内容编号 / 标题</th><th>字段与格式</th></tr></thead><tbody>{preview.rows.map((row) => <tr key={row.row_number} className={!row.valid ? "invalid-row" : ""}><td><b>第{row.manuscript_index}篇</b><div className="cell-subline">Excel 第{row.row_number}行</div></td><td><b>{String(row.normalized.supplier_external_id || row.normalized.external_id || "—")}</b><div className="cell-subline">{String(row.normalized.title || row.normalized.original_title || "未提供标题")}</div></td><td>{row.valid ? <span className="badge status-passed">通过</span> : row.errors.map((error) => <div className="validation-error" key={error}>{error}</div>)}{row.warnings.map((warning) => <div className="validation-warning" key={warning}>{warning}</div>)}</td></tr>)}</tbody></table></div>}
    </section>}

    {result && <section className="card"><div className="section-heading"><div><h3>批次已入库</h3><p className="small">#{result.id} · {result.project_type || projectType} · {result.name} · 负责人 {result.owner_name || ownerName} · {result.content_count} 条</p></div><div className="btn-row"><button className="btn btn-ghost" onClick={exportResult} disabled={!!busy || auditBusy} aria-label="导出当前批次">↓ {busy === "export" ? "导出中" : "导出批次"}</button><button className="btn btn-primary" onClick={() => startAudit(result.id)} disabled={auditBusy || !!busy} aria-label="开始审核本批次">{auditBusy ? "启动中..." : "开始审核本批次"}</button></div></div></section>}

    <details className="secondary-tool"><summary>手工录入单条内容</summary><form className="card" onSubmit={submitManual}><p className="small">沿用上方项目类型、负责人和批次编号。此入口仅用于临时补录。</p><div className="form-grid"><div className="field"><label htmlFor="manual-id">内容编号 *</label><input id="manual-id" value={manual.external_id} onChange={(e) => setManual({ ...manual, external_id: e.target.value })} /></div><div className="field"><label htmlFor="manual-platform">平台</label><select id="manual-platform" value={manual.platform} onChange={(e) => setManual({ ...manual, platform: e.target.value })}><option>微博</option><option>抖音</option><option>小红书</option><option>B站</option><option>其他</option></select></div><div className="field span-2"><label htmlFor="manual-title">标题 *</label><input id="manual-title" value={manual.title} onChange={(e) => setManual({ ...manual, title: e.target.value })} /></div><div className="field span-2"><label htmlFor="manual-body">正文 *</label><textarea id="manual-body" rows={5} value={manual.body} onChange={(e) => setManual({ ...manual, body: e.target.value })} /></div><div className="field span-2"><label htmlFor="manual-media">图片 *</label><input id="manual-media" type="file" accept=".jpg,.jpeg,.png,.webp" onChange={(e) => setManualFile(e.target.files?.[0] || null)} /></div></div><button className="btn btn-ghost" disabled={!!busy}>{busy === "manual" ? "提交中..." : "创建单条批次"}</button></form></details>
  </div>;
}
