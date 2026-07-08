"""表结构、状态、风险等级常量 —— 全流程的单一可信来源。

与 references/text_review_standards.md 保持一致，修改需同步两处。
"""

# ── 表字段（腾讯文档表头，中文）────────────────────────────
COL_ID = "内容编号"
COL_THEME = "活动主题"
COL_PLATFORM = "平台"
COL_TITLE = "标题"
COL_BODY = "正文"
COL_MEDIA = "图片/视频"
COL_PUBLISH_TIME = "发布时间"
COL_FORMAT_CHECK = "格式校验"
COL_STATUS = "内容状态"
COL_PROBLEM = "问题原因"
COL_SUGGESTION = "修改建议"
COL_MANUAL_CONTENT = "人工修改内容"
COL_FINAL_TITLE = "最终标题"
COL_FINAL_BODY = "最终正文"
COL_ROUND = "审核轮次"          # NEW：重试计数
COL_AUDIT = "审核留痕"          # NEW：审核时间/模型/命中规则/置信度

# 必填字段（流程三 格式校验用）
REQUIRED_FIELDS = [COL_ID, COL_THEME, COL_PLATFORM, COL_TITLE, COL_BODY, COL_PUBLISH_TIME]

ALL_COLUMNS = [
    COL_ID, COL_THEME, COL_PLATFORM, COL_TITLE, COL_BODY, COL_MEDIA, COL_PUBLISH_TIME,
    COL_FORMAT_CHECK, COL_STATUS, COL_PROBLEM, COL_SUGGESTION, COL_MANUAL_CONTENT,
    COL_FINAL_TITLE, COL_FINAL_BODY, COL_ROUND, COL_AUDIT,
]

# ── 格式校验取值 ───────────────────────────────────────────
FMT_EMPTY = ""
FMT_INCOMPLETE = "资料不完整"
FMT_BAD_FORMAT = "格式不合格"
FMT_PASS = "通过"

# ── 内容状态取值 ───────────────────────────────────────────
# 流转/等待态
ST_SUBMITTED = "已提交"          # 待处理，由「格式校验」列决定去向（消歧）
ST_WAIT_HUMAN = "待人工审核"      # 高/中风险、无法判断 → 等管理员
ST_MEDIA_FIX = "图片/视频修改"    # 等供应商换图
ST_WAIT_SUPPLIER = "待供应商补充"  # 格式退回，等供应商补资料（NEW，消除同批重复处理）
ST_NEED_MODIFY = "需修改"        # 低风险文案问题 → 自动改写
# 终态
ST_PASS = "通过"
ST_REJECTED = "已驳回"           # NEW：重试超限/明确拒绝
ST_DELETED = "已删除"           # NEW：管理员删除

WAITING_STATUS = {ST_WAIT_HUMAN, ST_MEDIA_FIX, ST_WAIT_SUPPLIER}
TERMINAL_STATUS = {ST_PASS, ST_REJECTED, ST_DELETED}

# ── 风险等级 ───────────────────────────────────────────────
RISK_LOW = "low"        # 低风险文案问题 → 自动改写
RISK_MID = "mid"        # 中风险 → 只给建议，转人工
RISK_HIGH = "high"      # 高风险 → 只给建议，转人工
RISK_UNKNOWN = "unknown"  # 无法判断 → 转人工
RISK_NONE = "none"      # 无问题 → 直接通过

# ── 媒体类型 ───────────────────────────────────────────────
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}

# ── 重试上限 ───────────────────────────────────────────────
MAX_ROUNDS = 3  # 供应商反复退回超过该次数 → 已驳回

# ── 审核维度（多专项子 agent）───────────────────────────────
# key -> (中文名, 全局标准文件名)
DIMENSIONS = {
    "compliance": "合规/广告法",
    "brand": "品牌一致性",
    "accuracy": "内容准确性",
    "quality": "内容质量",
    "external": "舆情与授权",
}
DIMENSION_FILES = {
    "compliance": "合规_广告法.md",
    "brand": "品牌一致性.md",
    "accuracy": "内容准确性.md",
    "quality": "内容质量.md",
    "external": "舆情与授权.md",
}
