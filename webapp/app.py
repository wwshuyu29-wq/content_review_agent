#!/usr/bin/env python3
"""
内容审核 Web 看板：真实可交互的审核网页，集成供应商上传入口。

设计说明：
  - 复用现有 CLI 脚本的核心逻辑（queue.jsonl 读写、决策规则、状态机），
    不重新实现业务逻辑，Flask 层只做路由 + 渲染，保证 CLI 和网页两种入口
    看到的是同一份数据、同一套规则。
  - 用户身份：页面顶部手动输入工号（reviewer_id），存 localStorage，
    不做真实 SSO 鉴权（沙箱环境无法接入内网 SSO，需用户后续按需接入）。
  - 图片安全策略：仅 auto_passed / human_approved 状态的内容允许通过
    /media/<content_id> 路由访问原图；其余状态返回 403，避免未审核/
    高风险内容通过网页直接曝光原图，与 sync_dashboard.py 的看板策略保持一致。
  - 供应商上传入口（/upload）：公网可访问的免费入口，供应商上传图片/视频后，
    文件保存到隔离的 upload_dir，并直接进入 queue.jsonl（status: queued_for_review）。
    供应商无法访问内网 BOS，内网也不直接暴露存储凭证，满足两侧隔离要求。

运行：
  python3 webapp/app.py --queue /path/to/queue.jsonl --port 5000

依赖：flask（pip install flask）
"""
import argparse
import json
import os
import sys
import time
import uuid

from flask import Flask, jsonify, request, send_file, abort, render_template

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))

from auto_review import decide_verdict, HIGH_RISK  # noqa: E402

APPROVED_STATUS = {"auto_passed", "human_approved"}
HUMAN_TERMINAL_STATUS = {"human_approved", "human_rejected"}

app = Flask(__name__)
app.config["QUEUE_PATH"] = None
app.config["UPLOAD_DIR"] = None

# 供应商上传允许的文件类型
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".m4v"}
ALLOWED_EXT = ALLOWED_IMAGE_EXT | ALLOWED_VIDEO_EXT
MAX_UPLOAD_MB = 200  # 单文件最大 200MB


def load_queue():
    path = app.config["QUEUE_PATH"]
    records = []
    if not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def save_queue(records):
    path = app.config["QUEUE_PATH"]
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def find_record(records, content_id):
    for r in records:
        if r["content_id"] == content_id:
            return r
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload")
def upload_page():
    """供应商上传入口页面（公网可访问，无需内网账号）。"""
    return render_template("upload.html")


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """供应商直传接口：接收文件 + 元信息，写入隔离存储，加入审核队列。"""
    upload_dir = app.config.get("UPLOAD_DIR")
    if not upload_dir:
        return jsonify({"error": "服务器未配置上传目录"}), 500

    supplier_id = (request.form.get("supplier_id") or "").strip()
    if not supplier_id:
        return jsonify({"error": "supplier_id 为必填项"}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "未收到文件"}), 400

    _, ext = os.path.splitext(file.filename.lower())
    if ext not in ALLOWED_EXT:
        return jsonify({"error": f"不支持的文件格式 {ext}，仅支持 jpg/png/webp/mp4/mov/m4v"}), 400

    content_type = "image" if ext in ALLOWED_IMAGE_EXT else "video"
    content_id = uuid.uuid4().hex[:12]
    safe_filename = f"{content_id}{ext}"
    save_path = os.path.join(upload_dir, safe_filename)

    # 流式写入，同时检查大小
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    total = 0
    try:
        with open(save_path, "wb") as f:
            while True:
                chunk = file.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    f.close()
                    os.remove(save_path)
                    return jsonify({"error": f"文件超过大小上限 {MAX_UPLOAD_MB}MB"}), 413
                f.write(chunk)
    except OSError as e:
        return jsonify({"error": f"文件保存失败: {e}"}), 500

    record = {
        "content_id": content_id,
        "supplier_id": supplier_id,
        "content_type": content_type,
        "source_url": f"[supplier_direct_upload] {file.filename}",
        "submit_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "intake_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "local_path": save_path,
        "status": "queued_for_review",
    }

    queue_path = app.config["QUEUE_PATH"]
    with open(queue_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return jsonify({
        "content_id": content_id,
        "status": "queued_for_review",
        "message": "上传成功，内容已进入审核队列"
    })


@app.route("/api/records")
def api_records():
    """看板列表：按 status 过滤，默认全量。"""
    status_filter = request.args.get("status")
    records = load_queue()
    if status_filter:
        records = [r for r in records if r.get("status") == status_filter]
    # 列表视图不返回 local_path（服务器本地路径无访问价值，且避免信息泄露）
    for r in records:
        r.pop("local_path", None)
    records.sort(key=lambda r: r.get("intake_time", ""), reverse=True)
    return jsonify({"count": len(records), "records": records})


@app.route("/api/records/<content_id>")
def api_record_detail(content_id):
    records = load_queue()
    r = find_record(records, content_id)
    if not r:
        return jsonify({"error": "not found"}), 404
    r = dict(r)
    r.pop("local_path", None)
    return jsonify(r)


@app.route("/media/<content_id>")
def media(content_id):
    """原图/视频访问：仅已通过状态放行，其余一律 403。"""
    records = load_queue()
    r = find_record(records, content_id)
    if not r:
        abort(404)
    if r.get("status") not in APPROVED_STATUS:
        abort(403, description="内容尚未通过审核，看板不展示原图，避免未审核/高风险内容曝光")
    local_path = r.get("local_path")
    if not local_path or not os.path.exists(local_path):
        abort(404, description="本地缓存文件已不存在，可通过 bos_url 走内网授权访问")
    return send_file(local_path)


@app.route("/api/human-queue")
def api_human_queue():
    """待人工审核列表（全员可见，用于认领）。"""
    records = load_queue()
    pending = [r for r in records if r.get("status") == "needs_human"]
    for r in pending:
        r.pop("local_path", None)
    return jsonify({"count": len(pending), "records": pending})


@app.route("/api/claim", methods=["POST"])
def api_claim():
    data = request.get_json(force=True) or {}
    content_id = data.get("content_id")
    reviewer = (data.get("reviewer") or "").strip()
    if not content_id or not reviewer:
        return jsonify({"error": "content_id 和 reviewer 均为必填"}), 400

    records = load_queue()
    r = find_record(records, content_id)
    if not r:
        return jsonify({"error": "未找到该记录"}), 404
    if r.get("status") != "needs_human":
        return jsonify({"error": f"记录当前状态为 {r.get('status')}，不是待人工审核状态"}), 409
    if r.get("claimed_by") and r.get("claimed_by") != reviewer:
        return jsonify({"error": f"已被 {r['claimed_by']} 认领，认领时间 {r.get('claim_time')}"}), 409

    r["claimed_by"] = reviewer
    r["claim_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_queue(records)
    return jsonify({"content_id": content_id, "claimed_by": reviewer})


@app.route("/api/release", methods=["POST"])
def api_release():
    """认领人自己主动释放认领（如临时处理不了），不改变 needs_human 状态。"""
    data = request.get_json(force=True) or {}
    content_id = data.get("content_id")
    reviewer = (data.get("reviewer") or "").strip()

    records = load_queue()
    r = find_record(records, content_id)
    if not r:
        return jsonify({"error": "未找到该记录"}), 404
    if r.get("claimed_by") != reviewer:
        return jsonify({"error": f"你不是认领人（当前认领人：{r.get('claimed_by')}），无法释放"}), 403

    r["claimed_by"] = None
    r["claim_time"] = None
    save_queue(records)
    return jsonify({"content_id": content_id, "released": True})


@app.route("/api/submit", methods=["POST"])
def api_submit():
    data = request.get_json(force=True) or {}
    content_id = data.get("content_id")
    reviewer = (data.get("reviewer") or "").strip()
    decision = data.get("decision")
    reason = (data.get("reason") or "").strip()

    if decision not in ("approved", "rejected"):
        return jsonify({"error": "decision 必须是 approved 或 rejected"}), 400
    if not reason:
        return jsonify({"error": "提交人工结论必须填写理由"}), 400

    records = load_queue()
    r = find_record(records, content_id)
    if not r:
        return jsonify({"error": "未找到该记录"}), 404
    if r.get("claimed_by") != reviewer:
        return jsonify({"error": f"未认领该记录（当前认领人：{r.get('claimed_by')}），请先认领"}), 403

    risk_cats = set(r.get("risk_categories") or [])
    if risk_cats & HIGH_RISK and len(reason) < 5:
        return jsonify({"error": "命中高风险类别，理由说明过于简单，请补充具体判断依据"}), 400

    r["human_decision"] = decision
    r["human_reason"] = reason
    r["human_reviewer"] = reviewer
    r["human_decision_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    r["status"] = "human_approved" if decision == "approved" else "human_rejected"
    save_queue(records)
    return jsonify({"content_id": content_id, "status": r["status"]})


@app.route("/api/stats")
def api_stats():
    records = load_queue()
    stats = {}
    for r in records:
        s = r.get("status", "unknown")
        stats[s] = stats.get(s, 0) + 1
    return jsonify({"total": len(records), "by_status": stats})


@app.route("/api/distribute", methods=["POST"])
def api_distribute():
    """内容分发接口：将已通过审核的内容标记为 distributed，记录分发渠道。
    
    适用场景：审核通过的内容需要同步到 CDN / 内容中台 / 推荐系统等下游。
    本接口只做状态流转和元信息记录，实际分发推送需用户自行接入下游系统。
    """
    data = request.get_json(force=True) or {}
    content_id = data.get("content_id")
    operator = (data.get("operator") or "").strip()
    channel = (data.get("channel") or "").strip()  # 分发渠道，如 cdn / cms / feed

    if not content_id:
        return jsonify({"error": "content_id 为必填"}), 400
    if not operator:
        return jsonify({"error": "operator（操作人工号）为必填"}), 400

    records = load_queue()
    r = find_record(records, content_id)
    if not r:
        return jsonify({"error": "未找到该记录"}), 404
    if r.get("status") not in APPROVED_STATUS:
        return jsonify({"error": f"内容状态为 {r.get('status')}，未通过审核，无法分发"}), 409

    r["distributed"] = True
    r["distribute_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    r["distribute_operator"] = operator
    r["distribute_channel"] = channel or "default"
    save_queue(records)

    return jsonify({
        "content_id": content_id,
        "distributed": True,
        "distribute_channel": r["distribute_channel"],
        "message": "已标记为分发，请在下游系统完成实际推送"
    })


def main():
    parser = argparse.ArgumentParser(description="内容审核 Web 看板")
    parser.add_argument("--queue", required=True, help="queue.jsonl 路径")
    parser.add_argument("--upload-dir", default="/tmp/content-review-uploads",
                        help="供应商上传文件保存目录（应与内网 BOS 隔离），默认 /tmp/content-review-uploads")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0（内网所有人可访问）")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    app.config["QUEUE_PATH"] = os.path.abspath(args.queue)
    app.config["UPLOAD_DIR"] = os.path.abspath(args.upload_dir)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    print(f"审核网页已启动，队列文件: {app.config['QUEUE_PATH']}")
    print(f"供应商上传目录: {app.config['UPLOAD_DIR']}")
    print(f"访问地址: http://<内网IP>:{args.port}/")
    print(f"供应商上传入口: http://<公网IP或域名>:{args.port}/upload")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
