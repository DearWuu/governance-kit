"""测试便捷入口：等价于 `python3 -m unittest discover -s tests -v`。

用法：
    python3 tests/run.py                              # 全量，详细输出
    python3 tests/run.py -q                           # 简洁输出
    python3 tests/run.py test_governkit.ScanCliTests  # 只跑某个类/模块（点号路径）
退出码：全绿 0，有失败/错误 1。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    verbosity = 1 if "-q" in args else 2
    targets = [a for a in args if not a.startswith("-")]

    loader = unittest.TestLoader()
    if targets:
        suite = loader.loadTestsFromNames(targets)
    else:
        suite = loader.discover(
            str(TESTS_DIR), pattern="test*.py", top_level_dir=str(TESTS_DIR)
        )

    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())