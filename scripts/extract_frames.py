#!/usr/bin/env python3
"""
视频关键帧抽取脚本（基于 opencv-python，不依赖系统 ffmpeg 二进制）。

用法：
  python3 extract_frames.py --video <video_path> --out-dir <dir> [--interval 5]

输出：
  stdout 打印 JSON: {"frames": ["<dir>/frame_0000.jpg", ...], "duration_sec": 123.4, "fps": 25.0}

设计说明：
  - 按 --interval 秒为间隔均匀抽帧，用于自动审核抽样，不追求逐帧覆盖
  - 视频时长 > 600 秒时自动放宽间隔到 max(interval, 10)，避免抽帧数量过多
"""
import argparse
import json
import os
import sys

import cv2


def main():
    parser = argparse.ArgumentParser(description="视频关键帧抽取")
    parser.add_argument("--video", required=True, help="视频文件路径")
    parser.add_argument("--out-dir", required=True, help="关键帧输出目录")
    parser.add_argument("--interval", type=float, default=5.0, help="抽帧间隔（秒）")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(json.dumps({"error": f"无法打开视频文件: {args.video}"}), file=sys.stderr)
        sys.exit(1)

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration_sec = total_frames / fps if fps else 0.0

    interval = args.interval
    if duration_sec > 600:
        interval = max(interval, 10.0)

    frame_paths = []
    t = 0.0
    idx = 0
    while t < duration_sec or idx == 0:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            break
        frame_path = os.path.join(args.out_dir, f"frame_{idx:04d}.jpg")
        cv2.imwrite(frame_path, frame)
        frame_paths.append(frame_path)
        idx += 1
        t += interval
        if idx > 200:  # 安全上限，避免异常长视频耗尽资源
            break

    cap.release()

    print(json.dumps({
        "frames": frame_paths,
        "duration_sec": round(duration_sec, 1),
        "fps": round(fps, 2),
        "interval_used": interval,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
