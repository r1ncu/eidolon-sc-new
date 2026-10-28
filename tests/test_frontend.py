"""前端的静态检查：写进 class 里的 Tailwind 工具类，CSS 产物里必须真的有。

Tailwind v4 的 CSS 只含源码里用过的类。搬原版的 CSS 时，原版没用过而我们用了的
那些（left-0、top-full、hover:bg-accent…）产物里就没有——写进 class 也不报错，
只是静默没有样式，最难查。这里把这件事变成一条测试。
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestFrontendClasses(unittest.TestCase):
    def test_every_class_in_app_js_exists_in_the_css(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_classes.py")],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(
            result.returncode,
            0,
            "有些 class 在 CSS 里没有：\n" + result.stdout,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
