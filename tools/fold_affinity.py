"""把旧的好感行折成「一个人一本」。

2026-11-06 起好感按**人物**记（`plugins/affinity.py` 的 `root`）：本体、幼年、if 线共用
一行，群聊和私聊仍旧各算各的。这个脚本收拾历史数据——改动之前，每个形态都写了自己的一行：

    python tools/fold_affinity.py --db /data/scbot.db --characters characters
    python tools/fold_affinity.py --db /data/scbot.db --characters characters --apply

带 `--apply` 才落库，不带只打印要做什么（默认干跑）。

**谁赢**：分数**最高的那一行**（同分取最近动过的）。不取「本体优先」也不取「最近优先」：
合并两本账不能像降级——这条和 `affinity.py` 当初把网页那本拆成群聊/私聊时是同一条理由
（「a group dropping from 95 to 50 overnight would read as 她没那么喜欢我了」）。
赢家连同 `sulk` / `cooling` / `gained_today` 一起搬过来，不合并：两个形态各生各的气，
合成一句「还在生气吗」没有正确答案。

**单行也要改**：只有幼年那一行、本体一行都没有的用户，也得把它改名成本体，否则改了规矩
以后那一行就成了孤儿（读本体时读不到，等于退回起点）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.store import Store  # noqa: E402
from plugins.characters import Characters  # noqa: E402


def _rows(store: Store, table: str) -> list[dict]:
    if table == "affinity":
        return [
            dict(row)
            for row in store.query(
                "SELECT user_id, character_id, value, updated_at, cooling,"
                " gained_today, gained_on FROM affinity"
            )
        ]
    return [
        dict(row)
        for row in store.query(
            "SELECT user_id, character_id, surface, value, updated_at, cooling,"
            " gained_today, gained_on, sulk FROM affinity_scenes"
        )
    ]


def _groups(rows: list[dict], family) -> dict[tuple, list[dict]]:
    """Rows that should end up as one, or that are filed under the wrong id.

    不只看「一对多」：一行但 id 不是本体，也得改名——不然新规矩读本体时读不到它。
    """
    found: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row["user_id"], family(row["character_id"]), row.get("surface", ""))
        found.setdefault(key, []).append(row)
    return {
        key: group
        for key, group in found.items()
        if len(group) > 1 or group[0]["character_id"] != key[1]
    }


def _winner(group: list[dict]) -> dict:
    """分数最高的那一行，同分取最近动过的。

    合并不能像降级：87 和 22 合成 22 会让「她突然没那么喜欢我了」。这条跟
    `affinity.py` 里「把网页那本拆成群聊/私聊」用的是同一条理由。
    """
    return max(group, key=lambda row: (round(float(row["value"]), 3), str(row["updated_at"])))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--characters", default="characters")
    parser.add_argument("--apply", action="store_true", help="真的写库（默认只打印）")
    args = parser.parse_args()

    store = Store(args.db)
    characters = Characters(args.characters)
    moved = dropped = 0
    for table in ("affinity", "affinity_scenes"):
        for (user, whole, surface), group in sorted(
            _groups(_rows(store, table), characters.family).items()
        ):
            keep = _winner(group)
            losers = [row for row in group if row is not keep]
            where = f"{user[:8]}… {whole}" + (f"/{surface}" if surface else "")
            print(
                f"{table:<17}{where:<26} keep {keep['character_id']}({float(keep['value']):.1f})"
                f"  drop " + "、".join(f"{r['character_id']}({float(r['value']):.1f})" for r in losers)
            )
            if not args.apply:
                continue
            if keep["character_id"] != whole:
                if table == "affinity":
                    store.execute(
                        "UPDATE affinity SET character_id = ? WHERE user_id = ? AND character_id = ?",
                        (whole, user, keep["character_id"]),
                    )
                else:
                    store.execute(
                        "UPDATE affinity_scenes SET character_id = ?"
                        " WHERE user_id = ? AND character_id = ? AND surface = ?",
                        (whole, user, keep["character_id"], surface),
                    )
                moved += 1
            for row in losers:
                if table == "affinity":
                    store.execute(
                        "DELETE FROM affinity WHERE user_id = ? AND character_id = ?",
                        (user, row["character_id"]),
                    )
                else:
                    store.execute(
                        "DELETE FROM affinity_scenes"
                        " WHERE user_id = ? AND character_id = ? AND surface = ?",
                        (user, row["character_id"], surface),
                    )
                dropped += 1
    print(f"\n{'已写入' if args.apply else '干跑'}：改名 {moved} 行、删除 {dropped} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
