"""只读三命令（show/refs/docs）与仓库索引忽略项的测试。

覆盖：show 的内容/分页/元信息/指针/任务、路径越界与敏感文件拒绝、refs 聚合、
docs 清单与分级、go/pkg/mod 与新增忽略目录。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import fixtures  # noqa: E402
from fixtures import (  # noqa: E402
    drop,
    frontmatter,
    new_dir,
    set_config,
    write,
    write_lines,
)
from governkit import core  # noqa: E402
from governkit.core import Repo  # noqa: E402

ROOT = TESTS_DIR.parent
ENGINE = ROOT / "engine" / "govern"


class DocInfoCase(unittest.TestCase):
    """每个用例一个 /tmp 迷你仓；CLI 一律走子进程。"""

    def setUp(self):
        self.root = new_dir("governkit-docinfo-")
        self.addCleanup(drop, self.root)

    def cli(self, *args, expect=None):
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


class ShowTests(DocInfoCase):
    def _repo(self):
        write(self.root, "AGENTS.md", "\n".join([
            "# 根",
            "",
            "指向：`docs/方案/a.md`",
            "",
        ]))
        write(self.root, "docs/方案/a.md", "# 方案 A\n")
        write_lines(self.root, "docs/方案/long.md", 10)

    def test_content_has_no_line_numbers_and_carries_metadata(self):
        self._repo()
        env = self.cli_json("--json", "show", "docs/方案/long.md", "--limit", "3")
        data = env["data"]
        self.assertEqual(env["cmd"], "show")
        self.assertEqual(data["path"], "docs/方案/long.md")
        self.assertTrue(data["exists"])
        self.assertEqual(data["lines_total"], 10)
        self.assertEqual(data["offset"], 1)
        self.assertEqual(len(data["content"]), 3)
        for line in data["content"]:
            self.assertNotIn("|", line.split("：")[0])
        self.assertIn("第 1 行", data["content"][0])
        self.assertNotIn("1|", data["content"][0])
        self.assertIsInstance(data["refs"], list)
        self.assertIsNone(data["task"])
        self.assertIn("git_last", data)

    def test_pagination_offset_limit_and_next(self):
        self._repo()
        first = self.cli_json("--json", "show", "docs/方案/long.md", "--offset", "3", "--limit", "4")
        data = first["data"]
        self.assertEqual(data["offset"], 3)
        self.assertEqual(len(data["content"]), 4)
        self.assertIn("第 3 行", data["content"][0])
        self.assertEqual(data["next"], 7)

        last = self.cli_json("--json", "show", "docs/方案/long.md", "--offset", "7", "--limit", "4")
        self.assertEqual(len(last["data"]["content"]), 4)
        self.assertIsNone(last["data"]["next"])

        tail = self.cli_json("--json", "show", "docs/方案/long.md", "--offset", "9", "--limit", "10")
        self.assertEqual(len(tail["data"]["content"]), 2)
        self.assertIsNone(tail["data"]["next"])

    def test_lines_range_overrides_pagination(self):
        self._repo()
        env = self.cli_json("--json", "show", "docs/方案/long.md", "--lines", "2:4")
        data = env["data"]
        self.assertEqual(data["offset"], 2)
        self.assertEqual(len(data["content"]), 3)
        self.assertIn("第 2 行", data["content"][0])
        self.assertIn("第 4 行", data["content"][-1])

    def test_tier_follows_config_prefixes(self):
        self._repo()
        set_config(self.root, {"tiers": {
            "ai_read": ["docs/开发参考"],
            "human_read": ["docs/方案"],
        }})
        write(self.root, "docs/开发参考/实证.md", "# 实证\n")
        cases = {
            "docs/开发参考/实证.md": "ai",
            "docs/方案/a.md": "human",
            "AGENTS.md": "none",
        }
        for rel, tier in cases.items():
            env = self.cli_json("--json", "show", rel)
            self.assertEqual(env["data"]["tier"], tier, rel)

    def test_refs_carry_status_and_resolved_path(self):
        write(self.root, "sub/note.md", "# note\n")
        write(self.root, "docs/方案/a.md", "# a\n")
        write(self.root, "docs/方案/deep.md", "# deep\n")
        write(self.root, "sub/AGENTS.md", "\n".join([
            "# 子目录",
            "",
            "目录相对：`note.md`",
            "根相对：`docs/方案/a.md`",
            "欠限定：`notes/deep.md`",
            "死链：`nope/missing.md`",
            "",
        ]))
        env = self.cli_json("--json", "show", "sub/AGENTS.md")
        refs = {r["target"]: r for r in env["data"]["refs"]}
        self.assertEqual(refs["note.md"]["status"], "ok")
        self.assertEqual(refs["note.md"]["resolved"], "sub/note.md")
        self.assertEqual(refs["docs/方案/a.md"]["status"], "qualified")
        self.assertEqual(refs["docs/方案/a.md"]["resolved"], "docs/方案/a.md")
        self.assertEqual(refs["notes/deep.md"]["status"], "under")
        self.assertIsNone(refs["notes/deep.md"]["resolved"])
        self.assertEqual(refs["nope/missing.md"]["status"], "dead")
        self.assertIsNone(refs["nope/missing.md"]["resolved"])
        for ref in env["data"]["refs"]:
            self.assertEqual(set(ref), {"line", "target", "status", "resolved"})
            self.assertIsInstance(ref["line"], int)

    def test_task_metadata_from_board(self):
        write(self.root, "docs/进度/task-ok.md", frontmatter(
            title="示例任务", status="待验收", owner="alice",
            updated="2026-01-02", goal="验证 show 契约", evidence="commit abc123",
            doc="docs/方案/a.md"))
        write(self.root, "docs/方案/a.md", "# 方案\n")
        set_config(self.root, {"board": {"tasks_dir": "docs/进度"}})
        env = self.cli_json("--json", "show", "docs/进度/task-ok.md")
        task = env["data"]["task"]
        self.assertIsNotNone(task)
        self.assertEqual(task["title"], "示例任务")
        self.assertEqual(task["status"], "待验收")
        self.assertEqual(task["owner"], "alice")
        self.assertEqual(task["updated"], "2026-01-02")
        self.assertEqual(task["goal"], "验证 show 契约")
        self.assertEqual(task["evidence"], "commit abc123")
        self.assertEqual(task["doc"], "docs/方案/a.md")

    def test_traversal_and_absolute_paths_are_rejected(self):
        outside = new_dir("governkit-outside-")
        self.addCleanup(drop, outside)
        outside_file = write(outside, "outside.md", "# 外部\n")
        rel_escape = os.path.relpath(outside_file, self.root)
        for rel in (rel_escape, "/etc/passwd", "docs/../../etc/passwd"):
            env = self.cli_json("--json", "show", rel, expect=2)
            self.assertFalse(env["data"]["exists"], rel)
            self.assertIn("error", env["data"], rel)
            self.assertIn("路径越界", env["data"]["error"], rel)

    def test_sensitive_paths_are_rejected(self):
        write(self.root, ".env", "SECRET=1\n")
        write(self.root, ".env.local", "SECRET=1\n")
        write(self.root, "server.pem", "-----BEGIN-----\n")
        write(self.root, "id.key", "key\n")
        write(self.root, "AGENTS.local.md", "# 本机\n")
        write(self.root, "credentials.json", "{}\n")
        for rel in (".env", ".env.local", "server.pem", "id.key",
                    "AGENTS.local.md", "credentials.json"):
            env = self.cli_json("--json", "show", rel, expect=2)
            self.assertFalse(env["data"]["exists"], rel)
            self.assertIn("error", env["data"], rel)
            self.assertIn("敏感", env["data"]["error"], rel)

    def test_missing_file_reports_not_exists(self):
        env = self.cli_json("--json", "show", "docs/不存在.md")
        self.assertFalse(env["data"]["exists"])
        self.assertEqual(env["data"]["content"], [])
        self.assertIsNone(env["data"]["next"])
        self.assertNotIn("error", env["data"])

    def test_text_mode_is_readable(self):
        self._repo()
        out = self.cli("show", "docs/方案/a.md").stdout
        self.assertIn("path: docs/方案/a.md", out)
        self.assertIn("tier:", out)
        self.assertIn("方案 A", out)


class RefsCmdTests(DocInfoCase):
    def _repo(self):
        set_config(self.root, {"indexes": {"files": ["docs/README.md"]}})
        write(self.root, "AGENTS.md", "\n".join([
            "# 根",
            "",
            "指针：`docs/方案/a.md`",
            "",
        ]))
        write(self.root, "docs/README.md", "\n".join([
            "# 索引",
            "",
            "指针：[方案](docs/方案/a.md)",
            "",
        ]))
        write(self.root, "docs/方案/a.md", "# 方案\n")
        write(self.root, "docs/方案/带死链.md", "死链：`nope/missing.md`\n")
        write(self.root, "docs/方案/无指针.md", "# 无指针\n")

    def test_refs_lists_only_files_with_pointers(self):
        self._repo()
        env = self.cli_json("--json", "refs")
        self.assertEqual(env["cmd"], "refs")
        paths = [item["path"] for item in env["data"]["files"]]
        self.assertEqual(paths, ["AGENTS.md", "docs/README.md"])
        refs = env["data"]["files"][0]["refs"]
        self.assertEqual(refs[0]["target"], "docs/方案/a.md")
        self.assertEqual(refs[0]["status"], "ok")
        self.assertEqual(refs[0]["resolved"], "docs/方案/a.md")

    def test_refs_scope_follows_agents_glob_and_indexes(self):
        self._repo()
        write(self.root, "pkg/AGENTS.md", "# 子\n\n指针：`docs/方案/a.md`\n")
        env = self.cli_json("--json", "refs")
        paths = [item["path"] for item in env["data"]["files"]]
        self.assertIn("pkg/AGENTS.md", paths)
        self.assertIn("docs/README.md", paths)

    def test_refs_text_mode_lists_sources(self):
        self._repo()
        out = self.cli("refs").stdout
        self.assertIn("AGENTS.md", out)
        self.assertIn("[ok]", out)


class DocsCmdTests(DocInfoCase):
    def _repo(self):
        set_config(self.root, {"tiers": {
            "ai_read": ["docs/开发参考"],
            "human_read": ["docs/方案"],
        }})
        write(self.root, "AGENTS.md", "# 根\n")
        write(self.root, "docs/开发参考/实证.md", "# 实证\n\n指针：`docs/方案/a.md`\n")
        write(self.root, "docs/方案/a.md", "# 方案\n")
        write(self.root, "docs/方案/死链.md", "死链：`nope/missing.md`\n")
        write(self.root, "src/app.py", "print(1)\n")

    def test_docs_lists_all_markdown_sorted_with_counts(self):
        self._repo()
        env = self.cli_json("--json", "docs")
        self.assertEqual(env["cmd"], "docs")
        docs = env["data"]["docs"]
        paths = [d["path"] for d in docs]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(paths, [
            "AGENTS.md", "docs/开发参考/实证.md", "docs/方案/a.md", "docs/方案/死链.md"])
        self.assertNotIn("src/app.py", paths)

        by_path = {d["path"]: d for d in docs}
        self.assertEqual(by_path["docs/开发参考/实证.md"]["tier"], "ai")
        self.assertEqual(by_path["docs/方案/a.md"]["tier"], "human")
        self.assertEqual(by_path["AGENTS.md"]["tier"], "none")
        self.assertEqual(by_path["docs/开发参考/实证.md"]["refs"], 1)
        self.assertEqual(by_path["docs/开发参考/实证.md"]["dead"], 0)
        self.assertEqual(by_path["docs/方案/死链.md"]["refs"], 1)
        self.assertEqual(by_path["docs/方案/死链.md"]["dead"], 1)
        self.assertEqual(by_path["docs/方案/a.md"]["lines"], 1)

    def test_docs_text_mode_lists_rows(self):
        self._repo()
        out = self.cli("docs").stdout
        self.assertIn("docs/方案/a.md", out)
        self.assertIn("tier=human", out)


class IgnoreIndexTests(DocInfoCase):
    def test_go_pkg_mod_cache_is_skipped(self):
        write(self.root, "go/pkg/mod/cache/download/x.md", "# x\n")
        write(self.root, "go/main.go", "package main\n")
        repo = Repo(self.root)
        self.assertNotIn("go/pkg/mod/cache/download/x.md", repo.file_set)
        self.assertIn("go/main.go", repo.file_set)

    def test_repo_root_inside_go_mod_cache_indexes_nothing(self):
        write(self.root, "go/pkg/mod/cache/download/x.md", "# x\n")
        repo = Repo(self.root / "go/pkg/mod/cache/download")
        self.assertEqual(repo.files, [])

    def test_new_ignore_dirs_are_skipped(self):
        for name in (".bun", ".npm", ".nvm", ".local", ".cargo",
                     ".rustup", ".gradle", ".m2"):
            self.assertIn(name, core.IGNORE_DIRS)
            write(self.root, f"{name}/cache/file.md", "# x\n")
        write(self.root, "keep/file.md", "# keep\n")
        repo = Repo(self.root)
        self.assertIn("keep/file.md", repo.file_set)
        for rel in repo.files:
            self.assertFalse(rel.startswith((".bun/", ".npm/", ".nvm/", ".local/",
                                             ".cargo/", ".rustup/", ".gradle/", ".m2/")), rel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
