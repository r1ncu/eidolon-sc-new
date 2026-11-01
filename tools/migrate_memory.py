"""把 `characters/memory/*.md` 搬进按角色分的目录。

记忆以前是「这个屋子的」：`memory/group-123456789.md`。现在它是「她的」：
`memory/<角色 id>/group-123456789.md`。不搬的话，换角色之后新角色读不到旧记忆（看起来像
失忆），而共用一份目录时她读到的可能是上一位的经历（看起来像人格错乱）。

    python tools/migrate_memory.py characters            # 只看看会发生什么
    python tools/migrate_memory.py characters --apply    # 真的复制过去

**只复制，不删除。** 旧文件留在原地当退路：回滚镜像之后，旧代码还能读到它们。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def plan(root: Path, character: str) -> list[tuple[Path, Path]]:
    """`(旧位置, 新位置)`, 只列真的需要搬的：新位置已经有同名文件就跳过。"""
    memory = root / "memory"
    if not memory.is_dir():
        return []
    target = memory / character
    moves: list[tuple[Path, Path]] = []
    for path in sorted(memory.glob("*.md")):
        landed = target / path.name
        if landed.exists():
            continue
        moves.append((path, landed))
    return moves


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把记忆搬进按角色分的目录")
    parser.add_argument("characters", type=Path, help="characters/ 目录")
    parser.add_argument("--character", default="", help="角色 id；默认取 QQ_CHARACTER 或目录里唯一一项")
    parser.add_argument("--apply", action="store_true", help="真的复制（默认只打印）")
    args = parser.parse_args(argv)

    root: Path = args.characters
    character = args.character.strip()
    if not character:
        import os

        character = (os.environ.get("QQ_CHARACTER") or "").strip()
    if not character:
        names = sorted(p.name for p in (root / "memory").glob("*") if p.is_dir())
        if len(names) == 1:
            character = names[0]
    if not character:
        sys.stderr.write("不知道搬给谁：用 --character <id> 指定\n")
        return 2

    moves = plan(root, character)
    if not moves:
        print(f"没有需要搬的（memory/ 下没有散落的 *.md，或 {character}/ 里已经有了）")
        return 0

    for old, new in moves:
        size = old.stat().st_size
        print(f"{'搬' if args.apply else '会搬'} {old.relative_to(root)} -> {new.relative_to(root)}（{size} 字节）")
        if args.apply:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, new)
    if args.apply:
        print(f"完成：{len(moves)} 份。旧文件没有删，回滚镜像时还能读到。")
        print("容器里的 uid 是 10001:999，如果新目录是 root 建的，记得 chown。")
    else:
        print("（只是预览。加 --apply 才会真的复制。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
