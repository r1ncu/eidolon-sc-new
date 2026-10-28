"""The OneBot 11 client: one WebSocket, events in, actions out.

Two things this file owns and the channel above it should not think about:

- **Reconnection.** A closed socket is normal (NapCat restarts, the box reboots, QQ drops
  the client). The loop backs off 1s → 60s and keeps going. Every action call made while
  disconnected fails immediately instead of hanging, so the caller can decide what to say.
- **`echo` correlation.** OneBot answers an action with the same `echo` it was given; there
  is no request id of its own. Actions and events share the socket, so the reply has to be
  matched or the two streams get mixed.

The keyboard-facing surface is small on purpose: `run` (the loop), `call` (an action), and
three counters a status page reads.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from . import ws

Event = dict[str, Any]
Handler = Callable[[Event], Awaitable[None]]


class OneBotError(RuntimeError):
    """The action was refused, or came back with a non-zero `retcode`."""


class OneBotDown(OneBotError):
    """No connection right now. The caller retries later or says something in character."""


class OneBot:
    def __init__(
        self,
        url: str,
        token: str = "",
        *,
        connect: Callable[..., Awaitable[ws.WebSocket]] = ws.connect,
        timeout: float = 20.0,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self.url = url
        self._token = (token or "").strip()
        self._connect = connect
        self._timeout = timeout
        self._log = on_log or (lambda line: None)
        self._conn: ws.WebSocket | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._echo = 0
        self._closing = False
        self.connects = 0
        self.events = 0
        self.actions = 0
        self.last_error = ""

    @property
    def connected(self) -> bool:
        return self._conn is not None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def run(
        self, on_event: Handler, on_connect: Handler | None = None
    ) -> None:
        """Read events until `stop()`. Reconnects in this loop; never returns on its own.

        `on_connect` runs every time the socket comes back, before any event is read. It is
        where the caller does the things that only make sense with a socket in hand — replay
        what failed to send while there was none. Ask before: a replay attempted before the
        first connection just burns the message's retry budget.
        """
        backoff = 1.0
        while not self._closing:
            try:
                conn = await self._connect(self.url, headers=self._headers(), timeout=15.0)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._log(f"onebot: connect failed ({self.last_error}), retry in {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            self._conn, backoff = conn, 1.0
            self.connects += 1
            self._log("onebot: connected")
            if on_connect is not None:
                # Scheduled, not awaited. An action's answer arrives over the very socket the
                # loop below reads, and that loop has not started yet — awaiting here means
                # every `call` made by the hook waits for its own timeout, so a replay of the
                # queue "fails" row by row without anything being sent. The hook returns in a
                # moment; the work it starts happens with the socket being read.
                asyncio.create_task(self._hook(on_connect))
            try:
                while True:
                    raw = await conn.recv()
                    self.events += 1
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(message, dict):
                        continue
                    if "echo" in message:
                        self._settle(message)
                    elif message.get("post_type"):
                        asyncio.create_task(self._deliver(on_event, message))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._log(f"onebot: dropped ({self.last_error})")
            finally:
                self._conn = None
                self._fail_pending(OneBotDown("connection closed"))
                await conn.close()

    async def _hook(self, hook: Handler) -> None:
        """Run `on_connect` beside the read loop; its failures are logged, not fatal."""
        try:
            await hook()
        except Exception as exc:
            self._log(f"onebot: on_connect failed ({type(exc).__name__}: {exc})")

    async def call(self, action: str, **params: Any) -> dict[str, Any]:
        """One action, its `data`, or an exception. Never waits longer than `timeout`."""
        data = await self.call_raw(action, **params)
        return data if isinstance(data, dict) else {}

    async def call_raw(self, action: str, **params: Any) -> Any:
        """The same call, with `data` exactly as it arrived.

        `call` narrows to a dict because that is what almost every action answers with — but
        `fetch_custom_face` answers with an *array* of URLs, and dropping it to `{}` would make
        the one action that returns a list look like an empty one. The echo correlation and the
        error handling are the same code, which is the point of splitting it here rather than
        re-implementing a socket read at the call site.
        """
        conn = self._conn
        if conn is None:
            raise OneBotDown("not connected")
        loop = asyncio.get_running_loop()
        self._echo += 1
        echo = str(self._echo)
        future: asyncio.Future = loop.create_future()
        self._pending[echo] = future
        payload = json.dumps(
            {"action": action, "params": params, "echo": self._echo}, ensure_ascii=False
        )
        try:
            await conn.send(payload)
            self.actions += 1
            answer = await asyncio.wait_for(future, self._timeout)
        finally:
            self._pending.pop(echo, None)
        code = answer.get("retcode", 0)
        if code not in (0, 1):  # 1 is "async accepted" in some builds
            raise OneBotError(f"{action}: retcode {code} {answer.get('message') or answer.get('wording') or ''}")
        return answer.get("data")

    async def stop(self) -> None:
        self._closing = True
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

    def _settle(self, message: Event) -> None:
        future = self._pending.pop(str(message.get("echo")), None)
        if future is not None and not future.done():
            future.set_result(message)

    def _fail_pending(self, exc: Exception) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)

    async def _deliver(self, on_event: Handler, message: Event) -> None:
        try:
            await on_event(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one bad event must not stop the socket
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._log(f"onebot: handler failed ({self.last_error})")
