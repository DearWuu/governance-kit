"""governance-kit 引擎测试套件（stdlib unittest，可脱离 pytest 独立运行）。

覆盖：仓库索引 / 配置装载 / AGENTS 行数与引用 / 索引双射 / 文档分级 /
进度板（旧式与新式）/ 技能同步 / 适配器编排 / CLI 契约（JSON 信封、退出码、
生成物漂移、打包）。CLI 一律走子进程 [sys.executable, engine/govern ...]。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import fixtures  # noqa: E402
from fixtures import (  # noqa: E402
    by_rule,
    drop,
    findings,
    frontmatter,
    load_config,
    new_dir,
    rules,
    set_config,
    view,
    write,
    write_lines,
    write_json,
)
from governkit.core import Finding, Repo, board_paths  # noqa: E402

ROOT = TESTS_DIR.parent
ENGINE = ROOT / "engine" / "govern"


def iso(days_ago: int = 0) -> str:
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


class TempRepoCase(unittest.TestCase):
    """每个用例一个 /tmp 迷你仓，结束即删。"""

    def setUp(self):
        self.root = new_dir()
        self.addCleanup(drop, self.root)

    def fs(self, rule: str, items=None):
        return by_rule(findings(self.root) if items is None else items, rule)


# ---------------------------------------------------------------- 1. 仓库索引

class RepoIndexTests(TempRepoCase):
    def _populate(self):
        write(self.root, "AGENTS.md", "# 根\n")
        write(self.root, "docs/方案/a.md", "# a\n")
        write(self.root, "src/app.py", "print(1)\n")
        write(self.root, "node_modules/pkg/index.js", "x\n")
        write(self.root, "node_modules/pkg/AGENTS.md", "# 不该被索引\n")
        write(self.root, ".git/config", "[core]\n")
        write(self.root, "dist/out.js", "x\n")

    def test_files_are_relative_posix_and_ignored_dirs_absent(self):
        self._populate()
        repo = Repo(self.root)
        self.assertIn("AGENTS.md", repo.file_set)
        self.assertIn("docs/方案/a.md", repo.file_set)
        self.assertIn("src/app.py", repo.file_set)
        for rel in repo.files:
            self.assertNotIn("node_modules", rel)
            self.assertNotIn(".git/", rel)
            self.assertNotIn("dist/", rel)
            self.assertNotIn("\\", rel)
        self.assertFalse(repo.truncated)

    def test_by_basename_index(self):
        self._populate()
        repo = Repo(self.root)
        self.assertEqual(repo.by_basename["a.md"], ["docs/方案/a.md"])
        self.assertEqual(repo.by_basename["AGENTS.md"], ["AGENTS.md"])
        self.assertNotIn("index.js", repo.by_basename)
        self.assertEqual(sorted(repo.by_basename["app.py"]), ["src/app.py"])

    def test_max_files_truncation_flag(self):
        self._populate()
        repo = Repo(self.root, max_files=2)
        self.assertTrue(repo.truncated)
        self.assertEqual(len(repo.files), 2)

    def test_read_text_and_exists(self):
        self._populate()
        repo = Repo(self.root)
        self.assertTrue(repo.exists("docs/方案/a.md"))
        self.assertFalse(repo.exists("docs/方案/none.md"))
        self.assertEqual(repo.read_text("docs/方案/a.md"), "# a\n")
        self.assertEqual(repo.read_text("docs/方案/none.md"), "")
        self.assertRegex(repo.summary_hash(), r"^[0-9a-f]{16}$")


# ---------------------------------------------------------------- 2. 配置装载

class ConfigTests(TempRepoCase):
    def test_defaults_when_no_config(self):
        cfg = load_config(self.root)
        self.assertEqual(cfg["agents"]["line_budget"], 60)
        self.assertEqual(cfg["agents"]["line_hard"], 100)
        self.assertEqual(cfg["agents"]["glob"], "AGENTS.md")
        self.assertEqual(cfg["board"]["path"], "")            # 空=自动发现
        self.assertEqual(cfg["board"]["tasks_dir"], "")       # 空=自动发现
        self.assertEqual(cfg["board"]["stale_days"], 14)
        self.assertIn("待启动", cfg["board"]["statuses"])
        self.assertEqual(cfg["board"]["legacy_status"]["待办"], "待启动")
        self.assertEqual(cfg["severity"]["AGENTS-LINE"], "warning")
        self.assertEqual(cfg["html"]["path"], "docs/文档全景图.html")
        self.assertEqual(cfg["adapters"], {})

    def test_json_override_deep_merges(self):
        set_config(self.root, {
            "agents": {"line_budget": 40},
            "board": {"stale_days": 7},
        })
        cfg = load_config(self.root)
        self.assertEqual(cfg["agents"]["line_budget"], 40)
        self.assertEqual(cfg["agents"]["line_hard"], 100)       # 深合并保留默认
        self.assertEqual(cfg["board"]["stale_days"], 7)
        self.assertEqual(cfg["board"]["path"], "")  # 同级未覆盖项保留（空=自动发现）
        self.assertIn("待验收", cfg["board"]["statuses"])

    def test_jsonc_comments_tolerated(self):
        write(self.root, ".governance/config.jsonc", "\n".join([
            "{",
            "  // 行注释",
            '  "agents": {"line_budget": 30}, /* 块注释 */',
            '  "board": {"stale_days": 5}',
            "}",
            "",
        ]))
        cfg = load_config(self.root)
        self.assertEqual(cfg["agents"]["line_budget"], 30)
        self.assertEqual(cfg["board"]["stale_days"], 5)

    def test_explicit_config_path_wins(self):
        alt = self.root / "alt.json"
        alt.write_text('{"agents": {"line_budget": 12}}', encoding="utf-8")
        set_config(self.root, {"agents": {"line_budget": 45}})
        cfg = load_config(self.root, alt)
        self.assertEqual(cfg["agents"]["line_budget"], 12)

    def test_default_config_not_mutated_by_override(self):
        set_config(self.root, {"agents": {"line_budget": 40}})
        load_config(self.root)
        self.assertEqual(fixtures.DEFAULT_CONFIG["agents"]["line_budget"], 60)


# ---------------------------------------------------------------- 3. AGENTS-LINE

class AgentsLineTests(TempRepoCase):
    def test_60_lines_ok(self):
        write_lines(self.root, "AGENTS.md", 60)
        self.assertEqual(self.fs("AGENTS-LINE"), [])

    def test_61_lines_warning(self):
        write_lines(self.root, "AGENTS.md", 61)
        hits = self.fs("AGENTS-LINE")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, "warning")
        self.assertEqual(hits[0].file, "AGENTS.md")
        self.assertIn("61", hits[0].message)
        self.assertIn("60", hits[0].message)

    def test_101_lines_error(self):
        write_lines(self.root, "AGENTS.md", 101)
        hits = self.fs("AGENTS-LINE")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].severity, "error")
        self.assertIn("101", hits[0].message)
        self.assertIn("100", hits[0].message)

    def test_nested_agents_md_also_checked(self):
        write_lines(self.root, "pkg/AGENTS.md", 70)
        hits = self.fs("AGENTS-LINE")
        self.assertEqual([h.file for h in hits], ["pkg/AGENTS.md"])
        self.assertEqual(hits[0].severity, "warning")

    def test_severity_override_from_config(self):
        set_config(self.root, {"severity": {"AGENTS-LINE": "error"}})
        write_lines(self.root, "AGENTS.md", 61)
        hits = self.fs("AGENTS-LINE")
        self.assertEqual(hits[0].severity, "error")


# ---------------------------------------------------------------- 4. AGENTS-REF

class AgentsRefTests(TempRepoCase):
    def _populate(self):
        write(self.root, "docs/方案/a.md", "# a\n")
        write(self.root, "docs/方案/deep.md", "# deep\n")
        write(self.root, "sub/note.md", "# note\n")
        write(self.root, "AGENTS.md", "\n".join([
            "# 根",
            "",
            "目录相对：`docs/方案/a.md`",
            "根相对链接：[方案](docs/方案/a.md)",
            "欠限定：`notes/deep.md`",
            "死链：`nope/missing.md`",
            "死链链接：[没了](gone/x.md)",
            "",
            "```text",
            "`nope/fenced.md`",
            "```",
            "",
        ]))
        write(self.root, "sub/AGENTS.md", "\n".join([
            "# 子目录",
            "",
            "目录相对：`note.md`",
            "根相对：`docs/方案/a.md`",
            "",
        ]))

    def test_resolver_three_levels(self):
        self._populate()
        repo = Repo(self.root)
        resolver = rules.Resolver(repo)
        self.assertEqual(resolver.resolve("sub/AGENTS.md", "note.md"), "ok")
        self.assertEqual(resolver.resolve("sub/AGENTS.md", "docs/方案/a.md"), "qualified")
        self.assertEqual(resolver.resolve("AGENTS.md", "notes/deep.md"), "under")
        self.assertEqual(resolver.resolve("AGENTS.md", "nope/missing.md"), "dead")
        # 根相对 + 目录相对在根文件里重合
        self.assertEqual(resolver.resolve("AGENTS.md", "docs/方案/a.md"), "ok")

    def test_dead_and_under_reported_exact_lines(self):
        self._populate()
        hits = [f for f in self.fs("AGENTS-REF") if f.file == "AGENTS.md"]
        self.assertEqual(sorted((h.line, h.message) for h in hits), [
            (5, "疑欠限定路径：notes/deep.md"),
            (6, "引用不存在：nope/missing.md"),
            (7, "引用不存在：gone/x.md"),
        ])
        self.assertTrue(all(h.severity == "warning" for h in hits))
        self.assertTrue(all(h.fix for h in hits))

    def test_ok_paths_do_not_warn(self):
        self._populate()
        hits = self.fs("AGENTS-REF")
        messages = " ".join(h.message for h in hits)
        self.assertNotIn("docs/方案/a.md", messages)
        self.assertNotIn("note.md", messages)
        self.assertEqual([h.file for h in hits if h.file == "sub/AGENTS.md"], [])

    def test_fenced_block_not_reported(self):
        self._populate()
        messages = " ".join(h.message for h in self.fs("AGENTS-REF"))
        self.assertNotIn("nope/fenced.md", messages)

    def test_backtick_and_markdown_link_same_target(self):
        self._populate()
        # `docs/方案/a.md` 与 [方案](docs/方案/a.md) 都命中，均不应报
        hits = [h for h in self.fs("AGENTS-REF") if "docs/方案/a.md" in h.message]
        self.assertEqual(hits, [])

    def test_check_refs_disabled(self):
        self._populate()
        set_config(self.root, {"agents": {"check_refs": False}})
        self.assertEqual(self.fs("AGENTS-REF"), [])


# ---------------------------------------------------------------- 5. INDEX-SYNC

class IndexSyncTests(TempRepoCase):
    def _setup(self, readme: str):
        set_config(self.root, {"indexes": {
            "files": ["docs/README.md"],
            "dirs": ["docs/方案"],
        }})
        write(self.root, "docs/README.md", readme)
        write(self.root, "docs/方案/设计稿.md", "# 设计稿\n")

    def test_unlisted_doc_warns(self):
        self._setup("# 索引\n\n- 别的文档：docs/其他.md\n")
        hits = self.fs("INDEX-SYNC")
        self.assertEqual(len(hits), 1)
        self.assertIn("docs/方案/设计稿.md", hits[0].message)
        self.assertEqual(hits[0].file, "docs/README.md")
        self.assertEqual(hits[0].severity, "warning")

    def test_listed_by_full_name_no_warning(self):
        self._setup("# 索引\n\n- [设计稿](docs/方案/设计稿.md)\n")
        self.assertEqual(self.fs("INDEX-SYNC"), [])

    def test_listed_by_stem_no_warning(self):
        self._setup("# 索引\n\n- 设计稿（待补链接）\n")
        self.assertEqual(self.fs("INDEX-SYNC"), [])

    def test_nested_docs_not_checked(self):
        self._setup("# 索引\n\n- 设计稿\n")
        write(self.root, "docs/方案/子目录/深层.md", "# 深层\n")
        self.assertEqual(self.fs("INDEX-SYNC"), [])


# ---------------------------------------------------------------- 6. DOC-TIER

class DocTierTests(TempRepoCase):
    def _setup(self, body: str, name: str = "docs/方案/设计.md"):
        set_config(self.root, {"tiers": {"human_read": ["docs/方案"]}})
        write(self.root, name, body)

    def test_file_line_ref_and_revision_heading_warn(self):
        self._setup("\n".join([
            "# 设计",
            "",
            "实现见 `app.py:12`。",
            "",
            "## 变更记录",
            "",
            "- 无",
            "",
        ]))
        hits = self.fs("DOC-TIER")
        self.assertEqual(sorted(h.line for h in hits), [3, 5])
        messages = " ".join(h.message for h in hits)
        self.assertIn("文件:行号", messages)
        self.assertIn("修订痕迹", messages)
        self.assertTrue(all(h.file == "docs/方案/设计.md" for h in hits))

    def test_fenced_code_block_exempt(self):
        self._setup("\n".join([
            "# 设计",
            "",
            "```text",
            "实现见 app.py:12",
            "## 变更记录",
            "```",
            "",
        ]))
        self.assertEqual(self.fs("DOC-TIER"), [])

    def test_clean_doc_no_warning(self):
        self._setup("# 设计\n\n只讲原理与取舍，正文不堆代码实证。\n")
        self.assertEqual(self.fs("DOC-TIER"), [])

    def test_docs_outside_human_read_not_checked(self):
        self._setup("# 设计\n\n实现见 app.py:12。\n## 变更记录\n",
                    name="docs/开发参考/实证.md")
        self.assertEqual(self.fs("DOC-TIER"), [])


# ---------------------------------------------------------------- 7. 进度板

class BoardTests(TempRepoCase):
    def _legacy(self, extra: str = ""):
        write(self.root, "进度板.md", "\n".join([
            "# 进度板",
            "",
            "## [待办] 做 A",
            "- 负责：alice",
            f"- 更新：{iso(0)}",
            "",
            "## [部分完成] 做 B",
            "- 负责：bob",
            f"- 更新：{iso(1)}",
            "",
            "## [完成] 做 C",
            "- 更新：2026-01-02",
            "",
            extra,
        ]))
        set_config(self.root, {"board": {"path": "进度板.md", "tasks_dir": ""}})
        return view(self.root)

    def test_legacy_status_mapping_and_fields(self):
        repo, cfg = self._legacy()
        tasks = rules.parse_board(repo, cfg)
        self.assertEqual([t["status"] for t in tasks], ["待启动", "进行中", "已完成"])
        self.assertEqual([t["raw_status"] for t in tasks], ["待办", "部分完成", "完成"])
        self.assertEqual([t["mode"] for t in tasks], ["legacy", "legacy", "legacy"])
        self.assertEqual(tasks[0]["owner"], "alice")
        self.assertEqual(tasks[1]["owner"], "bob")
        self.assertEqual(tasks[1]["title"], "做 B")
        self.assertEqual(tasks[1]["line"], 7)
        self.assertEqual(tasks[1]["updated"], iso(1))

    def test_legacy_board_clean_when_fresh(self):
        self._legacy()
        self.assertEqual(self.fs("BOARD-SCHEMA"), [])
        self.assertEqual(self.fs("BOARD-STALE"), [])

    def test_legacy_illegal_status_and_stale(self):
        write(self.root, "进度板.md", "\n".join([
            "# 进度板",
            "",
            "## [瞎写] 非法条目",
            "- 更新：" + iso(30),
            "",
            "## [部分完成] 停滞条目",
            "- 更新：" + iso(30),
            "",
        ]))
        set_config(self.root, {"board": {"path": "进度板.md", "tasks_dir": ""}})
        schema = self.fs("BOARD-SCHEMA")
        stale = self.fs("BOARD-STALE")
        self.assertEqual(len(schema), 1)
        self.assertIn("非法状态枚举", schema[0].message)
        self.assertEqual(len(stale), 1)
        self.assertIn("30 天", stale[0].message)
        self.assertEqual(stale[0].line, 6)

    def _task(self, name: str, text: str):
        write(self.root, f"进度/{name}.md", text)

    def test_task_file_schema_matrix(self):
        self._task("ok", frontmatter(
            title="合法任务", status="待验收", owner="alice",
            updated=iso(0), goal="验证 schema", evidence=""))
        self._task("no-fields", frontmatter(title="缺字段", status="进行中"))
        self._task("bad-status", frontmatter(
            title="非法状态", status="瞎写", owner="bob", updated=iso(0), goal="g"))
        self._task("done-no-evidence", frontmatter(
            title="完成无证据", status="已完成", owner="carol",
            updated=iso(0), goal="g", evidence=""))
        self._task("done-with-evidence", frontmatter(
            title="完成有证据", status="已完成", owner="dave",
            updated=iso(0), goal="g", evidence="commit abc123"))
        self._task("stale", frontmatter(
            title="停滞任务", status="进行中", owner="erin",
            updated=iso(30), goal="g"))
        repo, cfg = view(self.root)
        tasks = rules.parse_board(repo, cfg)
        self.assertEqual(len(tasks), 6)
        self.assertTrue(all(t["mode"] == "task-file" for t in tasks))
        self.assertEqual(
            sorted(Path(t["file"]).stem for t in tasks),
            ["bad-status", "done-no-evidence", "done-with-evidence",
             "no-fields", "ok", "stale"],
        )

        items = findings(self.root)
        per_file = {}
        for f in items:
            per_file.setdefault(Path(f.file).stem, []).append(f)

        self.assertEqual(per_file.get("ok", []), [])
        self.assertEqual(per_file.get("done-with-evidence", []), [])

        missing = sorted(f.message for f in per_file["no-fields"])
        self.assertEqual(len(missing), 3)
        self.assertTrue(all("任务缺必填字段" in m for m in missing))
        self.assertIn("任务缺必填字段：owner", missing)

        bad = per_file["bad-status"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].rule, "BOARD-SCHEMA")
        self.assertIn("非法状态枚举", bad[0].message)

        no_ev = per_file["done-no-evidence"]
        self.assertEqual(len(no_ev), 1)
        self.assertIn("缺 evidence", no_ev[0].message)

        stale = per_file["stale"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].rule, "BOARD-STALE")
        self.assertIn("30 天", stale[0].message)

    def test_stale_threshold_configurable(self):
        self._task("edge", frontmatter(
            title="临界任务", status="进行中", owner="frank",
            updated=iso(10), goal="g"))
        set_config(self.root, {"board": {"stale_days": 5}})
        stale = self.fs("BOARD-STALE")
        self.assertEqual(len(stale), 1)
        self.assertIn("10 天", stale[0].message)

    def test_invalid_date_reports_schema(self):
        self._task("bad-date", frontmatter(
            title="坏日期", status="进行中", owner="gina",
            updated="2026/01/02", goal="g"))
        schema = self.fs("BOARD-SCHEMA")
        self.assertTrue(any("日期格式非法" in f.message for f in schema))


# ---------------------------------------------------------------- 7.5 进度板位置

class BoardDiscoveryTests(TempRepoCase):
    """进度板位置：显式配置最优先，其次仓库根，最后 docs/ 旧位置。"""

    def _task_file(self, rel: str, title: str):
        write(self.root, rel, frontmatter(
            title=title, status="待启动", owner="alice",
            updated=iso(0), goal="验证位置发现"))

    def test_root_location_wins_over_docs(self):
        self._task_file("进度/task-root.md", "根位置任务")
        self._task_file("docs/进度/task-docs.md", "旧位置任务")
        write(self.root, "进度板.md", "# 进度板\n")
        write(self.root, "docs/进度板.md", "# 进度板\n")
        repo, cfg = view(self.root)
        self.assertEqual(board_paths(repo, cfg), ("进度板.md", "进度"))
        tasks = rules.parse_board(repo, cfg)
        self.assertEqual([t["file"] for t in tasks], ["进度/task-root.md"])

    def test_docs_location_used_when_root_absent(self):
        self._task_file("docs/进度/task-docs.md", "旧位置任务")
        write(self.root, "docs/进度板.md", "# 进度板\n")
        repo, cfg = view(self.root)
        self.assertEqual(board_paths(repo, cfg), ("docs/进度板.md", "docs/进度"))
        tasks = rules.parse_board(repo, cfg)
        self.assertEqual([t["file"] for t in tasks], ["docs/进度/task-docs.md"])

    def test_explicit_config_overrides_discovery(self):
        self._task_file("进度/task-root.md", "根位置任务")
        self._task_file("docs/进度/task-docs.md", "旧位置任务")
        set_config(self.root, {"board": {
            "path": "docs/进度板.md", "tasks_dir": "docs/进度"}})
        repo, cfg = view(self.root)
        self.assertEqual(board_paths(repo, cfg), ("docs/进度板.md", "docs/进度"))
        tasks = rules.parse_board(repo, cfg)
        self.assertEqual([t["file"] for t in tasks], ["docs/进度/task-docs.md"])

    def test_explicit_config_is_a_pair(self):
        """任一键非空即整对显式：未写的那键留空即不启用。"""
        self._task_file("进度/task-root.md", "根位置任务")
        set_config(self.root, {"board": {"path": "进度板.md", "tasks_dir": ""}})
        repo, cfg = view(self.root)
        self.assertEqual(board_paths(repo, cfg), ("进度板.md", ""))
        self.assertEqual(rules.parse_board(repo, cfg), [])

    def test_no_board_anywhere_is_empty_and_silent(self):
        repo, cfg = view(self.root)
        self.assertEqual(board_paths(repo, cfg), ("", ""))
        self.assertEqual(rules.parse_board(repo, cfg), [])
        board_hits = [f for f in findings(self.root) if f.rule.startswith("BOARD-")]
        self.assertEqual(board_hits, [])


# ---------------------------------------------------------------- 8. SKILL-SYNC

class SkillSyncTests(TempRepoCase):
    def _setup(self):
        set_config(self.root, {"skills": {
            "canonical": "skills",
            "copies": ["plugins/skill-copy"],
        }})
        write(self.root, "skills/SKILL.md", "# 技能正文\n")
        write(self.root, "plugins/skill-copy/SKILL.md", "# 技能正文\n")

    def test_identical_bytes_no_warning(self):
        self._setup()
        self.assertEqual(self.fs("SKILL-SYNC"), [])

    def test_divergent_copy_warns(self):
        self._setup()
        write(self.root, "plugins/skill-copy/SKILL.md", "# 技能正文（旧）\n")
        hits = self.fs("SKILL-SYNC")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].file, "plugins/skill-copy/SKILL.md")
        self.assertIn("字节不一致", hits[0].message)
        self.assertIn("govern sync", hits[0].fix)

    def test_missing_copy_warns(self):
        self._setup()
        (self.root / "plugins/skill-copy/SKILL.md").unlink()
        hits = self.fs("SKILL-SYNC")
        self.assertEqual(len(hits), 1)
        self.assertIn("兼容副本缺失", hits[0].message)

    def test_no_copies_configured_is_noop(self):
        write(self.root, "skills/SKILL.md", "# 技能正文\n")
        self.assertEqual(self.fs("SKILL-SYNC"), [])


# ---------------------------------------------------------------- 9. 适配器

class AdapterTests(TempRepoCase):
    def test_missing_command_skipped_silently(self):
        set_config(self.root, {"adapters": {
            "ghost": "definitely-not-installed-xyz-9347 --check",
        }})
        repo, cfg = view(self.root)
        self.assertEqual(rules.run_adapters(repo, cfg), [])
        self.assertEqual(self.fs("ADAPTER"), [])

    def test_failing_command_reports_adapter_finding(self):
        self.assertIsNotNone(shutil.which("false"), "本机需有 /usr/bin/false")
        set_config(self.root, {"adapters": {"failing": "false"}})
        repo, cfg = view(self.root)
        hits = rules.run_adapters(repo, cfg)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].rule, "ADAPTER:failing")
        self.assertIn("退出码 1", hits[0].message)
        self.assertIn("false", hits[0].fix)

    def test_skip_flag_returns_nothing(self):
        set_config(self.root, {"adapters": {"failing": "false"}})
        repo, cfg = view(self.root)
        self.assertEqual(rules.run_adapters(repo, cfg, skip=True), [])
        self.assertEqual(
            [f for f in rules.run_all(repo, cfg, skip_adapters=True)
             if f.rule.startswith("ADAPTER")], [])


# ---------------------------------------------------------------- 10. CLI

class CliCase(TempRepoCase):
    def cli(self, *args, expect=None):
        if not ENGINE.exists():
            self.fail(f"引擎入口缺失：{ENGINE}（CLI 契约尚未实现）")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, str(ENGINE), "--repo", str(self.root), *args]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, cwd=str(ROOT), timeout=180,
        )
        if expect is not None:
            self.assertEqual(
                proc.returncode, expect,
                f"命令 {' '.join(cmd)} 退出码异常\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        return proc

    def cli_json(self, *args, expect=0):
        proc = self.cli(*args, expect=expect)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"{' '.join(args)} 未输出合法 JSON\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

    def mini(self):
        root = fixtures.build_mini()
        self.addCleanup(drop, root)
        self.root = root
        return root


class ScanCliTests(CliCase):
    def test_scan_json_envelope_fields(self):
        self.mini()
        env = self.cli_json("--json", "scan")
        for key in ("v", "cmd", "repo", "data", "summary", "truncated"):
            self.assertIn(key, env)
        self.assertEqual(env["v"], 1)
        self.assertEqual(env["cmd"], "scan")
        self.assertIsInstance(env["repo"], str)
        self.assertTrue(env["repo"])
        self.assertIsInstance(env["data"], dict)
        self.assertIsInstance(env["truncated"], bool)
        for key in ("error", "warning", "info"):
            self.assertIsInstance(env["summary"][key], int)
        self.assertIn("findings", env["data"])
        self.assertIsInstance(env["data"]["findings"], list)
        self.assertRegex(env["data"]["scan_hash"], r"^[0-9a-f]{16}$")

    def test_scan_clean_repo_has_no_findings(self):
        self.mini()
        env = self.cli_json("--json", "scan")
        self.assertEqual(env["data"]["findings"], [])
        self.assertEqual(env["summary"]["error"], 0)
        self.assertEqual(env["summary"]["warning"], 0)

    def test_finding_shape(self):
        write(self.root, "AGENTS.md", "# 根\n\n死链：`nope/missing.md`\n")
        env = self.cli_json("--json", "--no-adapters", "scan")
        items = env["data"]["findings"]
        self.assertTrue(items, "应至少有一条 AGENTS-REF finding")
        for f in items:
            for key in ("rule", "severity", "file", "line", "message", "fix"):
                self.assertIn(key, f, f"finding 缺字段 {key}: {f}")
            self.assertIsInstance(f["line"], int)
            self.assertIn(f["severity"], ("error", "warning", "info"))

    def test_summary_counts_match_findings(self):
        self.mini()
        write(self.root, "AGENTS.md", "# 根\n\n死链：`nope/missing.md`\n")
        env = self.cli_json("--json", "--no-adapters", "scan")
        items = env["data"]["findings"]
        self.assertEqual(env["summary"]["warning"], len(items))

    def test_strict_exits_one_on_error(self):
        self.mini()
        write_lines(self.root, "AGENTS.md", 101)
        env = self.cli_json("--json", "--strict", "--no-adapters", "scan", expect=1)
        self.assertGreaterEqual(env["summary"]["error"], 1)
        rules_hit = {(f["rule"], f["severity"]) for f in env["data"]["findings"]}
        self.assertIn(("AGENTS-LINE", "error"), rules_hit)

    def test_strict_not_triggered_without_error(self):
        self.mini()
        write_lines(self.root, "AGENTS.md", 61)   # 仅 warning
        env = self.cli_json("--json", "--strict", "--no-adapters", "scan", expect=0)
        self.assertEqual(env["summary"]["error"], 0)

    def test_health_only_summary(self):
        self.mini()
        env = self.cli_json("--json", "health")
        self.assertEqual(env["cmd"], "health")
        self.assertEqual(set(env["summary"]), {"error", "warning", "info"})
        # "只 summary"：不返回逐条 findings；data 只允许标量汇总字段
        self.assertNotIn("findings", env["data"])
        for key, val in env["data"].items():
            self.assertIsInstance(
                val, (int, str), f"data.{key} 应是对汇总的标量，实际 {type(val).__name__}")

    def test_no_adapters_flag(self):
        set_config(self.root, {"adapters": {"failing": "false"}})
        write(self.root, "AGENTS.md", "# 根\n")
        env = self.cli_json("--json", "scan")
        self.assertTrue([f for f in env["data"]["findings"] if f["rule"].startswith("ADAPTER")])
        off = self.cli_json("--json", "--no-adapters", "scan")
        self.assertEqual([f for f in off["data"]["findings"] if f["rule"].startswith("ADAPTER")], [])

    def test_board_renders_status(self):
        self.mini()
        out = self.cli("board").stdout
        self.assertIn("示例任务", out)
        self.assertIn("待验收", out)
        self.assertIn("待启动的条目", out)

    def test_view_limit_truncates(self):
        self.mini()
        out = self.cli("view", "docs/方案/long.md", "--limit", "5").stdout
        self.assertIn("L001", out)
        self.assertIn("L005", out)
        self.assertNotIn("L006", out)

    def test_view_lines_range(self):
        self.mini()
        out = self.cli("view", "docs/方案/long.md", "--lines", "3:4").stdout
        self.assertIn("L003", out)
        self.assertIn("L004", out)
        self.assertNotIn("L001", out)
        self.assertNotIn("L005", out)

    def test_outline_lists_function(self):
        self.mini()
        out = self.cli("outline", "src/app.py").stdout
        self.assertIn("alpha_func", out)

    def test_map_shows_contract_chain(self):
        self.mini()
        out = self.cli("map", "src/api_route.py").stdout
        self.assertIn("契约链", out)          # 契约要求：输出含契约链
        self.assertIn("src/api_route.py", out)
        self.assertIn("AGENTS.md", out)       # 链上指向既有文档的引用

    def test_tree_output(self):
        self.mini()
        out = self.cli("tree").stdout
        self.assertIn("AGENTS.md", out)
        docs = self.cli("tree", "--docs").stdout
        self.assertIn("a.md", docs)
        self.assertNotIn("app.py", docs)

    def test_init_creates_loadable_skeleton(self):
        target = self.root / "fresh"
        self.cli("init", str(target), expect=0)
        cfg_path = target / ".governance" / "config.json"
        self.assertTrue(cfg_path.exists(), f"init 未生成 {cfg_path}")
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertTrue(data)
        self.assertTrue((target / "进度板.md").exists(), "init 未在仓库根生成进度板")
        self.assertTrue((target / "进度" / "README.md").exists(),
                        "init 未在仓库根生成进度目录")
        self.assertFalse((target / "docs" / "进度板.md").exists(),
                         "进度板不应再落在 docs/ 下")

    def test_html_then_rescan_no_drift(self):
        self.mini()
        self.cli("html", expect=0)
        html_path = self.root / "docs" / "文档全景图.html"
        self.assertTrue(html_path.exists(), "html 未生成文档全景图")
        text = html_path.read_text(encoding="utf-8")
        m = re.search(r'<meta\s+name="govern-scan-hash"\s+content="([^"]+)"', text)
        self.assertIsNotNone(m, "HTML 缺 govern-scan-hash meta")
        env = self.cli_json("--json", "--no-adapters", "scan")
        self.assertEqual([f for f in env["data"]["findings"] if f["rule"] == "GEN-DRIFT"], [])
        self.assertEqual(env["data"]["scan_hash"], m.group(1))

    def test_scan_twice_is_stable(self):
        self.mini()
        first = self.cli_json("--json", "--no-adapters", "scan")
        second = self.cli_json("--json", "--no-adapters", "scan")
        self.assertEqual(first["data"]["scan_hash"], second["data"]["scan_hash"])

    def test_pack_creates_listable_zip(self):
        self.mini()
        out_dir = new_dir("governkit-pack-")
        self.addCleanup(drop, out_dir)
        zip_path = out_dir / "bundle.zip"
        self.cli("pack", "--out", str(zip_path), expect=0)
        self.assertTrue(zip_path.exists(), "pack 未生成 zip")
        names = zipfile.ZipFile(zip_path).namelist()
        self.assertTrue(any(n.endswith("AGENTS.md") for n in names), names[:20])
        self.assertTrue(any(n.endswith(".governance/config.json") for n in names), names[:20])
        if shutil.which("unzip"):
            proc = subprocess.run(["unzip", "-l", str(zip_path)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("AGENTS.md", proc.stdout)


# ------------------------------------------------------------ 11. 打钩写接口

TASK_WITH_CHECKLIST = """---
title: 打钩任务
status: 待启动
owner: alice
updated: 2026-01-01
goal: 验证进度板打钩写接口
evidence: ""
doc: docs/方案/a.md
---
# 打钩任务

## 完成标准

- [ ] 第一步：跑通 `govern scan`，error 清零
- [ ] 第二步：文档与索引已同步
  - [ ] 嵌套项：缩进与中文原文逐字保留
"""


class CheckCommandTests(CliCase):
    """check 子命令：只翻转 checklist 标记，其余文本一字不改。"""

    rel = "进度/task-check.md"

    def task_repo(self):
        """迷你仓 + 一个带 checklist 的任务文件。"""
        self.mini()
        write(self.root, self.rel, TASK_WITH_CHECKLIST)
        return self.root

    def task_text(self) -> str:
        return (Path(self.root) / self.rel).read_text(encoding="utf-8")

    def test_toggle_on_off_round_trip(self):
        self.task_repo()
        env = self.cli_json("--json", "check", self.rel, "0")
        self.assertEqual(env["cmd"], "check")
        self.assertEqual(env["data"]["path"], self.rel)
        self.assertEqual(env["data"]["index"], 0)
        self.assertTrue(env["data"]["checked"])
        self.assertIn("- [x] 第一步", self.task_text())
        env = self.cli_json("--json", "check", self.rel, "0")
        self.assertFalse(env["data"]["checked"])
        self.assertIn("- [ ] 第一步", self.task_text())

    def test_preserves_indent_and_chinese_text(self):
        self.task_repo()
        self.cli("--json", "check", self.rel, "2", expect=0)
        lines = self.task_text().splitlines()
        self.assertIn("  - [x] 嵌套项：缩进与中文原文逐字保留", lines)
        self.assertIn("- [ ] 第一步：跑通 `govern scan`，error 清零", lines)
        self.assertIn("- [ ] 第二步：文档与索引已同步", lines)

    def test_index_out_of_range_exits_two(self):
        self.task_repo()
        env = self.cli_json("--json", "check", self.rel, "99", expect=2)
        self.assertIn("error", env["data"])
        self.assertIn("- [ ] 第一步", self.task_text())

    def test_non_task_file_exits_two(self):
        self.task_repo()
        target = Path(self.root) / "docs/方案/a.md"
        before = target.read_text(encoding="utf-8")
        env = self.cli_json("--json", "check", "docs/方案/a.md", "0", expect=2)
        self.assertIn("error", env["data"])
        self.assertEqual(before, target.read_text(encoding="utf-8"))

    def test_legacy_board_exits_two(self):
        self.task_repo()
        env = self.cli_json("--json", "check", "进度板.md", "0", expect=2)
        self.assertIn("error", env["data"])
        self.assertIn("legacy", env["data"]["error"])

    def test_updated_refreshed_to_today(self):
        self.task_repo()
        env = self.cli_json("--json", "check", self.rel, "0")
        self.assertEqual(env["data"]["updated"], dt.date.today().isoformat())
        self.assertIn(f"updated: {dt.date.today().isoformat()}", self.task_text())
        self.assertIn("updated: 2026-01-01", TASK_WITH_CHECKLIST)

    def test_board_json_reflects_check_immediately(self):
        self.task_repo()
        self.cli_json("--json", "show", self.rel)          # 预热 show / agg 缓存
        self.cli_json("--json", "check", self.rel, "0")
        board = self.cli_json("--json", "board")
        tasks = [t for g in board["data"]["groups"] for t in g["tasks"]
                 if t["file"] == self.rel]
        self.assertTrue(tasks, "进度板应含该任务文件")
        checklist = tasks[0]["checklist"]
        self.assertEqual(len(checklist), 3)
        self.assertTrue(checklist[0]["checked"])
        self.assertFalse(checklist[1]["checked"])
        self.assertEqual(checklist[0]["text"], "第一步：跑通 `govern scan`，error 清零")
        show = self.cli_json("--json", "show", self.rel)
        self.assertEqual(show["data"]["task"]["updated"], dt.date.today().isoformat())

    def test_on_is_idempotent_on_checked_item(self):
        self.task_repo()
        self.cli("--json", "check", self.rel, "0", "--on", expect=0)
        after_first = self.task_text()
        self.cli("--json", "check", self.rel, "0", "--on", expect=0)
        self.assertEqual(after_first, self.task_text())
        self.assertIn("- [x] 第一步", self.task_text())

    def test_explicit_toggle_flag_flips(self):
        self.task_repo()
        self.cli("--json", "check", self.rel, "0", "--toggle", expect=0)
        self.assertIn("- [x] 第一步", self.task_text())
        self.cli("--json", "check", self.rel, "0", "--toggle", expect=0)
        self.assertIn("- [ ] 第一步", self.task_text())

    def test_on_and_off_are_mutually_exclusive(self):
        self.task_repo()
        self.cli("--json", "check", self.rel, "0", "--on", "--off", expect=2)
        self.cli("--json", "check", self.rel, "0", "--on", "--toggle", expect=2)
        self.assertIn("- [ ] 第一步", self.task_text())

    def test_check_envelope_data_fields(self):
        self.task_repo()
        env = self.cli_json("--json", "check", self.rel, "1")
        data = env["data"]
        for key in ("path", "index", "checked", "line", "updated"):
            self.assertIn(key, data)
        self.assertEqual(data["index"], 1)
        self.assertIsInstance(data["checked"], bool)
        self.assertIsInstance(data["line"], int)
        self.assertGreater(data["line"], 0)

    def test_fenced_example_is_not_checkable(self):
        self.task_repo()
        write(self.root, self.rel, TASK_WITH_CHECKLIST + "\n## 示例\n\n"
              "```markdown\n- [ ] 示例行，不是真待办\n```\n")
        board = self.cli_json("--json", "board")
        task = [t for g in board["data"]["groups"] for t in g["tasks"]
                if t["file"] == self.rel][0]
        self.assertEqual(len(task["checklist"]), 3)
        env = self.cli_json("--json", "check", self.rel, "3", expect=2)
        self.assertIn("error", env["data"])
        self.assertIn("- [ ] 示例行，不是真待办", self.task_text())


class HtmlViewTests(CliCase):
    """不用 OpenCode 的可视化：HTML 全景图含近期功能实现与进度板清单。"""

    def test_html_logic_section_and_checklist(self):
        root = self.mini()
        doc = {
            "v": 1, "domain": "demo", "title": "演示功能", "updated": "2026-11-16",
            "overview": "域概览句子。",
            "crosscut": [{"title": "横切一", "body": "横切正文。"}],
            "entries": [{
                "id": "e1", "title": "演示条目", "group": "功能模块/演示",
                "kind": "module", "summary": "条目摘要。", "narrative": "叙事正文。",
                "sections": [{"title": "功能逻辑", "body": "小节正文。"}],
                "flow": ["步骤一"],
                "pointers": [{"file": "src/app.py", "symbol": "alpha_func",
                              "line": 0, "end": 0, "role": "核心"}],
                "source_hash": "", "ack_hash": "",
            }],
        }
        write(root, ".governance/logic/demo.json",
              json.dumps(doc, ensure_ascii=False, indent=2))
        write(root, "进度/html-demo.md", frontmatter(
            title="HTML 演示任务", status="进行中", owner="验证员",
            updated=dt.date.today().isoformat(), goal="验证全景图", evidence="", doc="",
        ) + "- [x] 已完成项\n- [ ] 未完成项\n")
        self.cli("html", expect=0)
        text = (root / "docs" / "文档全景图.html").read_text(encoding="utf-8")
        self.assertIn('id="logic"', text)
        for needle in ("演示功能", "演示条目", "功能逻辑", "小节正文。", "域概览句子。",
                       "横切一", "src/app.py", "data-status=", "☑ 已完成项", "☐ 未完成项",
                       "1/2"):
            self.assertIn(needle, text, f"HTML 缺 {needle}")

    def test_init_with_skill_installs_all_skills(self):
        self.mini()
        target = self.root / "fresh-skills"
        self.cli("init", str(target), "--with-skill", expect=0)
        kit_root = Path(__file__).resolve().parents[1]
        for name in ("governance-kit", "recent-features", "govern-view"):
            installed = target / ".agents" / "skills" / name / "SKILL.md"
            self.assertTrue(installed.exists(), f"init 未安装技能 {name}")
            canonical = (kit_root / "skill" / "SKILL.md" if name == "governance-kit"
                         else kit_root / "skills" / name / "SKILL.md")
            self.assertEqual(installed.read_bytes(), canonical.read_bytes(),
                             f"技能 {name} 副本与 canonical 不一致")


if __name__ == "__main__":
    unittest.main(verbosity=2)