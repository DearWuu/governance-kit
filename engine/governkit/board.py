"""进度板渲染：按状态分组输出可读文本与 JSON。

上游：cli.py 的 board 子命令传入 rules.parse_board() 的结果。
下游：htmlgen.py 复用 group_tasks() 生成 HTML 进度板区块。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

# 展示顺序：活跃状态在前，已完成沉底；未在表内的状态归入「其他」。
STATUS_ORDER = ["进行中", "待验收", "待启动", "挂起", "已完成"]
OTHER_STATUS = "其他"

TASK_FIELDS = (
    "file", "line", "title", "status", "raw_status",
    "owner", "updated", "goal", "evidence", "doc", "mode",
    "checklist",
)


def group_tasks(tasks: List[dict], status_filter: Optional[str] = None) -> List[dict]:
    """按状态分组；未知状态排到最后，组内按标题排序，保证确定性。"""
    buckets: Dict[str, List[dict]] = {}
    for task in tasks:
        status = task.get("status") or "(未标注)"
        if status_filter is not None and status != status_filter:
            continue
        buckets.setdefault(status, []).append(task)

    order = {name: idx for idx, name in enumerate(STATUS_ORDER)}

    def sort_key(status: str) -> tuple:
        return (order.get(status, len(STATUS_ORDER)), status)

    groups: List[dict] = []
    for status in sorted(buckets, key=sort_key):
        items = sorted(
            buckets[status],
            key=lambda t: (
                t.get("title", ""), t.get("file", ""), int(t.get("line", 0) or 0),
            ),
        )
        groups.append({"status": status, "count": len(items), "tasks": items})
    return groups


def _badges(task: dict) -> str:
    parts = []
    owner = task.get("owner", "")
    parts.append(f"负责:{owner}" if owner else "负责:未指派")
    updated = task.get("updated", "")
    parts.append(f"更新:{updated}" if updated else "更新:未知")
    evidence = task.get("evidence", "")
    if evidence:
        compact = evidence.replace("\n", " ").strip()
        if len(compact) > 60:
            compact = compact[:57] + "..."
        parts.append(f"证据:{compact}")
    else:
        parts.append("证据:无")
    return " · ".join(parts)


def _loc(task: dict) -> str:
    line = int(task.get("line", 0) or 0)
    return f"{task.get('file', '')}:{line}" if line else str(task.get("file", ""))


def render_board(tasks: List[dict], status_filter: Optional[str] = None) -> str:
    """按状态分组的可读文本，含 owner/updated/evidence 徽标。"""
    groups = group_tasks(tasks, status_filter)
    total = sum(g["count"] for g in groups)
    head = f"进度板：{total} 项"
    if status_filter is not None:
        head += f"（筛选状态：{status_filter}）"
    out: List[str] = [head, "=" * 40]
    if not groups:
        out.append("（无匹配条目）" if status_filter is not None else "（进度板为空）")
        return "\n".join(out)
    for group in groups:
        out.append("")
        out.append(f"## {group['status']} ({group['count']})")
        for task in group["tasks"]:
            title = task.get("title", "") or "(无标题)"
            out.append(f"- {title}")
            out.append(f"  {_badges(task)}")
            out.append(f"  位置 {_loc(task)}")
            doc = task.get("doc", "")
            if doc:
                out.append(f"  文档 {doc}")
            if task.get("mode") == "legacy" and task.get("raw_status") not in (None, task.get("status")):
                out.append(f"  原状态 {task.get('raw_status')}")
    return "\n".join(out)


def board_json(tasks: List[dict], status_filter: Optional[str] = None) -> dict:
    """进度板的结构化视图：分组 + 全量字段，供 --json 使用。"""
    groups = group_tasks(tasks, status_filter)
    data_groups = []
    for group in groups:
        items = []
        for task in group["tasks"]:
            item = {
                key: (task.get(key) if task.get(key) is not None else
                      ([] if key == "checklist" else ""))
                for key in TASK_FIELDS
            }
            items.append(item)
        data_groups.append({
            "status": group["status"],
            "count": group["count"],
            "tasks": items,
        })
    return {
        "total": sum(g["count"] for g in data_groups),
        "filter": status_filter,
        "groups": data_groups,
    }


def task_slug(task: dict) -> str:
    """任务文件主名，供 HTML 锚点/展示使用。"""
    return os.path.splitext(os.path.basename(task.get("file", "")))[0]