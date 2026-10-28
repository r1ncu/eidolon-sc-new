"""角色表自检：每个角色都要有「过线结论」（高中及以上）与两个等级。

判据纪律见 docs/恋爱状态与告白-构想.md §2.5：`plove` / `heavy` 只认游戏剧情与官方设定，
一条判据必须能指到具体位置。这个脚本只查「有没有、合不合法、有没有自相矛盾」，
查不了「判得对不对」——那部分在工作区的 283偶像评级/283偶像_Plove与重女评级.xlsx。

用法：python tools/check_roster.py [--quiet]
退出码 1 表示有需要人看一眼的地方。
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROSTER = ROOT / "characters" / "characters.toml"

SCHOOL_AGE = 15   # 高中生起点；初中 12-14、小学 6-11 不算


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    data = tomllib.loads(ROSTER.read_text(encoding="utf-8"))
    characters = data.get("characters") or []
    problems: list[str] = []
    notes: list[str] = []

    for c in characters:
        cid = c.get("id", "?")
        name = c.get("name", "")
        who = f"{cid}（{name}）"

        if "adult" not in c:
            problems.append(f"{who} 没写 adult——判定会回落到 age，age 也缺就按未成年处理")
        if "age" not in c:
            problems.append(f"{who} 没写 age")

        plove = c.get("plove")
        heavy = c.get("heavy")
        if plove is None:
            problems.append(f"{who} 没写 plove（缺省 3 只是兜底，不该当答案）")
        elif not (0 <= plove <= 5):
            problems.append(f"{who} plove={plove} 越界（0–5）")
        if heavy is None:
            problems.append(f"{who} 没写 heavy（缺省 3 只是兜底，不该当答案）")
        elif not (1 <= heavy <= 5):
            problems.append(f"{who} heavy={heavy} 越界（1–5）")

        age = c.get("age")
        adult = c.get("adult")
        if isinstance(age, int) and adult is not None:
            if adult and age < SCHOOL_AGE:
                problems.append(f"{who} adult=true 但 age={age}——初中及以下不该过线")
            if not adult and age >= SCHOOL_AGE:
                notes.append(f"{who} adult=false 但 age={age} 已过 15——只有仍在校初中/小学才该这样")
            if age == SCHOOL_AGE:
                notes.append(f"{who} age=15 正好在线上——15 岁有初三也有高一，确认 adult 是照学段写的")

        # plove=0 是「这条路不存在」，和「难追」是两回事；有 heavy 却 plove=0 是合法组合。
        if plove == 0 and heavy is not None and heavy >= 4:
            notes.append(f"{who} plove=0 但 heavy={heavy}——合法（重不等于想交往），确认是你想要的")

    if not quiet:
        for line in notes:
            print(f"  · {line}")
    if problems:
        print(f"\n{len(problems)} 处需要处理：")
        for line in problems:
            print(f"  ! {line}")
        return 1
    print(f"{len(characters)} 位角色的 age/adult/plove/heavy 都在，取值范围合法。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
