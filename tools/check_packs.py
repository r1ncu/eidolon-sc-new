"""角色包自检：每个有目录的包是不是「读得通、不缺必需的、没有还在旧路径上的」。

查不了「写得好不好」——那要人读。这里只查机制：
- `packs/<id>/` 存在时，`persona` / `qq` / `dm` 三样必须在包里（`REQUIRED`）；
- 其余每一项，要么在包里，要么退回旧路径（会列出来），要么干脆没有（`missing`）；
- 每个 `.toml` 能不能解析。

用法：
    python tools/check_packs.py            # 全部
    python tools/check_packs.py <id>       # 一位
退出码 1 表示有需要人看一眼的地方。
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from plugins import packs as pack_file  # noqa: E402

CHARACTERS = ROOT / "characters"
ROSTER = CHARACTERS / "characters.toml"


def main(argv: list[str]) -> int:
    wanted = [a for a in argv if not a.startswith("-")]
    data = tomllib.loads(ROSTER.read_text(encoding="utf-8"))
    characters = data.get("characters") or []

    problems: list[str] = []
    rows: list[tuple[str, str, str, str, str]] = []

    for character in characters:
        cid = character.get("id", "?")
        if wanted and cid not in wanted:
            continue
        home = CHARACTERS / "packs" / cid
        if not home.is_dir():
            continue
        pack = pack_file.Pack(root=CHARACTERS, character_id=cid)
        summary = pack.summary()
        ready = bool(summary["ready"])
        missing = ", ".join(summary["missing"] or []) or "-"
        fallback = ", ".join(summary["fallback"] or []) or "-"
        files = summary["files"] or {}
        rows.append((cid, "OK" if ready else "缺件", str(len(files)), missing, fallback))
        if not ready:
            problems.append(f"{cid}：必需的文件不在包里（{missing}）")
        derived = str(character.get("derived_from") or "").strip()
        # 「哪份特殊设定只属于哪一位」是部署者自己的人设约定，通用工具不认人：
        # 这里只查派生角色有没有误继承（下面那段），不替谁判断招牌该归谁。
        # 派生角色（幼年版、别的年纪）：好感记在本体名下（同一个人一本账），但主角色那套
        # 特殊语气一件都不带。
        if derived:
            if derived not in {c.get("id", "?") for c in characters}:
                problems.append(f"{cid}：derived_from 指向了不存在的角色（{derived}）")
            for kind in ("tease", "tiers"):
                if files.get(kind):
                    problems.append(
                        f"{cid}：派生角色带 {kind}——那是 {derived} 的招牌，她不该继承"
                    )
            if not files.get("night"):
                problems.append(f"{cid}：派生角色没有 night.toml——她几点睡得自己写")
        # 作息是角色数据：写了就要读得通，否则她会按通道默认（23:00）睡，而没人知道为什么。
        night = home / "voice" / "night.toml"
        if night.is_file():
            rule = pack_file.night_rule(pack)
            if rule.work and not (0 <= rule.work <= 23 and 0 <= rule.wake <= 23):
                problems.append(f"{cid}：night.toml 的 work / wake 不是合法钟点")
            if rule.work and rule.wake and rule.work == rule.wake:
                problems.append(f"{cid}：night.toml 里几点睡和几点醒是同一个钟点")

        for path in sorted(home.rglob("*.toml")):
            try:
                tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                problems.append(f"{cid}：{path.relative_to(home).as_posix()} 解析失败（{exc}）")

        stories = home / "facts-stories"
        if stories.is_dir():
            docs = [p for p in stories.rglob("*.md") if p.name != "README.md"]
            if not docs:
                problems.append(f"{cid}：facts-stories 里没有文档")

    width = max((len(r[0]) for r in rows), default=6)
    print(f"{'id':<{width}}  {'包':<4} {'项':>3}  {'缺':<24} 退回旧路径")
    for cid, state, count, missing, fallback in rows:
        print(f"{cid:<{width}}  {state:<4} {count:>3}  {missing:<24} {fallback}")

    if problems:
        print("\n需要人看一眼：")
        for line in problems:
            print("  -", line)
        return 1
    print(f"\n{len(rows)} 个包，全部读得通。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
