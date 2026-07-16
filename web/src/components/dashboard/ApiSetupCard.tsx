import { useState, type FormEvent } from "react";
import { api, type Config } from "../../api";

const DEFAULT_MODEL = "GPT 5.6 SOL";

export default function ApiSetupCard({ config, onSaved }: { config: Config | null; onSaved: (config: Config) => void }) {
  const [model, setModel] = useState(config?.model || DEFAULT_MODEL);
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!apiKey.trim() && !config?.key_set) {
      setMessage("请填写 One API key");
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const saved = await api.saveConfig({
        reviewer: "oneapi",
        model: model.trim() || DEFAULT_MODEL,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      onSaved(saved);
      setApiKey("");
      setMessage("已保存");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={`dashboard-panel api-setup-panel ${config?.key_set ? "is-ready" : ""}`}>
      <div className="panel-heading">
        <div>
          <h3>个人模型配置</h3>
          <p className="small">{config?.key_set ? "One API 已接入" : "需要接入 One API"}</p>
        </div>
        <span className={`dot ${config?.key_set ? "on" : "off"}`} />
      </div>
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="dashboard-model">模型</label>
          <select id="dashboard-model" value={model} onChange={(event) => setModel(event.target.value)}>
            <option value={DEFAULT_MODEL}>{DEFAULT_MODEL}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="dashboard-api-key">One API key</label>
          <input
            id="dashboard-api-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={config?.key_set ? "留空保持当前 key" : "粘贴个人 key"}
          />
        </div>
        {message && <p className={`api-setup-message ${message === "已保存" ? "ok" : "err"}`}>{message}</p>}
        <button className="btn btn-primary" disabled={saving}>{saving ? "保存中..." : "保存配置"}</button>
      </form>
    </section>
  );
}
