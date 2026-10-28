"""服务状态：部署方的各项服务现在好不好使（GLOSSARY.md「服务状态」）。

对话列表页首铃铛左边那个按钮按的就是它。原版（`service_status.py`）按最近若干分钟里
部署方 key 的调用成功率来判定，并用少量真实请求去试那些还没被调用过的服务；用户自己带的 key
不算——那说明不了部署的事。

新内核没有活动日志，也没有后台探测线程，所以这里的判定来自内核自己记下的调用：
每回合的模型调用（聊天和 judge 共用一个模型，记在 `chat_llm`）成功还是失败，落在
`service_calls` 表里；`/api/service-status` 按窗口把这些调用汇总成 ok / unsteady / down /
idle 四档。窗口内没有调用过的服务是 idle（"没什么可依据"），不是故障。

四项判定阈值照原版：失败占比 ≥5% 是 unsteady，≥50% 是 down。
"""

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from core.plugin import Plugin, Route, TurnContext
from core.store import Store

WINDOW_MINUTES = 15
"""看最近多少分钟的调用。原版跟着部署配置走（默认也是这个量级），这里固定。"""

UNSTEADY_AT = 0.05
"""失败占比从多少算「时好时坏」。"""

DOWN_AT = 0.5
"""失败占比从多少算「坏了」。"""

SERVICES = ("chat_llm", "memory_llm", "tts", "stt", "judge", "embedding", "rerank", "image")
"""原版列的服务，按显示顺序。这一版只有模型是真的在跑，其余没有实现。"""

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS service_calls ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " service TEXT NOT NULL,"
    " at TEXT NOT NULL,"
    " ok INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS service_calls_by_service ON service_calls(service, at)",
)


class ServiceStatus(Plugin):
    """记下模型调用的成败，按窗口汇总给前端。"""

    name = "status"

    def __init__(self, store: Store, *, window_minutes: int = WINDOW_MINUTES) -> None:
        self._store = store
        self._window = window_minutes

    def capabilities(self) -> dict[str, object]:
        return {"panel": "status"}

    def migrations(self) -> Sequence[str]:
        return _SCHEMA

    async def after_turn(self, turn: TurnContext) -> None:
        """一轮成功走完，记一笔：模型这次答了。失败的那次由调用方（serve 的流式处理）
        记，因为内核在失败时不会走到 observe。"""
        self.record("chat_llm", ok=True)

    def record(self, service: str, *, ok: bool, at: datetime | None = None) -> None:
        """记一笔调用。测试和后台任务直接调它。"""
        when = at or datetime.now(timezone.utc)
        self._store.execute(
            "INSERT INTO service_calls (service, at, ok) VALUES (?, ?, ?)",
            (service, when.isoformat(timespec="seconds"), 1 if ok else 0),
        )

    def status(self, now: datetime | None = None) -> dict[str, object]:
        """按窗口汇总。原版的形状：window_minutes + services[{service, status}]。"""
        moment = now or datetime.now(timezone.utc)
        since = (moment - timedelta(minutes=self._window)).isoformat(timespec="seconds")
        rows = self._store.query(
            "SELECT service, ok, COUNT(*) AS n FROM service_calls WHERE at >= ? GROUP BY service, ok",
            (since,),
        )
        counts: dict[str, list[int]] = {}
        for row in rows:
            entry = counts.setdefault(str(row["service"]), [0, 0])
            entry[0 if int(row["ok"]) else 1] += int(row["n"])
        services = []
        for service in SERVICES:
            if service not in counts:
                continue
            good, bad = counts[service]
            total = good + bad
            share = bad / total if total else 0.0
            if share >= DOWN_AT:
                state = "down"
            elif share >= UNSTEADY_AT:
                state = "unsteady"
            else:
                state = "ok"
            services.append({"service": service, "status": state})
        return {"window_minutes": self._window, "services": services}

    def routes(self) -> dict[tuple[str, str], Route]:
        async def read(query, body):  # noqa: ANN001 - plugin.Route
            return self.status()

        return {("GET", "/api/service-status"): read}
