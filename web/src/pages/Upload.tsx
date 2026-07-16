import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, saveBlob, type BatchDetail, type ImportPreview, type Project } from "../api";

type Message = { type: "ok" | "err"; text: string };

export default function Upload() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState(0);
  const [supplierName, setSupplierName] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [batchName, setBatchName] = useState("");
  const [reviewBrief, setReviewBrief] = useState("");
  const [excel, setExcel] = useState<File | null>(null);
  const [briefFile, setBriefFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<BatchDetail | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const [busy, setBusy] = useState("");
  const [auditBusy, setAuditBusy] = useState(false);

  useEffect(() => {
    api.projects().then((data) => {
      const techProjects = data.filter((project) => project.content_type === "TECH_MEDIA_REVIEW");
      setProjects(techProjects);
      setProjectId(techProjects[0]?.id || 0);
    }).catch((error: Error) => setMessage({ type: "err", text: error.message }));
  }, []);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    api.project(projectId).then((detail) => {
      if (!cancelled) setReviewBrief((current) => current.trim() ? current : detail.description || "");
    }).catch((error: Error) => {
      if (!cancelled) setMessage({ type: "err", text: error.message });
    });
    return () => { cancelled = true; };
  }, [projectId]);

  const selectedProject = projects.find((project) => project.id === projectId) || null;
  const projectType = selectedProject?.name || "";
  const supplierNameValue = supplierName.trim();
  const ownerNameValue = ownerName.trim();
  const batchNameValue = batchName.trim();
  const briefReady = Boolean(reviewBrief.trim() || briefFile);
  const identityReady = Boolean(projectId && supplierNameValue && ownerNameValue && batchNameValue && briefReady);
  const previewMatches = Boolean(
    preview
    && preview.project_id === projectId
    && preview.supplier_id === supplierNameValue
    && preview.batch_name === batchNameValue
    && preview.project_type === projectType
    && preview.owner_name === ownerNameValue,
  );
  const canConfirm = Boolean(
    previewMatches
    && preview?.token
    && preview.error_count === 0
    && preview.errors.length === 0
    && preview.valid_count === preview.total_count
    && preview.total_count > 0,
  );
  const step = result ? 5 : preview ? 4 : excel ? 3 : identityReady ? 2 : 1;

  const invalidatePreview = () => { setPreview(null); setResult(null); };

  const downloadTemplate = async () => {
    setBusy("template");
    setMessage(null);
    try {
      saveBlob(await api.importTemplate(), "tech-media-import-template.xlsx");
    } catch (error) {
      setMessage({ type: "err", text: error instanceof Error ? error.message : "模板下载失败" });
    } finally {
      setBusy("");
    }
  };

  const doPreview = async () => {
    if (!identityReady || !excel) {
      return setMessage({ type: "err", text: "请填写供应商名称、负责人、批次编号，并上传 Brief 与 Excel 文件" });
    }
    setBusy("preview");
    setMessage(null);
    setResult(null);
    try {
      const data = new FormData();
      data.append("project_id", String(projectId));
      data.append("supplier_id", supplierNameValue);
      data.append("batch_name", batchNameValue);
      data.append("project_type", projectType);
      data.append("owner_name", ownerNameValue);
      data.append("review_brief", reviewBrief.trim());
      data.append("excel_file", excel);
      if (briefFile) data.append("brief_file", briefFile);
      setPreview(await api.previewImport(data));
    } catch (error) {
      setPreview(null);
      setMessage({ type: "err", text: error instanceof Error ? error.message : "预检失败" });
    } finally {
      setBusy("");
    }
  };

  const confirm = async () => {
    if (!preview || !canConfirm) {
      return setMessage({ type: "err", text: "预检结果无效或已过期，不能确认导入" });
    }
    setBusy("confirm");
    setMessage(null);
    try {
      const created = await api.confirmImport(preview.token, {
        project_id: projectId,
        supplier_id: supplierNameValue,
        batch_name: batchNameValue,
        project_type: projectType,
        owner_name: ownerNameValue,
      });
      setResult(created);
      setMessage({ type: "ok", text: `批次 #${created.id} 导入完成，共 ${created.content_count} 条内容` });
    } catch (error) {
      setMessage({ type: "err", text: error instanceof Error ? error.message : "确认导入失败" });
    } finally {
      setBusy("");
    }
  };

  const exportResult = async () => {
    if (!result) return;
    setBusy("export");
    try {
      saveBlob(await api.exportBatch(result.id), `batch-${result.id}.xlsx`);
    } catch (error) {
      setMessage({ type: "err", text: error instanceof Error ? error.message : "导出失败" });
    } finally {
      setBusy("");
    }
  };

  const startAudit = async (batchId: number) => {
    setAuditBusy(true);
    setMessage(null);
    try {
      await api.auditBatch(batchId);
      navigate(`/review?batch_id=${batchId}`);
    } catch (error) {
      setMessage({ type: "err", text: `审核启动失败：${error instanceof Error ? error.message : "未知错误"}` });
    } finally {
      setAuditBusy(false);
    }
  };

  return <div>
    <div className="page-heading">
      <div>
        <h2>上传</h2>
        <p>把供应商、负责人、批次、Brief 和 Excel 放在同一个导入流程里，预检通过后进入审核。</p>
      </div>
    </div>

    <ol className="stepper" aria-label="导入步骤">
      {["批次信息", "Brief 与文件", "选择文件", "预检确认", "完成"].map((label, index) => (
        <li key={label} className={step > index + 1 ? "done" : step === index + 1 ? "current" : ""}>
          <span>{index + 1}</span>{label}
        </li>
      ))}
    </ol>

    {message && <div className={`msg ${message.type}`} role="status">{message.text}</div>}

    <section className="card import-shell upload-main-card">
      <div className="section-heading">
        <div>
          <h3>Brief / Excel 文件</h3>
          <p className="small">同一批次的 Brief 和 Excel 文字内容表在这里一次提交。</p>
        </div>
      </div>

      <div className="form-grid">
        <div className="field span-2">
          <label htmlFor="upload-project">项目类型 *</label>
          <select
            id="upload-project"
            value={projectId}
            onChange={(event) => {
              setProjectId(Number(event.target.value));
              setReviewBrief("");
              invalidatePreview();
            }}
          >
            <option value={0}>请选择</option>
            {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
          {projects.length === 0 && <p className="field-help">暂无可导入的项目类型</p>}
        </div>

        <div className="field">
          <label htmlFor="supplier-name">供应商名称 *</label>
          <input
            id="supplier-name"
            type="text"
            value={supplierName}
            onChange={(event) => { setSupplierName(event.target.value); invalidatePreview(); }}
            placeholder="例如：供应商 ABC"
          />
        </div>
        <div className="field">
          <label htmlFor="owner-name">负责人 *</label>
          <input
            id="owner-name"
            type="text"
            value={ownerName}
            onChange={(event) => { setOwnerName(event.target.value); invalidatePreview(); }}
            placeholder="例如：张三 / 内容组 A"
          />
        </div>
        <div className="field span-2">
          <label htmlFor="batch-name">批次编号 *</label>
          <input
            id="batch-name"
            type="text"
            value={batchName}
            onChange={(event) => { setBatchName(event.target.value); invalidatePreview(); }}
            placeholder="例如：第 1 批 / 2026-春季-01"
          />
        </div>

        <div className="field span-2">
          <label htmlFor="review-brief">审核 Brief *</label>
          <textarea
            id="review-brief"
            rows={5}
            value={reviewBrief}
            onChange={(event) => { setReviewBrief(event.target.value); invalidatePreview(); }}
            placeholder="填写这次上传稿件对应的产品功能、上线时期、营销点、禁止表达和特殊口径。也可以在下方上传 Word / Brief 文件。"
          />
          <p className="field-help">Brief 文本和 Word / Brief 文件会合并用于预检与 AI 评分。</p>
        </div>
      </div>

      <div className="import-actions">
        <button
          type="button"
          className="btn btn-ghost"
          onClick={downloadTemplate}
          disabled={busy === "template"}
          title="下载标准 Excel 模板"
          aria-label="下载标准 Excel 模板"
        >
          ↓ {busy === "template" ? "下载中" : "下载模板"}
        </button>
        <span className="small">内容表使用“标题、内容、类型、目标平台、作者、发布日期”六列；当前仅审核文字。</span>
      </div>

      <div className="file-grid">
        <div className="file-field">
          <label htmlFor="brief-file">Word / Brief 文件（可选）</label>
          <input
            id="brief-file"
            type="file"
            accept=".docx,.txt,.md"
            onChange={(event) => { setBriefFile(event.target.files?.[0] || null); invalidatePreview(); }}
          />
          <span>{briefFile?.name || "可上传 .docx / .txt / .md"}</span>
        </div>
        <div className="file-field">
          <label htmlFor="excel-file">Excel 文件 *</label>
          <input
            id="excel-file"
            type="file"
            accept=".xlsx"
            onChange={(event) => { setExcel(event.target.files?.[0] || null); invalidatePreview(); }}
          />
          <span>{excel?.name || "未选择 .xlsx 文件"}</span>
        </div>
      </div>

      <div className="btn-row">
        <button type="button" className="btn btn-primary" onClick={doPreview} disabled={!identityReady || !excel || !!busy}>
          {busy === "preview" ? "预检中..." : "预检文件"}
        </button>
        <button type="button" className="btn btn-pass" onClick={confirm} disabled={!canConfirm || !!busy} title={!canConfirm ? "仅无错误且身份未变化的预检可确认" : "确认导入"}>
          {busy === "confirm" ? "导入中..." : "确认导入"}
        </button>
      </div>
    </section>

    {preview && <section className="card preview-section">
      <div className="section-heading">
        <div>
          <h3>预检结果</h3>
          <p className="small">项目类型 {preview.project_type} · 批次 {preview.batch_name}</p>
          <p className="small">供应商 {preview.supplier_id} · 负责人 {preview.owner_name}</p>
          <p className="small">标准包 {preview.package_version} · Token {preview.token.slice(0, 10)}…</p>
          <p className="small">Brief：{preview.brief_summary}</p>
        </div>
        <div className="badge-group">
          <span className="badge status-passed">有效 {preview.valid_count}</span>
          <span className={`badge ${preview.error_count ? "status-invalid" : "neutral"}`}>错误 {preview.error_count}</span>
        </div>
      </div>
      {(preview.errors.length > 0 || preview.warnings.length > 0) && (
        <div className="validation-summary">
          {preview.errors.map((error) => <p className="validation-error" key={error}>错误：{error}</p>)}
          {preview.warnings.map((warning) => <p className="validation-warning" key={warning}>提示：{warning}</p>)}
        </div>
      )}
      {preview.rows.length === 0 ? <p className="empty">文件中没有可预览的内容行</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>序号</th><th>内容编号 / 标题</th><th>字段与格式</th></tr></thead>
            <tbody>{preview.rows.map((row) => (
              <tr key={row.row_number} className={!row.valid ? "invalid-row" : ""}>
                <td><b>第{row.manuscript_index}篇</b><div className="cell-subline">Excel 第{row.row_number}行</div></td>
                <td><b>{String(row.normalized.supplier_external_id || row.normalized.external_id || "—")}</b><div className="cell-subline">{String(row.normalized.title || row.normalized.original_title || "未提供标题")}</div></td>
                <td>
                  {row.valid ? <span className="badge status-passed">通过</span> : row.errors.map((error) => <div className="validation-error" key={error}>{error}</div>)}
                  {row.warnings.map((warning) => <div className="validation-warning" key={warning}>{warning}</div>)}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>}

    {result && <section className="card">
      <div className="section-heading">
        <div>
          <h3>批次已入库</h3>
          <p className="small">#{result.id} · {result.project_type || projectType} · {result.name} · 供应商 {result.supplier_id} · 负责人 {result.owner_name || ownerNameValue} · {result.content_count} 条</p>
        </div>
        <div className="btn-row">
          <button className="btn btn-ghost" onClick={exportResult} disabled={!!busy || auditBusy} aria-label="导出当前批次">↓ {busy === "export" ? "导出中" : "导出批次"}</button>
          <button className="btn btn-primary" onClick={() => startAudit(result.id)} disabled={auditBusy || !!busy} aria-label="开始审核本批次">{auditBusy ? "启动中..." : "开始审核本批次"}</button>
        </div>
      </div>
    </section>}
  </div>;
}
