"""文档地图/大纲/视图/目录树/测试镜像/符号检索/交叉引用：只读文本视图。

上游：cli.py 的 map/outline/view/tree/tests/symbols/xref 子命令传入 Repo 与配置。
下游：仅产出 str（JSON 模式下由 cli 包进 data.text），不写任何文件。
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from typing import Dict, Iterable, List, Tuple

from .core import Repo
from .docinfo import tier_of

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
PATHISH_EXT = (
    ".md", ".markdown", ".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".json",
    ".jsonc", ".toml", ".yaml", ".yml", ".css", ".sql", ".html", ".txt",
)
PY_EXTS = (".py",)
XREF_EXTS = (".py", ".ts", ".tsx")


def _lines(repo: Repo, rel: str) -> List[str]:
    return repo.read_text(rel).splitlines()


def _line_count(repo: Repo, rel: str) -> int:
    return len(repo.read_text(rel).splitlines())


def _norm(path: str) -> str:
    """POSIX 归一化，消掉 ./ 与 ..，保留相对根。"""
    parts: List[str] = []
    for seg in path.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def _join(base_dir: str, target: str) -> str:
    if not base_dir or base_dir == ".":
        return _norm(target)
    return _norm(base_dir + "/" + target)


def _is_external(target: str) -> bool:
    t = target.strip()
    return bool(
        re.match(r"^(https?://|mailto:|tel:|data:|ftp://|#|//)", t)
        or t.startswith("<")
    )


def _clean_target(raw: str) -> str:
    t = raw.strip()
    if t.startswith("<") and ">" in t:
        t = t[1:t.index(">")]
    t = t.split("#", 1)[0].split("?", 1)[0]
    return t.strip()


def _pathish(token: str) -> bool:
    token = token.strip()
    if not token or " " in token or "\t" in token or _is_external(token):
        return False
    if "/" in token:
        return True
    return token.lower().endswith(PATHISH_EXT)


def _first_paragraph(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    para = re.split(r"\n\s*\n", text, maxsplit=1)[0]
    return re.sub(r"\s+", " ", para).strip()


def _lead_summary(repo: Repo, rel: str) -> str:
    """py 用 ast docstring；其余取首个标题/注释块/正文首段。"""
    text = repo.read_text(rel)
    if not text.strip():
        return ""
    if rel.endswith(PY_EXTS):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            doc = ast.get_docstring(tree)
            if doc:
                return _first_paragraph(doc)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    comment_block: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if comment_block:
                break
            continue
        if stripped.startswith(("//", "#", "--", ";")):
            comment_block.append(stripped.lstrip("/#-; ").strip())
            continue
        break
    if comment_block:
        return " ".join(x for x in comment_block if x)[:200]
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def _chain(repo: Repo, rel: str) -> List[Tuple[str, int]]:
    """自下而上收集各级 AGENTS.md（只列存在的）。"""
    chain: List[Tuple[str, int]] = []
    cur = os.path.dirname(rel)
    while True:
        cand = "AGENTS.md" if cur in ("", ".") else cur + "/AGENTS.md"
        if repo.exists(cand):
            chain.append((cand, len(_lines(repo, cand))))
        if cur in ("", "."):
            break
        cur = os.path.dirname(cur)
    return chain


def _chain_refs(repo: Repo, chain: Iterable[Tuple[str, int]]) -> List[Tuple[str, int, str]]:
    """契约链里引用的、确实存在的文档路径（文件, 行号, 解析后的路径）。"""
    seen = set()
    out: List[Tuple[str, int, str]] = []
    for rel, _ in chain:
        base_dir = os.path.dirname(rel)
        for idx, line in enumerate(_lines(repo, rel), start=1):
            targets = [m.group(1) for m in MD_LINK_RE.finditer(line)]
            targets += [m.group(1) for m in CODE_SPAN_RE.finditer(line) if _pathish(m.group(1))]
            for raw in targets:
                if _is_external(raw):
                    continue
                target = _clean_target(raw)
                if not target:
                    continue
                resolved = _join(base_dir, target)
                if not resolved or not resolved.lower().endswith((".md", ".markdown")):
                    continue
                if not repo.exists(resolved):
                    continue
                key = (resolved, rel)
                if key in seen or resolved in {c for c, _ in chain}:
                    continue
                seen.add(key)
                out.append((rel, idx, resolved))
    return out


def _test_candidates(rel: str) -> List[str]:
    """按 tests/ 镜像、test_ 前缀与 _test 后缀生成候选测试路径。"""
    rel = _norm(rel)
    rel = re.sub(r"^src/", "", rel)
    base = os.path.basename(rel)
    stem = os.path.splitext(base)[0]
    directory = os.path.dirname(rel)
    cands = [
        f"tests/{directory}/test_{stem}.py" if directory else f"tests/test_{stem}.py",
        f"tests/test_{stem}.py",
        f"tests/{directory}/{stem}_test.py" if directory else f"tests/{stem}_test.py",
        f"{directory}/test_{stem}.py" if directory else f"test_{stem}.py",
        f"tests/{stem}_test.py",
    ]
    out: List[str] = []
    for cand in cands:
        cand = _norm(cand)
        if cand and cand not in out:
            out.append(cand)
    return out


def _git_log(repo: Repo, rel: str, count: int = 3) -> List[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo.root), "log", f"-{count}",
             "--format=%h %ad %s", "--date=short", "--", rel],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def map_text(repo: Repo, cfg: dict, rel: str) -> str:
    """单个文件的上下文地图：契约链、目标摘要、引用文档、镜像测试、最近提交。"""
    rel = _norm(rel)
    if not repo.exists(rel):
        return f"文件不存在：{rel}"
    lines = _lines(repo, rel)
    out: List[str] = [f"# 文档地图：{rel}", ""]

    chain = _chain(repo, rel)
    out.append("## 契约链（自下而上）")
    if chain:
        for path, n in chain:
            out.append(f"- {path}（{n} 行）")
    else:
        out.append("（未找到任何 AGENTS.md）")
    out.append("")

    summary = _lead_summary(repo, rel)
    out.append("## 目标摘要")
    out.append(summary if summary else "（无 docstring/标题/注释，取不到摘要）")
    out.append("")

    out.append("## 契约链引用的既有文档")
    refs = _chain_refs(repo, chain)
    if refs:
        for src, line, target in refs[:40]:
            out.append(f"- {src}:{line} -> {target}")
        if len(refs) > 40:
            out.append(f"- ...另有 {len(refs) - 40} 条，已截断")
    else:
        out.append("（无）")
    out.append("")

    out.append("## 镜像测试")
    cands = _test_candidates(rel)
    found = [c for c in cands if repo.exists(c)]
    missing = [c for c in cands if c not in found]
    if found:
        for cand in found:
            out.append(f"- {cand}（存在）")
    if missing:
        out.append("- 候选未命中：" + "、".join(missing))
    if not found:
        out.append("- 未找到镜像测试；按 tests/ 同名 test_* 约定补一份")
    out.append("")

    out.append("## 最近提交")
    log = _git_log(repo, rel)
    if log:
        out.extend(f"- {line}" for line in log)
    else:
        out.append("（git 不可用或无提交历史）")
    out.append("")
    out.append(f"（共 {len(lines)} 行）")
    return "\n".join(out)


def _func_sig(node: ast.AST) -> str:
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    args = ast.unparse(node.args)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args}){ret}"


def _span(node: ast.AST) -> str:
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start) or start
    return f"L{start}-{end}"


def _doc_first_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node)  # type: ignore[arg-type]
    if not doc:
        return ""
    return _first_paragraph(doc).splitlines()[0][:160]


def _outline_py(repo: Repo, rel: str, text: str) -> List[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"（python 语法错误，无法解析：{exc.msg} @ L{exc.lineno}）", ""]
    out: List[str] = []

    def emit(node: ast.AST, depth: int) -> None:
        indent = "  " * depth
        decorators = getattr(node, "decorator_list", []) or []
        for dec in decorators:
            out.append(f"{indent}@{ast.unparse(dec)}")
        if isinstance(node, ast.ClassDef):
            bases = ast.unparse(node.bases) if node.bases else ""
            head = f"class {node.name}" + (f"({bases})" if bases else "")
            doc = _doc_first_line(node)
            out.append(f"{indent}{_span(node)} {head}" + (f" — {doc}" if doc else ""))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    emit(child, depth + 1)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = _doc_first_line(node)
            out.append(f"{indent}{_span(node)} {_func_sig(node)}" + (f" — {doc}" if doc else ""))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            emit(node, 0)
    return out


def _headings(lines: List[str]) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            out.append((idx, m.group(2).strip()))
    return out


def _preview(lines: List[str], start: int, end: int) -> List[str]:
    width = len(str(max(end, 1)))
    return [f"  {str(i).rjust(width)}| {lines[i - 1]}" for i in range(start, end + 1)]


def outline_text(repo: Repo, rel: str) -> str:
    """结构大纲：py 走 ast（签名/行区间/docstring 首行/装饰器），其余标题+首尾预览。"""
    rel = _norm(rel)
    if not repo.exists(rel):
        return f"文件不存在：{rel}"
    text = repo.read_text(rel)
    lines = text.splitlines()
    out: List[str] = []
    if rel.endswith(PY_EXTS):
        out.append(f"# 结构大纲：{rel}（python，{len(lines)} 行）")
        out.append("")
        body = _outline_py(repo, rel, text)
        out.extend(body)
        if not body:
            out.append("（未定义 class/def）")
        return "\n".join(out)

    out.append(f"# 结构大纲：{rel}（非 python，{len(lines)} 行，行号近似）")
    out.append("")
    heads = _headings(lines)
    if heads:
        out.append("## 标题")
        for line_no, title in heads:
            out.append(f"- L{line_no} {title}")
        out.append("")
    out.append("## 首部预览（approx）")
    out.extend(_preview(lines, 1, min(5, len(lines))) if lines else ["（空文件）"])
    if len(lines) > 8:
        out.append("")
        out.append("## 尾部预览（approx）")
        out.extend(_preview(lines, max(1, len(lines) - 4), len(lines)))
    return "\n".join(out)


def view_text(repo: Repo, rel: str, offset: int = 0, limit: int = 400) -> str:
    """带行号查看文件，超限时尾部提示继续用 --offset。"""
    rel = _norm(rel)
    if not repo.exists(rel):
        return f"文件不存在：{rel}"
    lines = _lines(repo, rel)
    total = len(lines)
    start = max(1, int(offset or 1))
    if limit <= 0:
        limit = 400
    if start > total:
        return f"{rel}（共 {total} 行）：起点 {start} 超出文件末尾，用更小的 --offset"
    end = min(total, start + limit - 1)
    width = len(str(total))
    out: List[str] = [f"{rel}（共 {total} 行；显示 {start}-{end}）"]
    if start > 1:
        out.append(f"... 上方还有 {start - 1} 行；--offset 1 查看开头")
    for idx in range(start, end + 1):
        out.append(f"{str(idx).rjust(width)}| {lines[idx - 1]}")
    if end < total:
        out.append(f"... 下方还有 {total - end} 行；继续：--offset {end + 1}")
    return "\n".join(out)


def _tier_of(rel: str, cfg: dict) -> str:
    """分级标签（中文展示）；判定口径统一走 docinfo.tier_of。"""
    return {"ai": "AI读", "human": "人读", "none": "未分级"}[tier_of(rel, cfg)]


def tree_text(repo: Repo, cfg: dict, mode: str) -> str:
    """目录树：all=目录+文件计数，docs=md 按分级分组，contracts=AGENTS.md 层级。"""
    mode = (mode or "all").lower()
    if mode == "docs":
        return _tree_docs(repo, cfg)
    if mode == "contracts":
        return _tree_contracts(repo, cfg)
    return _tree_all(repo)


def _tree_all(repo: Repo) -> str:
    dirs: Dict[str, int] = {".": 0}
    root_files: List[str] = []
    for rel in repo.files:
        if "/" not in rel:
            root_files.append(rel)
            dirs["."] = dirs.get(".", 0) + 1
            continue
        parts = rel.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            dirs[d] = dirs.get(d, 0) + 1
    out = [f"仓库树：{repo.root}（共 {len(repo.files)} 文件）", ""]
    for name in sorted(root_files):
        out.append(f"- {name}")
    max_depth = 4
    prev = ""
    for d in sorted(dirs):
        if d == ".":
            continue
        depth = d.count("/") + 1
        if depth > max_depth:
            continue
        name = d.split("/")[-1] + "/"
        indent = "  " * (depth - 1)
        out.append(f"{indent}{name}  [{dirs[d]} 文件]")
        prev = d
    if repo.truncated:
        out.append("")
        out.append("（文件数触顶，索引已截断）")
    return "\n".join(out)


def _tree_docs(repo: Repo, cfg: dict) -> str:
    md_files = sorted(repo.iter_md())
    groups: Dict[str, List[str]] = {"AI读": [], "人读": [], "未分级": []}
    for rel in md_files:
        groups[_tier_of(rel, cfg)].append(rel)
    out = [f"文档树：{len(md_files)} 个 markdown（分级取 .governance 配置）", ""]
    for tier in ("AI读", "人读", "未分级"):
        items = groups[tier]
        out.append(f"## {tier}（{len(items)}）")
        if not items:
            out.append("- （无）")
        for rel in items:
            out.append(f"- [{tier}] {rel}（{_line_count(repo, rel)} 行）")
        out.append("")
    return "\n".join(out).rstrip()


def _tree_contracts(repo: Repo, cfg: dict) -> str:
    agents = [rel for rel in repo.files
              if rel.endswith("AGENTS.md") or rel.endswith("AGENTS.override.md")]
    agents.sort(key=lambda r: (r.count("/"), r))
    budget = int(cfg.get("agents", {}).get("line_budget", 60))
    out = [f"契约树：{len(agents)} 个 AGENTS.md（预算 {budget} 行）", ""]
    if not agents:
        out.append("（无 AGENTS.md）")
        return "\n".join(out)
    for rel in agents:
        n = len(_lines(repo, rel))
        depth = rel.count("/")
        indent = "  " * depth
        flag = " 超预算" if n > budget else ""
        out.append(f"{indent}- {rel}（{n} 行{flag}）")
    return "\n".join(out)


def tests_text(repo: Repo, rel: str) -> str:
    """候选镜像测试路径 + 文件名/模块名匹配。"""
    rel = _norm(rel)
    base = os.path.basename(rel)
    stem = os.path.splitext(base)[0]
    cands = _test_candidates(rel)
    out = [f"测试映射：{rel}（stem={stem}）", "", "## 候选路径"]
    for cand in cands:
        out.append(f"- {cand}（{'存在' if repo.exists(cand) else '缺失'}）")

    name_matches = [
        f for f in repo.files
        if (f.endswith(".py") and ("test_" + stem) in os.path.basename(f))
        or (f.endswith(".py") and (stem + "_test") in os.path.basename(f))
    ]
    out.append("")
    out.append("## 文件名匹配")
    if name_matches:
        out.extend(f"- {f}" for f in sorted(name_matches))
    else:
        out.append("（无）")

    module = stem
    mention_re = re.compile(r"(?<![\w])" + re.escape(module) + r"(?![\w])")
    hits: List[str] = []
    for f in sorted(repo.files):
        if not f.endswith(".py") or not ("test" in os.path.basename(f)):
            continue
        if mention_re.search(repo.read_text(f)):
            hits.append(f)
            if len(hits) >= 20:
                break
    out.append("")
    out.append("## 模块名引用（测试文件内）")
    if hits:
        out.extend(f"- {f}" for f in hits)
    else:
        out.append("（无）")
    return "\n".join(out)


def _iter_py(repo: Repo) -> Iterable[str]:
    for rel in repo.files:
        if rel.endswith(PY_EXTS):
            yield rel


def symbols_text(repo: Repo, query: str, limit: int = 20) -> str:
    """ast 扫 .py 的 def/class 名子串匹配，输出 file:line 类型 名 doc首行。"""
    q = (query or "").strip().lower()
    if not q:
        return "请给出符号名片段（symbols <query>）"
    if limit <= 0:
        limit = 20
    found: List[Tuple[str, int, str, str, str]] = []
    for rel in sorted(_iter_py(repo)):
        text = repo.read_text(rel)
        if q not in text.lower():
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "def"
            elif isinstance(node, ast.ClassDef):
                kind = "class"
            else:
                continue
            if q not in node.name.lower():
                continue
            found.append((rel, node.lineno, kind, node.name, _doc_first_line(node)))
            if len(found) >= limit + 1:
                break
        if len(found) >= limit + 1:
            break
    out = [f"符号检索：{query}（最多 {limit} 条）", ""]
    if not found:
        out.append("（未命中）")
        return "\n".join(out)
    for rel, line, kind, name, doc in found[:limit]:
        out.append(f"{rel}:{line} {kind} {name}" + (f" — {doc}" if doc else ""))
    if len(found) > limit:
        out.append(f"... 已达上限 {limit}，用更精确的 query 收窄")
    return "\n".join(out)


def xref_text(repo: Repo, symbol: str, limit: int = 50) -> str:
    """按单词边界在 .py/.ts/.tsx 里搜符号，按文件分组 file:line。"""
    sym = (symbol or "").strip()
    if not sym:
        return "请给出符号名（xref <symbol>）"
    if limit <= 0:
        limit = 50
    pattern = re.compile(r"(?<![\w])" + re.escape(sym) + r"(?![\w])")
    files = [f for f in sorted(repo.files) if f.endswith(XREF_EXTS)]
    hits: List[Tuple[str, int, str]] = []
    for rel in files:
        text = repo.read_text(rel)
        if sym not in text:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                snippet = line.strip()
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."
                hits.append((rel, idx, snippet))
                if len(hits) >= limit + 1:
                    break
        if len(hits) >= limit + 1:
            break
    out = [f"交叉引用：{sym}（最多 {limit} 处）", ""]
    if not hits:
        out.append("（未命中）")
        return "\n".join(out)
    current = None
    for rel, idx, snippet in hits[:limit]:
        if rel != current:
            out.append(f"{rel}")
            current = rel
        out.append(f"  {idx}: {snippet}")
    if len(hits) > limit:
        out.append(f"... 已达上限 {limit}，用更精确的符号名收窄")
    return "\n".join(out)