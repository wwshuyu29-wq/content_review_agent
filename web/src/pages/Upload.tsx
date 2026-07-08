import { useState } from "react";
import { api } from "../api";

export default function Upload() {
  const [f, setF] = useState({ supplier_id: "", theme: "", platform: "微博", title: "", body: "", publish_time: "" });
  const [file, setFile] = useState<File | null>(null);
  const [msg, setMsg] = useState<{ t: "ok" | "err"; s: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.value });

  const submit = async () => {
    if (!f.supplier_id || !f.title || !f.body) {
      setMsg({ t: "err", s: "供应商ID、标题、正文为必填" });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const fd = new FormData();
      Object.entries(f).forEach(([k, v]) => fd.append(k, v));
      if (file) fd.append("file", file);
      const r = await api.upload(fd);
      setMsg({ t: "ok", s: `上传成功，内容编号 ${r.content_id}，已进入审核队列` });
      setF({ supplier_id: f.supplier_id, theme: f.theme, platform: f.platform, title: "", body: "", publish_time: "" });
      setFile(null);
    } catch (e: any) {
      setMsg({ t: "err", s: e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h2>供应商上传</h2>
      <div className="card" style={{ maxWidth: 620 }}>
        <div className="row">
          <div className="field" style={{ flex: 1 }}>
            <label>供应商 ID *</label>
            <input type="text" value={f.supplier_id} onChange={set("supplier_id")} placeholder="如 supplier_001" />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>活动主题</label>
            <input type="text" value={f.theme} onChange={set("theme")} placeholder="如 五一KOL" />
          </div>
        </div>
        <div className="row">
          <div className="field" style={{ flex: 1 }}>
            <label>平台</label>
            <select value={f.platform} onChange={set("platform")}>
              <option>微博</option><option>抖音</option><option>小红书</option><option>B站</option><option>其他</option>
            </select>
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>发布时间</label>
            <input type="text" value={f.publish_time} onChange={set("publish_time")} placeholder="2026-05-01" />
          </div>
        </div>
        <div className="field">
          <label>标题 *</label>
          <input type="text" value={f.title} onChange={set("title")} />
        </div>
        <div className="field">
          <label>正文 / 脚本 *</label>
          <textarea rows={5} value={f.body} onChange={set("body")} />
        </div>
        <div className="field">
          <label>图片（可选，JPG/PNG/WEBP）</label>
          <input type="text" style={{ display: "none" }} />
          <input type="file" accept=".jpg,.jpeg,.png,.webp" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </div>
        {msg && <div className={`msg ${msg.t}`}>{msg.s}</div>}
        <button className="btn btn-primary" onClick={submit} disabled={busy}>{busy ? "上传中…" : "提交审核"}</button>
      </div>
    </div>
  );
}
