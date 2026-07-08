// 后端 API 封装。开发时经 Vite 代理到 FastAPI。
export type Row = Record<string, string>;

async function J<T = any>(r: Response): Promise<T> {
  if (r.ok) return r.json();
  let msg = `HTTP ${r.status}`;
  try {
    const e = await r.json();
    msg = e.detail || JSON.stringify(e);
  } catch {
    /* ignore */
  }
  throw new Error(msg);
}

const jsonInit = (body: unknown): RequestInit => ({
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface Config {
  reviewer: string;
  model: string;
  base_url: string;
  project: string;
  key_set: boolean;
}

export const api = {
  rows: (status?: string) =>
    fetch(`/api/rows${status ? `?status=${encodeURIComponent(status)}` : ""}`).then(J),
  upload: (fd: FormData) => fetch("/api/upload", { method: "POST", body: fd }).then(J),
  runBatch: () => fetch("/api/run-batch", { method: "POST" }).then(J),
  humanQueue: () => fetch("/api/human-queue").then(J),
  human: (id: string, body: { decision: string; reason?: string; manual_content?: string }) =>
    fetch(`/api/rows/${id}/human`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(J),
  standards: () => fetch("/api/standards").then(J),
  saveDim: (key: string, content: string) =>
    fetch(`/api/standards/dimension/${key}`, jsonInit({ content })).then(J),
  project: (name: string) => fetch(`/api/standards/project/${encodeURIComponent(name)}`).then(J),
  saveProject: (name: string, content: string) =>
    fetch(`/api/standards/project/${encodeURIComponent(name)}`, jsonInit({ content })).then(J),
  saveRules: (rules: Record<string, unknown>) => fetch("/api/rules", jsonInit(rules)).then(J),
  report: () => fetch("/api/report").then(J),
  distill: (confirm: boolean) =>
    fetch(`/api/distill-rules?confirm=${confirm ? "true" : "false"}`, { method: "POST" }).then(J),
  config: (): Promise<Config> => fetch("/api/config").then(J),
  saveConfig: (cfg: Partial<Config>): Promise<Config> => fetch("/api/config", jsonInit(cfg)).then(J),
};
