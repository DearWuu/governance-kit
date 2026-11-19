"""内置组合规则：AGENTS 行数/引用、索引双射、文档分级、进度板、技能字节相等、代码逻辑漂移、生成物漂移、适配器编排。

上游：scan.py 调用 run_all()；下游：htmlgen/cli 消费 findings。
规则只写这一份；外部 linter 由编排器归一化后并入，不重复实现。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import urllib.parse
from typing import Dict, List, Optional, Tuple

from .core import Finding, Repo, board_paths, strip_fenced_code

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
# checklist 行：indent + "- [" + 标记(空格/x) + "] " + 原文。
# 分三组是为了重写时只替换标记字符，缩进、分隔空白与正文逐字保留。
CHECKLIST_RE = re.compile(r"^(\s*-\s\[)( |x|X)(\]\s+.*)$")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
OWNER_RE = re.compile(r"负责\s*[:：]\s*`?([^\s|`]+)`?")
PATHISH_EXT = (
    ".md", ".markdown", ".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".json",
    ".jsonc", ".jsonl", ".toml", ".yaml", ".yml", ".css", ".sql", ".html",
    ".txt", ".go", ".rs",
)
# 文档里常见的非仓库路径：家目录/运行时点目录/示例占位首段
DOT_DIR_ALLOW = {".agents", ".github", ".opencode", ".trae", ".claude", ".config", ".vscode"}
PLACEHOLDER_SEGS = {"foo", "bar", "baz", "qux", "example", "sample", "dummy", "placeholder"}


def _sev(cfg: dict, rule: str, default: str = "warning") -> str:
    return cfg.get("severity", {}).get(rule, default)


def _rel_dir(rel_file: str) -> str:
    d = os.path.dirname(rel_file)
    return d if d else "."


def _norm(path: str) -> str:
    path = path.replace(os.sep, "/")
    parts: List[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


def _is_external(target: str) -> bool:
    t = target.strip()
    return bool(
        re.match(r"^(https?://|mailto:|tel:|data:|ftp://|#|//)", t)
        or t.startswith("<")
    )


def _clean_target(raw: str) -> str:
    t = raw.strip()
    if t.startswith("<") and ">" in t:
        t = t[1 : t.index(">")]
    t = t.split("#", 1)[0].split("?", 1)[0]
    return urllib.parse.unquote(t).strip()


class Resolver:
    """三级+后缀解析：目录相对 → 根相对 → 后缀匹配 → basename 兜底。"""

    def __init__(self, repo: Repo):
        self.repo = repo

    def resolve_detail(self, rel_file: str, target: str) -> Tuple[str, Optional[str]]:
        """返回 (状态, 命中路径)；状态为 'ok'/'qualified'/'under'/'dead'。

        命中路径是仓库内实际相对路径；'under'/'dead' 时为 None。
        """
        t = _norm(target)
        if not t:
            return "ok", None
        base_dir = _rel_dir(rel_file)
        cand_dir = _norm(os.path.join(base_dir, t))
        if cand_dir and self.repo.exists(cand_dir):
            return "ok", cand_dir
        if self.repo.exists(t):
            return "qualified", t
        suffix = "/" + t
        for rel in self.repo.files:
            if rel == t or rel.endswith(suffix):
                return "ok", rel
        base = os.path.basename(t)
        if base and base in self.repo.by_basename:
            return "under", None
        return "dead", None

    def resolve(self, rel_file: str, target: str) -> str:
        """返回 'ok' / 'qualified' / 'under' / 'dead'。"""
        return self.resolve_detail(rel_file, target)[0]


def _looks_like_path(token: str) -> bool:
    # 反引号内的 token 仅在“像仓库内文件路径”时校验，尽量压误报：
    # 需以已知扩展名结尾（大小写敏感，排除 Schema.Json 之类类型名）；
    # 跳过家目录(~)、绝对路由(/)、URL scheme、通配/模板、示例占位、运行时点目录。
    if not token or " " in token or "\t" in token:
        return False
    if _is_external(token):
        return False
    if "://" in token or token.startswith(("/", "~")):
        return False
    if any(ch in token for ch in "*?<>{}") or "${" in token:
        return False
    if token.startswith(("-", "$", "@")):
        return False
    if "/" not in token and token.startswith("."):
        return False
    first = token.split("/", 1)[0]
    if "/" in token and first.startswith(".") and first not in DOT_DIR_ALLOW:
        return False
    if first in PLACEHOLDER_SEGS:
        return False
    return token.endswith(PATHISH_EXT)


def _extract_refs(text: str) -> List[Tuple[int, str]]:
    stripped = strip_fenced_code(text)
    refs: List[Tuple[int, str]] = []
    for idx, line in enumerate(stripped.splitlines(), start=1):
        for m in MD_LINK_RE.finditer(line):
            target = _clean_target(m.group(1))
            if target and not _is_external(m.group(1)):
                refs.append((idx, target))
        for m in CODE_SPAN_RE.finditer(line):
            token = m.group(1).strip()
            if _looks_like_path(token):
                refs.append((idx, _clean_target(token)))
    return refs


def rule_agents_line(repo: Repo, cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    a = cfg["agents"]
    budget = int(a.get("line_budget", 60))
    hard = int(a.get("line_hard", 100))
    glob = a.get("glob", "AGENTS.md")
    for rel in repo.match_glob(glob):
        if not rel.endswith(("AGENTS.md", "AGENTS.override.md")):
            continue
        n = len(repo.read_text(rel).splitlines())
        if n > hard:
            # 硬上限固定阻断（error），默认不可被 AGENTS-LINE 的软告警级别覆盖
            hard_sev = cfg.get("severity", {}).get("AGENTS-LINE_HARD", "error")
            out.append(Finding(
                "AGENTS-LINE", hard_sev, rel, 0,
                f"{n} 行，超过硬上限 {hard}",
                f"拆分到子树 AGENTS.md，根文件只留索引（超 {n - hard} 行）",
            ))
        elif n > budget:
            out.append(Finding(
                "AGENTS-LINE", _sev(cfg, "AGENTS-LINE"), rel, 0,
                f"{n} 行，超过目标预算 {budget}",
                f"删叙事/枚举改指针（超 {n - budget} 行）",
            ))
    return out


def rule_agents_ref(repo: Repo, cfg: dict) -> List[Finding]:
    if not cfg["agents"].get("check_refs", True):
        return []
    out: List[Finding] = []
    resolver = Resolver(repo)
    scope = cfg["agents"].get("ref_scope", ["AGENTS.md"]) or ["AGENTS.md"]
    seen = set()
    for pattern in scope:
        for rel in repo.match_glob(pattern):
            if rel in seen or not rel.endswith((".md", ".markdown")):
                continue
            seen.add(rel)
            for line, target in _extract_refs(repo.read_text(rel)):
                status = resolver.resolve(rel, target)
                if status == "dead":
                    out.append(Finding(
                        "AGENTS-REF", _sev(cfg, "AGENTS-REF"), rel, line,
                        f"引用不存在：{target}", "改为真实相对路径或删除",
                    ))
                elif status == "under":
                    out.append(Finding(
                        "AGENTS-REF", _sev(cfg, "AGENTS-REF"), rel, line,
                        f"疑欠限定路径：{target}",
                        "同名文件在别处；补全相对当前文件的路径",
                    ))
    return out


def rule_index_sync(repo: Repo, cfg: dict) -> List[Finding]:
    idx = cfg.get("indexes", {})
    index_files = [f for f in idx.get("files", []) if repo.exists(f)]
    dirs = [d for d in idx.get("dirs", []) if repo.exists(d)]
    if not dirs:
        return []
    blob = "\n".join(repo.read_text(f) for f in index_files)
    out: List[Finding] = []
    for d in dirs:
        prefix = d.rstrip("/") + "/"
        for rel in repo.iter_md(d):
            rest = rel[len(prefix):] if rel.startswith(prefix) else rel
            if "/" in rest:
                continue
            name = os.path.basename(rel)
            stem = os.path.splitext(name)[0]
            if name in blob or stem in blob:
                continue
            out.append(Finding(
                "INDEX-SYNC", _sev(cfg, "INDEX-SYNC"),
                index_files[0] if index_files else d, 0,
                f"{rel} 未出现在索引", f"在索引补一行指向 {rel}",
            ))
    return out


def rule_doc_tier(repo: Repo, cfg: dict) -> List[Finding]:
    t = cfg.get("tiers", {})
    human = [d for d in t.get("human_read", []) if repo.exists(d)]
    forbid = set(t.get("human_read_forbid", []))
    if not human or not forbid:
        return []
    ext = t.get("file_line_ext", "py|ts")
    fileline_re = re.compile(r"[A-Za-z0-9_./\-]+\.(?:" + ext + r"):\d+")
    revision_re = re.compile(
        r"^#{1,6}\s*.*(更新记录|变更记录|修订记录|修订历史|改动记录|Changelog)", re.I)
    out: List[Finding] = []
    for d in human:
        for rel in repo.iter_md(d):
            text = strip_fenced_code(repo.read_text(rel))
            for idx, line in enumerate(text.splitlines(), start=1):
                if "file_line_ref" in forbid and fileline_re.search(line):
                    out.append(Finding(
                        "DOC-TIER", _sev(cfg, "DOC-TIER"), rel, idx,
                        "人读文档正文出现 文件:行号",
                        "移到开发参考或附录；人读正文不堆代码实证",
                    ))
                if "revision_history" in forbid and revision_re.match(line):
                    out.append(Finding(
                        "DOC-TIER", _sev(cfg, "DOC-TIER"), rel, idx,
                        "出现修订痕迹标题", "删除该章节；修订历史交给 git",
                    ))
    return out


# ---------- 进度板 ----------

def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: Dict[str, str] = {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
        if ":" in lines[i]:
            k, v = lines[i].split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    if end is None:
        return {}, text
    return meta, "\n".join(lines[end + 1:])


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def parse_checklist(text: str) -> List[dict]:
    """解析任务文件正文的 checklist 项。

    每项给 index（0 起，按文件内出现顺序）、checked、text（去掉前缀后的原文）与
    line（文件内 1 起行号，含 frontmatter 行数）。frontmatter 内的行不计入；
    围栏代码块里的行也不算——那是示例文本，不是真待办，打钩不得写进去。
    """
    lines = text.splitlines()
    start = 0
    if text.startswith("---") and lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    items: List[dict] = []
    # strip_fenced_code 用等量空行替换围栏内容，行号对齐不变
    body_lines = strip_fenced_code(text).splitlines()
    for i in range(start, len(body_lines)):
        m = CHECKLIST_RE.match(body_lines[i])
        if not m:
            continue
        items.append({
            "index": len(items),
            "checked": m.group(2) != " ",
            "text": m.group(3)[1:].lstrip(),
            "line": i + 1,
        })
    return items


def parse_board(repo: Repo, cfg: dict) -> List[dict]:
    b = cfg.get("board", {})
    statuses = set(b.get("statuses", []))
    legacy = b.get("legacy_status", {})
    tasks: List[dict] = []
    board_path, tasks_dir = board_paths(repo, cfg)
    if tasks_dir and repo.exists(tasks_dir):
        prefix = tasks_dir.rstrip("/") + "/"
        for rel in repo.iter_md(tasks_dir):
            if rel.startswith(prefix) and "/" in rel[len(prefix):]:
                continue
            raw = repo.read_text(rel)
            meta, body = _parse_frontmatter(raw)
            tasks.append({
                "file": rel, "line": 0,
                "title": meta.get("title") or _first_heading(body) or os.path.basename(rel),
                "status": meta.get("status", ""),
                "raw_status": meta.get("status", ""),
                "owner": meta.get("owner", ""),
                "updated": meta.get("updated", ""),
                "goal": meta.get("goal", ""),
                "evidence": meta.get("evidence", ""),
                "doc": meta.get("doc", ""),
                "mode": "task-file",
                "checklist": parse_checklist(raw),
            })
    path = board_path
    if path and repo.exists(path):
        lines = repo.read_text(path).splitlines()
        cur = None
        for idx, line in enumerate(lines, start=1):
            m = re.match(r"^##\s*\[(.+?)\]\s*(.*)$", line)
            if m:
                if cur:
                    tasks.append(cur)
                raw = m.group(1).strip()
                status = raw if raw in statuses else legacy.get(raw, raw)
                cur = {
                    "file": path, "line": idx, "title": m.group(2).strip() or "(无标题)",
                    "status": status, "raw_status": raw, "owner": "",
                    "updated": "", "goal": "", "evidence": "", "doc": "",
                    "mode": "legacy",
                    # legacy 单文件板不提供打钩：任务正文不在任务文件里
                    "checklist": [],
                }
                continue
            if cur is not None:
                dm = DATE_RE.search(line)
                if dm and not cur["updated"]:
                    cur["updated"] = dm.group(1)
                om = OWNER_RE.search(line)
                if om and not cur["owner"]:
                    cur["owner"] = om.group(1)
        if cur:
            tasks.append(cur)
    return tasks


def rule_board(repo: Repo, cfg: dict) -> List[Finding]:
    b = cfg.get("board", {})
    statuses = set(b.get("statuses", []))
    if not statuses:
        return []
    stale_days = int(b.get("stale_days", 14))
    today = _dt.date.today()
    out: List[Finding] = []
    for task in parse_board(repo, cfg):
        rel, line, st = task["file"], task.get("line", 0), task["status"]
        if not st or st not in statuses:
            out.append(Finding(
                "BOARD-SCHEMA", _sev(cfg, "BOARD-SCHEMA"), rel, line,
                f"非法状态枚举：{task.get('raw_status')!r}",
                f"改为 {sorted(statuses)} 之一",
            ))
        if task["mode"] == "task-file":
            for field in ("owner", "updated", "goal"):
                if not task.get(field):
                    out.append(Finding(
                        "BOARD-SCHEMA", _sev(cfg, "BOARD-SCHEMA"), rel, 0,
                        f"任务缺必填字段：{field}",
                        "frontmatter 补 status/owner/updated/goal/evidence",
                    ))
        if st in ("进行中", "待验收") and task["updated"]:
            try:
                age = (today - _dt.date.fromisoformat(task["updated"])).days
                if age > stale_days:
                    out.append(Finding(
                        "BOARD-STALE", _sev(cfg, "BOARD-STALE"), rel, line,
                        f"进行中条目已 {age} 天未更新（阈值 {stale_days}）",
                        "更新进展/证据或改挂起；停滞需人工介入",
                    ))
            except ValueError:
                out.append(Finding(
                    "BOARD-SCHEMA", _sev(cfg, "BOARD-SCHEMA"), rel, line,
                    f"日期格式非法：{task['updated']!r}", "用 YYYY-MM-DD",
                ))
        if st == "已完成" and task["mode"] == "task-file" and not task.get("evidence"):
            out.append(Finding(
                "BOARD-SCHEMA", _sev(cfg, "BOARD-SCHEMA"), rel, 0,
                "已完成但缺 evidence", "AI 自报完成必须附证据（commit/测试/截图）",
            ))
        if b.get("ui_evidence_required") and st == "已完成" and task["mode"] == "task-file":
            hay = task.get("goal", "") + task.get("title", "")
            if "视觉" in hay or "UI" in hay:
                if "截图" not in task.get("evidence", ""):
                    out.append(Finding(
                        "BOARD-SCHEMA", _sev(cfg, "BOARD-SCHEMA"), rel, 0,
                        "UI 类已完成条目缺截图证据",
                        "evidence 附 expected/actual/diff 三联图路径",
                    ))
    return out


def rule_skill_sync(repo: Repo, cfg: dict) -> List[Finding]:
    s = cfg.get("skills", {})
    canonical = s.get("canonical", "")
    copies = s.get("copies", []) or []
    if not canonical or not copies:
        return []
    canon_abs = repo.abs(canonical)
    if not canon_abs.exists():
        return []
    out: List[Finding] = []
    for copy_root in copies:
        copy_abs = repo.abs(copy_root)
        for src in canon_abs.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(canon_abs)
            dst = copy_abs / rel
            dst_rel = f"{copy_root.rstrip('/')}/{rel.as_posix()}"
            if not dst.exists():
                out.append(Finding(
                    "SKILL-SYNC", _sev(cfg, "SKILL-SYNC"), dst_rel, 0,
                    "兼容副本缺失", f"运行 govern sync（从 {canonical} 刷新）",
                ))
                continue
            try:
                if src.read_bytes() != dst.read_bytes():
                    out.append(Finding(
                        "SKILL-SYNC", _sev(cfg, "SKILL-SYNC"), dst_rel, 0,
                        "兼容副本与 canonical 字节不一致", "运行 govern sync 重刷副本",
                    ))
            except OSError:
                pass
    return out


def rule_logic(repo: Repo, cfg: dict) -> List[Finding]:
    """代码逻辑漂移：LOGIC-BROKEN（指针失效）/ LOGIC-STALE（叙事未复核）。

    logic 侧只依赖 core，函数内导入避免 rules→logic→docmap→docinfo→rules 的循环。
    """
    from . import logic  # noqa: PLC0415 —— 顶层导入会形成循环依赖

    return logic.findings(repo, cfg)


def rule_gen_drift(repo: Repo, cfg: dict, scan_hash: str) -> List[Finding]:
    html_path = cfg.get("html", {}).get("path", "")
    if not html_path or not repo.exists(html_path):
        return []
    text = repo.read_text(html_path)
    m = re.search(r'<meta\s+name="govern-scan-hash"\s+content="([^"]+)"', text)
    if not m:
        return [Finding(
            "GEN-DRIFT", _sev(cfg, "GEN-DRIFT"), html_path, 0,
            "HTML 缺 govern-scan-hash", "用 govern html 重新生成",
        )]
    if m.group(1) != scan_hash:
        return [Finding(
            "GEN-DRIFT", _sev(cfg, "GEN-DRIFT"), html_path, 0,
            "HTML 与当前扫描不一致（生成物漂移）", "重跑 govern html 并提交",
        )]
    return []


# ---------- 适配器编排 ----------

def _run(cmd: str, cwd) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)


def _first_token(cmd: str) -> str:
    tok = cmd.strip().split()[0] if cmd.strip() else ""
    return tok.split("=")[0]


def run_adapters(repo: Repo, cfg: dict, skip: bool = False) -> List[Finding]:
    if skip:
        return []
    out: List[Finding] = []
    for name, cmd in (cfg.get("adapters") or {}).items():
        if not cmd:
            continue
        if not shutil.which(_first_token(cmd)):
            continue
        code, stdout, stderr = _run(cmd, repo.root)
        if "sarif" in cmd:
            out.extend(_parse_sarif(name, stdout, cfg))
        elif "lychee" in cmd:
            out.extend(_parse_lychee(name, stdout, cfg))
        elif code != 0:
            tail = (stderr or stdout).strip().splitlines()
            out.append(Finding(
                f"ADAPTER:{name}", _sev(cfg, "ADAPTER"), ".", 0,
                f"适配器失败：{tail[-1] if tail else f'退出码 {code}'}",
                f"修复后重跑：{cmd}",
            ))
    return out


def _parse_sarif(name: str, raw: str, cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return out
    for run in data.get("runs", []):
        for res in run.get("results", []):
            phys = ((res.get("locations") or [{}])[0].get("physicalLocation") or {})
            art = (phys.get("artifactLocation") or {}).get("uri", "")
            line = int((phys.get("region") or {}).get("startLine", 0) or 0)
            level = res.get("level", "warning")
            sev = {"error": "error", "warning": "warning", "note": "info"}.get(level, "warning")
            msg = res.get("message", {})
            text = msg.get("text", "") if isinstance(msg, dict) else str(msg)
            out.append(Finding(f"ADAPTER:{name}:{res.get('ruleId', 'SARIF')}",
                               sev, art, line, text, ""))
    return out


def _parse_lychee(name: str, raw: str, cfg: dict) -> List[Finding]:
    out: List[Finding] = []
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return out
    for key in ("failures", "errors"):
        for it in data.get(key) or []:
            if isinstance(it, dict):
                out.append(Finding(
                    f"ADAPTER:{name}", _sev(cfg, "ADAPTER"), it.get("source", ""), 0,
                    f"死链：{it.get('url', '')} ({it.get('status', '') or it.get('error', '')})",
                    "修复链接或改相对路径",
                ))
    return out


def run_all(repo: Repo, cfg: dict, skip_adapters: bool = False) -> List[Finding]:
    findings: List[Finding] = []
    findings += rule_agents_line(repo, cfg)
    findings += rule_agents_ref(repo, cfg)
    findings += rule_index_sync(repo, cfg)
    findings += rule_doc_tier(repo, cfg)
    findings += rule_board(repo, cfg)
    findings += rule_skill_sync(repo, cfg)
    findings += rule_logic(repo, cfg)
    findings += run_adapters(repo, cfg, skip=skip_adapters)
    findings.sort(key=lambda f: f.sort_key())
    return findings