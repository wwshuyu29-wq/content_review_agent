export type JsonObject = Record<string, unknown>;
export type FormatStatus = "PENDING" | "PASSED" | "INCOMPLETE" | "INVALID";
export type ReviewStatus =
  | "NOT_STARTED"
  | "AI_REVIEWING"
  | "HUMAN_REVIEW_REQUIRED"
  | "SUPPLIER_REVISION_REQUIRED"
  | "AUTO_FIX_PENDING"
  | "PASSED"
  | "PASSED_WITH_SUGGESTIONS"
  | "BLOCKED"
  | "REJECTED";
export type PublishStatus = "NOT_READY" | "READY" | "PUBLISHED";
export type AgentId = "CONTENT_QUALITY" | "COMPLIANCE" | "BRAND" | "PRODUCT_ACCURACY" | "CAMPAIGN_EFFECTIVENESS";
export type EvidenceStatus = "PRESENT" | "MISSING" | "NONE";

export const AGENT_ORDER: AgentId[] = [
  "CONTENT_QUALITY", "COMPLIANCE", "BRAND", "PRODUCT_ACCURACY", "CAMPAIGN_EFFECTIVENESS",
];

export interface Project {
  id: number;
  name: string;
  code: string | null;
  content_type: string | null;
  description: string | null;
  current_rule_version_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface RuleVersion {
  id: number;
  project_id: number;
  version: number;
  business_domain: string | null;
  document_type: string | null;
  project_code: string | null;
  content_type: string | null;
  package_version: string | null;
  package_digest: string | null;
  dimension_standards: JsonObject;
  project_facts: JsonObject;
  structured_rules: JsonObject;
  prompt_version: string;
  created_at: string;
}

export interface ProjectDetail extends Project {
  current_rule_version: RuleVersion | null;
  rule_versions: RuleVersion[];
}

export interface Batch {
  id: number;
  project_id: number;
  supplier_id: string;
  name: string;
  project_type: string | null;
  owner_name: string | null;
  status: string;
  review_brief: string | null;
  created_at: string;
}

export interface ContentVersion {
  id: number;
  content_item_id: number;
  version: number;
  source: string;
  title: string;
  body: string;
  payload: JsonObject;
  created_at: string;
}

export interface ContentSummary {
  id: number;
  project_id: number;
  batch_id: number;
  external_id: string;
  title: string;
  format_status: FormatStatus;
  review_status: ReviewStatus;
  publish_status: PublishStatus;
  created_at: string;
  updated_at: string;
  versions: ContentVersion[];
}

export interface BatchDetail extends Batch {
  content_count: number;
  contents: ContentSummary[];
}

export interface AgentResult {
  id: number;
  audit_run_id: number;
  agent_name: string;
  agent_id: AgentId | string | null;
  agent_version: string | null;
  decision: string | null;
  summary: string | null;
  score: number | null;
  status: string;
  created_at: string;
}

export interface ContentTableAgent {
  agent_id: AgentId;
  agent_name: string;
  agent_version: string | null;
  decision: string | null;
  summary: string | null;
  score: number | null;
  status: string;
}

export interface Issue {
  id: number;
  audit_run_id: number;
  agent_result_id: number | null;
  rule_id: string;
  category: string;
  severity: string;
  field: string;
  evidence_quote: string;
  evidence_start: number | null;
  evidence_end: number | null;
  evidence_asset_id: string | null;
  evidence_timestamp: string | null;
  source_reference: string[];
  reason: string;
  suggestion: string;
  auto_fixable: boolean;
  human_required: boolean;
  confidence: number;
  created_at: string;
}

export interface AuditRun {
  id: number;
  content_item_id: number;
  content_version_id: number;
  rule_version_id: number;
  review_key: string | null;
  model: string;
  prompt_version: string;
  status: string;
  created_at: string;
  completed_at: string | null;
}

export interface AuditDetail extends AuditRun {
  agent_results: AgentResult[];
  issues: Issue[];
}

export interface ReviewTask {
  id: number;
  content_item_id: number;
  target_content_version_id: number;
  audit_run_id: number;
  issue_id: number | null;
  issue_ids: number[];
  task_key: string | null;
  task_type: string;
  status: string;
  assigned_to: string | null;
  created_at: string;
  closed_at: string | null;
}

export interface ContentDetail extends ContentSummary {
  latest_audit: AuditDetail | null;
  open_tasks: ReviewTask[];
}

export interface Asset {
  id: number;
  content_item_id: number;
  asset_id: string;
  external_id: string | null;
  kind: "IMAGE" | "VIDEO" | "SCREENSHOT" | "SCREEN_RECORDING" | "TEST_LOG";
  filename: string;
  storage_key: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  asset_metadata: JsonObject;
  created_at: string;
}

export interface TestEvidence {
  id: number;
  test_case_id: number;
  asset_id: number;
  asset: Asset;
}

export interface TestCase {
  id: number;
  content_item_id: number;
  content_version_id: number;
  external_test_case_id: string;
  claim: string;
  command: string;
  observed_result: string;
  city: string | null;
  tested_at: string | null;
  app_version: string | null;
  device: string | null;
  operating_system: string | null;
  network_environment: string | null;
  test_metadata: JsonObject;
  evidence: TestEvidence[];
}

export interface ImportTestPreview {
  content_external_id: string;
  external_test_case_id: string;
  claim: string | null;
  command: string | null;
  observed_result: string | null;
  city: string | null;
  tested_at: string | null;
  app_version: string | null;
  device: string | null;
  operating_system: string | null;
  network_environment: string | null;
  evidence_filenames: string[];
}

export interface ImportRowPreview {
  manuscript_index: number;
  row_number: number;
  normalized: JsonObject;
  errors: string[];
  warnings: string[];
  valid: boolean;
  tests: ImportTestPreview[];
}

export interface ImportPreview {
  token: string;
  rows: ImportRowPreview[];
  tests: ImportTestPreview[];
  errors: string[];
  warnings: string[];
  total_count: number;
  valid_count: number;
  error_count: number;
  test_count: number;
  project_id: number;
  project_code: string;
  content_type: string;
  package_version: string;
  supplier_id: string;
  batch_name: string;
  project_type: string;
  owner_name: string;
  review_brief: string;
  brief_summary: string;
}

export interface ContentTableRow {
  id: number;
  project_id: number;
  batch_id: number;
  supplier_external_id: string;
  campaign_theme: string | null;
  account_name: string | null;
  account_type: string | null;
  platform: string | null;
  original_title: string;
  original_body: string;
  final_title: string;
  final_body: string;
  body_summary: string;
  publish_time: string | null;
  note: string | null;
  row_number: number | null;
  format_status: FormatStatus;
  format_errors: string[];
  review_status: ReviewStatus;
  publish_status: PublishStatus;
  issues: Issue[];
  issue_count: number;
  highest_severity: string | null;
  categories: string[];
  suggestions: string[];
  open_task_count: number;
  open_task_types: string[];
  latest_audit_id: number | null;
  agents: ContentTableAgent[];
  media_url: string | null;
  test_count: number;
  evidence_count: number;
  evidence_status: EvidenceStatus;
}

export interface HumanDecision {
  id: number;
  review_task_id: number;
  decision: string;
  reviewer: string;
  note: string | null;
  payload: JsonObject;
  created_at: string;
}

export interface ReportData {
  project: { id: number; name: string };
  batch: { id: number; name: string } | null;
  totals: { contents: number; issues: number; tasks: number };
  historical_totals: { issues: number; tasks: number };
  status_counts: Record<string, number>;
  category_counts: Record<string, number>;
  rule_counts: Record<string, number>;
  manual_metrics: { contents: number; tasks: number; rate: number };
}

export interface Config { reviewer: string; model: string; key_set: boolean; }
export interface DashboardMonthMetrics { month: string; uploaded_count: number; audit_started_count: number; human_decision_count: number; }
export interface DashboardWorkloadRow { user_id: number; username: string; display_name: string; months: DashboardMonthMetrics[]; }
export interface DashboardBatchQuality { batch_id: number; batch_name: string; total_count: number; passed_count: number; pass_rate: number; }
export interface DashboardQuality { total_count: number; passed_count: number; pass_rate: number; batches: DashboardBatchQuality[]; }
export interface DashboardProjectQuality { project_id: number; project_name: string; total_count: number; passed_count: number; pass_rate: number; }
export interface DashboardMonthlyReview { month: string; reviewed_count: number; }
export interface DashboardSupplierQuality { supplier_name: string; project_names: string[]; total_count: number; passed_count: number; pass_rate: number; }
export interface DashboardIssueManuscript { content_id: number; title: string; severity: string; reason: string; }
export interface DashboardIssueCluster { category: string; issue_count: number; manuscript_count: number; high_count: number; manuscripts: DashboardIssueManuscript[]; }
export interface DashboardOverview {
  month: string;
  workload: DashboardWorkloadRow[];
  quality: DashboardQuality;
  project_quality: DashboardProjectQuality[];
  monthly_reviews: DashboardMonthlyReview[];
  supplier_quality: DashboardSupplierQuality[];
  issue_clusters: DashboardIssueCluster[];
}

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  role: string;
  is_active: boolean;
}
export interface AuthResponse { user: AuthUser; csrf_token: string; }

export interface BatchAuditResult { content_id: number; status: "success" | "error"; audit_run_id: number | null; error: string | null; }

export type AuditJobStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "COMPLETED_WITH_ERRORS" | "FAILED" | "INTERRUPTED";
export type ManuscriptProgressStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED";
export type AgentProgressStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface AuditJobStart {
  job_id: number;
  batch_id: number;
  status: AuditJobStatus;
}

export interface AgentProgress {
  id: number;
  manuscript_job_id: number;
  agent_id: AgentId;
  position: number;
  status: AgentProgressStatus;
  attempt_count: number;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  decision: string | null;
  score: number | null;
  error_summary: string | null;
}

export interface ManuscriptProgress {
  id: number;
  audit_job_id: number;
  content_item_id: number;
  position: number;
  status: ManuscriptProgressStatus;
  started_at: string | null;
  completed_at: string | null;
  error_summary: string | null;
  agents: AgentProgress[];
}

export interface AuditJobProgress {
  id: number;
  batch_id: number;
  model: string;
  status: AuditJobStatus;
  total_count: number;
  completed_count: number;
  failed_count: number;
  skipped_count: number;
  running_count: number;
  pending_count: number;
  current_content_item_id: number | null;
  current_agent_id: AgentId | null;
  heartbeat_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_summary: string | null;
  created_at: string;
  updated_at: string;
  manuscripts: ManuscriptProgress[];
  current_agents: AgentProgress[];
}
export interface ContentFilters { project_id?: number; batch_id?: number; format_status?: FormatStatus; review_status?: ReviewStatus; publish_status?: PublishStatus; }

function query(params: Record<string, string | number | undefined>): string {
  const values = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== "") values.set(key, String(value)); });
  const encoded = values.toString();
  return encoded ? `?${encoded}` : "";
}

// CSRF token lives only in memory; never persisted to localStorage.
let csrfToken: string | null = null;
const unauthorizedHandlers = new Set<() => void>();

export function setCsrfToken(token: string | null): void { csrfToken = token; }
export function getCsrfToken(): string | null { return csrfToken; }
export function onUnauthorized(handler: () => void): () => void {
  unauthorizedHandlers.add(handler);
  return () => { unauthorizedHandlers.delete(handler); };
}
function handleUnauthorized(): void {
  csrfToken = null;
  unauthorizedHandlers.forEach((handler) => handler());
}

function buildInit(init?: RequestInit): RequestInit {
  const method = (init?.method || "GET").toUpperCase();
  const headers = new Headers(init?.headers);
  if ((method === "POST" || method === "PUT" || method === "PATCH" || method === "DELETE") && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  return { ...init, headers, credentials: "include" as RequestCredentials };
}

const SAFE_BUSINESS_DETAILS: Record<string, string> = {
  "Invalid username or password": "用户名或密码错误。",
  "Username already exists": "用户名已存在。",
  "Project name already exists": "项目名称已存在。",
  "Batch contains duplicate content identifiers": "批次中存在重复的内容编号。",
  "Image exceeds 20MB limit": "图片超过 20MB 限制。",
  "Upload exceeds size limit": "上传文件超过大小限制。",
  "审核任务暂时无法启动，请稍后重试。": "审核任务暂时无法启动，请稍后重试。",
};

export function normalizeApiError(status: number, detail?: unknown): string {
  if (typeof detail === "string" && SAFE_BUSINESS_DETAILS[detail]) return SAFE_BUSINESS_DETAILS[detail];
  if (status === 401) return "登录状态已失效，请重新登录。";
  if (status === 403) return "当前操作无权限或安全校验失败，请刷新后重试。";
  if (status === 404) return "未找到请求的数据。";
  if (status === 409) return "当前数据状态已变化，请刷新后重试。";
  if (status === 413) return "上传文件过大，请压缩后重试。";
  if (status === 422) return "提交信息有误，请检查后重试。";
  if (status === 429) return "操作过于频繁，请稍后重试。";
  if (status >= 500) return "系统服务暂时不可用，请稍后重试。";
  return "请求失败，请稍后重试。";
}

async function fetchResponse(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, buildInit(init));
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error("网络连接异常，请检查网络后重试。");
  }
}

async function errorDetail(response: Response): Promise<unknown> {
  try { return (await response.json() as { detail?: unknown }).detail; }
  catch { return undefined; }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetchResponse(url, init);
  if (response.status === 401) { handleUnauthorized(); }
  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }
  throw new Error(normalizeApiError(response.status, await errorDetail(response)));
}

async function requestVoid(url: string, init?: RequestInit): Promise<void> {
  const response = await fetchResponse(url, init);
  if (response.status === 401) { handleUnauthorized(); }
  if (response.ok || response.status === 204) return;
  throw new Error(normalizeApiError(response.status, await errorDetail(response)));
}

async function requestBlob(url: string, init?: RequestInit): Promise<Blob> {
  const response = await fetchResponse(url, init);
  if (response.status === 401) { handleUnauthorized(); }
  if (response.ok) return response.blob();
  throw new Error(normalizeApiError(response.status, await errorDetail(response)));
}

function jsonRequest(method: "POST" | "PUT" | "PATCH", body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function saveBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
}

export const api = {
  me: (): Promise<AuthResponse> => request("/api/auth/me"),
  login: (username: string, password: string): Promise<AuthResponse> =>
    request("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }) }),
  logout: (): Promise<void> => requestVoid("/api/auth/logout", { method: "POST" }),
  projects: (): Promise<Project[]> => request("/api/projects"),
  project: (id: number): Promise<ProjectDetail> => request(`/api/projects/${id}`),
  updateProjectBrief: (id: number, description: string): Promise<Project> =>
    request(`/api/projects/${id}/brief`, jsonRequest("PATCH", { description })),
  batches: (projectId?: number, signal?: AbortSignal): Promise<Batch[]> => request(`/api/batches${query({ project_id: projectId })}`, { signal }),
  createBatch: (body: FormData): Promise<BatchDetail> => request("/api/batches", { method: "POST", body }),
  importTemplate: (): Promise<Blob> => requestBlob("/api/import-template"),
  previewImport: (body: FormData): Promise<ImportPreview> => request("/api/imports/preview", { method: "POST", body }),
  confirmImport: (token: string, body: { project_id: number; supplier_id: string; batch_name: string; project_type?: string; owner_name?: string }): Promise<BatchDetail> =>
    request(`/api/imports/${encodeURIComponent(token)}/confirm`, jsonRequest("POST", body)),
  exportBatch: (batchId: number): Promise<Blob> => requestBlob(`/api/batches/${batchId}/export`),
  contents: (filters: Pick<ContentFilters, "project_id" | "batch_id" | "review_status">): Promise<ContentSummary[]> =>
    request(`/api/contents${query(filters)}`),
  contentTable: (filters: ContentFilters, signal?: AbortSignal): Promise<ContentTableRow[]> => request(`/api/contents/table${query({ project_id: filters.project_id, batch_id: filters.batch_id, format_status: filters.format_status, review_status: filters.review_status, publish_status: filters.publish_status })}`, { signal }),
  content: (id: number, signal?: AbortSignal): Promise<ContentDetail> => request(`/api/contents/${id}`, { signal }),
  contentTestCases: (id: number, signal?: AbortSignal): Promise<TestCase[]> => request(`/api/contents/${id}/test-cases`, { signal }),
  auditContent: (id: number): Promise<AuditDetail> => request(`/api/contents/${id}/audit`, { method: "POST" }),
  startAuditJob: (batchId: number): Promise<AuditJobStart> => request(`/api/batches/${batchId}/audit-jobs`, { method: "POST" }),
  auditJob: (jobId: number, signal?: AbortSignal): Promise<AuditJobProgress> => request(`/api/audit-jobs/${jobId}`, { signal }),
  batchAuditJob: (batchId: number, signal?: AbortSignal): Promise<AuditJobProgress | null> => request(`/api/batches/${batchId}/audit-job`, { signal }),
  auditBatch: (id: number): Promise<{ batch_id: number; audited: number; audit_run_ids: number[]; results: BatchAuditResult[] }> =>
    request(`/api/batches/${id}/audit`, { method: "POST" }),
  reviewTasks: (filters: { status?: string; project_id?: number; batch_id?: number }): Promise<ReviewTask[]> =>
    request(`/api/review-tasks${query(filters)}`),
  resolveTask: (id: number, body: { decision: string; reviewer: string; note?: string; payload?: JsonObject }): Promise<HumanDecision> =>
    request(`/api/review-tasks/${id}/resolve`, jsonRequest("POST", body)),
  report: (projectId: number, batchId?: number): Promise<ReportData> => request(`/api/reports${query({ project_id: projectId, batch_id: batchId })}`),
  publishPackage: (projectId: number, body: { project_code: string; package_version: string }): Promise<RuleVersion> =>
    request(`/api/projects/${projectId}/rule-versions`, jsonRequest("POST", body)),
  config: (): Promise<Config> => request("/api/config"),
  saveConfig: (body: Partial<Pick<Config, "reviewer" | "model">> & { api_key?: string; clear_key?: boolean }): Promise<Config> => request("/api/config", jsonRequest("PUT", body)),
  dashboardOverview: (month?: string): Promise<DashboardOverview> => request(`/api/dashboard/overview${query({ month })}`),
};
