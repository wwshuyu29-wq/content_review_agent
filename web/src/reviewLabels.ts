import type { AgentId, Issue } from "./api";

const AGENTS: Record<string, string> = { CONTENT_QUALITY: "基础内容校对", COMPLIANCE: "合规审核", BRAND: "品牌一致性", PRODUCT_ACCURACY: "产品准确性", CAMPAIGN_EFFECTIVENESS: "传播有效性" };
const DECISIONS: Record<string, string> = { PASS: "通过", PASS_WITH_SUGGESTIONS: "通过但有建议", NEED_TEXT_FIX: "需要修改", HUMAN_REVIEW: "需要人工确认", BLOCK: "阻断" };
const STATUSES: Record<string, string> = { QUEUED: "排队中", COMPLETED: "已完成", COMPLETED_WITH_ERRORS: "完成但有异常", RUNNING: "审核中", PENDING: "待审核", FAILED: "审核失败", INTERRUPTED: "已中断", SKIPPED: "已跳过", NOT_RUN: "未运行", NOT_STARTED: "未开始", AI_REVIEWING: "AI 审核中", HUMAN_REVIEW_REQUIRED: "需人工确认", SUPPLIER_REVISION_REQUIRED: "需内容修改", AUTO_FIX_PENDING: "待确认修复", PASSED: "已通过", PASSED_WITH_SUGGESTIONS: "通过但有建议", BLOCKED: "已阻断", REJECTED: "已拒绝", OPEN: "待处理", CLOSED: "已处理" };
const SEVERITIES: Record<string, string> = { CRITICAL: "严重", HIGH: "高风险", MEDIUM: "中风险", LOW: "低风险", NONE: "无风险" };
const CATEGORIES: Record<string, string> = {
  CONTENT_QUALITY: "基础内容校对",
  COMPLIANCE: "合规审核",
  BRAND: "品牌一致性",
  PRODUCT_ACCURACY: "产品准确性",
  CAMPAIGN_EFFECTIVENESS: "传播有效性",
  ADVERTISING: "广告表达",
  CLAIM: "宣传主张",
  "CLAIM-UNSUPPORTED-ABSOLUTE-001": "绝对化 / 保证表述",
  "CLAIM-PENDING-001": "待确认产品能力",
  "TEST-COUNT-001": "测试数量规则（已停用）",
  "TEST-EVIDENCE-001": "实测证据规则（已停用）",
  "内容质量": "内容质量",
  "合规表达": "合规表达",
  "品牌一致性": "品牌一致性",
  "产品准确性": "产品准确性",
  "传播有效性": "传播有效性",
  external: "外部问题",
};
const FIELDS: Record<string, string> = { title: "标题", body: "正文", image: "图片", video: "视频", account_name: "账号名称", platform: "发布平台" };
const PUBLISH: Record<string, string> = { READY: "可发布", NOT_READY: "暂不可发布", PUBLISHED: "已发布" };
const FORMAT: Record<string, string> = { PENDING: "待检查", PASSED: "格式正常", INCOMPLETE: "信息不完整", INVALID: "格式无效" };
const ASSETS: Record<string, string> = { IMAGE: "图片", VIDEO: "视频", SCREENSHOT: "截图", SCREEN_RECORDING: "录屏", TEST_LOG: "测试日志" };
const CONTENT_SOURCES: Record<string, string> = { SUPPLIER: "原稿提交", AI_PROPOSED: "AI 修订建议", HUMAN_CONFIRMED: "人工确认" };
const TASKS: Record<string, string> = { AUTO_FIX_PROPOSAL: "自动修复确认", HUMAN_REVIEW: "人工审核", BLOCK_REVIEW: "阻断复核", SUPPLIER_REVISION: "内容修改" };

export const agentLabel = (value?: string | null) => AGENTS[value || ""] || "评分维度";
export const decisionLabel = (value?: string | null) => DECISIONS[value || ""] || "未给出结论";
export const statusLabel = (value?: string | null) => STATUSES[value || ""] || "未知状态";
export const severityLabel = (value?: string | null) => SEVERITIES[value || ""] || "未知风险";
export const categoryLabel = (value?: string | null) => CATEGORIES[value || ""] || "基础内容校对";
export const fieldLabel = (value?: string | null) => FIELDS[value || ""] || "相关内容";
export const publishStatusLabel = (value?: string | null) => PUBLISH[value || ""] || "未知状态";
export const taskTypeLabel = (value?: string | null) => TASKS[value || ""] || "审核任务";
export const formatStatusLabel = (value?: string | null) => FORMAT[value || ""] || "未知状态";
export const evidenceStatusLabel = (value?: string | null) => ({ PRESENT: "已提供", MISSING: "缺失", NONE: "无测试" }[value || ""] || "未知状态");
export const assetKindLabel = (value?: string | null) => ASSETS[value || ""] || "未知类型";
export const taskStatusLabel = (value?: string | null) => ({ OPEN: "待处理", CLOSED: "已处理" }[value || ""] || "未知状态");
export const contentSourceLabel = (value?: string | null) => CONTENT_SOURCES[value || ""] || "未知来源";
export const sourceLabel = (agentId?: AgentId | string | null) => agentLabel(agentId);

export type AgentDetail = { summary: string; evidence: string; reason: string; suggestion: string; confidence: string; source: string };
export function agentDetail(result: { id?: number; agent_id?: string | null; summary?: string | null }, issues: Issue[]): AgentDetail {
  const issue = issues.find((entry) => entry.agent_result_id === result.id);
  return { summary: result.summary || "未提供评分摘要", evidence: issue?.evidence_quote || "未提供证据摘录", reason: issue?.reason || "未发现结构化问题", suggestion: issue?.suggestion || "暂无修改建议", confidence: issue ? `${Math.round(issue.confidence * 100)}%` : "未评分", source: `维度：${sourceLabel(result.agent_id)}` };
}
