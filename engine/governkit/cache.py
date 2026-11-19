"""持久缓存：进程内 + 磁盘两层 JSON 缓存，替慢 IO 存储（/mnt/d 等）兜底。

缓存位置：<系统临时目录>/governance-kit/cache/<仓库路径 sha256[:16]>.json
（环境变量 GOVERN_CACHE_DIR 可整体改写缓存目录，供测试与 CI 隔离。）

失效策略（按 section 独立判定，互不影响）：
  - index：仓库索引（files / by_basename / summary_hash）。TTL 默认 60 秒，
    GOVERN_INDEX_TTL 覆盖；过期整份重算。新文件靠 TTL 自然浮现，不做目录 mtime 判定。
  - show：单文件视图（分页内容 + 文件内指针）。键含源文件 mtime_ns + size 与仓库
    索引摘要哈希——源文件一改即失效；TTL 默认 300 秒（GOVERN_SHOW_TTL）只做回收兜底。
  - git：git_last。键含源文件 mtime_ns + size；TTL 默认 30 秒（GOVERN_GIT_TTL），
    提交之后不必等文件 mtime 变化也能在 TTL 内刷出新日期。
  - agg：整仓聚合（docs / refs / board）。TTL 默认 20 秒，GOVERN_AGG_TTL 覆盖。

写操作（进度板打钩）后调 invalidate() 显式清 show/agg 两段：源文件变了但仓库索引
摘要哈希不变，键不会自动失效，同进程内会读到旧内容。

为什么不用文件锁：缓存没有跨进程读-改-写事务，各 section 的键互相独立。落盘走
「同目录临时文件 + os.replace」原子替换，读者只会看到旧完整文件或新完整文件，读不到
半截 JSON；并发进程互相覆盖的代价只是缓存未命中（下次重算），不影响结果正确性。
损坏文件：JSON 解析失败即当空缓存，下一次写入原子覆盖修复。

调用方：core.Repo（仓库索引）、docinfo（show / git_last / 聚合）、checkcmd（打钩后失效
show/agg）、cli（--no-cache 旁路与 cache --clear）。
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CACHE_VERSION = 1

SECTION_INDEX = "index"
SECTION_SHOW = "show"
SECTION_GIT = "git"
SECTION_AGG = "agg"

# section -> (环境变量名, 默认 TTL 秒)
_TTL_POLICY: Dict[str, Tuple[str, float]] = {
    SECTION_INDEX: ("GOVERN_INDEX_TTL", 60.0),
    SECTION_SHOW: ("GOVERN_SHOW_TTL", 300.0),
    SECTION_GIT: ("GOVERN_GIT_TTL", 30.0),
    SECTION_AGG: ("GOVERN_AGG_TTL", 20.0),
}

# 单 section 最多保留多少条目，超出丢最旧的（按写入顺序）
_MAX_ENTRIES = 400

_enabled = True
# 进程内层：mem_key -> {"version": int, "entries": {复合键: {"at": float, "v": Any}}}
_mem: Dict[str, Dict[str, Any]] = {}
# mem_key -> 该仓库的缓存文件路径（落盘时用）
_paths: Dict[str, Path] = {}
_dirty: set = set()


def set_enabled(flag: bool) -> None:
    """全局开关；False 时所有缓存读写旁路（CLI --no-cache）。"""
    global _enabled
    _enabled = bool(flag)


def is_enabled() -> bool:
    """缓存是否启用。"""
    return _enabled


def cache_dir() -> Path:
    """缓存目录：默认 <系统临时目录>/governance-kit/cache。"""
    override = os.environ.get("GOVERN_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path(tempfile.gettempdir()) / "governance-kit" / "cache"


def repo_key(repo_root) -> str:
    """仓库标识：绝对路径的 sha256 前 16 位（也是缓存文件名主干）。"""
    try:
        resolved = str(Path(repo_root).resolve())
    except OSError:
        resolved = str(repo_root)
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def cache_file(repo_root) -> Path:
    """本仓库的缓存文件路径。"""
    return cache_dir() / f"{repo_key(repo_root)}.json"


def ttl_for(section: str) -> float:
    """section 的 TTL（秒）；环境变量可覆盖，非法值回落默认。"""
    env_name, default = _TTL_POLICY.get(section, ("GOVERN_CACHE_TTL", 20.0))
    raw = os.environ.get(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if val >= 0 else default


def _mem_key(repo_root) -> str:
    return f"{cache_dir()}::{repo_key(repo_root)}"


def _blank(repo_root=None) -> Dict[str, Any]:
    return {"version": CACHE_VERSION, "repo": str(repo_root or ""), "entries": {}}


def _load(repo_root) -> Dict[str, Any]:
    """取进程内层；没有就读磁盘（缺失或损坏按空缓存处理）。"""
    mk = _mem_key(repo_root)
    if mk in _mem:
        return _mem[mk]
    data = _blank(repo_root)
    try:
        raw = cache_file(repo_root).read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        parsed = None
    if (isinstance(parsed, dict)
            and parsed.get("version") == CACHE_VERSION
            and isinstance(parsed.get("entries"), dict)):
        data = parsed
        data["repo"] = str(repo_root)
    _mem[mk] = data
    _paths[mk] = cache_file(repo_root)
    return data


def _prune(data: Dict[str, Any], section: str, keep: str) -> None:
    """条目过多时丢最旧的（按写入顺序），避免缓存文件无限增长。"""
    prefix = f"{section}\x1f"
    keys = [k for k in data["entries"] if k.startswith(prefix)]
    if len(keys) <= _MAX_ENTRIES:
        return
    drop = len(keys) - _MAX_ENTRIES
    for key in keys:
        if drop <= 0:
            break
        if key == keep:
            continue
        data["entries"].pop(key, None)
        drop -= 1


def get(repo_root, section: str, key: str) -> Optional[Any]:
    """取缓存值；未命中、过期、缓存关闭或条目损坏时返回 None。"""
    if not _enabled:
        return None
    entry = _load(repo_root)["entries"].get(f"{section}\x1f{key}")
    if not isinstance(entry, dict) or "v" not in entry:
        return None
    try:
        age = time.time() - float(entry.get("at", 0.0))
    except (TypeError, ValueError):
        return None
    if age >= ttl_for(section):
        return None
    return entry["v"]


def put(repo_root, section: str, key: str, value: Any) -> None:
    """写缓存值（进程内层立即可见；磁盘层由 flush 落盘）。"""
    if not _enabled:
        return
    data = _load(repo_root)
    full = f"{section}\x1f{key}"
    data["entries"][full] = {"at": time.time(), "v": value}
    _prune(data, section, full)
    _dirty.add(_mem_key(repo_root))


def _write_mem(mk: str) -> None:
    """把某个仓库的进程内层原子落盘（同目录 tmp 文件 + os.replace）。"""
    data = _mem.get(mk)
    path = _paths.get(mk)
    _dirty.discard(mk)
    if data is None or path is None:
        return
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass


def flush(repo_root) -> None:
    """落盘本仓库的进程内层。"""
    _write_mem(_mem_key(repo_root))


def flush_all() -> None:
    """落盘所有待写仓库。"""
    for mk in list(_dirty):
        _write_mem(mk)


def clear(repo_root) -> int:
    """清掉本仓库的缓存（磁盘文件 + 进程内层）；返回删除的文件数。"""
    mk = _mem_key(repo_root)
    _mem.pop(mk, None)
    _paths.pop(mk, None)
    _dirty.discard(mk)
    path = cache_file(repo_root)
    try:
        if path.exists():
            path.unlink()
            return 1
    except OSError:
        return 0
    return 0


def invalidate(repo_root, sections) -> int:
    """失效指定 section 的全部条目（进程内层 + 磁盘层）；返回删除条目数。

    写操作后调用（如进度板打钩）：源文件变了但仓库索引摘要哈希不变，
    show/agg 段的键不会自动失效，必须显式清掉，否则同进程内读到旧内容。
    """
    data = _load(repo_root)
    prefixes = tuple(f"{section}\x1f" for section in sections)
    stale = [key for key in data["entries"] if key.startswith(prefixes)]
    for key in stale:
        data["entries"].pop(key, None)
    if stale:
        _dirty.add(_mem_key(repo_root))
        flush(repo_root)
    return len(stale)


def clear_all() -> int:
    """清空整个缓存目录（所有仓库）；返回删除的文件数。"""
    removed = 0
    root = cache_dir()
    try:
        for path in sorted(root.iterdir()):
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
                elif path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass
    _mem.clear()
    _paths.clear()
    _dirty.clear()
    return removed


def stats() -> Dict[str, Any]:
    """缓存目录现状：路径、文件数、字节数。"""
    root = cache_dir()
    files: List[Path] = []
    try:
        files = sorted(p for p in root.iterdir() if p.is_file())
    except OSError:
        files = []
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return {
        "dir": str(root),
        "files": [p.name for p in files],
        "bytes": total,
    }


atexit.register(flush_all)
