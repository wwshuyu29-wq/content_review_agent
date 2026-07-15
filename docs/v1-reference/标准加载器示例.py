from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class StandardLoadError(RuntimeError):
    """标准文件加载失败。生产环境中应阻止审核任务继续执行。"""


@dataclass(frozen=True)
class LoadedFile:
    path: str
    content: Any
    sha256: str


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_text(path: Path) -> LoadedFile:
    if not path.is_file():
        raise StandardLoadError(f"标准文件不存在：{path}")

    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StandardLoadError(f"标准文件不是 UTF-8：{path}") from exc

    if not text.strip():
        raise StandardLoadError(f"标准文件为空：{path}")

    return LoadedFile(str(path), text, _sha256(raw))


def load_json(path: Path) -> LoadedFile:
    loaded = load_text(path)
    try:
        data = json.loads(loaded.content)
    except json.JSONDecodeError as exc:
        raise StandardLoadError(f"JSON 解析失败：{path}，{exc}") from exc
    return LoadedFile(loaded.path, data, loaded.sha256)


def load_yaml(path: Path) -> LoadedFile:
    loaded = load_text(path)
    try:
        data = yaml.safe_load(loaded.content)
    except yaml.YAMLError as exc:
        raise StandardLoadError(f"YAML 解析失败：{path}，{exc}") from exc
    if not isinstance(data, dict):
        raise StandardLoadError(f"YAML 顶层必须为对象：{path}")
    return LoadedFile(loaded.path, data, loaded.sha256)


def load_agent_config(repo_root: Path) -> dict[str, Any]:
    config_file = repo_root / "config/审核Agent配置.json"
    config = load_json(config_file).content

    agents = config.get("审核Agent", {})
    if not agents:
        raise StandardLoadError("未配置审核 Agent")

    seen_global_files: set[str] = set()
    for agent_id, agent in agents.items():
        global_path = agent.get("全局标准")
        if not global_path:
            raise StandardLoadError(f"Agent {agent_id} 未配置全局标准")
        if global_path in seen_global_files:
            raise StandardLoadError(f"多个 Agent 重复使用同一全局标准：{global_path}")
        seen_global_files.add(global_path)

        load_text(repo_root / global_path)

        for source in agent.get("项目资料", []):
            path = repo_root / source
            if path.suffix.lower() in {".yaml", ".yml"}:
                load_yaml(path)
            elif path.suffix.lower() == ".json":
                load_json(path)
            else:
                load_text(path)

        prompt = agent.get("Prompt")
        schema = agent.get("输出结构")
        if prompt:
            load_text(repo_root / prompt)
        if schema:
            load_json(repo_root / schema)

    load_json(repo_root / config["确定性规则"])
    load_json(repo_root / config["术语词典"])
    load_json(repo_root / config["自动替换规则"])

    return config


def build_standard_snapshot(repo_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """生成审核任务应绑定的标准文件快照信息。"""
    paths: set[str] = {
        config["确定性规则"],
        config["术语词典"],
        config["自动替换规则"],
    }

    for agent in config["审核Agent"].values():
        paths.add(agent["全局标准"])
        paths.update(agent.get("项目资料", []))
        if agent.get("条件性补充标准"):
            paths.add(agent["条件性补充标准"])
        paths.add(agent["Prompt"])
        paths.add(agent["输出结构"])

    files = []
    for relative_path in sorted(paths):
        path = repo_root / relative_path
        loaded = load_text(path)
        files.append(
            {
                "path": relative_path,
                "sha256": loaded.sha256,
            }
        )

    snapshot_raw = json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")
    snapshot_id = hashlib.sha256(snapshot_raw).hexdigest()

    return {
        "snapshot_id": snapshot_id,
        "files": files,
    }


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    cfg = load_agent_config(root)
    snapshot = build_standard_snapshot(root, cfg)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
