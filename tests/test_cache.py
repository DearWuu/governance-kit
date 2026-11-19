"""持久缓存（governkit/cache.py）测试：索引 TTL、show 命中、mtime 失效、
--no-cache 旁路、cache --clear、损坏文件容错。

覆盖：core.Repo 索引缓存命中/过期、docinfo show 二次命中免磁盘读、源文件改动即失效、
docs 聚合缓存、CLI 契约（--no-cache 不写缓存、cache --clear 清缓存）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import fixtures  # noqa: E402
from fixtures import drop, new_dir, write  # noqa: E402
from governkit import cache as cache_mod  # noqa: E402
from governkit import core, docinfo  # noqa: E402

ROOT = TESTS_DIR.parent
ENGINE = ROOT / "engine" / "govern"


class CacheCase(unittest.TestCase):
    """每个用例一个 /tmp 迷你仓 + 一个隔离的缓存目录。"""

    def setUp(self):
        self.root = new_dir("governkit-cache-")
        self.addCleanup(drop, self.root)
        self.cache_home = new_dir("governkit-cachedir-")
        self.addCleanup(drop, self.cache_home)
        self._env = dict(os.environ)
        os.environ["GOVERN_CACHE_DIR"] = str(self.cache_home)
        self.addCleanup(self._restore_env)
        cache_mod.set_enabled(True)
        self.addCleanup(cache_mod.set_enabled, True)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._env)

    def write(self, rel: str, text: str) -> Path:
        return write(self.root, rel, text)

    def cfg(self) -> dict:
        return core.load_config(self.root)

    def cache_files(self):
        return sorted(self.cache_home.glob("*.json"))

    def cli(self, *args, expect=0):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [sys.executable, str(ENGINE), "--repo", str(self.root), *args]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, cwd=str(ROOT), timeout=180,
        )
        self.assertEqual(
            proc.returncode, expect,
            f"命令 {' '.join(cmd)} 退出码异常\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        return proc

    def cli_json(self, *args, expect=0):
        proc = self.cli(*args, expect=expect)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            self.fail(f"{' '.join(args)} 未输出合法 JSON\nSTDOUT:\n{proc.stdout}")


class IndexCacheTests(CacheCase):
    def test_second_index_build_skips_walk(self):
        self.write("AGENTS.md", "# 根\n")
        real_walk = os.walk
        calls = []

        def counting_walk(*args, **kwargs):
            calls.append(args[0] if args else None)
            return real_walk(*args, **kwargs)

        with mock.patch.object(core.os, "walk", counting_walk):
            first = core.Repo(self.root)
            second = core.Repo(self.root)
        self.assertEqual(len(calls), 1, "第二次建索引应命中缓存，不再 os.walk")
        self.assertFalse(first.index_cached)
        self.assertTrue(second.index_cached)
        self.assertEqual(first.files, second.files)
        self.assertEqual(second.by_basename, first.by_basename)
        self.assertEqual(second.summary_hash(), first.summary_hash())

    def test_index_ttl_expires_and_rebuilds(self):
        self.write("AGENTS.md", "# 根\n")
        os.environ["GOVERN_INDEX_TTL"] = "0"
        real_walk = os.walk
        calls = []

        def counting_walk(*args, **kwargs):
            calls.append(args[0] if args else None)
            return real_walk(*args, **kwargs)

        with mock.patch.object(core.os, "walk", counting_walk):
            core.Repo(self.root)
            again = core.Repo(self.root)
        self.assertEqual(len(calls), 2, "TTL=0 时缓存立即过期，必须重建索引")
        self.assertFalse(again.index_cached)

    def test_cache_file_lives_in_cache_dir_named_by_repo_hash(self):
        self.write("AGENTS.md", "# 根\n")
        core.Repo(self.root)
        cache_mod.flush_all()
        path = cache_mod.cache_file(self.root)
        self.assertEqual(path.parent, self.cache_home)
        self.assertEqual(path.name, f"{cache_mod.repo_key(self.root)}.json")
        self.assertTrue(path.exists(), "索引缓存应落盘")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("entries", payload)

    def test_corrupt_cache_file_is_tolerated(self):
        self.write("AGENTS.md", "# 根\n")
        core.Repo(self.root)
        cache_mod.flush_all()
        path = cache_mod.cache_file(self.root)
        path.write_text("{ 这不是合法 JSON", encoding="utf-8")
        cache_mod._mem.clear()
        repo = core.Repo(self.root)
        self.assertIn("AGENTS.md", repo.file_set)
        self.assertFalse(repo.index_cached)


class ShowCacheTests(CacheCase):
    def _repo(self):
        self.write("AGENTS.md", "# 根\n")
        self.write("docs/a.md", "\n".join(f"第 {i} 行" for i in range(1, 51)) + "\n")

    def _count_reads(self):
        calls = []
        real = Path.read_bytes

        def counting(self, *args, **kwargs):
            calls.append(str(self))
            return real(self, *args, **kwargs)

        return calls, counting

    def test_second_show_hits_cache_without_disk_read(self):
        self._repo()
        cfg = self.cfg()
        first = docinfo.build_show(core.Repo(self.root), cfg, "docs/a.md", offset=1, limit=10)
        self.assertEqual(first["lines_total"], 50)
        self.assertEqual(len(first["content"]), 10)

        calls, counting = self._count_reads()
        with mock.patch.object(Path, "read_bytes", counting):
            second = docinfo.build_show(core.Repo(self.root), cfg, "docs/a.md", offset=1, limit=10)
        self.assertEqual(calls, [], "缓存命中时不应再读磁盘")
        self.assertEqual(second["content"], first["content"])
        self.assertEqual(second["lines_total"], 50)
        self.assertEqual(second["next"], first["next"])
        self.assertEqual(second["refs"], first["refs"])

    def test_second_show_does_not_fork_git(self):
        self._repo()
        cfg = self.cfg()
        docinfo.build_show(core.Repo(self.root), cfg, "docs/a.md")
        calls = []
        real_run = subprocess.run

        def counting_run(*args, **kwargs):
            calls.append(args[0] if args else None)
            return real_run(*args, **kwargs)

        with mock.patch.object(docinfo.subprocess, "run", counting_run):
            docinfo.build_show(core.Repo(self.root), cfg, "docs/a.md")
        self.assertEqual(calls, [], "git_last 命中缓存时不应再 fork git")

    def test_mtime_change_invalidates_show_cache(self):
        self._repo()
        cfg = self.cfg()
        first = docinfo.build_show(core.Repo(self.root), cfg, "docs/a.md")
        self.assertEqual(first["lines_total"], 50)

        path = self.root / "docs/a.md"
        st = path.stat()
        self.write("docs/a.md", "# 改过了\n只剩两行\n")
        os.utime(path, ns=(st.st_atime_ns + 10 ** 9, st.st_mtime_ns + 10 ** 9))

        second = docinfo.build_show(core.Repo(self.root), cfg, "docs/a.md")
        self.assertEqual(second["lines_total"], 2)
        self.assertNotEqual(second["content"], first["content"])

    def test_docs_aggregate_second_call_skips_disk_read(self):
        self._repo()
        cfg = self.cfg()
        first = docinfo.build_docs(core.Repo(self.root), cfg)
        self.assertTrue(first)

        calls = []
        real = Path.read_bytes

        def counting(self, *args, **kwargs):
            calls.append(str(self))
            return real(self, *args, **kwargs)

        with mock.patch.object(Path, "read_bytes", counting):
            second = docinfo.build_docs(core.Repo(self.root), cfg)
        self.assertEqual(calls, [], "聚合缓存命中时不应再读磁盘")
        self.assertEqual(second, first)


class CliCacheTests(CacheCase):
    def test_no_cache_bypasses_every_cache(self):
        self.write("AGENTS.md", "# 根\n")
        self.write("docs/a.md", "# a\n")
        env = self.cli_json("--no-cache", "--json", "show", "docs/a.md")
        self.assertTrue(env["data"]["exists"])
        self.assertEqual(self.cache_files(), [], "--no-cache 不应写任何缓存")

        env2 = self.cli_json("--json", "show", "docs/a.md")
        self.assertTrue(env2["data"]["exists"])
        self.assertTrue(self.cache_files(), "默认应写缓存")

    def test_no_cache_flag_accepted_after_subcommand(self):
        self.write("docs/a.md", "# a\n")
        env = self.cli_json("--json", "show", "docs/a.md", "--no-cache")
        self.assertTrue(env["data"]["exists"])
        self.assertEqual(self.cache_files(), [])

    def test_cache_clear_removes_repo_cache(self):
        self.write("AGENTS.md", "# 根\n")
        self.write("docs/a.md", "# a\n")
        self.cli_json("--json", "show", "docs/a.md")
        self.assertTrue(self.cache_files())
        env = self.cli_json("--json", "cache", "--clear")
        self.assertEqual(env["cmd"], "cache")
        self.assertEqual(env["data"]["removed"], 1)
        self.assertEqual(self.cache_files(), [], "cache --clear 应清掉本仓库缓存")

    def test_cache_clear_all_empties_cache_dir(self):
        self.write("docs/a.md", "# a\n")
        self.cli_json("--json", "show", "docs/a.md")
        self.assertTrue(self.cache_files())
        env = self.cli_json("--json", "cache", "--clear", "--all")
        self.assertEqual(env["data"]["removed"], 1)
        self.assertEqual(self.cache_files(), [])

    def test_cache_reports_dir_and_files(self):
        env = self.cli_json("--json", "cache")
        self.assertEqual(env["cmd"], "cache")
        self.assertEqual(env["data"]["dir"], str(self.cache_home))
        self.assertIsInstance(env["data"]["files"], list)
        self.assertIsInstance(env["data"]["bytes"], int)

    def test_corrupt_cache_file_does_not_break_cli(self):
        self.write("docs/a.md", "# a\n")
        self.cli_json("--json", "show", "docs/a.md")
        path = self.cache_files()[0]
        path.write_text("{ 坏掉的 JSON", encoding="utf-8")
        env = self.cli_json("--json", "show", "docs/a.md")
        self.assertTrue(env["data"]["exists"])
        self.assertEqual(env["data"]["content"], ["# a"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
