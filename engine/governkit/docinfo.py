"""单文件内容与元信息、指针聚合、文档清单：给插件 UI 的「内容 + 元数据 + 指针」只读入口。

上游：cli.py 的 show/refs/docs 子命令传入 Repo 与合并后的配置。
下游：只产出 dict/str（JSON 模式由 cli 包进 data），不写文件、不改仓库状态。
约束：纯函数——只读仓库与 git 历史；纯 stdlib、Python>=3.10。
指针状态四态：ok（目录相对命中）/qualified（根相对命中）/under（仅文件名命中）/dead（不存在）。
缓存：show 按 (rel,offset,limit + 源文件 mtime_ns+size) 缓存，git_last 单独按 mtime 缓存，
docs/refs/board 走整仓聚合缓存（TTL 20 秒）；命中即免慢 IO 磁盘读，源文件一改即失效。
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import stat as stat_mod
import subprocess
from typing import Dict, List, Optional, Tuple

from . import cache
from .core import Repo, board_paths
from .rules import Resolver, _extract_refs, parse_board

MD_SUFFIX = ".md"
DEFAULT_LIMIT = 120

# 敏感文件：环境变量、私钥、本机配置与凭据导出，一律拒绝读
SENSITIVE_NAMES = {".env", "agents.local.md"}
SENSITIVE_SUFFIXES = (".pem", ".key")
SENSITIVE_PREFIXES = ("credentials",)


def _norm_rel(rel: str) -> str:
    """相对路径归一化（POSIX、去 ./ 与 ..），空输入返回空串。"""
    raw = (rel or "").strip().replace("\\", "/")
    if not raw:
        return ""
    norm = posixpath.normpath(raw)
    return "" if norm == "." else norm


def _is_sensitive(name: str) -> bool:
    """单个路径段是否敏感（大小写不敏感）。"""
    low = name.lower()
    if low in SENSITIVE_NAMES or low.startswith(".env."):
        return True
    if any(low.endswith(sfx) for sfx in SENSITIVE_SUFFIXES):
        return True
    return any(low.startswith(pfx) for pfx in SENSITIVE_PREFIXES)


def _is_sensitive_rel(rel: str) -> bool:
    """整条相对路径是否命中敏感项（任一段敏感即算）。"""
    return any(_is_sensitive(seg) for seg in rel.split("/") if seg and seg != ".")


def resolve_rel(repo: Repo, rel: str) -> Tuple[str, Optional[str]]:
    """规范化并做路径安全校验。

    返回 (规范化相对路径, 错误信息)；错误非空表示空路径、越界或敏感文件。
    """
    norm = _norm_rel(rel)
    if not norm:
        return "", "路径不能为空"
    if norm.startswith("/") or norm == ".." or norm.startswith("../"):
        return norm, f"路径越界：{norm}"
    root = repo.root.resolve()
    try:
        real = (root / norm).resolve()
    except OSError as exc:
        return norm, f"路径不可解析：{norm}（{exc}）"
    try:
        real.relative_to(root)
    except ValueError:
        return norm, f"路径越界：{norm}"
    real_rel = str(real.relative_to(root)).replace(os.sep, "/")
    if _is_sensitive_rel(norm) or _is_sensitive_rel(real_rel):
        return norm, f"敏感文件拒绝读取：{norm}"
    return norm, None


def _tier_prefixes(cfg: dict, key: str) -> List[str]:
    vals = (cfg.get("tiers") or {}).get(key) or []
    out: List[str] = []
    for val in vals:
        if isinstance(val, str) and val.strip():
            norm = _norm_rel(val)
            if norm:
                out.append(norm)
    return out


def tier_of(rel: str, cfg: dict) -> str:
    """文档分级：ai / human / none（按 tiers.ai_read、tiers.human_read 前缀）。"""
    if not rel:
        return "none"
    for key, code in (("ai_read", "ai"), ("human_read", "human")):
        for prefix in _tier_prefixes(cfg, key):
            if rel == prefix or rel.startswith(prefix.rstrip("/") + "/"):
                return code
    return "none"


def extract_refs(repo: Repo, rel: str, resolver: Optional[Resolver] = None) -> List[dict]:
    """抽出文件内指针；仅 .md 提取，状态与解析路径复用 rules.Resolver。"""
    if not rel.endswith(MD_SUFFIX):
        return []
    resolver = resolver or Resolver(repo)
    out: List[dict] = []
    for line, target in _extract_refs(repo.read_text(rel)):
        status, resolved = resolver.resolve_detail(rel, target)
        out.append({
            "line": line,
            "target": target,
            "status": status,
            "resolved": resolved,
        })
    return out


def _cfg_fingerprint(cfg: dict) -> str:
    """配置指纹：聚合结果依赖配置，配置一改缓存必须失效。"""
    try:
        raw = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = repr(sorted(cfg))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _file_stamp(repo: Repo, rel: str) -> Optional[str]:
    """源文件指纹 "mtime_ns:size"；不可 stat 时返回 None。"""
    try:
        st = repo.abs(rel).stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"


def git_last_commit(repo: Repo, rel: str, stamp: Optional[str] = None) -> str:
    """该文件最后一次提交日期（YYYY-MM-DD）；无 git 或无历史返回空串。

    按 (rel, mtime_ns+size) 单独缓存：翻页与切文件不必反复 fork git。
    """
    if stamp is None:
        stamp = _file_stamp(repo, rel)
    key = f"{rel}|{stamp}" if stamp else rel
    cached = cache.get(repo.root, cache.SECTION_GIT, key)
    if isinstance(cached, str):
        return cached
    value = ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo.root), "log", "-1",
             "--format=%ad", "--date=short", "--", rel],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        proc = None
    if proc is not None and proc.returncode == 0:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line:
                value = line
                break
    cache.put(repo.root, cache.SECTION_GIT, key, value)
    return value


def _board_tasks(repo: Repo, cfg: dict) -> List[dict]:
    """进度板条目（整仓聚合缓存，TTL 20 秒）：show 取 task 时不必重读进度板。"""
    board_path, tasks_dir = board_paths(repo, cfg)
    key = f"board|{_cfg_fingerprint(cfg)}|{board_path}|{tasks_dir}|{repo.summary_hash()}"
    cached = cache.get(repo.root, cache.SECTION_AGG, key)
    if isinstance(cached, list):
        return cached
    tasks = parse_board(repo, cfg)
    cache.put(repo.root, cache.SECTION_AGG, key, tasks)
    return tasks


def find_task(repo: Repo, cfg: dict, rel: str) -> Optional[dict]:
    """该文件命中进度板任务时返回任务字段，否则 None。"""
    for task in _board_tasks(repo, cfg):
        if task.get("file") != rel:
            continue
        return {
            "title": task.get("title", ""),
            "status": task.get("status", ""),
            "owner": task.get("owner", ""),
            "updated": task.get("updated", ""),
            "goal": task.get("goal", ""),
            "evidence": task.get("evidence", ""),
            "doc": task.get("doc", ""),
        }
    return None


def build_show(repo: Repo, cfg: dict, rel: str, offset: int = 1,
               limit: int = DEFAULT_LIMIT) -> dict:
    """单文件视图：内容分页 + 元信息 + 文件内指针 + 关联任务。

    越界或敏感路径只回元信息与 error 字段，不读内容。
    命中持久缓存时不读源文件（键含 mtime_ns+size，源文件一改即失效）。
    """
    norm, err = resolve_rel(repo, rel)
    start = max(1, int(offset or 1))
    data: Dict = {
        "path": norm or (rel or "").strip(),
        "exists": False,
        "tier": tier_of(norm, cfg),
        "lines_total": 0,
        "git_last": "",
        "offset": start,
        "next": None,
        "content": [],
        "refs": [],
        "task": None,
    }
    if err:
        data["error"] = err
        return data
    if limit <= 0:
        limit = DEFAULT_LIMIT
    try:
        st = repo.abs(norm).stat()
    except OSError:
        return data
    if not stat_mod.S_ISREG(st.st_mode):
        return data
    data["exists"] = True
    stamp = f"{st.st_mtime_ns}:{st.st_size}"
    key = f"{norm}|{start}|{limit}|{stamp}|{repo.summary_hash()}"
    payload = cache.get(repo.root, cache.SECTION_SHOW, key)
    if not isinstance(payload, dict):
        lines = repo.read_text(norm).splitlines()
        chunk = lines[start - 1: start - 1 + limit]
        end = start - 1 + len(chunk)
        payload = {
            "lines_total": len(lines),
            "content": chunk,
            "next": end + 1 if end < len(lines) else None,
            "refs": extract_refs(repo, norm),
        }
        cache.put(repo.root, cache.SECTION_SHOW, key, payload)
    data["lines_total"] = payload.get("lines_total", 0)
    data["content"] = payload.get("content", [])
    data["next"] = payload.get("next")
    data["refs"] = payload.get("refs", [])
    data["git_last"] = git_last_commit(repo, norm, stamp=stamp)
    data["task"] = find_task(repo, cfg, norm)
    return data


def scope_files(repo: Repo, cfg: dict) -> List[str]:
    """指针聚合范围：cfg.agents.glob 命中的 AGENTS.md + cfg.indexes.files（只取 .md）。"""
    glob = (cfg.get("agents") or {}).get("glob") or "AGENTS.md"
    cands = set()
    for rel in repo.match_glob(glob):
        if rel.endswith(MD_SUFFIX):
            cands.add(rel)
    for rel in (cfg.get("indexes") or {}).get("files") or []:
        if not isinstance(rel, str) or not rel.strip():
            continue
        norm = _norm_rel(rel)
        if norm and norm.endswith(MD_SUFFIX) and repo.exists(norm):
            cands.add(norm)
    return sorted(cands)


def build_refs(repo: Repo, cfg: dict) -> List[dict]:
    """指针聚合：只列含指针的文件，逐条给来源行、目标、状态与解析路径。

    整仓聚合缓存（TTL 20 秒）：键含配置指纹与仓库索引摘要，配置或文件列表变了即失效。
    """
    key = f"refs|{_cfg_fingerprint(cfg)}|{repo.summary_hash()}"
    cached = cache.get(repo.root, cache.SECTION_AGG, key)
    if isinstance(cached, list):
        return cached
    resolver = Resolver(repo)
    out: List[dict] = []
    for rel in scope_files(repo, cfg):
        refs = extract_refs(repo, rel, resolver)
        if refs:
            out.append({"path": rel, "refs": refs})
    cache.put(repo.root, cache.SECTION_AGG, key, out)
    return out


def build_docs(repo: Repo, cfg: dict) -> List[dict]:
    """文档清单：全部 .md 的分级、行数、指针数与失效指针数（按路径排序）。

    整仓聚合缓存（TTL 20 秒）：键含配置指纹与仓库索引摘要。
    """
    key = f"docs|{_cfg_fingerprint(cfg)}|{repo.summary_hash()}"
    cached = cache.get(repo.root, cache.SECTION_AGG, key)
    if isinstance(cached, list):
        return cached
    resolver = Resolver(repo)
    out: List[dict] = []
    for rel in sorted(repo.files):
        if not rel.endswith(MD_SUFFIX):
            continue
        refs = extract_refs(repo, rel, resolver)
        out.append({
            "path": rel,
            "tier": tier_of(rel, cfg),
            "lines": len(repo.read_text(rel).splitlines()),
            "refs": len(refs),
            "dead": sum(1 for r in refs if r["status"] == "dead"),
        })
    cache.put(repo.root, cache.SECTION_AGG, key, out)
    return out


def render_show(data: dict) -> str:
    """show 的可读文本输出（JSON 模式外用）。"""
    out: List[str] = [
        f"path: {data['path']}",
        f"exists: {data['exists']}  tier: {data['tier']}  "
        f"lines_total: {data['lines_total']}  git_last: {data['git_last'] or '-'}",
        f"offset: {data['offset']}  next: {data['next'] if data['next'] is not None else '-'}",
    ]
    if data.get("error"):
        out.append(f"error: {data['error']}")
        return "\n".join(out)
    if not data["exists"]:
        out.append("（文件不存在）")
        return "\n".join(out)
    out.append("--- content ---")
    out.extend(data["content"])
    out.append("--- refs ---")
    if data["refs"]:
        for ref in data["refs"]:
            out.append(f"L{ref['line']} {ref['target']} [{ref['status']}] -> "
                       f"{ref['resolved'] or '-'}")
    else:
        out.append("（无）")
    out.append("--- task ---")
    task = data["task"]
    if task:
        for key in ("title", "status", "owner", "updated", "goal", "evidence", "doc"):
            out.append(f"{key}: {task[key]}")
    else:
        out.append("（无）")
    return "\n".join(out)


def render_refs(files: List[dict]) -> str:
    """refs 的可读文本输出。"""
    if not files:
        return "（无指针）"
    out: List[str] = []
    for item in files:
        out.append(item["path"])
        for ref in item["refs"]:
            out.append(f"  L{ref['line']} {ref['target']} [{ref['status']}] -> "
                       f"{ref['resolved'] or '-'}")
    return "\n".join(out)


def render_docs(docs: List[dict]) -> str:
    """docs 的可读文本输出。"""
    if not docs:
        return "（无文档）"
    out: List[str] = []
    for item in docs:
        out.append(f"{item['path']}  tier={item['tier']}  lines={item['lines']}  "
                   f"refs={item['refs']}  dead={item['dead']}")
    return "\n".join(out)
