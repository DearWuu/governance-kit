"""governkit.logic 契约测试：夹具 + 前 8 个用例。"""

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

try:
    from governkit import core
except ImportError:  # pragma: no cover - 引擎未落盘时跳过
    core = None

try:
    from governkit import logic
except ImportError:  # pragma: no cover - logic 未实现时跳过
    logic = None


def pointer_range(entry):
    """取首条 pointer 的 (start, end)，兼容 start/line 两种字段命名。"""
    pointer = entry["pointers"][0]
    start = pointer.get("start", pointer.get("line"))
    end = pointer.get("end")
    return start, end


def stored_hash(entry):
    """取条目上已存盘的源码哈希（可能存在 pointer 上）。"""
    for holder in (entry, entry["pointers"][0] if entry.get("pointers") else {}):
        for key in ("source_hash", "hash"):
            value = holder.get(key)
            if value:
                return value
    return ""


def finding_rule(finding):
    """取 finding 的规则名（core.Finding 用 rule 字段，兼容 code 命名）。"""
    for attr in ("rule", "code"):
        value = getattr(finding, attr, None)
        if value:
            return value
    return ""


@unittest.skipIf(logic is None or core is None, "logic 未实现")
class LogicTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = core.Repo(self.root)
        self.cfg = {"logic": {"dir": ".governance/logic"}, "severity": {}}

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def domain_path(self, domain="demo"):
        return self.root / ".governance" / "logic" / f"{domain}.json"

    def init(self, domain="demo", title=""):
        return logic.init_domain(self.repo, self.cfg, domain, title=title)

    def add(self, entry_id="foo", file="sample.py", symbol="foo", domain="demo"):
        return logic.add_entry(self.repo, self.cfg, domain, entry_id, file, symbol=symbol)

    def new_repo(self):
        """新建 Repo 实例：core.Repo 内部缓存文件文本，改文件后必须换实例才看得到新内容。"""
        return core.Repo(self.root)

    def read_domain(self, domain="demo"):
        return json.loads(self.domain_path(domain).read_text(encoding="utf-8"))

    def read_entry(self, domain="demo", entry_id="foo"):
        for entry in self.read_domain(domain).get("entries", []):
            if entry.get("id") == entry_id:
                return entry
        raise AssertionError(f"entry not found: {entry_id}")

    def write_domain_entry(self, entry, domain="demo"):
        path = self.domain_path(domain)
        data = json.loads(path.read_text(encoding="utf-8"))
        for index, current in enumerate(data.get("entries", [])):
            if current.get("id") == entry.get("id"):
                data["entries"][index] = entry
                break
        else:
            raise AssertionError(f"entry not found: {entry.get('id')}")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_init_domain_creates_skeleton(self):
        path = self.init(domain="demo", title="示例域")
        self.assertEqual(path, self.domain_path("demo"))
        raw = path.read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("\n"), "domain JSON 应以换行结尾")
        data = json.loads(raw)
        self.assertEqual(data.get("v"), 1)
        self.assertEqual(data.get("domain"), "demo")
        self.assertEqual(data.get("entries"), [])

    def test_init_domain_rejects_duplicate(self):
        self.init(domain="demo")
        with self.assertRaises(ValueError):
            self.init(domain="demo")

    def test_add_entry_locates_python_function(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        entry = self.add()
        self.assertEqual(pointer_range(entry), (1, 3))
        self.assertEqual(entry.get("ack_hash"), "")
        computed = logic.source_hash(self.repo, entry["pointers"])
        self.assertTrue(computed, "source_hash 应非空")
        stored = stored_hash(entry)
        if stored:
            self.assertEqual(stored, computed)

    def test_add_entry_locates_typescript_function(self):
        self.write("sample.ts", "function foo() {\n  return 1;\n}\n")
        self.init(domain="demo")
        entry = self.add(file="sample.ts")
        start, end = pointer_range(entry)
        self.assertIsNotNone(start)
        self.assertIsNotNone(end)
        self.assertGreaterEqual(end, start)

    def test_add_entry_rejects_duplicate_id(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        with self.assertRaises(ValueError):
            self.add()

    def test_add_entry_rejects_missing_domain(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        with self.assertRaises(ValueError):
            self.add(domain="ghost")

    def test_source_hash_is_deterministic_and_content_sensitive(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        entry = self.add()
        pointers = entry["pointers"]
        first = logic.source_hash(self.repo, pointers)
        second = logic.source_hash(self.repo, pointers)
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.write("sample.py", "def foo():\n    value = 2\n    return value\n")
        self.assertNotEqual(logic.source_hash(self.repo, pointers), first)

    def test_resolve_symbol_missing_and_whole_file(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.assertIsNone(logic.resolve_symbol(self.repo, "sample.py", "absent"))
        self.assertEqual(logic.resolve_symbol(self.repo, "sample.py", ""), (1, 3))


    def test_accept_marks_entry_fresh(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        accepted = logic.accept(self.repo, self.cfg, "demo", "foo")
        entry = self.read_entry()
        self.assertEqual(entry["ack_hash"], logic.source_hash(self.new_repo(), entry["pointers"]))
        self.assertEqual(self.read_domain().get("updated"), datetime.date.today().isoformat())
        status, _detail = logic.entry_status(self.new_repo(), entry)
        self.assertEqual(status, "fresh")
        if isinstance(accepted, dict):
            self.assertEqual(accepted.get("ack_hash"), entry["ack_hash"])

    def test_accept_rejects_missing_entry(self):
        self.init(domain="demo")
        with self.assertRaises(ValueError):
            logic.accept(self.repo, self.cfg, "demo", "absent")

    def test_accept_rejects_broken_entry(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        self.write("sample.py", "def other():\n    value = 1\n    return value\n")
        with self.assertRaises(ValueError):
            logic.accept(self.new_repo(), self.cfg, "demo", "foo")

    def test_sync_reports_changed_and_keeps_narrative(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        entry = self.read_entry()
        entry["narrative"] = "保留这段说明"
        self.write_domain_entry(entry)
        before_hash = entry.get("source_hash", "")
        before_ack = entry.get("ack_hash", "")
        before_updated = entry.get("updated", "")
        self.write("sample.py", "def foo():\n    value = 2\n    return value\n")
        result = logic.sync(self.new_repo(), self.cfg)
        actions = {(item["domain"], item["id"]): item["action"] for item in result}
        self.assertEqual(actions.get(("demo", "foo")), "changed")
        after = self.read_entry()
        self.assertEqual(after["narrative"], "保留这段说明")
        self.assertEqual(after.get("ack_hash", ""), before_ack)
        self.assertEqual(after.get("updated", ""), before_updated)
        self.assertNotEqual(after.get("source_hash", ""), before_hash)
        self.assertEqual(after.get("source_hash", ""), logic.source_hash(self.new_repo(), after["pointers"]))

    def test_sync_reports_moved_when_only_blank_line_inserted(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        self.write("sample.py", "\ndef foo():\n    value = 1\n    return value\n")
        result = logic.sync(self.new_repo(), self.cfg)
        actions = {(item["domain"], item["id"]): item["action"] for item in result}
        self.assertEqual(actions.get(("demo", "foo")), "moved")
        pointer = self.read_entry()["pointers"][0]
        start = pointer.get("line", pointer.get("start"))
        self.assertEqual((start, pointer.get("end")), (2, 4))

    def test_sync_and_status_report_broken_after_symbol_removed(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        self.write("sample.py", "def other():\n    value = 1\n    return value\n")
        repo = self.new_repo()
        status, _detail = logic.entry_status(repo, self.read_entry())
        self.assertEqual(status, "broken")
        result = logic.sync(repo, self.cfg)
        actions = {(item["domain"], item["id"]): item["action"] for item in result}
        self.assertEqual(actions.get(("demo", "foo")), "broken")

    def test_findings_report_stale_after_accept_then_change(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        logic.accept(self.repo, self.cfg, "demo", "foo")
        self.write("sample.py", "def foo():\n    value = 2\n    return value\n")
        # 新语义：entry_status 用实时字节现算 hash，不跑 sync 也能检出 stale
        findings = logic.findings(self.new_repo(), self.cfg)
        stale = [item for item in findings if finding_rule(item) == "LOGIC-STALE"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].severity, "warning")
        self.assertIn("demo.json", stale[0].file)

    def test_line_move_without_content_change_stays_fresh(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        logic.accept(self.repo, self.cfg, "demo", "foo")
        self.write("sample.py", "\ndef foo():\n    value = 1\n    return value\n")
        repo = self.new_repo()
        status, _detail = logic.entry_status(repo, self.read_entry())
        self.assertEqual(status, "fresh")
        findings = logic.findings(repo, self.cfg)
        stale = [item for item in findings if finding_rule(item) == "LOGIC-STALE"]
        self.assertEqual(stale, [])

    def test_source_hash_ignores_line_position(self):
        self.write("a/sample.py", "def foo():\n    value = 1\n    return value\n")
        self.write("b/sample.py", "\n\ndef foo():\n    value = 1\n    return value\n")
        hashes = []
        for sub in ("a", "b"):
            repo = core.Repo(self.root / sub)
            logic.init_domain(repo, self.cfg, "demo")
            entry = logic.add_entry(repo, self.cfg, "demo", "foo", "sample.py", symbol="foo")
            hashes.append(logic.source_hash(repo, entry["pointers"]))
        self.assertTrue(hashes[0])
        self.assertEqual(hashes[0], hashes[1])

    def test_sync_twice_on_same_repo_reports_changed_then_unchanged(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        repo = self.repo
        self.write("sample.py", "def foo():\n    value = 2\n    return value\n")
        first = {(item["domain"], item["id"]): item["action"] for item in logic.sync(repo, self.cfg)}
        self.assertEqual(first.get(("demo", "foo")), "changed")
        second = {(item["domain"], item["id"]): item["action"] for item in logic.sync(repo, self.cfg)}
        self.assertEqual(second.get(("demo", "foo")), "unchanged")

    def test_findings_report_broken_with_error_severity(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        self.write("sample.py", "def other():\n    value = 1\n    return value\n")
        findings = logic.findings(self.new_repo(), self.cfg)
        broken = [item for item in findings if finding_rule(item) == "LOGIC-BROKEN"]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0].severity, "error")

    def test_findings_ignore_draft_entries(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        findings = logic.findings(self.new_repo(), self.cfg)
        logic_findings = [item for item in findings if finding_rule(item).startswith("LOGIC")]
        self.assertEqual(logic_findings, [])

    def test_add_entry_rejects_absolute_path(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        with self.assertRaises(ValueError):
            self.add(file=str(self.root / "sample.py"))

    def test_add_entry_rejects_parent_traversal_path(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        with self.assertRaises(ValueError):
            self.add(file="../x.py")

    def test_list_domains_tolerates_corrupt_json(self):
        self.init(domain="demo")
        broken = self.root / ".governance" / "logic" / "broken.json"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{not json", encoding="utf-8")
        domains = logic.list_domains(self.new_repo(), self.cfg)
        self.assertIsInstance(domains, list)

    def test_entry_status_broken_wins_over_draft(self):
        self.write("sample.py", "def foo():\n    value = 1\n    return value\n")
        self.init(domain="demo")
        self.add()
        entry = self.read_entry()
        entry["ack_hash"] = ""
        self.write_domain_entry(entry)
        self.write("sample.py", "def other():\n    value = 1\n    return value\n")
        status, _detail = logic.entry_status(self.new_repo(), self.read_entry())
        self.assertEqual(status, "broken")


if __name__ == "__main__":
    unittest.main()
