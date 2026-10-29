"""The channel as a service: one thread, one loop, one socket, and a status endpoint.

It has the shape of a plugin (a name, tables, a route, a capability) because that is how this
codebase says "a feature you can switch off". It is not put in the `Host`, though: the host's
job is to be asked about turns, and a channel has no opinion about a turn — it starts a
thread and delivers messages. So `serve.py` mounts it, which is honest about the one
difference: this feature has a lifecycle, and the turn loop does not.

The thread is why a crash here cannot take the page down, and the loop is why the socket can
be written the ordinary async way while `serve.py` stays a threaded HTTP server.
"""

import asyncio
import random
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.plugin import Plugin, Route

from . import identities as who
from . import outbox as box
from . import qq
from . import qzone as space
from . import qzone_reply as replies
from . import qzone_web as web
from . import romance
from . import stickers as pack
from .config import QQConfig
from .digest import DM as DIGEST_DM, GROUP as DIGEST_GROUP
from .onebot import OneBot
from .presence import Presence
from .world import World

FACES_FIRST_WAIT = 20.0
"""Seconds after start before the first attempt: the socket is opened inside `client.run`."""

FACES_EVERY = 24 * 3600.0
"""A favourites list changes when somebody adds a face, not by the minute."""

FACES_RETRY = 300.0
"""After a failure, try again sooner than a day — the usual cause is a socket that is not up."""

FLOOR_EVERY = 300.0
"""How often the producer's affinity floor is re-applied. Cheap: one row, one comparison."""

ALIVE_EVERY = 300.0
"""How often the account is asked who is logged in. One cheap call; the point is the log
line when the answer stops matching `QQ_SELF_ID`."""

LOVE_TICK = 300.0
"""How coarsely the stored due time is waited on. It is read from the database, so the
clock survives a reboot; this is only how often the loop looks at it."""

LOVE_FLOOR = 1200.0
"""Never two of them closer than this, whatever the stored time says. A host that reboots
every few minutes would otherwise find 「due in the past」 on every boot and send each time."""

LOVE_NEXT = "love:next"
LOVE_LAST = "love:last"
"""`qq_flags` keys: when the next one is due (epoch), and when the last one went out."""

SHE_TICK = 300.0
"""多久看一次「她该不该自己开口」。

不是每分钟：这件事的时间尺度是天，跑得再勤也只是白查库。五分钟足够——她又不会
因为多等五分钟就改主意。"""

MONDAY_TAG = "上班"
"""The tag the Monday sticker is picked by. A person gives it: `/表情标 上班`."""

QZONE_TICK = 60.0
"""How often the space asks whether a post is due. A minute is finer than the calendar needs
and coarser than a clock; the decision itself is stored, so a missed tick costs nothing."""

QZONE_FEED_TICK = 300.0
"""How often friends' walls are looked at. A 说说 is not a chat message: five minutes is
already faster than anyone posts, and each look is one action call plus, at best, one model
call — so this is a cost knob as much as a clock one."""

WAIT_CONNECT = 5.0
"""How long the two space loops wait before looking again when the socket is not up yet.

Not the full tick: a restart at 21:05 should post at 21:05, and a poll five seconds after
`onebot: connected` is what makes 「重启之后它自己接上了」 observable. Five seconds because
the reconnect backoff starts at one and doubles — this only has to be shorter than the gap
between attempts, not short in absolute terms."""

DIGEST_FIRST = 90.0
"""Seconds before the first memory rewrite is considered.

Shorter than the rest because it needs nothing but the database: the file it writes is what
she reads, and a deployment that just turned this on should see it today. A clean install
has nothing to write, so the check costs one query and stops."""

DIGEST_EVERY = 3600.0
"""How often the two dates are looked at. Hourly is far more often than needed, and that is
the point: restarting, sleeping, or missing the hour cannot make it skip a cycle."""


class QQService(Plugin):
    name = "qq"
    requires = ("characters", "affinity")

    def __init__(
        self,
        app: Any,
        config: QQConfig | None = None,
        *,
        client: OneBot | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.app = app
        self.config = config or QQConfig.from_env()
        self._log = log or (lambda line: print(line, flush=True))
        self.problem = self.config.reason_off()
        self.client = client or OneBot(self.config.ws, self.config.token, on_log=self._log)
        # Under the media volume, not next to the code: a container's own filesystem is
        # thrown away on the next rebuild, and a pack that vanishes every deploy would make
        # 「/表情」 answer "I have none" forever.
        root = (
            Path(self.config.sticker_dir)
            if self.config.sticker_dir
            else Path(app.settings.media) / "stickers"
        )
        self.pack = pack.Pack(app.store, root, log=self._log)
        # ── 时区 ──
        # 每一条「几点之后」的规则都该读同一个时区：`_maybe_monday` 用的是
        # `datetime.now(_zone(app.settings.timezone))`。空间这里原来用的是
        # `datetime.now().astimezone()`，也就是**容器自己的时区**；而 compose 只给
        # napcat 设了 TZ，scbot 里是 UTC。结果 `QQ_QZONE_HOUR=21` 实际在早上五点才算到点——
        # 白天看是「从来没发过」，日志里也看不出错。
        self._clock = lambda: datetime.now(_zone(self.app.settings.timezone))
        # Her own space, next to her own voice: the post is written from the same character
        # and published through the same socket, but on a calendar of its own.
        self.qzone = space.Qzone(
            self.client,
            app.llm,
            app.store,
            character=self.config.character,
            persona=qq.world_path(
                app.settings.characters, f"{self.config.character}-qzone.md"
            )
            if self.config.character
            else None,
            flags=self.pack,
            ugc_right=self.config.qzone_right,
            hour=self.config.qzone_hour,
            chance=self.config.qzone_p,
            gap=self.config.qzone_gap,
            clock=self._clock,
            log=self._log,
        )
        # 读别人的墙有两条路：NapCat 上的 QZone 扩展 action（少见），或者直接走 QZone 的
        # Web 接口（用 NapCat 给的 Cookie，官方接口里没有读动态这件事）。默认后者。
        self.web = (
            web.Web(self.client, self_id=self.config.self_id, log=self._log)
            if self.config.qzone_web
            else None
        )
        # 她自己的墙上的评论：看一眼有没有人回她，回一句。和写说说是两件事——写那条一天
        # 最多一次，这条是有多少人说就回几句（有上限）。
        self.wall = replies.Wall(
            self.client,
            app.llm,
            app.store,
            web=self.web,
            persona=qq.world_path(
                app.settings.characters, f"{self.config.character}-qzone.md"
            )
            if self.config.character
            else None,
            flags=self.pack,
            self_id=self.config.self_id,
            # 全局忽略名单也在这里生效：`QQ_IGNORE_IDS` 是「完全不理的号」，一个在群里
            # 不理、却在她墙上回一句的号，等于那张名单只在半个产品里有效。
            deny=(*self.config.qzone_reply_ignore, *self.config.ignore),
            owners=self.config.owners,
            # 谁是谁的机器人：空间里也一样，不然一条ルカ的台词会被当成陌生群友接。
            bots={
                qq_id: (self.app.characters.name_of(who) or who)
                for qq_id, who in self.config.bots
            },
            hourly=self.config.qzone_reply_hourly,
            gap=self.config.qzone_reply_gap,
            age=self.config.qzone_reply_age,
            posts=self.config.qzone_reply_posts,
            clock=self._clock,
            log=self._log,
            on=self.config.qzone_reply,
        )
        self.channel = qq.QQChannel(
            app,
            self.client,
            self.config,
            identities=who.Identities(app.store, now=_now()),
            world=World(
                app.settings.characters,
                config=app.settings.characters_config,
                meta=self.config.meta,
                log=self._log,
            ),
            presence=Presence(
                qq.world_path(app.settings.characters, f"{self.config.character}-qq.md"),
                direct=qq.world_path(app.settings.characters, f"{self.config.character}-dm.md"),
                stage=qq.world_path(
                    app.settings.characters, f"{self.config.character}-stage.toml"
                )
                if self.config.character
                else None,
                log=self._log,
                owner_stage=self.config.owner_stage,
            ),
            outbox=box.Outbox(app.store, self.client.call, log=self._log),
            stickers=self.pack,
            log=self._log,
        )
        # 「她怎么称呼对面」只有一份，频道读过的那个 toml 就是它。
        self.owner_name = self.channel.owner_name
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        """The scheduled loops (Monday, faces, floor, love, idle, digest). Kept so `stop()`
        can cancel them: a coroutine still waiting on a closed loop is a log line that looks
        like a bug every time the process restarts."""

    # ---- the plugin surface

    def migrations(self) -> Sequence[str]:
        return (*who.SCHEMA, *box.SCHEMA, *pack.SCHEMA, *space.SCHEMA, *replies.SCHEMA)

    def capabilities(self) -> dict[str, Any]:
        return {
            "enabled": not self.problem,
            "why": self.problem,
            "group": self.config.groups[0] if self.config.groups else "",
            "private": self.config.private,
            "ambient": self.config.ambient,
            "stickers": self.pack.count(),
            "character": self.config.character,
            "qzone": self.config.qzone,
            "qzone_reply": self.config.qzone_reply,
        }

    def routes(self) -> dict[tuple[str, str], Route]:
        async def status(query, body):  # noqa: ANN001 - plugin.Route
            return qq.status(self.channel) | {
                "problem": self.problem,
                "enabled": not self.problem,
                "running": self._thread is not None,
            }

        async def reload(query, body):  # noqa: ANN001 - plugin.Route
            return self.channel.reload()

        async def ping(query, body):  # noqa: ANN001 - plugin.Route
            mode = str((body or {}).get("mode") or query.get("mode") or "dm").strip().lower()
            return self.ping(mode)

        async def sticker(query, body):  # noqa: ANN001 - plugin.Route
            room = str((body or {}).get("group") or query.get("group") or "").strip()
            tag = str((body or {}).get("tag") or query.get("tag") or "").strip()
            return await self.send_sticker(room, tag)

        async def sticker_tag(query, body):  # noqa: ANN001 - plugin.Route
            body = body or {}
            tag = str(body.get("tag") or query.get("tag") or "").strip()
            sha1 = str(body.get("sha1") or query.get("sha1") or "").strip()
            row = self.pack.tag(tag, sha1=sha1)
            if row is None:
                return {"tagged": False, "why": "没有这张（给 sha1，或者先在群里发一张）"}
            self._log(f"qq: 表情 {row['sha1'][:8]} 标成「{tag or '（无）'}」")
            return {"tagged": True, "sha1": row["sha1"], "tag": tag}

        async def note(query, body):  # noqa: ANN001 - plugin.Route
            body = body or {}
            qq_id = str(body.get("qq") or query.get("qq") or "").strip()
            if not qq_id:
                return {"named": False, "why": "要一个 QQ 号"}
            text = body.get("note")
            if text is None:
                text = query.get("note", "")
            answer = self.channel.identities.set_note(qq_id, str(text))
            if answer is None:
                return {"named": False, "why": "这个人还没说过话（先让她回一次，再写）"}
            self._log(f"qq: {answer['name'] or qq_id} 的备注改成「{answer['note'] or '（清空）'}」")
            return {"named": True, **answer}

        async def confess_now(query, body):  # noqa: ANN001 - plugin.Route
            if not self.config.owners:
                return {"ok": False, "why": "没有主人"}
            qq_id = self.config.owners[0]
            answer = await self.channel.confess(qq_id, card="")
            if answer.get("said"):
                # 真机上要看得见她那句话：接口本身只返回结果，不带发送。
                await self.channel.outbox.send(qq_id, str(answer["said"]), private=True)
            return answer

        async def she_now(query, body):  # noqa: ANN001 - plugin.Route
            """让她现在开口。`force=1` 跳过「等够 21 天」和骰子，只保留硬条件。

            这是测试入口：真机上想让那句话出现，不能等二十一天，也不能指望 25% 的骰子。
            它改的仍然是**真状态**——所以只该在愿意承担后果的时候按。
            """
            if not self.config.owners:
                return {"ok": False, "why": "没有主人"}
            qq_id = self.config.owners[0]
            force = str((body or {}).get("force") or query.get("force") or "").lower() in (
                "1", "true", "yes", "on",
            )
            if not force:
                await self._she_once(datetime.now(_zone(self.app.settings.timezone)))
            else:
                await self.channel.she_speaks(qq_id)
            return {
                "relation": self.channel.identities.relation(qq_id),
                "offered_at": self.channel.identities.offered_at(qq_id),
            }

        async def space_status(query, body):  # noqa: ANN001 - plugin.Route
            return self.qzone.status() | {"enabled": self.config.qzone}

        async def space_post(query, body):  # noqa: ANN001 - plugin.Route
            return await self.speak_out(body or {})

        async def space_delete(query, body):  # noqa: ANN001 - plugin.Route
            tid = str((body or {}).get("tid") or query.get("tid") or "").strip()
            if not tid:
                return {"deleted": False, "why": "没有 tid"}
            try:
                await self.qzone.delete(tid)
            except space.PublishError as exc:
                return {"deleted": False, "why": str(exc)}
            self._log(f"qq: 空间删掉了一条（tid {tid}）")
            return {"deleted": True, "tid": tid}

        async def wall_status(query, body):  # noqa: ANN001 - plugin.Route
            return self.wall.status()

        async def wall_poll(query, body):  # noqa: ANN001 - plugin.Route
            # 手动拉一次：不等到下一个五分钟。「有没有接到评论」靠这个按钮验。
            try:
                return await self.wall.poll()
            except Exception as exc:  # 一个按钮不该把页面带走
                return {"ok": False, "why": f"{type(exc).__name__}: {exc}"}

        async def wall_reply(query, body):  # noqa: ANN001 - plugin.Route
            body = body or {}
            tid = str(body.get("comment_tid") or body.get("tid") or query.get("comment_tid") or "").strip()
            if not tid:
                return {"replied": False, "why": "没有 comment_tid"}
            try:
                return await self.wall.answer_tid(tid, str(body.get("text") or "").strip())
            except Exception as exc:
                return {"replied": False, "why": f"{type(exc).__name__}: {exc}"}

        # Both spellings: a reload is a write to the running process, so POST; GET with the
        # same path answers the same thing and is there because `curl` is easier to type.
        return {
            ("GET", "/api/qq/status"): status,
            ("POST", "/api/qq/reload"): reload,
            ("GET", "/api/qq/reload"): reload,
            # 「让她现在先开口一次」——测试用的，不是功能。
            ("POST", "/api/qq/ping"): ping,
            ("GET", "/api/qq/ping"): ping,
            # 「让她现在发一张表情」——测试用的，跟 /api/qq/ping 一样是按钮不是功能。
            ("POST", "/api/qq/sticker"): sticker,
            ("GET", "/api/qq/sticker"): sticker,
            # 给某一张表情命名（`tag` 为空 = 取消命名）。`/表情标` 的远程版，不用先发到群里。
            ("POST", "/api/qq/sticker/tag"): sticker_tag,
            # 某个人的一句话事实（「她是女性」）。人写的，不是算出来的；空字符串 = 清掉。
            ("POST", "/api/qq/note"): note,
            # 恋爱系统的测试入口：替他说一次 / 让她现在开口。
            ("POST", "/api/qq/confess"): confess_now,
            ("POST", "/api/qq/she"): she_now,
            # 空间：看一眼 / 手动发一条 / 撤回一条。
            ("GET", "/api/qzone"): space_status,
            ("POST", "/api/qzone"): space_post,
            ("POST", "/api/qzone/delete"): space_delete,
            ("GET", "/api/qzone/delete"): space_delete,
            # 她自己的墙：评论状态 / 现在拉一次 / 手动回一条评论。
            ("GET", "/api/qzone/comments"): wall_status,
            ("POST", "/api/qzone/poll"): wall_poll,
            ("GET", "/api/qzone/poll"): wall_poll,
            ("POST", "/api/qzone/reply"): wall_reply,
        }

    async def send_sticker(self, group: str = "", tag: str = "") -> dict[str, Any]:
        """让她现在主动发一张表情，不用等谁开口。

        人工按钮，所以不过 `STICKER_EVERY` 那道节流——那是给「自动」用的，人点一下不该被
        自己的规矩挡住。但它会把节流时间戳顶上去：手发完十分钟内，自动那条不会紧接着再来一张。
        """
        room = group or (self.config.groups[0] if self.config.groups else "")
        if not room:
            return {"sent": False, "why": "没有配置群"}
        row = self.pack.pick(tag) if tag else self.channel._sticker_row()
        if row is None:
            why = f"没有标「{tag}」的表情" if tag else "表情包是空的（先发一张进群，或者「/表情 同步」）"
            return {"sent": False, "why": why}
        ok = await self.channel.outbox.send_image(room, self.pack.data_url(row))
        if ok:
            self.channel._marked[room] = asyncio.get_running_loop().time()
            self._log(f"qq: 主动发了一张表情到 {room}（{row.get('tag') or '收藏'}）")
        return {
            "sent": bool(ok),
            "group": room,
            "tag": row.get("tag") or "收藏",
            "sha1": row["sha1"],
        }

    async def speak_out(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """发一条说说，立刻。`{"text": "…"}` 原样发，`{"publish": false}` 只写不发。

        这条路径不掷骰子、不看钟点、也不记「今天发过了」：它是给人用的，不是日历的一部分。
        内容该做的检查一样做——手写的句子也要经过那道闸。但开关是开关：`QQ_QZONE` 没开的时候
        它也拒绝，否则「功能关着」就只是「定时器关着」。
        """
        if not self.config.qzone:
            return {"posted": False, "why": "空间功能没开（QQ_QZONE=0）"}
        text = str(body.get("text") or "").strip()
        publish = body.get("publish")
        publish = True if publish is None else bool(publish)
        if not text:
            try:
                text = await self.qzone.compose()
            except space.ContentError as exc:
                self._log(f"qq: 空间写不出来（{exc.why}）")
                return {"posted": False, "why": f"写不出来（{exc.why}）"}
            if not publish:
                return {"posted": False, "why": "只看不发", "text": text}
        try:
            return await self.qzone.post(text)
        except space.ContentError as exc:
            self._log(f"qq: 空间这一条不通过（{exc.why}）")
            return {"posted": False, "why": f"不通过（{exc.why}）", "text": text}

    def ping(self, mode: str = "dm") -> dict[str, Any]:
        """Make her speak first once, right now, in the room asked for.

        Deliberately an endpoint and not a command: the whole point of the schedules is that
        they are random and she is not summonable. This exists so a deployment can prove the
        sending half works — the timer is not the part that breaks, the message is.
        """
        if self._loop is None or self._thread is None:
            return {"ok": False, "why": "QQ 通道没在跑"}
        mode = (mode or "dm").strip().lower()
        if mode == "group":
            if not self.config.groups:
                return {"ok": False, "why": "QQ_GROUP_IDS 是空的"}
            work = self.channel.speak_up(self.config.groups[0], force=True)
        elif mode in ("dm", "private"):
            if not self.config.owners:
                return {"ok": False, "why": "QQ_OWNER_IDS 是空的"}
            work = self.channel.love_ping(self.config.owners[0], force=True)
        else:
            return {"ok": False, "why": "mode 只能是 dm 或 group"}
        try:
            said = asyncio.run_coroutine_threadsafe(work, self._loop).result(timeout=180)
        except Exception as exc:  # a model that hangs must not take the page thread with it
            return {"ok": False, "mode": mode, "why": f"{type(exc).__name__}: {exc}"}
        if not said:
            return {
                "ok": False,
                "mode": mode,
                "why": "她没开口：可能被静音、还没进入恋爱、或者这条通道本身关着（看日志）",
            }
        return {"ok": True, "mode": mode}

    # ---- the lifecycle

    def start(self) -> None:
        if self.problem:
            # Only complain when this deployment meant to use it. A page-only install has no
            # `QQ_*` variables, and printing a failure for a feature nobody asked for every
            # time a test builds an `App` is noise, not a warning.
            if self.config.enabled:
                self._log(f"QQ 通道没有启动：{self.problem}")
            return
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="qq", daemon=True)
        self._thread.start()
        self._log(
            "QQ 通道已启动：" + self.config.where()
            + f" / 角色 {self.config.character} / {self.config.ws}"
        )

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._cancel(), loop).result(timeout=5)
        except Exception:
            pass
        try:
            asyncio.run_coroutine_threadsafe(self.client.stop(), loop).result(timeout=5)
        except Exception:
            pass
        thread.join(timeout=5)
        self._thread = None
        self._loop = None

    def _run(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as exc:  # a broken channel is a log line, not a dead page
            self._log(f"qq: stopped ({type(exc).__name__}: {exc})")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    def _spawn(self, work: Any) -> None:
        """Start a scheduled loop that dies with the channel."""
        self._tasks.append(asyncio.create_task(work))

    async def _cancel(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _serve(self) -> None:
        self.pack.prune()
        # Not here: a replay attempted before the socket is up fails every row and spends
        # its retry budget (`attempts < 5`). It belongs on the connect, which also covers
        # every reconnection after that — a QQ that drops the login for an hour must not
        # silently swallow the messages said during it.
        self._spawn(self._alive())
        if self.config.monday_hour >= 0 and self._monday_rooms():
            self._spawn(self._monday())
            # Said out loud at startup because this one fails quietly: no sticker tagged
            # 「上班」 means Monday arrives and nothing is sent, and the only trace is a log
            # line nobody reads. The count is the whole answer to 「为什么没发」.
            self._log(
                f"qq: 周一 {self.config.monday_hour}:00 之后发一张「上班」表情到 "
                f"{','.join(self._monday_rooms())}（表情包里标了「上班」的有 "
                f"{self.pack.count(MONDAY_TAG)} 张）"
            )
        if self.config.stickers:
            # The faces this account has favourited are the ones she is certainly allowed to
            # send here; a group's own pack starts empty and fills up slowly. First attempt
            # soon after the socket comes up, then once a day.
            self._spawn(self._faces())
            face = (self.config.sticker_face or "").strip()
            if face and not self.pack.count(face):
                # 安静的那种失败：标签没有，那张表情永远不会出现，而日志里只会有「她今天很安静」。
                self._log(
                    f"qq: 「{face}」的表情还没有标 —— 发那张到群里然后「/表情标 {face}」，"
                    f"或者 POST /api/qq/sticker/tag"
                )
        if self.config.owners and (
            self.config.owner_affinity > 0 or self.config.owner_love_floor > 0
        ):
            # Checked on a schedule, not only at start: the judge can lower him mid-day, and
            # the point of the floor is that he never *sees* the tier below it.
            self._spawn(self._floor())
            # Two floors now, and either may be off: the one before they are together, and
            # the one after. Saying only the first would be a lie half the time.
            warm = (
                f"恋爱中不低于 {self.config.owner_love_floor:g}"
                if self.config.owner_love_floor > 0
                else "恋爱里没有地板（低于 70 时说错话会让她生闷气）"
            )
            self._log(
                f"qq: {self.owner_name}的好感度恋爱前不低于 {self.config.owner_affinity:g}，{warm}"
            )
        if self.config.owner_love > 0 and self.config.owners:
            # 她等太久会自己开口。开关跟恋爱系统同一个：`QQ_OWNER_LOVE=0` 表示这个部署
            # 不要恋爱这条线，那她也没什么可说的。
            self._spawn(self._she())
            self._log(
                f"qq: 好感在 {romance.SHE_FLOOR:g} 以上待满 {romance.SHE_DAYS} 天、他还不说，"
                f"她可能自己开口（每天一次）"
            )
        if self.config.love_ping and self.config.owners:
            # Only fires once they are actually together; the random gap is the feature.
            self._spawn(self._love())
            self._log(
                f"qq: 恋爱之后她会先发消息（间隔 {self.config.love_ping_min:g}–"
                f"{self.config.love_ping_max:g} 分钟，只在 "
                f"{qq.IDLE_QUIET_HOURS[0]}:00–{qq.IDLE_QUIET_HOURS[1]}:00 之间）"
            )
        if self.config.ambient and self.config.ambient_idle > 0 and self.config.groups:
            # Off by default: a bot that starts talking into a quiet room is the feature most
            # likely to get muted. On, it is what makes her feel like a person in the group
            # rather than a thing that answers.
            self._spawn(self._idle())
            self._log(
                f"qq: 安静 {int(self.config.ambient_idle)} 分钟以上时，她可能自己开口"
                f"（{qq.IDLE_QUIET_HOURS[0]}:00–{qq.IDLE_QUIET_HOURS[1]}:00 之间）"
            )
        if self.config.digest and (
            self.config.digest_dm_days > 0 or self.config.digest_group_days > 0
        ):
            # The memory file is the long-term half of 「她还记得」: the plugin keeps the last
            # few facts per turn, this keeps the shape of the whole thing, and it costs one
            # model call every three days rather than one per message.
            self._spawn(self._digest())
            where = self.channel.digest.directory
            self._log(
                f"qq: 记忆文件每 {self.config.digest_dm_days:g} 天重写一次（私聊）、"
                f"每 {self.config.digest_group_days:g} 天（群聊），写在 {where}"
            )
        if self.config.qzone:
            # A post is the one thing with no reader to correct it: it goes on the wall, and
            # the wall keeps what it is given. So it runs on its own clock, one a day at most.
            self._spawn(self._qzone())
            self._log(
                f"qq: QQ 空间每天 {self.config.qzone_hour}:00 之后有 "
                f"{self.config.qzone_p:.0%} 的概率发一条"
                f"（{space.RIGHT.get(self.qzone.right, '')}，两条之间至少隔 "
                f"{self.config.qzone_gap:g} 小时，按 {self.app.settings.timezone} 的时间）"
            )
            if self.config.qzone_p <= 0:
                # 0% 是「永远不会」，而启动日志里那句「有 0% 的概率」读起来像「有时候」。
                # 线上真踩过：开关是开的，骰子被设成 0，一个月里一条都没发。
                self._log(
                    "qq: QQ_QZONE_P=0 —— 空间永远不会自己发（这不是概率低，是不会发生）。"
                    "要它自己发，把 QQ_QZONE_P 设成 0.3 左右"
                )
        if self.config.qzone_reply:
            self._spawn(self._qzone_wall())
            self._log(
                f"qq: 空间会回评论 —— 每 {QZONE_FEED_TICK / 60:.0f} 分钟看自己的说说，"
                f"一小时最多 {self.config.qzone_reply_hourly} 条，两条之间至少 "
                f"{self.config.qzone_reply_gap:g} 分钟，只回 "
                f"{self.config.qzone_reply_age:g} 小时以内、最近 "
                f"{self.config.qzone_reply_posts} 条说说下的"
            )
        await self.client.run(self.channel.on_event, on_connect=self._reconnected)

    # ---- one thing happens on a schedule

    async def _reconnected(self) -> None:
        """What was said while there was no socket. Runs on every connect, first one included.

        `Outbox.send` keeps a failed chunk as `pending` and never raises, so a QQ that is
        logged out for an hour leaves a queue instead of losing the sentence. Nothing was
        replaying it: the old call happened at startup, *before* the socket was up, where
        every row failed and spent one of its five retries. On a reboot loop that quietly
        ate the whole queue without ever sending any of it.
        """
        try:
            done = await self.channel.outbox.resend_pending()
        except Exception as exc:  # a replay that fails must not cost us the connection
            self._log(f"qq: 重连后补发失败（{type(exc).__name__}: {exc}）")
            return
        if done:
            self._log(f"qq: 重连后补发了 {done} 条（掉线期间没送出去的）")


    async def _alive(self) -> None:
        """Every five minutes, ask who is logged in (see `_check_login`)."""
        while True:
            await asyncio.sleep(ALIVE_EVERY)
            await self._check_login()

    async def _check_login(self) -> str:
        """`ok`, `other` or `down` — and a line in the log when it is not `ok`.

        An open socket is not the same as being logged in: QQ can drop the account — another
        phone logs in, the password changes, a security check trips — and NapCat keeps the
        websocket open while delivering nothing. From the outside that is 「有时候不回，过一
        阵又好了」, and from the inside there is no exception anywhere to read. One cheap call
        turns it into a log line with a cause in it.

        It also catches the other half: if the account logged in is not `QQ_SELF_ID`, every
        `@` check compares against the wrong number and she stops noticing that anyone is
        talking to her at all.
        """
        try:
            info = await self.client.call("get_login_info")
        except Exception as exc:  # a socket that is down is `run`'s problem, not this one
            self._log(f"qq: 心跳失败（{type(exc).__name__}: {exc}）")
            return "down"
        uin = str((info or {}).get("user_id") or "")
        mine = str(self.config.self_id or "")
        if uin and mine and uin != mine:
            self._log(f"qq: 现在登录的是 {uin}，配置里是 {mine} —— 这个号被挤下线过")
            return "other"
        return "ok"

    async def _faces(self) -> None:
        """Sync the account's favourites once after the socket is up, then once a day.

        Not in `_serve` directly: the socket connects inside `client.run`, so an immediate
        call would fail every time. A short first wait and then a long one is enough — the
        collection changes when the producer adds a face, not by the minute.
        """
        delay = FACES_FIRST_WAIT
        while True:
            await asyncio.sleep(delay)
            try:
                if getattr(self.client, "connected", False):
                    await self.channel.import_faces()
                    delay = FACES_EVERY
            except Exception as exc:  # a QQ that refuses the action is not a dead channel
                self._log(f"qq: 收藏表情同步失败（{type(exc).__name__}: {exc}）")
                delay = FACES_RETRY
        
    async def _floor(self) -> None:
        """Every five minutes; `channel.hold_affinity` does the work and logs when it acts.

        `refresh_love` first: the two bars can be met without a new message — he may have
        crossed them yesterday — and until the flag is set she will not write first.
        """
        while True:
            try:
                for qq_id in self.config.owners:
                    self.channel.refresh_love(qq_id)
                self.channel.hold_affinity()
            except Exception as exc:  # a database that is locked must not take the channel down
                self._log(f"qq: 好感度下限检查失败（{type(exc).__name__}: {exc}）")
            await asyncio.sleep(FLOOR_EVERY)

    async def _she(self) -> None:
        while True:
            try:
                await self._she_once(datetime.now(_zone(self.app.settings.timezone)))
            except Exception as exc:  # 一个后台判断不该把整条通道带走
                self._log(f"qq: 检查「她该不该开口」失败（{type(exc).__name__}: {exc}）")
            await asyncio.sleep(SHE_TICK)

    async def _she_once(self, now: datetime) -> None:
        """她今晚要不要说出口，或者她还在等的那件事是不是该收回来了。

        每小时问一次库，便宜的判断排在前面：关系不是 `none` 就没有她开口这回事；
        私聊关着就没人能听见；不在清醒时段不说。
        """
        if not self.config.owners:
            return
        qq_id = self.config.owners[0]
        channel = self.channel
        relation = channel._relation(qq_id)
        if relation == "offered":
            await self._offer_timeout(qq_id, now)
            return
        if relation != "none":
            return
        if not self.config.may_direct(qq_id):
            return
        hour = now.hour
        if not (qq.IDLE_QUIET_HOURS[0] <= hour < qq.IDLE_QUIET_HOURS[1]):
            return
        character = channel._character()
        if character is None:
            return
        user_id = channel.identities.resolve(qq_id, card="", role="owner")
        affinity = channel._affinity(user_id)
        waited = self._warm_days(qq_id, affinity, now)
        ready, why = romance.she_ready(
            character,
            affinity,
            relation,
            waited,
            unknown_is_adult=self.config.unknown_age_adult,
        )
        if not ready:
            if why == "not_long_enough":
                self.pack.set_flag("she:waiting", f"{waited:.1f}")
            return
        today = now.date().isoformat()
        if self.pack.flag(f"she:roll:{today}"):
            return
        # 一天只掷一次，结果落库：内存里的骰子在这台机器上活不过一次重启。
        self.pack.set_flag(f"she:roll:{today}", "1")
        chance = romance.she_chance(character, waited)
        roll = channel._rng.random()
        self._log(
            f"qq: 她在考虑开口（等了 {waited:.0f} 天，概率 {chance:.0%}，掷 {roll:.2f}）"
        )
        if roll >= chance:
            return
        if await channel.she_speaks(qq_id):
            self.pack.set_flag("she:spoken", now.isoformat(timespec="seconds"))

    def _warm_days(self, qq_id: str, affinity: float, now: datetime) -> float:
        """她在门槛以上「连续」多少天。掉下去就清零，不是暂停。"""
        identities = self.channel.identities
        if affinity < romance.SHE_FLOOR:
            identities.clear_warm(qq_id)
            return 0.0
        identities.mark_warm(qq_id, now.isoformat(timespec="seconds"))
        since = identities.warm_since(qq_id)
        if not since:
            return 0.0
        try:
            when = datetime.fromisoformat(since)
        except ValueError:
            return 0.0
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max((now - when).total_seconds() / 86400, 0.0)

    async def _offer_timeout(self, qq_id: str, now: datetime) -> None:
        """她说了，他七天没答。她自己把它收回来——不说话，也不再提。

        故意比被拒更狠（`romance.LOSS_SILENCE`）：拒绝是个答案，不回答不是。这条是
        「重」这个设定最该长出来的东西，也是这套里唯一一处「什么都不做」的惩罚。
        """
        identities = self.channel.identities
        at = identities.offered_at(qq_id)
        if not at or not romance.offer_expired(at, now):
            return
        character = self.channel._character()
        character_id = character.id if character is not None else ""
        user_id = identities.resolve(qq_id, card="", role="owner")
        identities.set_relation(qq_id, "crush")
        identities.set_offered(qq_id, None)
        self.app.affinity.change(
            user_id, character_id, romance.LOSS_SILENCE, surface=qq.DM_SURFACE
        )
        identities.record_confession(
            qq_id,
            character_id,
            by="her",
            at=now.isoformat(timespec="seconds"),
            outcome="timeout",
            reason="silence",
            affinity=self.channel._affinity(user_id),
            turns=identities.turns(qq_id),
            plove=romance.level(character) if character is not None else 0,
            heavy=romance.heaviness(character) if character is not None else 0,
        )
        self._log("qq: 她说了之后没人答——七天过去了，她自己收回来了（好感 -12）")

    async def _love(self) -> None:
        """She writes first, at a random hour, a random number of times a day.

        Deliberately not a fixed interval: 「每天九点一条」 is an alarm clock, and an alarm
        clock is not a person thinking about you. The gap is drawn fresh each round, so the
        count in a day varies too.

        Outside the waking hours the timer sleeps *until the window opens* instead of drawing
        a gap. Drawing one at 23:00 means sleeping four hours to 03:00, finding the room
        closed, and drawing again — every night threw away one full gap, so the first message
        of the morning drifted later the more nights it ran.

        The due time lives in `qq_flags`, not only in this task. With the gap held in memory
        a reboot throws it away: on a host that restarts every half hour, the loop never
        reached the end of a 60–150 minute gap and she never wrote first at all. Stored, a
        reboot waits for the same moment, and a gap that came due while the host was down
        fires shortly after it is back — which is what 「过一会儿她想起来了」 actually looks
        like.
        """
        if self._love_due() <= 0:
            # Fresh install, nothing stored: draw now and store it. No first delay — the gap
            # itself (60–150 minutes) already means a deploy is not a message, and a delay
            # that lived only in memory was itself lost on every restart, which is how a
            # host that reboots every twenty minutes never got past it.
            self._love_draw(datetime.now(_zone(self.app.settings.timezone)))
        while True:
            try:
                now = datetime.now(_zone(self.app.settings.timezone))
                step = self._love_step(now)
                if step == "wait":
                    await asyncio.sleep(min(self._love_due() - now.timestamp(), LOVE_TICK))
                    continue
                if step == "night":
                    # Came due in the dead of night (or the host was down until then): push
                    # it to the window instead of writing to a closed room.
                    self._love_draw(now, until_window=True)
                    continue
                if step == "floor":
                    # Just sent one — the host probably rebooted straight afterwards and the
                    # stored time was still 「due」. Draw again rather than send twice.
                    self._love_draw(now)
                    continue
                if step == "draw":
                    self._love_draw(now)
                    continue
                for qq_id in self.config.owners:
                    await self.channel.love_ping(qq_id)
                self.pack.set_flag(LOVE_LAST, f"{now.timestamp():.0f}")
                self._love_draw(datetime.now(_zone(self.app.settings.timezone)))
            except Exception as exc:  # a failed message must not stop the next one
                self._log(f"qq: 主动私聊失败（{type(exc).__name__}: {exc}）")
                await asyncio.sleep(60)

    def _love_step(self, now: datetime) -> str:
        """What this moment asks for: `draw`, `wait`, `night`, `floor` or `fire`.

        Split out from the loop so the five cases can be checked without waiting for a clock.
        """
        start, end = qq.IDLE_QUIET_HOURS
        due = self._love_due()
        if due <= 0:
            return "draw"  # nothing stored: the first draw of this installation
        if due > now.timestamp():
            return "wait"
        if not start <= now.hour < end:
            return "night"
        last = _stamp(self.pack.flag(LOVE_LAST))
        if last and now.timestamp() - last < LOVE_FLOOR:
            return "floor"
        return "fire"

    def _love_due(self) -> float:
        """The stored moment the next one is due, or 0 when nothing is stored yet."""
        return _stamp(self.pack.flag(LOVE_NEXT))

    def _love_draw(self, now: datetime, *, until_window: bool = False) -> float:
        """Draw the next moment and store it, so a reboot does not draw a new one.

        `until_window` is the night case: the next thing that happens is the window opening,
        not a message, so the gap is not drawn at all — that would waste one per night.
        """
        start, end = qq.IDLE_QUIET_HOURS
        if until_window or not start <= now.hour < end:
            gap = _until(start, now)
        else:
            gap = random.uniform(self.config.love_ping_min, self.config.love_ping_max) * 60
        due = now.timestamp() + gap
        self.pack.set_flag(LOVE_NEXT, f"{due:.0f}")
        when = datetime.fromtimestamp(due, now.tzinfo)
        self._log(f"qq: 下一次主动私聊 {when:%H:%M}（还有 {gap / 60:.0f} 分钟）")
        return due

    async def _idle(self) -> None:
        """Checked once a minute; the guards live in `channel.speak_up`."""
        while True:
            try:
                await self._maybe_idle()
            except Exception as exc:  # a quiet room must not take the channel down
                self._log(f"qq: idle check failed ({type(exc).__name__}: {exc})")
            await asyncio.sleep(60)

    async def _maybe_idle(self, now: datetime | None = None) -> bool:
        """`now` is injectable so the quiet hours can be tested without waiting for morning."""
        now = now or datetime.now(_zone(self.app.settings.timezone))
        start, end = qq.IDLE_QUIET_HOURS
        if not start <= now.hour < end:
            return False
        said = False
        for group in self.config.groups:
            said = await self.channel.speak_up(group) or said
        return said

    async def _digest(self) -> None:
        """Rewrite the memory files when their dates come round.

        Checked hourly and decided by two dates, not by a timer: a container that was down
        for the three days must not skip the rewrite, and one that restarts twice must not
        do it twice.
        """
        await asyncio.sleep(DIGEST_FIRST)
        while True:
            try:
                await self._maybe_digest()
            except Exception as exc:  # a failed rewrite must not stop the channel
                self._log(f"qq: 记忆整理失败（{type(exc).__name__}: {exc}）")
            await asyncio.sleep(DIGEST_EVERY)

    async def _maybe_digest(self, now: datetime | None = None) -> list[str]:
        """`now` is injectable so the cadence can be tested without waiting three days."""
        if not self.config.digest:
            return []
        now = now or datetime.now(_zone(self.app.settings.timezone))
        written: list[str] = []
        jobs: list[tuple[str, str, str, float]] = []
        if self.config.digest_dm_days > 0:
            for qq_id in self.config.owners:
                jobs.append(
                    (DIGEST_DM, qq_id, f"和{self.owner_name}的记忆", self.config.digest_dm_days)
                )
        if self.config.digest_group_days > 0:
            for group in self.config.groups:
                jobs.append(
                    (DIGEST_GROUP, group, "群里的记忆", self.config.digest_group_days)
                )
        for kind, key, heading, days in jobs:
            if not self.channel.digest.due(kind, key, days, now):
                continue
            path = await self.channel.digest.run(
                kind, key, heading=heading, days=days, now=now
            )
            if path is not None:
                written.append(str(path))
        return written

    async def _qzone(self) -> None:
        """Every minute, ask whether a post is due. The answer is persisted, not remembered.

        宿主机一夜重启三次的时候，内存里的「今天还没发」每次都是从头发一遍 —— 主动私聊
        踩过这个坑（`love:next` 存库），这里一开始就照做：日期、骰子、失败等待全在 `qq_flags`。

        `connected` 这道闸是补上的：`_serve` 里 spawn 这条循环的时候 socket 还没连上，
        第一次 tick 会以 `not connected` 结束。以前它会记一条失败并把今天压掉两小时，
        于是「过了点的晚上重启一次」就等于「今天不发」。没连上就不该是发布。
        """
        while True:
            try:
                if not getattr(self.client, "connected", False):
                    await asyncio.sleep(WAIT_CONNECT)
                    continue
                await self.qzone.maybe()
            except Exception as exc:  # 一条说说发不出去，不该把整条通道带走
                self._log(f"qq: 空间这一轮出错（{type(exc).__name__}: {exc}）")
            await asyncio.sleep(QZONE_TICK)

    async def _qzone_wall(self) -> None:
        """Look at her own posts on a timer, answer the comments under them.

        No push exists for this: NapCat has no `notice` event for 「有评论」, and its only
        official QZone actions are send and delete. So it is a poll, and the design is about
        being a polite one — five minutes apart, an hourly cap, a gap between two answers, the
        first run only records, and a post whose comment count has not changed is not fetched
        again.
        """
        while True:
            try:
                if not getattr(self.client, "connected", False):
                    await asyncio.sleep(WAIT_CONNECT)
                    continue
                result = await self.wall.poll()
                if result.get("replied"):
                    self._log(f"qq: 空间回了 {result['replied']} 条评论")
            except Exception as exc:  # 回不了评论，聊天照旧
                self._log(f"qq: 空间评论这一轮出错（{type(exc).__name__}: {exc}）")
            await asyncio.sleep(QZONE_FEED_TICK)

    async def _monday(self) -> None:
        """Every Monday, one sticker. Checked once a minute so a restart, a long sleep or a
        missed hour cannot skip the week: the flag records the ISO week, not the run."""
        while True:
            try:
                await self._maybe_monday()
            except Exception as exc:  # a bad Monday must not take the channel down
                self._log(f"qq: monday check failed ({type(exc).__name__}: {exc})")
            await asyncio.sleep(60)

    def _monday_rooms(self) -> list[str]:
        """Where the Monday sticker goes: `QQ_MONDAY_GROUPS` when it names rooms, else all."""
        return list(self.config.monday_groups or self.config.groups)

    async def _maybe_monday(self, now: datetime | None = None) -> None:
        """`now` is injectable so the calendar can be tested without waiting for a Monday."""
        now = now or datetime.now(_zone(self.app.settings.timezone))
        if now.weekday() != 0 or now.hour < self.config.monday_hour:
            return
        week = f"monday:{now:%G-W%V}"
        if self.pack.flag(week):
            return
        row = self.pack.pick(MONDAY_TAG)
        if row is None:
            missing = f"monday-missing:{now:%Y-%m-%d}"
            if not self.pack.flag(missing):
                self.pack.set_flag(missing, "1")
                self._log(
                    "qq: 周一的表情没有发 —— 表情包里还没有标「上班」的那张"
                    "（在群里发一次，然后「/表情标 上班」）"
                )
            return
        data = self.pack.data_url(row)
        rooms = self._monday_rooms()
        for group in rooms:
            await self.channel.outbox.send_image(group, data)
        self.pack.set_flag(week, now.isoformat(timespec="seconds"))
        self._log(f"qq: 周一的表情已发到 {len(rooms)} 个群（{','.join(rooms)}）")


def _stamp(value: str) -> float:
    """A stored epoch from `qq_flags`, or 0 when it is missing or is not a number."""
    try:
        return float(str(value or "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _until(hour: int, now: datetime) -> float:
    """Seconds until the next `hour`:00, plus a few random minutes.

    The random tail is what keeps it from being 「每天九点整」: the morning's first thought
    should not arrive on the minute.
    """
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds() + random.uniform(0, 600)


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse(env: Mapping[str, str] | None = None) -> QQConfig:
    return QQConfig.from_env(env)
