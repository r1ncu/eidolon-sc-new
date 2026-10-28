"""查前端用到的 Tailwind 工具类在 CSS 产物里是否真的存在。

Tailwind v4 的 CSS 产物只包含**源码里出现过**的工具类。搬运原版的 CSS 时，
原版没用过的类（比如原版靠 base-ui 的 focus 机制，我们改用 hover）产物里就没有，
写进 class 字符串也不会有任何样式——静默失效，最难查。

用法：python tools/check_classes.py
（在 scbot-new/ 下跑；退出码非 0 表示有类缺失）
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "web" / "assets"
SRC = ASSETS / "app.js"
CSS_FILES = ("css/app.css", "css/zh.css", "css/ja.css")


def escape(cls: str) -> str:
    """Tailwind v4 的选择器里，: / . / [ ] 等字符要转义。"""
    return re.sub(r"([:./\[\]()%,#!])", r"\\\1", cls)


def simple(token: str) -> bool:
    """只查「单个工具类」那种。复合选择器（*:…、group/…、[…]、&…）没法按字符串比对，跳过。"""
    if not token or token in {"%", "*", "==", "?"}:
        return False
    if not re.fullmatch(r"[a-z][a-z0-9-]*(:[a-z0-9./\[\]%()-]+)?", token):
        return False
    if any(ch in token for ch in "*[&"):
        return False
    return True


def main() -> int:
    css = "".join((ASSETS / name).read_text(encoding="utf-8") for name in CSS_FILES)
    src = SRC.read_text(encoding="utf-8")

    used: set[str] = set()
    for match in re.finditer(r'class="([^"]*)"', src):
        for token in match.group(1).split():
            if token and "${" not in token:
                used.add(token)

    missing = sorted(
        token for token in used if simple(token) and f".{escape(token)}" not in css
    )
    if missing:
        print(f"CSS 里没有的类（{len(missing)} 个）：")
        for token in missing:
            print(f"  {token}")
        print("\n要么改 class，要么把规则补进 css/app.css。")
        return 1
    print(f"都对得上：{len(used)} 个类（简单工具类逐个查过），CSS 里都有。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
