export type JsonObject = Record<string, unknown>;
export type FormatStatus = "PENDING" | "PASSED" | "INCOMPLETE" | "INVALID";
export type ReviewStatus = "NOT_STARTED" | "AI_REVIEWING" | "MANUAL_REQUIRED" | "FIX_PROPOSED" | "APPROVED" | "REJECTED";
export type PublishStatus = "NOT_READY" | "READY" | "PUBLISHED";

export interface Project {
  id: number;
  name: string;
  description: string | null;
  current_rule_version_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface RuleVersion {
  id: number;
  project_id: number;
  version: number;
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
  status: string;
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
  status: string;
  raw_result: JsonObject;
  created_at: string;
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
  task_type: "REVIEW_FIX_PROPOSAL" | "RISK_REVIEW" | string;
  status: string;
  assigned_to: string | null;
  created_at: string;
  closed_at: string | null;
}

export interface ContentDetail extends ContentSummary {
  latest_audit: AuditDetail | null;
  open_tasks: ReviewTask[];
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

export interface Config {
  reviewer: string;
  model: string;
  key_set: boolean;
}

export interface BatchAuditResult {
  content_id: number;
  status: "success" | "error";
  audit_run_id: number | null;
  error: string | null;
}

export interface RuleVersionInput {
  dimension_standards: JsonObject;
  project_facts: JsonObject;
  structured_rules: JsonObject;
  prompt_version: string;
}

export interface ContentFilters {
  project_id?: number;
  batch_id?: number;
  review_status?: string;
}

function query(params: Record<string, string | number | undefined>): string {
  const values = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") values.set(key, String(value));
  });
  const encoded = values.toString();
  return encoded ? `?${encoded}` : "";
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (response.ok) return response.json() as Promise<T>;
  let message = `HTTP ${response.status}`;
  try {
    const error = await response.json() as { detail?: string };
    message = error.detail || JSON.stringify(error);
  } catch {
    // Keep the status fallback for non-JSON responses.
  }
  throw new Error(message);
}

function jsonRequest(method: "POST" | "PUT", body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  projects: (): Promise<Project[]> => request("/api/projects"),
  project: (id: number): Promise<ProjectDetail> => request(`/api/projects/${id}`),
  createProject: (body: { name: string; description?: string }): Promise<Project> =>
    request("/api/projects", jsonRequest("POST", body)),
  ruleVersions: (projectId: number): Promise<RuleVersion[]> =>
    request(`/api/projects/${projectId}/rule-versions`),
  createRuleVersion: (projectId: number, body: RuleVersionInput): Promise<RuleVersion> =>
    request(`/api/projects/${projectId}/rule-versions`, jsonRequest("POST", body)),
  publishRuleVersion: (projectId: number, ruleVersionId: number): Promise<Project> =>
    request(`/api/projects/${projectId}/rule-versions/${ruleVersionId}/publish`, { method: "POST" }),
  batches: (projectId?: number): Promise<Batch[]> =>
    request(`/api/batches${query({ project_id: projectId })}`),
  createBatch: (body: FormData): Promise<BatchDetail> =>
    request("/api/batches", { method: "POST", body }),
  contents: (filters: ContentFilters): Promise<ContentSummary[]> =>
    request(`/api/contents${query({
      project_id: filters.project_id,
      batch_id: filters.batch_id,
      review_status: filters.review_status,
    })}`),
  content: (id: number): Promise<ContentDetail> => request(`/api/contents/${id}`),
  auditContent: (id: number): Promise<AuditDetail> =>
    request(`/api/contents/${id}/audit`, { method: "POST" }),
  auditBatch: (id: number): Promise<{ batch_id: number; audited: number; audit_run_ids: number[]; results: BatchAuditResult[] }> =>
    request(`/api/batches/${id}/audit`, { method: "POST" }),
  reviewTasks: (filters: { status?: string; project_id?: number; batch_id?: number }): Promise<ReviewTask[]> =>
    request(`/api/review-tasks${query(filters)}`),
  resolveTask: (
    id: number,
    body: { decision: string; reviewer: string; note?: string; payload?: JsonObject },
  ): Promise<HumanDecision> => request(`/api/review-tasks/${id}/resolve`, jsonRequest("POST", body)),
  report: (projectId: number, batchId?: number): Promise<ReportData> =>
    request(`/api/reports${query({ project_id: projectId, batch_id: batchId })}`),
  config: (): Promise<Config> => request("/api/config"),
  saveConfig: (body: Partial<Pick<Config, "reviewer" | "model">>): Promise<Config> =>
    request("/api/config", jsonRequest("PUT", body)),
};
