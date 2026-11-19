"""新语法（PEP 695 泛型 / except*）下指针定位的回退验证。

背景：仓库目标版本是 Python 3.12+，而引擎可能运行在 3.10，ast.parse 直接
SyntaxError；此时必须退回正则定位，不能把「解析不了」误报成「指针失效」。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from governkit import core, logic  # noqa: E402


class Pep695FallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "src").mkdir()
        (root / "src" / "runtime.py").write_text(
            "import asyncio\n\n"
            "def run_async[T](coro):\n"
            "    return asyncio.run(coro)\n\n"
            "async def _safe[T](value: T) -> T:\n"
            "    try:\n"
            "        return value\n"
            "    except* ValueError:\n"
            "        raise\n",
            encoding="utf-8",
        )
        self.repo = core.Repo(root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_generic_function_resolves(self) -> None:
        span = logic.resolve_symbol(self.repo, "src/runtime.py", "run_async")
        self.assertIsNotNone(span)
        start, end = span
        self.assertEqual(start, 3)
        self.assertGreaterEqual(end, 4)

    def test_async_generic_and_except_star_resolves(self) -> None:
        span = logic.resolve_symbol(self.repo, "src/runtime.py", "_safe")
        self.assertIsNotNone(span)
        start, end = span
        self.assertEqual(start, 6)

    def test_missing_symbol_still_none(self) -> None:
        self.assertIsNone(logic.resolve_symbol(self.repo, "src/runtime.py", "no_such_fn"))


if __name__ == "__main__":
    unittest.main()
