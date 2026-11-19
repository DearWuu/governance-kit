"""迷你仓 fixture 构造辅助（tests 共用）。

上游：test_governkit.py 在 setUp 里调 new_dir()/build_mini()。
下游：直接构造 engine/governkit 的 Repo 视图与 run_all 结果，供单元级断言。
约束：只写 /tmp，纯 stdlib；不触碰引擎实现。
"""
from __future__ import annotations

import atexit
import datetime as _dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 引擎持久缓存隔离：测试跑在会话级临时缓存目录，不污染系统临时目录
# （外部已设 GOVERN_CACHE_DIR 时以外部为准）
_CACHE_HOME = tempfile.mkdtemp(prefix="governkit-cache-home-")
os.environ.setdefault("GOVERN_CACHE_DIR", _CACHE_HOME)
atexit.register(lambda: shutil.rmtree(_CACHE_HOME, ignore_errors=True))

# 引擎以 namespace package 形式放在 engine/ 下（无 __init__.py），加入 sys.path 即可导入
ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from governkit import rules  # noqa: E402
from governkit.core import (  # noqa: E402
    DEFAULT_CONFIG,
    Finding,
    Repo,
    load_config,
)

__all__ = [
    "DEFAULT_CONFIG",
    "ENGINE_DIR",
    "Finding",
    "Repo",
    "build_mini",
    "by_rule",
    "drop",
    "findings",
    "frontmatter",
    "load_config",
    "new_dir",
    "rules",
    "set_config",
    "view",
    "write",
    "write_json",
    "write_lines",
]


def new_dir(prefix: str = "governkit-") -> Path:
    """在 /tmp 下建临时目录（不自动清理，交由 addCleanup）。"""
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


def drop(path) -> None:
    shutil.rmtree(Path(path), ignore_errors=True)


def write(root, rel: str, text: str = "") -> Path:
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def write_lines(root, rel: str, count: int,
                template: str = "第 {i} 行：只写规则与指针，不写叙事。") -> Path:
    body = "\n".join(template.format(i=i) for i in range(1, count + 1))
    return write(root, rel, body + "\n")


def write_json(root, rel: str, data) -> Path:
    return write(root, rel, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def set_config(root, data: dict) -> Path:
    return write_json(root, ".governance/config.json", data)


def view(root):
    """返回 (Repo, cfg)：与 CLI 同路径（Repo(root) + load_config(root)）。"""
    root = Path(root)
    return Repo(root), load_config(root)


def findings(root, skip_adapters: bool = True):
    repo, cfg = view(root)
    return rules.run_all(repo, cfg, skip_adapters=skip_adapters)


def by_rule(items, rule: str):
    return [f for f in items if f.rule == rule]


def frontmatter(**kv) -> str:
    """拼任务文件 frontmatter；值为空字符串时输出 `key:` 形式。"""
    lines = ["---"]
    for key, val in kv.items():
        lines.append(f"{key}: {val}")
    lines += ["---", "", "正文。", ""]
    return "\n".join(lines)


def _iso(days_ago: int = 0) -> str:
    return (_dt.date.today() - _dt.timedelta(days=days_ago)).isoformat()


def build_mini(prefix: str = "governkit-cli-") -> Path:
    """CLI 用的迷你仓：AGENTS/文档索引/方案/进度板/任务文件/源码/技能副本齐全。

    刻意保持 scan 干净（无 error/warning），便于把 CLI 断言集中在契约本身。
    """
    root = new_dir(prefix)
    today = _iso(0)

    write(root, "AGENTS.md", "\n".join([
        "# 根索引",
        "",
        "- 文档索引：[docs/README.md](docs/README.md)",
        "- 方案正文：`docs/方案/a.md`",
        "- 进度板：`进度板.md`",
        "",
    ]))

    write(root, "docs/README.md", "\n".join([
        "# 文档索引",
        "",
        "- 方案 a：docs/方案/a.md",
        "- 方案 long：docs/方案/long.md",
        "- 进度板：进度板.md",
        "- 进度目录：进度",
        "",
    ]))

    write(root, "docs/方案/a.md", "\n".join([
        "# 方案 A",
        "",
        "只讲原理与取舍，正文不堆代码实证。",
        "",
        "## 背景",
        "",
        "服务侧需要一份可追溯的索引。",
        "",
    ]))

    write(root, "docs/方案/long.md",
          "\n".join(f"L{i:03d} 正文行，用于 view 截断断言。" for i in range(1, 51)) + "\n")

    write(root, "进度板.md", "\n".join([
        "# 进度板",
        "",
        "## [待办] 待启动的条目",
        "- 负责：bob",
        f"- 更新：{today}",
        "",
        "## [部分完成] 进行中的条目",
        "- 负责：carol",
        f"- 更新：{today}",
        "",
    ]))

    write(root, "进度/task-ok.md", frontmatter(
        title="示例任务",
        status="待验收",
        owner="alice",
        updated=today,
        goal="验证进度板渲染与 schema",
        evidence="",
        doc="docs/方案/a.md",
    ))

    write(root, "src/app.py", "\n".join([
        "def alpha_func(x):",
        "    return x + 1",
        "",
        "",
        "class BetaClass:",
        "    def method_one(self):",
        "        return alpha_func(1)",
        "",
    ]))

    write(root, "src/api_route.py", "\n".join([
        "from app import alpha_func",
        "",
        "",
        "def route():",
        "    return alpha_func(2)",
        "",
    ]))

    write(root, "skills/SKILL.md", "# 技能正文\n")
    write(root, "plugins/skill-copy/SKILL.md", "# 技能正文\n")

    set_config(root, {
        "indexes": {
            "files": ["docs/README.md"],
            "dirs": ["docs/方案"],
        },
        "tiers": {"human_read": ["docs/方案"]},
        "skills": {
            "canonical": "skills",
            "copies": ["plugins/skill-copy"],
        },
    })
    return root