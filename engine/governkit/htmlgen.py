"""自包含 HTML 全景图：总览、契约树、文档分级树、进度板、近期功能实现、findings。

上游：cli.py 的 html 子命令传入 Repo/配置/findings/tasks/scan_hash。
下游：写盘的 HTML 由 rules.rule_gen_drift 用 <meta name="govern-scan-hash"> 校验。
约束：单文件、内联 CSS/JS、不引外部资源、输出确定性（无时间戳/随机）。
不用 OpenCode 时的可视化入口：浏览器直接打开本产物（见 skills/govern-view）。
"""

from __future__ import annotations

import html
from typing import Dict, List

from . import board as board_mod
from . import logic as logic_mod
from .core import Finding, Repo


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _depth(rel: str) -> int:
    return rel.count("/")


def _counts(findings: List[Finding]) -> Dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        sev = finding.severity if finding.severity in counts else "warning"
        counts[sev] += 1
    return counts


def _tier_of(rel: str, cfg: dict) -> str:
    for key, label in (("ai_read", "AI读"), ("human_read", "人读")):
        for raw in cfg.get("tiers", {}).get(key, []) or []:
            if not isinstance(raw, str) or not raw.strip():
                continue
            prefix = raw.strip().strip("/")
            if rel == prefix or rel.startswith(prefix + "/"):
                return label
    return "未分级"


def _line_count(repo: Repo, rel: str) -> int:
    return len(repo.read_text(rel).splitlines())


def _stat_cards(repo: Repo, cfg: dict, findings: List[Finding], tasks: List[dict],
                domains: List[dict]) -> str:
    counts = _counts(findings)
    md_files = list(repo.iter_md())
    agents = [f for f in repo.files if f.endswith("AGENTS.md")]
    entries = [e for d in domains for e in (d.get("entries") or [])]
    abnormal = sum(1 for e in entries if e.get("status") in ("stale", "broken"))
    cards = [
        ("文件", len(repo.files)),
        ("Markdown", len(md_files)),
        ("AGENTS.md", len(agents)),
        ("任务", len(tasks)),
        ("功能条目", len(entries)),
        ("待复核", abnormal),
        ("error", counts["error"]),
        ("warning", counts["warning"]),
        ("info", counts["info"]),
    ]
    cells = "".join(
        f'<div class="card"><div class="num">{_e(num)}</div>'
        f'<div class="label">{_e(label)}</div></div>'
        for label, num in cards
    )
    return f'<div class="cards">{cells}</div>'


def _contract_tree(repo: Repo, cfg: dict) -> str:
    agents = sorted(
        (rel for rel in repo.files if rel.endswith("AGENTS.md")),
        key=lambda r: (r.count("/"), r),
    )
    budget = int(cfg.get("agents", {}).get("line_budget", 60))
    if not agents:
        return '<p class="empty">（无 AGENTS.md）</p>'
    rows = []
    for rel in agents:
        n = _line_count(repo, rel)
        cls = " over" if n > budget else ""
        rows.append(
            f'<div class="tree-row" style="padding-left:{min(_depth(rel), 8) * 1.3:.1f}rem">'
            f'<code>{_e(rel)}</code>'
            f'<span class="badge{cls}">{_e(n)} 行</span></div>'
        )
    return "\n".join(rows)


def _docs_tree(repo: Repo, cfg: dict) -> str:
    tiers: Dict[str, List[str]] = {"AI读": [], "人读": [], "未分级": []}
    for rel in sorted(repo.iter_md()):
        tiers[_tier_of(rel, cfg)].append(rel)
    blocks = []
    for tier in ("AI读", "人读", "未分级"):
        items = tiers[tier]
        rows = "".join(
            f'<div class="tree-row"><code>{_e(rel)}</code>'
            f'<span class="tier tier-{_e(tier)}">{_e(tier)}</span>'
            f'<span class="muted">{_e(_line_count(repo, rel))} 行</span></div>'
            for rel in items
        )
        if not rows:
            rows = '<p class="empty">（无）</p>'
        blocks.append(
            f'<div class="subgroup"><h3>{_e(tier)} <span class="muted">({len(items)})</span></h3>'
            f'{rows}</div>'
        )
    return "\n".join(blocks)


def _board_section(tasks: List[dict]) -> str:
    groups = board_mod.group_tasks(tasks)
    if not groups:
        return '<p class="empty">（进度板为空）</p>'
    blocks = []
    for group in groups:
        rows = []
        for task in group["tasks"]:
            owner = task.get("owner") or "未指派"
            updated = task.get("updated") or "未知"
            evidence = task.get("evidence") or ""
            ev_badge = (
                f'<span class="badge ok">证据 {_e(evidence[:60])}</span>'
                if evidence else '<span class="badge warn">无证据</span>'
            )
            loc = task.get("file", "")
            if int(task.get("line", 0) or 0):
                loc += f":{task['line']}"
            checks = task.get("checklist") or []
            check_html = ""
            if checks:
                items = "".join(
                    f'<div class="check{" done" if item.get("checked") else ""}">'
                    f'{"☑" if item.get("checked") else "☐"} {_e(item.get("text", ""))}</div>'
                    for item in checks if isinstance(item, dict)
                )
                done = sum(1 for item in checks if isinstance(item, dict) and item.get("checked"))
                check_html = (
                    f'<div class="checks"><span class="muted">{done}/{len(checks)}</span>{items}</div>'
                )
            rows.append(
                '<div class="task">'
                f'<div class="task-title">{_e(task.get("title", "") or "(无标题)")}</div>'
                f'<div class="task-meta"><span class="badge">负责 {_e(owner)}</span>'
                f'<span class="badge">更新 {_e(updated)}</span>{ev_badge}'
                f'<code class="muted">{_e(loc)}</code></div>'
                f'{check_html}'
                "</div>"
            )
        blocks.append(
            f'<div class="subgroup"><h3>{_e(group["status"])} '
            f'<span class="muted">({group["count"]})</span></h3>{"".join(rows)}</div>'
        )
    return "\n".join(blocks)


def _findings_section(findings: List[Finding]) -> str:
    if not findings:
        return '<p class="empty">（无 findings）</p>'
    grouped: Dict[str, Dict[str, List[Finding]]] = {}
    for item in findings:
        grouped.setdefault(item.rule, {}).setdefault(item.file, []).append(item)
    sev_rank = {"error": 0, "warning": 1, "info": 2}
    blocks = []
    for rule in sorted(grouped, key=lambda r: (sev_rank.get(grouped[r][list(grouped[r])[0]][0].severity, 9), r)):
        files = grouped[rule]
        first_sev = files[list(files)[0]][0].severity
        rows = []
        for rel in sorted(files):
            for item in sorted(files[rel], key=lambda f: (f.line, f.message)):
                loc = f"{item.file}:{item.line}"
                fix = f'<div class="fix">fix → {_e(item.fix)}</div>' if item.fix else ""
                rows.append(
                    f'<div class="finding" data-sev="{_e(item.severity)}" '
                    f'data-rule="{_e(rule)}" data-hay="{_e(loc + " " + item.message)}">'
                    f'<span class="dot dot-{_e(item.severity)}"></span>'
                    f'<code class="loc">{_e(loc)}</code>'
                    f'<span class="msg">{_e(item.message)}</span>{fix}</div>'
                )
        blocks.append(
            f'<details class="rule-group" data-rule="{_e(rule)}" open>'
            f'<summary><span class="dot dot-{_e(first_sev)}"></span>'
            f'<b>{_e(rule)}</b> <span class="muted">({len(rows)})</span></summary>'
            f'{"".join(rows)}</details>'
        )
    return "\n".join(blocks)


def _filter_bar(rules: List[str]) -> str:
    options = "".join(f'<option value="{_e(r)}">{_e(r)}</option>' for r in rules)
    return (
        '<div class="filter-bar">'
        '<label>严重度 '
        '<select id="sev-filter"><option value="">全部</option>'
        '<option value="error">error</option><option value="warning">warning</option>'
        '<option value="info">info</option></select></label>'
        f'<label>规则 <select id="rule-filter"><option value="">全部</option>{options}</select></label>'
        '<label>搜索 <input id="q" type="search" placeholder="文件或消息片段"></label>'
        '<span id="hit" class="muted"></span>'
        "</div>"
    )


_STATUS_LABEL = {"fresh": "已同步", "stale": "待复核", "broken": "指针失效", "draft": "草稿"}
_STATUS_DOT = {"fresh": "ok", "stale": "warning", "broken": "error", "draft": "info"}


def _logic_entry_html(entry: dict) -> str:
    status = str(entry.get("status", "draft"))
    label = _STATUS_LABEL.get(status, status)
    dot = _STATUS_DOT.get(status, "info")
    secs = []
    for sec in entry.get("sections") or []:
        if isinstance(sec, dict) and sec.get("title"):
            secs.append(
                f'<div class="logic-sec"><h4>{_e(sec["title"])}</h4>'
                f'<div class="sec-body">{_e(sec.get("body", ""))}</div></div>'
            )
    flow = entry.get("flow") or []
    flow_html = ""
    if flow:
        steps = "".join(f"<li>{_e(step)}</li>" for step in flow)
        flow_html = f'<ol class="logic-flow">{steps}</ol>'
    ptrs = []
    for ptr in entry.get("pointers") or []:
        if not isinstance(ptr, dict):
            continue
        loc = str(ptr.get("file", ""))
        if ptr.get("line"):
            loc += f":{ptr['line']}"
            if ptr.get("end") and ptr.get("end") != ptr.get("line"):
                loc += f"-{ptr['end']}"
        sym = f' <code>{_e(ptr["symbol"])}</code>' if ptr.get("symbol") else ""
        role = f'<span class="badge">{_e(ptr.get("role", ""))}</span>' if ptr.get("role") else ""
        ptrs.append(f'<div class="pointer">{role}<code>{_e(loc)}</code>{sym}</div>')
    narrative = entry.get("narrative") or ""
    nar_html = f'<div class="sec-body">{_e(narrative)}</div>' if narrative else ""
    return (
        f'<details class="logic-entry" data-status="{_e(status)}">'
        f'<summary><span class="dot dot-{dot}"></span>'
        f'<b>{_e(entry.get("title") or entry.get("id"))}</b>'
        f'<span class="badge">{_e(label)}</span>'
        f'<span class="muted">{_e(entry.get("group", ""))} · {_e(entry.get("kind", ""))}</span></summary>'
        f'<div class="logic-body"><p>{_e(entry.get("summary", ""))}</p>'
        f'{nar_html}{"".join(secs)}{flow_html}'
        f'<div class="pointers">{"".join(ptrs)}</div></div></details>'
    )


def _logic_section(domains: List[dict]) -> str:
    """近期功能实现：域概览/横切 + 条目（状态、小节、流程、指针），浏览器可折叠阅读。"""
    if not domains:
        return '<p class="empty">（无近期功能实现文档：govern logic init 建域）</p>'
    blocks = []
    for dom in domains:
        title = dom.get("title") or dom.get("domain", "")
        if dom.get("error"):
            blocks.append(
                f'<div class="subgroup"><h3>{_e(title)}</h3>'
                f'<p class="empty">JSON 损坏：{_e(dom["error"])}</p></div>'
            )
            continue
        entries = dom.get("entries") or []
        abnormal = sum(1 for e in entries if e.get("status") in ("stale", "broken"))
        suffix = f" · 异常 {abnormal}" if abnormal else ""
        head = (
            f'<h3>{_e(title)} <span class="muted">({len(entries)} 条{suffix})</span></h3>'
        )
        overview = dom.get("overview") or ""
        ov_html = f'<div class="sec-body">{_e(overview)}</div>' if overview else ""
        cross = []
        for item in dom.get("crosscut") or []:
            if isinstance(item, dict) and item.get("title"):
                cross.append(
                    f'<details class="logic-x"><summary>{_e(item["title"])}</summary>'
                    f'<div class="sec-body">{_e(item.get("body", ""))}</div></details>'
                )
        ordered = sorted(entries, key=lambda e: (str(e.get("group", "")), str(e.get("title", ""))))
        entry_html = "".join(_logic_entry_html(e) for e in ordered)
        blocks.append(f'<div class="subgroup">{head}{ov_html}{"".join(cross)}{entry_html}</div>')
    return "\n".join(blocks)


def render_html(repo: Repo, cfg: dict, findings: List[Finding],
                tasks: List[dict], scan_hash: str) -> str:
    """渲染单文件 HTML；内容全部转义，排序固定，输出可复现。"""
    counts = _counts(findings)
    rules = sorted({item.rule for item in findings})
    title = f"文档全景图 · {repo.root.name or 'repo'}"
    domains = logic_mod.list_domains(repo, cfg)
    board_html = _board_section(tasks)
    findings_html = _findings_section(findings)
    logic_html = _logic_section(domains)
    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="govern-scan-hash" content="{_e(scan_hash)}">
<title>{_e(title)}</title>
<style>
:root {{ --fg:#1f2328; --muted:#6a737d; --line:#d8dee4; --bg:#fff; --soft:#f6f8fa;
  --error:#cf222e; --warning:#9a6700; --info:#0969da; --ok:#1a7f37; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:1.5rem 2rem 3rem; font:14px/1.6 -apple-system,"Segoe UI",
  "Noto Sans SC",sans-serif; color:var(--fg); background:var(--bg); max-width:1200px; }}
h1 {{ font-size:1.5rem; margin:0 0 .25rem; }}
h2 {{ font-size:1.15rem; margin:2rem 0 .6rem; padding-bottom:.3rem;
  border-bottom:1px solid var(--line); }}
h3 {{ font-size:1rem; margin:1rem 0 .4rem; }}
a {{ color:var(--info); }}
code {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:.86em;
  background:var(--soft); padding:.1rem .3rem; border-radius:4px; }}
.muted {{ color:var(--muted); font-size:.85em; }}
.empty {{ color:var(--muted); }}
.cards {{ display:flex; flex-wrap:wrap; gap:.6rem; margin:1rem 0; }}
.card {{ border:1px solid var(--line); border-radius:8px; padding:.6rem .9rem; min-width:6rem; }}
.card .num {{ font-size:1.35rem; font-weight:600; }}
.card .label {{ color:var(--muted); font-size:.8rem; }}
nav.toc {{ display:flex; gap:1rem; flex-wrap:wrap; font-size:.9rem; }}
.tree-row {{ display:flex; align-items:center; gap:.6rem; padding:.12rem 0;
  border-bottom:1px dashed #eef1f4; }}
.tree-row code {{ background:transparent; padding:0; }}
.badge {{ display:inline-block; border:1px solid var(--line); border-radius:999px;
  padding:0 .5rem; font-size:.78rem; color:var(--muted); background:var(--soft); }}
.badge.over {{ color:var(--error); border-color:var(--error); }}
.badge.ok {{ color:var(--ok); border-color:var(--ok); }}
.badge.warn {{ color:var(--warning); border-color:var(--warning); }}
.tier {{ font-size:.75rem; border-radius:4px; padding:0 .35rem; border:1px solid var(--line); }}
.tier-AI读 {{ color:var(--info); border-color:var(--info); }}
.tier-人读 {{ color:var(--ok); border-color:var(--ok); }}
.subgroup {{ margin:.8rem 0 1.2rem; }}
.task {{ border-left:3px solid var(--line); padding:.35rem .7rem; margin:.4rem 0; }}
.task-title {{ font-weight:600; }}
.task-meta {{ display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; margin-top:.2rem; }}
.dot {{ display:inline-block; width:.6rem; height:.6rem; border-radius:50%; margin-right:.35rem; }}
.dot-error {{ background:var(--error); }}
.dot-warning {{ background:var(--warning); }}
.dot-info {{ background:var(--info); }}
.filter-bar {{ display:flex; gap:1rem; flex-wrap:wrap; align-items:center;
  background:var(--soft); border:1px solid var(--line); border-radius:8px; padding:.5rem .8rem; }}
.filter-bar select,.filter-bar input {{ font:inherit; padding:.15rem .3rem; }}
.rule-group {{ border:1px solid var(--line); border-radius:8px; margin:.6rem 0; padding:.3rem .7rem; }}
.rule-group summary {{ cursor:pointer; padding:.2rem 0; }}
.finding {{ padding:.28rem 0 .28rem .2rem; border-top:1px dashed #eef1f4; }}
.finding .loc {{ color:var(--muted); }}
.finding .msg {{ margin-left:.5rem; }}
.finding .fix {{ color:var(--ok); font-size:.84em; margin-left:1.6rem; }}
.finding.hidden {{ display:none; }}
.dot-ok {{ background:var(--ok); }}
.checks {{ margin:.3rem 0 .1rem .4rem; }}
.check {{ padding:.08rem 0; font-size:.92em; }}
.check.done {{ color:var(--muted); text-decoration:line-through; }}
.logic-entry {{ border:1px solid var(--line); border-radius:8px; margin:.5rem 0; padding:.3rem .7rem; }}
.logic-entry summary {{ cursor:pointer; display:flex; gap:.5rem; align-items:center;
  flex-wrap:wrap; padding:.2rem 0; }}
.logic-body {{ padding:.3rem 0 .4rem .4rem; }}
.logic-sec h4 {{ margin:.7rem 0 .2rem; font-size:.95rem; }}
.sec-body {{ white-space:pre-wrap; }}
.logic-x {{ margin:.3rem 0; }}
.logic-x summary {{ cursor:pointer; color:var(--info); }}
.logic-flow {{ margin:.4rem 0; padding-left:1.4rem; }}
.pointers {{ margin-top:.5rem; }}
.pointer {{ display:flex; gap:.5rem; align-items:center; padding:.12rem 0; font-size:.9em; }}
footer {{ margin-top:2.5rem; color:var(--muted); font-size:.8rem; }}
</style>
</head>
<body>
<header>
<h1>{_e(title)}</h1>
<div class="muted">repo: {_e(repo.root)} · scan-hash: <code>{_e(scan_hash)}</code></div>
<nav class="toc">
<a href="#overview">总览</a><a href="#contracts">契约树</a><a href="#docs">文档树</a>
<a href="#board">进度板</a><a href="#logic">近期功能实现</a><a href="#findings">findings</a>
</nav>
</header>

<section id="overview">
<h2>总览</h2>
{_stat_cards(repo, cfg, findings, tasks, domains)}
<p class="muted">error {counts['error']} · warning {counts['warning']} · info {counts['info']}
· 生成物用 govern html 刷新，勿手改</p>
</section>

<section id="contracts">
<h2>契约树（AGENTS 层级）</h2>
{_contract_tree(repo, cfg)}
</section>

<section id="docs">
<h2>文档树（AI读 / 人读）</h2>
{_docs_tree(repo, cfg)}
</section>

<section id="board">
<h2>进度板</h2>
{board_html}
</section>

<section id="logic">
<h2>近期功能实现（域 → 条目 → 机制）</h2>
{logic_html}
</section>

<section id="findings">
<h2>Findings（按规则 + 文件）</h2>
{_filter_bar(rules)}
<div id="findings-list">
{findings_html}
</div>
</section>

<footer>由 governance-kit 生成 · 单文件自包含 · 无外部资源</footer>
<script>
(function () {{
  var sev = document.getElementById('sev-filter');
  var rule = document.getElementById('rule-filter');
  var q = document.getElementById('q');
  var hit = document.getElementById('hit');
  var rows = Array.prototype.slice.call(document.querySelectorAll('.finding'));
  function apply() {{
    var sv = sev ? sev.value : '', rv = rule ? rule.value : '';
    var needle = q ? q.value.trim().toLowerCase() : '';
    var shown = 0;
    rows.forEach(function (row) {{
      var ok = (!sv || row.dataset.sev === sv)
        && (!rv || row.dataset.rule === rv)
        && (!needle || (row.dataset.hay || '').toLowerCase().indexOf(needle) >= 0);
      row.classList.toggle('hidden', !ok);
      if (ok) {{ shown++; }}
    }});
    if (hit) {{ hit.textContent = '显示 ' + shown + ' / ' + rows.length; }}
    document.querySelectorAll('.rule-group').forEach(function (g) {{
      var visible = g.querySelectorAll('.finding:not(.hidden)').length;
      g.style.display = visible ? '' : 'none';
    }});
  }}
  [sev, rule].forEach(function (el) {{ if (el) {{ el.addEventListener('change', apply); }} }});
  if (q) {{ q.addEventListener('input', apply); }}
  apply();
}})();
</script>
</body>
</html>
"""
    return body