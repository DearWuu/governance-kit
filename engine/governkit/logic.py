"""近期功能实现文档：读写、指针定位、hash 与状态派生。

上游：cli.py 的 logic 子命令、rules.py 的代码逻辑漂移规则。
下游：core.py 的仓库视图（Repo）与 Finding 模型。
约束：纯 stdlib；JSON 统一 UTF-8/LF/2 空格缩进/末尾换行；写文件走原子替换。
"""
from __future__ import annotations

import ast
import datetime as _dt
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from . import core

DEFAULT_DIR = ".governance/logic"

# 派生状态优先级：broken > draft > stale > fresh；读取时算，不落盘。
STATUS_ORDER = ("broken", "draft", "stale", "fresh")

# 非 Python 的声明前缀（export/const 等组合写法在前缀组内重复匹配）。
DECL_RE = re.compile(
    r"(?m)^[ \t]*((?:(?:export|def|func|function|class|const|let|var|async|pub|static)\s+)+)"
    r"([A-Za-z_$][\w$]*)"
)


def _today() -> str:
    """当天日期（YYYY-MM-DD）。"""
    return _dt.date.today().isoformat()


def _atomic_write(path: Path, payload: dict) -> None:
    """原子写 JSON：同目录临时文件 + os.replace，读者只见旧或新完整文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _safe_rel(file: str) -> str:
    """仓库相对 POSIX 路径；绝对路径或含 .. 一律拒绝。"""
    raw = (file or "").strip().replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"路径越界或非法：{file!r}")
    rel = pure.as_posix()
    if rel == ".":
        raise ValueError("路径不能为空")
    return rel


def logic_dir(repo, cfg) -> Path:
    """逻辑文档目录：cfg.logic.dir 相对仓库根，缺省 .governance/logic。"""
    value = str((cfg.get("logic") or {}).get("dir", DEFAULT_DIR)).strip()
    return repo.root / _safe_rel(value or DEFAULT_DIR)


def _domain_path(repo, cfg, domain: str) -> Path:
    """域文件路径：<logic dir>/<domain>.json；domain 只允许单层文件名。"""
    name = (domain or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"domain 非法：{domain!r}")
    return logic_dir(repo, cfg) / f"{name}.json"


def _load(path: Path) -> dict:
    """读并补齐默认字段；schema 宽松，多余字段原样保留。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"代码逻辑文件不是对象：{path}")
    data.setdefault("v", 1)
    data.setdefault("domain", path.stem)
    data.setdefault("title", data["domain"])
    data.setdefault("updated", "")
    entries = data.get("entries")
    if not isinstance(entries, list):
        entries = []
    data["entries"] = entries
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry.setdefault("id", "")
        entry.setdefault("title", "")
        entry.setdefault("group", "")
        entry.setdefault("kind", "module")
        entry.setdefault("summary", "")
        entry.setdefault("narrative", "")
        entry.setdefault("flow", [])
        entry.setdefault("pointers", [])
        entry.setdefault("source_hash", "")
        entry.setdefault("ack_hash", "")
        for pointer in entry["pointers"]:
            if not isinstance(pointer, dict):
                continue
            pointer.setdefault("file", "")
            pointer.setdefault("symbol", "")
            pointer.setdefault("line", 0)
            pointer.setdefault("end", 0)
            pointer.setdefault("role", "")
    return data


def _py_symbol_span(text: str, symbol: str) -> Optional[Tuple[int, int]]:
    """AST 定位函数/类/方法；含装饰器起算行，支持 Class.method 与裸名。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    def visit(nodes, prefix: str = ""):
        for node in nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            qualname = f"{prefix}.{node.name}" if prefix else node.name
            if symbol in (node.name, qualname):
                decorators = getattr(node, "decorator_list", []) or []
                start = min([node.lineno] + [d.lineno for d in decorators])
                return start, getattr(node, "end_lineno", node.lineno) or node.lineno
            found = visit(getattr(node, "body", []), qualname)
            if found is not None:
                return found
        return None

    return visit(tree.body)


def resolve_symbol(repo, rel: str, symbol: str) -> Optional[Tuple[int, int]]:
    """定位符号行区间（1 起含端点）；空 symbol 返回整文件；找不到返回 None。"""
    if not repo.exists(rel):
        return None
    text = _read_source_text(repo, rel)
    lines = text.splitlines()
    if not symbol:
        return 1, max(1, len(lines))
    if rel.endswith(".py"):
        span = _py_symbol_span(text, symbol)
        if span is not None:
            return span
        # AST 解析失败（如 PEP 695 泛型、except* 等新语法超出本机解释器版本）或
        # 未命中时退回正则定位，避免把「解析不了」误报成「指针失效」。
    matches = [m for m in DECL_RE.finditer(text) if m.group(2) == symbol]
    if not matches:
        return None
    start = text.count("\n", 0, matches[0].start()) + 1
    end = text.count("\n", 0, matches[1].start()) if len(matches) > 1 else max(1, len(lines))
    return start, max(start, end)


def source_hash(repo, pointers) -> str:
    """摘要（只含内容）：按 pointers 顺序拼 `file\\n<区间原文>`，段间 \\n；无有效段返回 ""。

    口径不含行号：行号移动不改变摘要，因此「仅行号移动」与「内容变更」可区分。
    """
    segments: List[str] = []
    for pointer in pointers or []:
        if not isinstance(pointer, dict):
            continue
        try:
            rel = _safe_rel(str(pointer.get("file") or ""))
        except ValueError:
            continue
        span = resolve_symbol(repo, rel, str(pointer.get("symbol") or ""))
        if span is None:
            continue
        start, end = span
        raw_lines = _read_source_text(repo, rel).splitlines(keepends=True)
        segments.append(f"{rel}\n" + "".join(raw_lines[start - 1:end]))
    if not segments:
        return ""
    return hashlib.sha256("\n".join(segments).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 状态派生

def entry_status(repo, entry: Dict) -> Tuple[str, str]:
    """派生状态（实时计算）：broken > draft > stale > fresh。

    stale 比较「按当前指针位置重算的实时 hash」与 ack_hash：只移动行号、内容未变时
    实时 hash 不变，仍判 fresh；只有源码内容真的变了才判 stale。
    """
    problems: List[str] = []
    for pointer in entry.get("pointers") or []:
        if not isinstance(pointer, dict):
            problems.append("pointer 不是对象")
            continue
        file = str(pointer.get("file") or "")
        symbol = str(pointer.get("symbol") or "")
        label = f"{file}::{symbol}" if symbol else f"{file}（整文件）"
        try:
            rel = _safe_rel(file)
        except ValueError as exc:
            problems.append(f"{label}：{exc}")
            continue
        if not repo.exists(rel):
            problems.append(f"{label}：文件缺失")
            continue
        if symbol and resolve_symbol(repo, rel, symbol) is None:
            problems.append(f"{label}：symbol 定位失败")
    if problems:
        return "broken", "；".join(problems)
    ack = str(entry.get("ack_hash") or "")
    if not ack:
        return "draft", "ack_hash 为空，叙事待复核"
    live = source_hash(repo, entry.get("pointers") or [])
    if live != ack:
        return "stale", f"实时 hash={live} 与 ack_hash={ack} 不一致，叙事待复核"
    return "fresh", "实时 hash 与 ack_hash 一致"


def list_domains(repo, cfg) -> List[dict]:
    """列出全部域（按 domain 排序）；单个 JSON 损坏不抛异常，带 error 项返回。"""
    directory = logic_dir(repo, cfg)
    out: List[dict] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        rel = path.relative_to(repo.root).as_posix()
        try:
            data = _load(path)
        except (OSError, ValueError) as exc:
            out.append({
                "domain": path.stem,
                "file": rel,
                "entries": [],
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        entries = []
        for entry in data["entries"]:
            if not isinstance(entry, dict):
                continue
            status, detail = entry_status(repo, entry)
            entries.append({
                "id": entry.get("id", ""),
                "title": entry.get("title", ""),
                "group": entry.get("group", ""),
                "kind": entry.get("kind", "module"),
                "summary": entry.get("summary", ""),
                "narrative": entry.get("narrative", ""),
                "sections": entry.get("sections") or [],
                "flow": entry.get("flow") or [],
                "pointers": entry.get("pointers") or [],
                "status": status,
                "status_detail": detail,
            })
        out.append({
            "domain": data.get("domain", path.stem),
            "title": data.get("title", ""),
            "updated": data.get("updated", ""),
            "overview": data.get("overview", ""),
            "crosscut": data.get("crosscut") or [],
            "file": rel,
            "entries": entries,
        })
    out.sort(key=lambda item: str(item.get("domain") or ""))
    return out


# ---------------------------------------------------------------- 读写操作

def init_domain(repo, cfg, domain: str, title: str = "") -> Path:
    """新建域骨架文件；已存在抛 ValueError。"""
    path = _domain_path(repo, cfg, domain)
    if path.exists():
        raise ValueError(f"代码逻辑域已存在：{domain}")
    _atomic_write(path, {
        "v": 1,
        "domain": domain,
        "title": title or domain,
        "updated": _today(),
        "entries": [],
    })
    return path


def add_entry(repo, cfg, domain: str, entry_id: str, file: str,
              symbol: str = "", title: str = "", role: str = "",
              group: str = "", kind: str = "module") -> dict:
    """追加条目：定位 line/end 并算 source_hash；ack_hash 留空（待复核）。

    group 为斜杠分隔的分组路径（树状划分），kind 为分类；两者只做展示与检索，
    不参与 source_hash，也不影响 entry_status。
    """
    path = _domain_path(repo, cfg, domain)
    if not path.is_file():
        raise ValueError(f"代码逻辑域不存在：{domain}")
    data = _load(path)
    if any(str(item.get("id")) == entry_id
           for item in data["entries"] if isinstance(item, dict)):
        raise ValueError(f"条目 id 重复：{domain}/{entry_id}")
    rel = _safe_rel(file)
    span = resolve_symbol(repo, rel, symbol)
    line, end = span if span else (0, 0)
    pointer = {"file": rel, "symbol": symbol, "line": line, "end": end, "role": role}
    entry = {
        "id": entry_id,
        "title": title or entry_id,
        "group": group,
        "kind": kind,
        "summary": "",
        "narrative": "",
        "flow": [],
        "pointers": [pointer],
        "source_hash": source_hash(repo, [pointer]),
        "ack_hash": "",
    }
    data["entries"].append(entry)
    _atomic_write(path, data)
    return entry


def _sync_entry(repo, entry: Dict) -> str:
    """重定位单条目指针并重算 source_hash；返回 moved/changed/broken/unchanged。

    action 判定：hash 变 = changed；hash 未变但 line/end 变 = moved；都没变 = unchanged；
    任一指针无法解析 = broken（此时保留上一次 source_hash，不写半截摘要）。
    """
    old_hash = str(entry.get("source_hash") or "")
    old_positions: List[Tuple[int, int]] = []
    broken = False
    for pointer in entry.get("pointers") or []:
        if not isinstance(pointer, dict):
            old_positions.append((0, 0))
            continue
        old_positions.append((int(pointer.get("line") or 0), int(pointer.get("end") or 0)))
        file = str(pointer.get("file") or "")
        symbol = str(pointer.get("symbol") or "")
        try:
            rel = _safe_rel(file)
        except ValueError:
            pointer["line"], pointer["end"] = 0, 0
            broken = True
            continue
        span = resolve_symbol(repo, rel, symbol)
        if span is None:
            pointer["line"], pointer["end"] = 0, 0
            broken = True
            continue
        pointer["line"], pointer["end"] = span
    if broken:
        return "broken"
    pointers = entry.get("pointers") or []
    new_hash = source_hash(repo, pointers)
    entry["source_hash"] = new_hash
    if new_hash != old_hash:
        return "changed"
    moved = any(
        isinstance(pointer, dict)
        and (int(pointer.get("line") or 0), int(pointer.get("end") or 0)) != old
        for pointer, old in zip(pointers, old_positions)
    )
    return "moved" if moved else "unchanged"


def sync(repo, cfg, domain: str = "") -> List[dict]:
    """同步全部（或指定域的）指针行号与 source_hash；不动 narrative/ack_hash/updated。"""
    directory = logic_dir(repo, cfg)
    if domain:
        path = _domain_path(repo, cfg, domain)
        if not path.is_file():
            raise ValueError(f"代码逻辑域不存在：{domain}")
        paths = [path]
    elif directory.is_dir():
        paths = sorted(directory.glob("*.json"))
    else:
        paths = []
    out: List[dict] = []
    for path in paths:
        try:
            data = _load(path)
        except (OSError, ValueError):
            continue
        domain_name = str(data.get("domain") or path.stem)
        touched = False
        for entry in data["entries"]:
            if not isinstance(entry, dict):
                continue
            action = _sync_entry(repo, entry)
            touched = True
            out.append({
                "domain": domain_name,
                "id": str(entry.get("id") or ""),
                "action": action,
            })
        if touched:
            _atomic_write(path, data)
    return out


def accept(repo, cfg, domain: str, entry_id: str) -> dict:
    """复核盖戳：ack_hash 取实时 hash，条目 source_hash 同值，域级 updated=今天。

    条目不存在或 broken 抛 ValueError（叙事不可在指针失效时盖戳）。
    """
    path = _domain_path(repo, cfg, domain)
    if not path.is_file():
        raise ValueError(f"代码逻辑域不存在：{domain}")
    data = _load(path)
    entry = next((item for item in data["entries"]
                  if isinstance(item, dict) and str(item.get("id")) == entry_id), None)
    if entry is None:
        raise ValueError(f"代码逻辑条目不存在：{domain}/{entry_id}")
    status, detail = entry_status(repo, entry)
    if status == "broken":
        raise ValueError(f"broken 条目不能复核：{domain}/{entry_id}：{detail}")
    live = source_hash(repo, entry.get("pointers") or [])
    entry["ack_hash"] = live
    entry["source_hash"] = live
    data["updated"] = _today()
    _atomic_write(path, data)
    return entry


def findings(repo, cfg) -> List[core.Finding]:
    """LOGIC-BROKEN（指针失效，默认 error）与 LOGIC-STALE（叙事过期，默认 warning）。"""
    severity = cfg.get("severity") or {}
    out: List[core.Finding] = []
    directory = logic_dir(repo, cfg)
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        rel = path.relative_to(repo.root).as_posix()
        try:
            data = _load(path)
        except (OSError, ValueError):
            continue
        domain = str(data.get("domain") or path.stem)
        for entry in data["entries"]:
            if not isinstance(entry, dict):
                continue
            status, detail = entry_status(repo, entry)
            entry_id = str(entry.get("id") or "")
            if status == "broken":
                out.append(core.Finding(
                    "LOGIC-BROKEN",
                    str(severity.get("LOGIC-BROKEN", "error")),
                    rel, 0,
                    f"代码逻辑 {domain}/{entry_id} 指针失效：{detail}",
                    "govern logic sync",
                ))
            elif status == "stale":
                out.append(core.Finding(
                    "LOGIC-STALE",
                    str(severity.get("LOGIC-STALE", "warning")),
                    rel, 0,
                    f"代码逻辑 {domain}/{entry_id} 叙事未复核：{detail}",
                    f"govern logic accept {domain} {entry_id}",
                ))
    return out


def _read_source_text(repo, rel: str) -> str:
    """读源码文本：绕开 core.Repo 的进程内文本缓存，保证 hash 跟随磁盘最新内容。

    机械同步依赖「当前字节」判定 changed/moved；进程内缓存会让同一 Repo 内的
    重复读取拿到旧内容，从而漏报代码变更。截断上限与 core.Repo.read_text 一致。
    """
    try:
        data = repo.abs(rel).read_bytes()[:2_000_000]
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")
