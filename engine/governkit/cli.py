"""govern CLI：argparse 子命令编排与 JSON 信封输出。

上游：engine/govern 启动器调用 main()。
下游：core（仓库/配置）、rules（findings/进度板）、board、docmap、docinfo、htmlgen、
initcmd、checkcmd（进度板打钩）、cache（持久缓存）。
退出码约定：0 正常；1 --strict 且存在 error；2 用法/IO 错误（含 show 的越界与敏感路径、
check 的非法目标）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import List, Optional

from . import (
    __version__,
    board as board_mod,
    cache as cache_mod,
    checkcmd,
    core,
    docinfo,
    docmap,
    htmlgen,
    initcmd,
    logic as logic_mod,
    rules,
)

PACK_DIRS = ("engine", "skill", "skills", "templates", "adapters", "reference-adapters")
PACK_FILES = ("README.md", "AGENTS.md")


def _opt(ns: argparse.Namespace, name: str, default=None):
    return getattr(ns, name, default)


def _envelope(cmd: str, root: Path, data: dict, findings: Optional[List] = None,
              truncated: bool = False) -> dict:
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings or []:
        if finding.severity in counts:
            counts[finding.severity] += 1
    return {
        "v": 1,
        "cmd": cmd,
        "repo": str(root),
        "data": data,
        "summary": counts,
        "truncated": bool(truncated),
    }


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def _repo_root(args: argparse.Namespace) -> Path:
    raw = _opt(args, "repo")
    start = Path(raw).expanduser() if raw else Path.cwd()
    if not start.exists():
        raise OSError(f"仓库路径不存在：{start}")
    return core.discover_repo(start).resolve()


def _load_cfg(root: Path, args: argparse.Namespace) -> dict:
    raw = _opt(args, "config")
    if not raw:
        return core.load_config(root)
    cand = Path(raw).expanduser()
    if not cand.is_absolute():
        first = (root / cand)
        cand = first if first.exists() else cand.resolve()
    if not cand.exists():
        raise OSError(f"配置文件不存在：{cand}")
    return core.load_config(root, cand)


def _stable_summary_hash(repo: core.Repo, cfg: dict) -> str:
    """与 Repo.summary_hash 同构，但排除 html 生成物自身。

    html 产物会进入下一次扫描的文件列表；若算进摘要，生成后重扫必然漂移，
    GEN-DRIFT 永远无法清零。审计对象是「输入文件 + findings」，生成物不参与。
    """
    excluded = (cfg.get("html", {}) or {}).get("path", "") or ""
    files = [rel for rel in repo.files if rel != excluded]
    digest = hashlib.sha256()
    digest.update(str(len(files)).encode())
    for rel in files[:5000]:
        digest.update(rel.encode())
    return digest.hexdigest()[:16]


def _run_scan(root: Path, cfg: dict, skip_adapters: bool):
    repo = core.Repo(root)
    findings = rules.run_all(repo, cfg, skip_adapters=skip_adapters)
    payload = [f.to_dict() for f in findings]
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False) + _stable_summary_hash(repo, cfg)
    scan_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return repo, findings, scan_hash


def _print_finding(finding) -> None:
    loc = f"{finding.file}:{finding.line}"
    tail = f"  → {finding.fix}" if finding.fix else ""
    print(f"{finding.severity} {finding.rule} {loc} {finding.message}{tail}")


def cmd_scan(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo, findings, scan_hash = _run_scan(root, cfg, bool(_opt(args, "no_adapters", False)))
    findings = findings + rules.rule_gen_drift(repo, cfg, scan_hash)
    findings.sort(key=lambda f: f.sort_key())

    as_json = bool(_opt(args, "json", False))
    if as_json:
        data = {"findings": [f.to_dict() for f in findings], "scan_hash": scan_hash}
        _emit(_envelope("scan", root, data, findings, repo.truncated), True)
    else:
        print(f"repo: {root}（{len(repo.files)} 文件，scan-hash {scan_hash}）")
        if not findings:
            print("（无 findings）")
        for finding in findings:
            _print_finding(finding)
        counts = _envelope("scan", root, {}, findings)
        summary = counts["summary"]
        print(f"\nsummary: error={summary['error']} warning={summary['warning']} "
              f"info={summary['info']}")
    errors = sum(1 for f in findings if f.severity == "error")
    if _opt(args, "strict", False) and errors:
        return 1
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo, findings, scan_hash = _run_scan(root, cfg, bool(_opt(args, "no_adapters", False)))
    findings = findings + rules.rule_gen_drift(repo, cfg, scan_hash)
    envelope = _envelope("health", root, {"scan_hash": scan_hash, "files": len(repo.files)},
                         findings, repo.truncated)
    summary = envelope["summary"]
    if _opt(args, "json", False):
        _emit(envelope, True)
    else:
        print(f"repo: {root}")
        print(f"files: {len(repo.files)}")
        print(f"scan-hash: {scan_hash}")
        print(f"error={summary['error']} warning={summary['warning']} info={summary['info']}")
    if _opt(args, "strict", False) and summary["error"]:
        return 1
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    tasks = rules.parse_board(repo, cfg)
    status = _opt(args, "status")
    if _opt(args, "json", False):
        _emit(_envelope("board", root, board_mod.board_json(tasks, status), None, repo.truncated), True)
    else:
        print(board_mod.render_board(tasks, status))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """进度板打钩：翻转任务文件 checklist 项；非法目标或越界退出码 2。"""
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    on = bool(_opt(args, "on", False))
    off = bool(_opt(args, "off", False))
    toggle = bool(_opt(args, "toggle", False))
    if sum((on, off, toggle)) > 1:
        raise ValueError("--on / --off / --toggle 互斥")
    direction = "on" if on else "off" if off else "toggle"
    data = checkcmd.run_check(root, cfg, args.rel, int(args.index), direction)
    if _opt(args, "json", False):
        _emit(_envelope("check", root, data, None), True)
    else:
        if data.get("error"):
            print(f"govern: {data['error']}", file=sys.stderr)
        else:
            mark = "已勾选" if data["checked"] else "已取消勾选"
            print(f"{mark}：{data['path']} #{data['index']}"
                  f"（第 {data['line']} 行，updated={data['updated'] or '-'}）")
    return 2 if data.get("error") else 0


def _logic_domains_text(domains: List[dict]) -> str:
    """人读渲染：域 → 条目（状态、分组、分类）。"""
    if not domains:
        return "（无代码逻辑域）"
    out: List[str] = []
    for dom in domains:
        out.append(f"{dom.get('domain', '')}  {dom.get('title', '')}  "
                   f"updated={dom.get('updated', '')}")
        for entry in dom.get("entries") or []:
            group = entry.get("group") or ""
            kind = entry.get("kind") or ""
            tag = f"  {group}" if group else ""
            kind_tag = f"  <{kind}>" if kind else ""
            detail = entry.get("status_detail") or ""
            suffix = f"（{detail}）" if entry.get("status") != "fresh" and detail else ""
            out.append(f"  - {entry.get('id', '')}  {entry.get('status', '')}  "
                       f"{entry.get('title', '')}{tag}{kind_tag} {suffix}".rstrip())
    return "\n".join(out)


def cmd_logic(args: argparse.Namespace) -> int:
    """近期功能实现文档：init/add/list/sync/accept/check（JSON 信封 + 人读输出）。"""
    action = _opt(args, "logic_cmd")
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    as_json = bool(_opt(args, "json", False))

    if action == "init":
        path = logic_mod.init_domain(repo, cfg, args.domain,
                                     title=_opt(args, "title", "") or "")
        data = {"domain": args.domain, "file": path.relative_to(root).as_posix()}
        items = logic_mod.findings(repo, cfg)
        if as_json:
            _emit(_envelope("logic", root, data, items), True)
        else:
            print(f"已初始化代码逻辑域：{args.domain}（{data['file']}）")
        return 0

    if action == "add":
        entry = logic_mod.add_entry(
            repo, cfg, args.domain, args.id, args.file,
            symbol=_opt(args, "symbol", "") or "",
            title=_opt(args, "title", "") or "",
            role=_opt(args, "role", "") or "",
            group=_opt(args, "group", "") or "",
            kind=_opt(args, "kind", "module") or "module",
        )
        data = {"domain": args.domain, "id": args.id, "entry": entry}
        items = logic_mod.findings(repo, cfg)
        if as_json:
            _emit(_envelope("logic", root, data, items), True)
        else:
            pointer = (entry.get("pointers") or [{}])[0]
            print(f"已添加条目 {args.domain}/{args.id}：{pointer.get('file', '')}:"
                  f"{pointer.get('line', 0)}-{pointer.get('end', 0)}")
        return 0

    if action == "list":
        domains = logic_mod.list_domains(repo, cfg)
        data = {"dir": (cfg.get("logic") or {}).get("dir", logic_mod.DEFAULT_DIR),
                "domains": domains}
        items = logic_mod.findings(repo, cfg)
        if as_json:
            _emit(_envelope("logic", root, data, items), True)
        else:
            print(f"代码逻辑目录：{data['dir']}（{len(domains)} 域）")
            print(_logic_domains_text(domains))
        return 0

    if action == "sync":
        results = logic_mod.sync(repo, cfg, _opt(args, "domain", "") or "")
        data = {"results": results}
        items = logic_mod.findings(repo, cfg)
        if as_json:
            _emit(_envelope("logic", root, data, items), True)
        else:
            if not results:
                print("（无条目需要同步）")
            for item in results:
                print(f"{item.get('domain', '')}/{item.get('id', '')}: "
                      f"{item.get('action', '')}")
        return 0

    if action == "accept":
        entry = logic_mod.accept(repo, cfg, args.domain, args.id)
        data = {"domain": args.domain, "id": args.id, "entry": entry}
        items = logic_mod.findings(repo, cfg)
        if as_json:
            _emit(_envelope("logic", root, data, items), True)
        else:
            status = logic_mod.entry_status(repo, entry)[0]
            print(f"已复核 {args.domain}/{args.id}：{status}")
        return 0

    if action == "check":
        items = logic_mod.findings(repo, cfg)
        data = {"findings": [f.to_dict() for f in items]}
        if as_json:
            _emit(_envelope("logic", root, data, items, repo.truncated), True)
        else:
            if not items:
                print("（无 findings）")
            for finding in items:
                _print_finding(finding)
            summary = _envelope("logic", root, {}, items)["summary"]
            print(f"\nsummary: error={summary['error']} warning={summary['warning']} "
                  f"info={summary['info']}")
        if _opt(args, "strict", False) and any(f.severity == "error" for f in items):
            return 1
        return 0

    print("govern: 未知 logic 子命令", file=sys.stderr)
    return 2


def _text_cmd(cmd: str, args: argparse.Namespace, key: str, text: str) -> int:
    root = _repo_root(args)
    if _opt(args, "json", False):
        _emit(_envelope(cmd, root, {key: text}, None), True)
    else:
        print(text)
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    text = docmap.map_text(repo, cfg, args.rel)
    return _text_cmd("map", args, "text", text)


def cmd_outline(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    text = docmap.outline_text(repo, args.rel)
    return _text_cmd("outline", args, "text", text)


def _parse_lines(spec: str) -> tuple:
    if ":" not in spec:
        raise ValueError("--lines 格式为 A:B（行号从 1 起）")
    left, right = spec.split(":", 1)
    try:
        start = int(left) if left.strip() else 1
        end = int(right) if right.strip() else start
    except ValueError as exc:
        raise ValueError(f"--lines 需要整数行号：{spec!r}") from exc
    if start < 1 or end < start:
        raise ValueError(f"--lines 区间非法：{spec!r}")
    return start, end - start + 1


def cmd_view(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    offset = int(_opt(args, "offset", 0) or 0)
    limit = int(_opt(args, "limit", 400) or 400)
    lines_spec = _opt(args, "lines")
    if lines_spec:
        offset, limit = _parse_lines(lines_spec)
    text = docmap.view_text(repo, args.rel, offset=offset, limit=limit)
    return _text_cmd("view", args, "text", text)


def cmd_tree(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    mode = _opt(args, "mode", "all") or "all"
    if _opt(args, "docs", False):
        mode = "docs"
    elif _opt(args, "contracts", False):
        mode = "contracts"
    text = docmap.tree_text(repo, cfg, mode)
    return _text_cmd("tree", args, "text", text)


def cmd_tests(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    text = docmap.tests_text(repo, args.rel)
    return _text_cmd("tests", args, "text", text)


def cmd_symbols(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    text = docmap.symbols_text(repo, args.query, limit=int(_opt(args, "limit", 20) or 20))
    return _text_cmd("symbols", args, "text", text)


def cmd_xref(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    text = docmap.xref_text(repo, args.symbol, limit=int(_opt(args, "limit", 50) or 50))
    return _text_cmd("xref", args, "text", text)


def cmd_show(args: argparse.Namespace) -> int:
    """单文件内容 + 元信息 + 指针 + 关联任务；越界与敏感路径退出码 2。"""
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    offset = int(_opt(args, "offset", 1) or 1)
    limit = int(_opt(args, "limit", docinfo.DEFAULT_LIMIT) or docinfo.DEFAULT_LIMIT)
    lines_spec = _opt(args, "lines")
    if lines_spec:
        offset, limit = _parse_lines(lines_spec)
    data = docinfo.build_show(repo, cfg, args.rel, offset=offset, limit=limit)
    if _opt(args, "json", False):
        _emit(_envelope("show", root, data, None, repo.truncated), True)
    else:
        print(docinfo.render_show(data))
    return 2 if data.get("error") else 0


def cmd_refs(args: argparse.Namespace) -> int:
    """聚合 AGENTS.md 与索引文件里的指针，逐条给来源、状态与解析路径。"""
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    files = docinfo.build_refs(repo, cfg)
    if _opt(args, "json", False):
        _emit(_envelope("refs", root, {"files": files}, None, repo.truncated), True)
    else:
        print(docinfo.render_refs(files))
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    """文档清单：分级、行数、指针数与失效指针数。"""
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo = core.Repo(root)
    docs = docinfo.build_docs(repo, cfg)
    if _opt(args, "json", False):
        _emit(_envelope("docs", root, {"docs": docs}, None, repo.truncated), True)
    else:
        print(docinfo.render_docs(docs))
    return 0


def cmd_html(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    repo, findings, scan_hash = _run_scan(root, cfg, bool(_opt(args, "no_adapters", False)))
    tasks = rules.parse_board(repo, cfg)
    page = htmlgen.render_html(repo, cfg, findings, tasks, scan_hash)

    out_raw = _opt(args, "out") or cfg.get("html", {}).get("path", "docs/文档全景图.html")
    out = Path(out_raw).expanduser()
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    if _opt(args, "json", False):
        _emit(_envelope("html", root, {"path": str(out), "scan_hash": scan_hash},
                        findings, repo.truncated), True)
    else:
        print(str(out))
    return 0


def _copy_tree(src: Path, dst: Path) -> int:
    count = 0
    for item in sorted(src.rglob("*")):
        if not item.is_file():
            continue
        if "__pycache__" in item.parts or item.suffix == ".pyc":
            continue
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        count += 1
    return count


def cmd_cache(args: argparse.Namespace) -> int:
    """缓存管理：默认看现状；--clear 清本仓库缓存，--all 清整个缓存目录。"""
    if _opt(args, "clear", False):
        if _opt(args, "all", False):
            removed = cache_mod.clear_all()
            target = str(cache_mod.cache_dir())
        else:
            root = _repo_root(args)
            removed = cache_mod.clear(root)
            target = str(cache_mod.cache_file(root))
        if _opt(args, "json", False):
            _emit(_envelope("cache", Path.cwd(),
                            {"removed": removed, "target": target}, None), True)
        else:
            print(f"已清理 {removed} 个缓存文件：{target}")
        return 0
    data = cache_mod.stats()
    if _opt(args, "json", False):
        _emit(_envelope("cache", Path.cwd(), data, None), True)
    else:
        print(f"缓存目录：{data['dir']}")
        print(f"缓存文件：{len(data['files'])} 个，合计 {data['bytes']} 字节")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    root = _repo_root(args)
    cfg = _load_cfg(root, args)
    skills = cfg.get("skills", {}) or {}
    canonical = skills.get("canonical", "")
    copies = skills.get("copies", []) or []
    if not canonical or not copies:
        print("未配置 skills.canonical / skills.copies，跳过技能同步。")
        _emit(_envelope("sync", root, {"copied": []}, None), bool(_opt(args, "json", False)))
        return 0
    src = root / canonical
    if not src.is_dir():
        raise OSError(f"canonical 技能目录不存在：{src}")
    result = []
    for copy in copies:
        dst = root / copy
        dst.mkdir(parents=True, exist_ok=True)
        count = _copy_tree(src, dst)
        result.append({"to": str(copy), "files": count})
        print(f"{src} -> {dst}（{count} 文件）")
    if _opt(args, "json", False):
        _emit(_envelope("sync", root, {"copied": result}, None), True)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir).expanduser()
    messages = initcmd.init_repo(target, bool(_opt(args, "with_skill", False)), _kit_root())
    if _opt(args, "json", False):
        root = target.resolve()
        _emit(_envelope("init", root, {"messages": messages}, None), True)
    else:
        for line in messages:
            print(line)
    return 0


def _kit_root() -> Path:
    return Path(__file__).resolve().parents[2]


def cmd_pack(args: argparse.Namespace) -> int:
    kit_root = _kit_root()
    out = Path(args.out).expanduser()
    if not out.is_absolute():
        out = Path.cwd() / out
    out.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    for name in PACK_DIRS:
        base = kit_root / name
        if base.is_dir():
            entries.extend(sorted(p for p in base.rglob("*") if p.is_file()))
    for name in PACK_FILES:
        path = kit_root / name
        if path.is_file():
            entries.append(path)
    packed = 0
    missing = [name for name in (*PACK_DIRS, *PACK_FILES) if not (kit_root / name).exists()]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in entries:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if "node_modules" in path.parts:
                continue
            zf.write(path, path.relative_to(kit_root).as_posix())
            packed += 1
    print(f"已打包 {packed} 文件 -> {out}")
    if missing:
        print("缺失未打包：" + "、".join(missing))
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    print("vendored 模式请用新版本覆盖 engine/ 与 skill/；"
          "发布版用 pipx upgrade governance-kit")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(f"governance-kit {__version__}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=argparse.SUPPRESS, metavar="PATH",
                        help="仓库根（缺省从当前目录向上发现）")
    common.add_argument("--config", default=argparse.SUPPRESS, metavar="PATH",
                        help="治理配置文件（缺省 .governance/config.json）")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="输出 JSON 信封")
    common.add_argument("--strict", action="store_true", default=argparse.SUPPRESS,
                        help="存在 error 时退出码 1")
    common.add_argument("--no-adapters", action="store_true", default=argparse.SUPPRESS,
                        help="跳过外部适配器")
    common.add_argument("--no-cache", action="store_true", default=argparse.SUPPRESS,
                        help="旁路全部持久缓存（对照与 CI 用）")

    parser = argparse.ArgumentParser(
        prog="govern", parents=[common],
        description="仓库治理 CLI：扫描、进度板、文档地图、全景图。",
    )
    parser.add_argument("--version", action="version", version=f"governance-kit {__version__}")
    sub = parser.add_subparsers(dest="cmd", metavar="<cmd>")

    sub.add_parser("scan", parents=[common], help="运行全部规则")
    sub.add_parser("health", parents=[common], help="只看汇总计数")
    p_board = sub.add_parser("board", parents=[common], help="进度板")
    p_board.add_argument("--status", default=None, help="按状态过滤")

    p_check = sub.add_parser("check", parents=[common],
                             help="进度板打钩：翻转任务文件 checklist 项")
    p_check.add_argument("rel", help="任务文件相对路径（进度板任务文件）")
    p_check.add_argument("index", type=int, help="checklist 序号（0 起）")
    p_check.add_argument("--on", action="store_true", default=argparse.SUPPRESS,
                         help="强制勾选（已勾选则幂等不改）")
    p_check.add_argument("--off", action="store_true", default=argparse.SUPPRESS,
                         help="强制取消勾选（未勾选则幂等不改）")
    p_check.add_argument("--toggle", action="store_true", default=argparse.SUPPRESS,
                         help="翻转勾选状态（缺省行为）")

    p_map = sub.add_parser("map", parents=[common], help="单文件上下文地图")
    p_map.add_argument("rel")

    p_logic = sub.add_parser("logic", parents=[common],
                              help="近期功能实现文档：树状分组 + 代码指针")
    logic_sub = p_logic.add_subparsers(dest="logic_cmd", metavar="<logic-cmd>",
                                        required=True)
    p_l_init = logic_sub.add_parser("init", parents=[common], help="初始化业务域")
    p_l_init.add_argument("domain")
    p_l_init.add_argument("--title", default=None, help="域标题")
    p_l_add = logic_sub.add_parser("add", parents=[common], help="追加条目")
    p_l_add.add_argument("domain")
    p_l_add.add_argument("id")
    p_l_add.add_argument("--file", required=True, help="仓库相对源码路径")
    p_l_add.add_argument("--symbol", default=None, help="符号名（留空=整文件）")
    p_l_add.add_argument("--title", default=None, help="条目标题")
    p_l_add.add_argument("--role", default=None, help="指针角色，如 入口/核心/下游")
    p_l_add.add_argument("--group", default=None, help="分组路径，如 功能模块/知识库上传")
    p_l_add.add_argument("--kind", default=None,
                         help="分类：module/service/resource/api/data/task")
    logic_sub.add_parser("list", parents=[common], help="列出全部域与条目状态")
    p_l_sync = logic_sub.add_parser("sync", parents=[common], help="同步指针行号与摘要")
    p_l_sync.add_argument("domain", nargs="?", default=None, help="缺省同步全部域")
    p_l_accept = logic_sub.add_parser("accept", parents=[common], help="复核盖戳")
    p_l_accept.add_argument("domain")
    p_l_accept.add_argument("id")
    logic_sub.add_parser("check", parents=[common], help="门控：broken/stale 检查")
    p_outline = sub.add_parser("outline", parents=[common], help="结构大纲")
    p_outline.add_argument("rel")
    p_view = sub.add_parser("view", parents=[common], help="带行号查看")
    p_view.add_argument("rel")
    p_view.add_argument("--lines", default=None, metavar="A:B")
    p_view.add_argument("--offset", type=int, default=argparse.SUPPRESS)
    p_view.add_argument("--limit", type=int, default=argparse.SUPPRESS)
    p_tree = sub.add_parser("tree", parents=[common], help="目录树")
    p_tree.add_argument("--mode", choices=("all", "docs", "contracts"), default="all")
    p_tree.add_argument("--docs", action="store_true", default=argparse.SUPPRESS,
                        help="等价 --mode docs")
    p_tree.add_argument("--contracts", action="store_true", default=argparse.SUPPRESS,
                        help="等价 --mode contracts")
    p_tests = sub.add_parser("tests", parents=[common], help="镜像测试候选")
    p_tests.add_argument("rel")
    p_symbols = sub.add_parser("symbols", parents=[common], help="符号检索")
    p_symbols.add_argument("query")
    p_symbols.add_argument("--limit", type=int, default=argparse.SUPPRESS)
    p_xref = sub.add_parser("xref", parents=[common], help="交叉引用")
    p_xref.add_argument("symbol")
    p_xref.add_argument("--limit", type=int, default=argparse.SUPPRESS)

    p_show = sub.add_parser("show", parents=[common],
                             help="只读查看单文件：内容 + 元信息 + refs + task")
    p_show.add_argument("rel")
    p_show.add_argument("--offset", type=int, default=argparse.SUPPRESS,
                        help="起始行（从 1 起）")
    p_show.add_argument("--limit", type=int, default=argparse.SUPPRESS,
                        help=f"返回行数（默认 {docinfo.DEFAULT_LIMIT}）")
    p_show.add_argument("--lines", default=None, metavar="A:B",
                        help="行区间（从 1 起，含端点）")
    sub.add_parser("refs", parents=[common], help="聚合 AGENTS/索引文件的指针及状态")
    sub.add_parser("docs", parents=[common], help="文档清单：tier / lines / refs / dead")

    p_html = sub.add_parser("html", parents=[common], help="生成全景图")
    p_html.add_argument("--out", default=None, metavar="PATH")
    p_cache = sub.add_parser("cache", parents=[common], help="查看/清理引擎持久缓存")
    p_cache.add_argument("--clear", action="store_true", default=argparse.SUPPRESS,
                         help="清空缓存（默认只清本仓库，--all 清整个缓存目录）")
    p_cache.add_argument("--all", action="store_true", default=argparse.SUPPRESS,
                         help="清整个缓存目录（所有仓库）")
    sub.add_parser("sync", parents=[common], help="同步技能副本")
    p_init = sub.add_parser("init", parents=[common], help="初始化治理骨架")
    p_init.add_argument("dir")
    p_init.add_argument("--with-skill", action="store_true", default=argparse.SUPPRESS)
    p_pack = sub.add_parser("pack", parents=[common], help="打包引擎与技能")
    p_pack.add_argument("--out", required=True, metavar="ZIP")
    sub.add_parser("upgrade", parents=[common], help="升级提示")
    sub.add_parser("version", parents=[common], help="打印版本")
    return parser


COMMANDS = {
    "scan": cmd_scan,
    "health": cmd_health,
    "board": cmd_board,
    "check": cmd_check,
    "logic": cmd_logic,
    "map": cmd_map,
    "outline": cmd_outline,
    "view": cmd_view,
    "tree": cmd_tree,
    "tests": cmd_tests,
    "symbols": cmd_symbols,
    "xref": cmd_xref,
    "show": cmd_show,
    "refs": cmd_refs,
    "docs": cmd_docs,
    "html": cmd_html,
    "cache": cmd_cache,
    "sync": cmd_sync,
    "init": cmd_init,
    "pack": cmd_pack,
    "upgrade": cmd_upgrade,
    "version": cmd_version,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 用法错误或 --version
        code = exc.code
        return int(code) if isinstance(code, int) else 0
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    if _opt(args, "no_cache", False):
        cache_mod.set_enabled(False)
    handler = COMMANDS.get(args.cmd)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"govern: {exc}", file=sys.stderr)
        return 2
    finally:
        cache_mod.flush_all()


if __name__ == "__main__":
    raise SystemExit(main())