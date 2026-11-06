"""`qq_flags` 的读写——一个 key→value 的小 KV 表，QQ 通道、网页设置页、表情包都靠它记状态。

统一在这里，是因为这件事以前在 `qq.py`、`plugin_qq.py`、`web_compat.py`、`stickers.Pack`
各写了一份 SQL，而且**不一致**：

- 缺值时有的返 `None`（`qq.py::_flag`，给 `_switch_overrides` 用 `is not None` 区分
  「没拨过开关」和「拨成 0」），有的返 `""`（其余几处）。
- 时间戳有的写 UTC（`qq.py` / `plugin_qq.py`），有的写机器本地（`web_compat.py`），
  有的写本地带偏移（`Pack` 用 `time.strftime("%Y-%m-%dT%H:%M:%S%z")`）。
- 失败时有的 log（`qq.py`），有的吞（`plugin_qq.py`），有的抛（`web_compat.py` / `Pack`）。

这里保住 `None` 语义（`get` 返 `None`），给 `""` 语义一个 `require`；时间戳统一 UTC
isoformat——`qq_flags.updated_at` 没有任何代码读回来，只是人看的，统一后不再三种混写；
失败一律 log 不抛，「没有那张表也得能说话」是项目硬约束（`Store.execute` 在表没建时
会抛，而单测里经常有只起一个 `QQChannel`、不起整个 `QQService` 的场景）。

**没把 `qq_flags` 的建表语句搬过来**：那张表的 `CREATE TABLE` 一直由 `stickers.SCHEMA`
带上、跟着 `Pack.migrations()` 跑。搬出来要改 `QQService.migrations()` 的拼接顺序，且
`Pack` 在没有 `QQService` 的测试里也会被直接构造——让它继续拥有那张表更省心。这里只
统一读写，不动建表。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable


class Flags:
    """qq_flags 的读写。一份实现，三处语义：`get`（None）、`require`（""）、`set`（log 不抛）。"""

    def __init__(self, store: Any, *, log: Callable[[str], None] | None = None) -> None:
        self._store = store
        self._log = log or (lambda line: None)

    def get(self, key: str) -> str | None:
        """`None` = 没写过这个键（或表都没有）。给需要区分「没拨过」和「拨成 0」的调用方。

        `qq.py::_switch_overrides` 就靠这个 `is not None`：开关没拨过时不覆盖 `.env` 的值，
        拨成 0 时覆盖成关。换成 `""` 就分不开「没拨过」和「拨成空字符串」了。
        """
        try:
            rows = self._store.query("SELECT value FROM qq_flags WHERE key = ?", (key,))
        except Exception:  # 没有那张表（channel 单独跑）也得能说话
            return None
        return str(rows[0]["value"]) if rows else None

    def require(self, key: str) -> str:
        """`""` = 没写过这个键。给不区分「没拨过」和「拨成 0」的调用方用（多数都是这种）。"""
        return self.get(key) or ""

    def set(self, key: str, value: str) -> None:
        """写一行，失败 log 不抛。

        `updated_at` 统一写 UTC isoformat——以前 `web_compat.py` 写机器本地、`Pack` 写
        本地带偏移、`qq.py`/`plugin_qq.py` 写 UTC，三种混写在同一列里。这一列没有任何代码
        读回来，只是人查库时看的，统一成 UTC 后日志和库里的时刻对得上。
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            self._store.execute(
                "INSERT INTO qq_flags (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (key, value, now),
            )
        except Exception as exc:
            self._log(f"qq: 记不下 {key}（{type(exc).__name__}）")
