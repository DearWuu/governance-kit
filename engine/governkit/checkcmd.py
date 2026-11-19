"""进度板打钩：任务文件 checklist 唯一允许的写操作。

上游：cli.py 的 check 子命令传入仓库根、配置、任务文件相对路径、checklist 序号与方向。
下游：rules.parse_board/parse_checklist 定位目标行；cache 失效 show/agg 段。

写操作边界（硬约束）：只翻转 `- [ ]` / `- [x]` 的标记字符，缩进、分隔空白与正文逐字
保留；附带把 frontmatter 的 updated 刷成当天日期（打钩自带的元数据更新）。其余任何
文本都不改，不新增文件，不动其他行。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import cache as cache_mod
from . import core, rules

# frontmatter 里的 updated 字段：只替换值，保留键与缩进
UPDATED_RE = re.compile(r"^(\s*updated\s*:\s*)(.*)$")

# 写后必须失效的缓存段：show（含任务字段）与 agg（含进度板聚合）
INVALIDATE_SECTIONS = (cache_mod.SECTION_SHOW, cache_mod.SECTION_AGG)

DIRECTIONS = ("on", "off", "toggle")


def _error(rel: str, index: int, message: str, line: int = 0,
           updated: str = "") -> Dict:
    return {
        "path": rel, "index": index, "checked": False, "line": line,
        "updated": updated, "error": message,
    }


def _frontmatter_end(text: str, lines: List[str]) -> Optional[int]:
    """frontmatter 闭合行下标（0 起）；无 frontmatter 或未闭合返回 None。"""
    if not text.startswith("---") or not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return None


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace 原子替换。"""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def run_check(root: Path, cfg: dict, rel: str, index: int,
              direction: str = "toggle") -> Dict:
    """翻转任务文件第 index 项 checklist；返回结果字典（失败时带 error）。

    只接受本仓进度板任务文件（task-file 模式）；legacy 单文件板与非任务文件一律报错。
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"方向非法：{direction!r}（只认 {'/'.join(DIRECTIONS)}）")
    repo = core.Repo(root)
    tasks = rules.parse_board(repo, cfg)
    task = next((t for t in tasks if t.get("file") == rel), None)
    if task is None:
        return _error(rel, index, f"不是本仓进度板任务文件：{rel}")
    if task.get("mode") != "task-file":
        return _error(rel, index,
                      f"legacy 单文件进度板不支持打钩：{rel}（改用任务文件模式）")
    checklist = task.get("checklist") or []
    if index < 0 or index >= len(checklist):
        span = f"0–{len(checklist) - 1}" if checklist else "（该文件没有 checklist 项）"
        return _error(rel, index,
                      f"checklist 序号越界：{index}（合法范围 {span}）")

    path = repo.abs(rel)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _error(rel, index, f"读取任务文件失败：{exc}")
    lines = text.splitlines()
    item = checklist[index]
    line_no = int(item.get("line", 0))
    if line_no < 1 or line_no > len(lines):
        return _error(rel, index, "任务文件在解析后被改动，行号失效，请重跑")
    match = rules.CHECKLIST_RE.match(lines[line_no - 1])
    if match is None:
        return _error(rel, index, "任务文件在解析后被改动，该行已不是 checklist 项，请重跑")

    current = match.group(2) != " "
    target = (not current) if direction == "toggle" else (direction == "on")
    if target != current:
        # 只替换标记字符：前缀（含缩进）与后缀（分隔空白 + 正文）逐字保留
        lines[line_no - 1] = match.group(1) + ("x" if target else " ") + match.group(3)

    updated = task.get("updated", "")
    fm_end = _frontmatter_end(text, lines)
    if fm_end is not None:
        today = _dt.date.today().isoformat()
        for i in range(1, fm_end):
            m = UPDATED_RE.match(lines[i])
            if m:
                rest = m.group(2)
                dm = rules.DATE_RE.match(rest)
                lines[i] = (m.group(1) + today + rest[dm.end():] if dm
                            else m.group(1) + today)
                updated = today
                break

    new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    try:
        _atomic_write(path, new_text)
    except OSError as exc:
        return _error(rel, index, f"写入任务文件失败：{exc}", line=line_no,
                      updated=updated)

    # 源文件变了但索引摘要哈希不变：显式失效 show/agg，保证 board 立即反映
    cache_mod.invalidate(root, INVALIDATE_SECTIONS)

    return {
        "path": rel, "index": index, "checked": target, "line": line_no,
        "updated": updated,
    }
