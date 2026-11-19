"""治理套件核心：仓库索引、配置装载、findings 模型。

上游：cli.py 调用 build_repo()/load_config() 得到只读仓库视图与合并后的配置。
下游：rules.py 的规则消费 Repo（文件集合、basename 索引、文本缓存）。
设计约束：纯 stdlib、Python>=3.10；单趟 os.walk 建索引，禁 per-name find（WSL 慢 IO）。
索引构建结果走 cache.py 的持久缓存（TTL 60 秒），命中即免 os.walk。
"""
from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import cache

IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out", "target",
    ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".next", ".turbo", ".cache", "vendor", "coverage",
    ".idea", ".vscode", ".DS_Store", "site-packages",
    ".bun", ".npm", ".nvm", ".local", ".cargo", ".rustup", ".gradle", ".m2",
}

# Go 模块缓存路径段：家目录扫描时的噪声，整体跳过
GO_MOD_SEGMENTS = ("go", "pkg", "mod")

TEXT_EXTS = {
    ".md", ".markdown", ".json", ".jsonc", ".toml", ".yaml", ".yml",
    ".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".css", ".sql", ".txt",
}

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclasses.dataclass
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    message: str
    fix: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def sort_key(self) -> Tuple[int, str, int, str]:
        return (SEVERITY_ORDER.get(self.severity, 9), self.file, self.line, self.rule)


def discover_repo(start: Path) -> Path:
    """从 start 向上找 .git；找不到再看 .governance / AGENTS.md；都没有则用 start。"""
    cur = start.resolve()
    for cand in [cur, *cur.parents]:
        if (cand / ".git").exists():
            return cand
    for cand in [cur, *cur.parents]:
        if (cand / ".governance").exists() or (cand / "AGENTS.md").exists():
            return cand
    return cur


def _has_go_mod_segment(path: str) -> bool:
    """路径是否含连续的 go/pkg/mod 路径段（Go 模块缓存）。"""
    parts = [p for p in str(path).replace(os.sep, "/").split("/") if p]
    for i in range(len(parts) - len(GO_MOD_SEGMENTS) + 1):
        if tuple(parts[i:i + len(GO_MOD_SEGMENTS)]) == GO_MOD_SEGMENTS:
            return True
    return False


class Repo:
    """只读仓库视图。构建后所有路径为相对 POSIX 字符串。

    索引（files / by_basename / summary_hash）走 cache.py 的持久缓存：命中即免
    os.walk（慢 IO 存储上的主要开销），TTL 默认 60 秒，新文件靠 TTL 自然浮现。
    """

    def __init__(self, root: Path, max_files: int = 60000, use_cache: bool = True):
        self.root = root
        self.max_files = max_files
        self.files: List[str] = []
        self.file_set: set = set()
        self.by_basename: Dict[str, List[str]] = {}
        self._text_cache: Dict[str, str] = {}
        self._summary_hash: str = ""
        self.truncated = False
        self.index_cached = False
        if use_cache and cache.is_enabled():
            payload = cache.get(self.root, cache.SECTION_INDEX, str(max_files))
            if isinstance(payload, dict) and self._restore(payload):
                self.index_cached = True
                return
        self._build()
        if use_cache and cache.is_enabled():
            cache.put(self.root, cache.SECTION_INDEX, str(max_files), {
                "files": list(self.files),
                "by_basename": {k: list(v) for k, v in self.by_basename.items()},
                "summary_hash": self.summary_hash(),
                "truncated": self.truncated,
            })

    def _restore(self, payload: dict) -> bool:
        """从缓存恢复索引；结构不合法返回 False，调用方改走重建。"""
        files = payload.get("files")
        by_basename = payload.get("by_basename")
        if not isinstance(files, list) or not isinstance(by_basename, dict):
            return False
        clean_files: List[str] = []
        for rel in files:
            if not isinstance(rel, str):
                return False
            clean_files.append(rel)
        clean_base: Dict[str, List[str]] = {}
        for base, rels in by_basename.items():
            if not isinstance(base, str) or not isinstance(rels, list):
                return False
            clean_base[base] = [r for r in rels if isinstance(r, str)]
        self.files = clean_files
        self.file_set = set(clean_files)
        self.by_basename = clean_base
        self.truncated = bool(payload.get("truncated", False))
        digest = payload.get("summary_hash")
        self._summary_hash = digest if isinstance(digest, str) else ""
        return True

    def _build(self) -> None:
        root = self.root
        if _has_go_mod_segment(root):
            return
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in IGNORE_DIRS and not d.startswith(".git")
                and not _has_go_mod_segment(os.path.join(rel_dir, d))
            )
            for name in sorted(filenames):
                if name in (".DS_Store",):
                    continue
                rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                rel = rel.replace(os.sep, "/")
                self.files.append(rel)
                self.file_set.add(rel)
                base = os.path.basename(rel)
                self.by_basename.setdefault(base, []).append(rel)
                count += 1
                if count >= self.max_files:
                    self.truncated = True
                    return

    def abs(self, rel: str) -> Path:
        return self.root / rel

    def exists(self, rel: str) -> bool:
        return rel in self.file_set or self.abs(rel).exists()

    def read_text(self, rel: str, limit: int = 2_000_000) -> str:
        if rel in self._text_cache:
            return self._text_cache[rel]
        try:
            data = self.abs(rel).read_bytes()[:limit]
            text = data.decode("utf-8", errors="replace")
        except OSError:
            text = ""
        self._text_cache[rel] = text
        return text

    def iter_md(self, prefix: str = ""):
        for rel in self.files:
            if not rel.endswith((".md", ".markdown")):
                continue
            if prefix and not rel.startswith(prefix.rstrip("/") + "/") and rel != prefix:
                continue
            yield rel

    def match_glob(self, pattern: str):
        for rel in self.files:
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(
                os.path.basename(rel), pattern
            ):
                yield rel

    def summary_hash(self) -> str:
        """文件列表摘要（16 位十六进制）：给缓存键与生成物漂移比对用。"""
        if self._summary_hash:
            return self._summary_hash
        h = hashlib.sha256()
        h.update(str(len(self.files)).encode())
        for rel in self.files[:5000]:
            h.update(rel.encode())
        self._summary_hash = h.hexdigest()[:16]
        return self._summary_hash


DEFAULT_CONFIG: dict = {
    "agents": {
        "line_budget": 60,
        "line_hard": 100,
        "glob": "AGENTS.md",
        "ref_scope": ["AGENTS.md"],
        "check_refs": True,
        "ref_ignore": [],
        "ref_ignore_tokens": [],
    },
    "tiers": {
        "ai_read": [],
        "human_read": [],
        "human_read_forbid": ["file_line_ref", "revision_history"],
        "file_line_ext": "py|ts|tsx|js|jsx|sh|go|rs|java|kt|rb|php|css|sql",
    },
    "board": {
        # 空串 = 自动发现：根目录 `进度板.md`/`进度/` 优先，docs/ 为兼容旧位置
        "path": "",
        "tasks_dir": "",
        "stale_days": 14,
        "ui_evidence_required": False,
        "statuses": [
            "待启动", "进行中", "待验收", "已完成", "挂起",
        ],
        "legacy_status": {
            "待办": "待启动",
            "部分完成": "进行中",
            "完成": "已完成",
        },
    },
    "skills": {
        "canonical": "",
        "copies": [],
        "peer_repo": "",
    },
    "indexes": {
        "files": [],
        "dirs": [],
    },
    "adapters": {},
    "html": {
        "path": "docs/文档全景图.html",
    },
    "logic": {
        # 近期功能实现文档目录：一业务域一 JSON，随 git 入库
        "dir": ".governance/logic",
    },
    "severity": {
        "AGENTS-LINE": "warning",
        "AGENTS-LINE_HARD": "error",
        "AGENTS-REF": "warning",
        "INDEX-SYNC": "warning",
        "DOC-TIER": "warning",
        "BOARD-SCHEMA": "warning",
        "BOARD-STALE": "warning",
        "SKILL-SYNC": "warning",
        "GEN-DRIFT": "warning",
        "ADAPTER": "warning",
        "LOGIC-BROKEN": "error",
        "LOGIC-STALE": "warning",
    },
}


def board_paths(repo: Repo, cfg: dict) -> Tuple[str, str]:
    """进度板位置：显式配置优先，否则自动发现（根目录优先，docs/ 兼容旧位置）。

    返回 (索引文件相对路径, 任务目录相对路径)；两项都空表示本仓没有进度板。
    cfg 里任一键非空即视为显式配置：两键原样使用（未写的那键为空即不启用）。
    两键都空才自动发现：根目录 `进度板.md`/`进度/` 优先，其次 `docs/` 旧位置。
    """
    board = cfg.get("board") or {}
    path = str(board.get("path") or "").strip()
    tasks_dir = str(board.get("tasks_dir") or "").strip()
    if path or tasks_dir:
        return path, tasks_dir
    if repo.exists("进度板.md") or repo.exists("进度"):
        return "进度板.md", "进度"
    if repo.exists("docs/进度板.md") or repo.exists("docs/进度"):
        return "docs/进度板.md", "docs/进度"
    return "", ""


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config(repo_root: Path, config_path: Optional[Path] = None) -> dict:
    """装载 .governance/config.json（或 .toml 经 tomllib，3.11+ 才支持）。

    返回深合并默认值后的配置。缺文件时返回默认值。
    """
    candidates = []
    if config_path is not None:
        candidates.append(config_path)
    else:
        candidates = [
            repo_root / ".governance" / "config.json",
            repo_root / ".governance" / "config.jsonc",
            repo_root / ".governance" / "config.toml",
        ]
    for path in candidates:
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".toml":
            try:
                import tomllib  # type: ignore
            except ModuleNotFoundError as exc:  # pragma: no cover
                raise SystemExit(
                    "config.toml 需要 Python>=3.11（tomllib）；请改用 config.json"
                ) from exc
            data = tomllib.loads(raw)
        else:
            data = json.loads(_strip_jsonc(raw))
        return _deep_merge(DEFAULT_CONFIG, data)
    return dict(DEFAULT_CONFIG)


def _strip_jsonc(text: str) -> str:
    """去 // 与 /* */ 注释（字符串内的不动），兼容 jsonc。"""
    out = []
    i = 0
    n = len(text)
    in_str = False
    escape = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_fenced_code(text: str) -> str:
    """把 ``` / ~~~ 围栏代码块替换为等量空行（保留行号对齐）。"""
    lines = text.splitlines()
    out: List[str] = []
    fence = None
    for line in lines:
        stripped = line.strip()
        if fence is None and (stripped.startswith("```") or stripped.startswith("~~~")):
            fence = stripped[:3]
            out.append("")
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)