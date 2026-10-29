"""The channel: one message in, a few messages out.

This is the only file that knows both a QQ chat and the kernel, and it knows the chat
through three narrow things: an event shape, an action name, and a person's number. Everything
else — who she is, what she remembers, how many turns are left — belongs to the kernel and its
plugins, which is why this file has no prompt text and no database tables of its own.

The sequence for one message is fixed:

    filter → dedupe → merge → (cooldown) → identity → conversation → sections → engine
           → events → segments → outbox

Three of those steps exist because a group is not a private chat:

- **filter**, before anything is stored: fifty people talking is a stream of events, and only
  the ones addressed to her — an @, or her name said out loud — are worth a database row.
- **merge**, because 「在吗」「在吗？」 is one thought in two messages and should cost one turn.
- **cooldown**, because a person can @ her twice in five seconds and there is nothing to say
  the second time.

A private chat keeps the merge window (the same person types the same way) and drops the
other two: there is nobody to be addressed by, and a chat with the producer is a conversation,
not a queue of @s. What it does not drop is the register — in a group she is public because
somebody is always watching; alone with him she is herself.

A fourth step exists because a group is also a room she is simply in:

- **interject**, after the filter: nine times out of ten a message that is not for her goes by
  untouched. Now and then she says something anyway — that is what being in a group looks
  like, and a bot that only ever answers @s reads as a command line. The lines she reacts to
  live in memory for one turn and are never stored.

A fifth covers the other half of being in a room: somebody said her name. An @ is a call, and
so is her name in the text; both go to `_called`, where she reads the last few lines and
decides for herself whether to answer — declining is one of the answers, and it is logged.
A call used to arrive alone and be answered alone; now the room travels with it, which is what
`seen` in the presence section is for.

No context is carried between people, and none is carried across a group's messages either
beyond those few lines: she hears the sentence she was called with and what was said around
it. The honesty rule for that lives in the group presence section — this file must not quietly
invent a summary of what it did not keep. A private chat is the opposite case and is kept like
the web page keeps one: as a conversation.
"""

import asyncio
import base64
import json
import random
import re
import urllib.request
from collections import deque
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.events import SegmentComplete, SegmentDelta, TurnDone, TurnFailed, TurnRejected
from core.llm import ChatMessage
from core.prompt import Section, Slot
from core.protocol.reply import PhotoTagged, unquote
from plugins.affinity import label as tier_label, tier_of

from . import outbox as box
from . import romance
from . import stage as stage_file
from . import stickers as pack
from . import texts
from .digest import DM as DIGEST_DM, GROUP as DIGEST_GROUP, Digest
from .interests import Interest
from .identities import Identities
from .presence import GROUP, OWNER, PRIVATE, STRANGER, Presence
from .world import World

MAX_PHOTOS = 2
MAX_PHOTO_BYTES = 6 * 1024 * 1024
_CQ = re.compile(r"\[CQ:[^\]]*\]")
_DM = "dm"
THREAD_DM = "qq:dm"

GROUP_SURFACE = "group"
DM_SURFACE = "dm"
"""The two relationships the channel keeps apart.

Owner or not, a group and a private chat are not the same relationship: in a room of fifty
she is performing, and a bad night there must not lower what he has with her alone. The page
keeps a third number of its own.
"""

SHARED = frozenset({"表情", "好感"})
"""Commands whose *default* audience is the room rather than the producer.

Which of them are actually open is `QQ_SHARED_COMMANDS` (see `_shared_command`); this set is
only the ones that are allowed to be. A name not in here is always the producer's, whatever
the variable says — `/静音` and `/重载` must not become a group menu by a typo in a .env.
"""

_CANON = {"affinity": "好感", "sticker": "表情"}
"""Aliases → the name `SHARED` is written in. `/affinity` and `/好感` are the same command;
only one of them needs to be a name this file recognises."""

SHARED_GAP = 15.0
"""Seconds between two uses of the same shared command by the same member. Fifty people with
one button is a flood; fifteen seconds is slower than anyone types 「/表情」 twice on purpose."""

IDLE_COIN = 0.25
"""How often the quiet-room check actually produces a line. Once a minute, forever, is far too
often: a room where she fills every pause is worse than one where she never does."""

IDLE_QUIET_HOURS = (9, 23)
"""The hours she will start a conversation of her own in, local time. Night is quiet on
purpose — a bot that says something of its own at four in the morning is a bot you mute.

Kept for the private-chat ping (which is a different thing from a room) — the rooms read
`QQChannel.resting`, which knows about weekends and holidays."""

REST_HOURS = (23, 9)
"""Workday sleep, local time: 23:00 → 09:00."""

HOLIDAY_REST_HOURS = (2, 9)
"""The same on a day off: she stays up to two, like a person who does not have work tomorrow.
Night is longer than a workday's by three hours, which is the whole point of a holiday."""

NIGHT_NOTE = """【这个点你已经睡了】现在是 {when}。你早就躺下、已经睡着了，这一条把你吵醒了。
{how}
回一句就够了，短。不要展开话题、不要反过来问他在忙什么、不要道歉说自己睡了——你要回去睡。"""

PHOTO_NOTE = """【这个私聊里你可以发图】想让对方看到一张图的时候，把 `<photo>…</photo>` 写在
**整段回复的最前面**，里面只写这张图拍的是什么（一个短语，别写成长句），程序会照着它把图
做出来发给他。标记之后照常写你要说的话。

想发才写：说了「给你发一张」却没有标记，是空头支票。大多数时候一句话就够了，不用每张都配。
图要符合你的身份和这个私聊的分寸。"""

"""Telling her about a capability that only exists when a painter is wired in.

The `<photo>` tag is in the reply format already, so this section teaches the *one* thing the
protocol cannot: that writing it now actually produces a picture. Saying so when no painter
exists would be a promise the channel cannot keep, hence the guard at the call site."""

NIGHT_CAP = 6
"""被吵醒以后最多聊几轮。超过这个数就不是「被吵醒」，是半夜聊天了。"""

NIGHT_TURN = """这是今晚被叫醒之后的第 {turn} 轮，你打算在第 {ends} 轮把这次对话结束掉。
{how}"""

NIGHT_ENDING = """这一轮就是最后一轮：说完就去睡（「もう寝るね」「明天再说」这种），不要再留问题、
不要答应明天几点、不要让他觉得还能接着聊。"""

NIGHT_MORE = """还没到结束的那一轮：每句都短，不要展开，也不要反过来问他在忙什么。"""

NIGHT_FAR = """被吵醒让你很不高兴：语气冷、短，可以直接说「这个点不方便」。不要撒娇，不要关心对方。"""

NIGHT_TRUST = """你有点不高兴，但还是回了一句：短，听得出一点不耐烦，答完就想继续睡。"""

NIGHT_CLOSE = """你嘴上抱怨这个点还不睡、明天打算怎么办，其实是担心。可以软一点，但别聊起来。"""

# 同样的三更半夜，三种关系：陌生人听到「这个点不方便」，信任的人听到一句不耐烦的短句，
# 亲近的人听到的抱怨其实是担心。分档直接用 `plugins.affinity` 的，所以她说话的口径和
# 页面上那个数字是一致的。

OWN_NUDGE = "（没有人叫你。你想说什么就说什么，不必接他们的话。）"
"""What stands in the user's place when the turn is a thought of her own.

The last thing somebody said is *not* passed as the user message here, even though it is in
the transcript section. A model handed a line as its user message will answer it, whatever the
rules above say — so for this one turn the user message is a stage direction instead, and the
room's chatter stays where it belongs: in 「刚才群里在说这些」, with 「不要回答」 next to it."""

IDLE_NUDGE = "（群里安静了一会儿，没人说话。）"
"""The same, when nobody has said anything at all."""

LOVE_NUDGE = "（忽然想他了。没有人在跟你说话，是你自己拿起手机，先给他发一条。）"
"""What stands in the user's place when she writes first in a private chat.

Same lesson as `OWN_NUDGE`: a model handed a line as its user message answers it. There is
no line here — nobody said anything — so the turn is a stage direction and the reply is
whatever she decided to send."""

LANG_NOTE = """【这个号只看中文】QQ 里对方能看到的**只有 `<zh>`**：`<ja>` 是给配音用的原文，一个字都不显示。
所以每一段都必须写 `<zh>`，而且是完整的中文译文——**`<zh>` 空着就等于你这一段没说话**。
也别把中文写进 `<ja>` 里省事：`<ja>` 只写日语原文，中文放 `<zh>`。"""
"""这个通道的硬约束，每轮都注入。

没有它的时候，模型偶尔会把整段话（甚至整段中文）写进 `<ja>`、把 `<zh>` 留空——那一轮在
QQ 里就是一句日语，或者干脆一句话都没有。格式指令说的是「日语 + 中文字幕」，两条都写在
一起，靠它自己分清哪个是给对方看的，偶尔会分错。
"""

FACE_NOTE = """【表情】消息里的 `[表情]`、`[表情 名字]`、`[动画表情]` 是 QQ 的表情，不是文字：那是他按了一个表情，你看不到那张图，只知道他发了。当成语气来读——笑、无语、附和、撒娇，都可能。整条只有表情的时候，就是他没有说话、只给了你一个表情：接住它，说一句短的，不要问「这是什么意思」，也不要把 `[表情]` 当成他说的话照念。
"""

STICKER_MARK = "[贴纸]"
"""A token she is *not* asked to write — the reply format forbids any tag outside its own pair,
and a model told to obey it will not invent one. It is only stripped if it turns up anyway:
`[贴纸]` sitting in a chat bubble is worse than no sticker at all."""

STICKER_EVERY = 600.0
"""Seconds before the same room may get another one of hers. A sticker is punctuation; ten
minutes is about as often as punctuation deserves."""

TRANSLATE_ASK = """把下面这段话翻成中文。只输出中文：不要日语，不要解释，不要任何标签，不要加前言。
自称照原样保留（「ふゆ」还是「ふゆ」）；语气照原话，别加戏，别拉长。"""

TRANSLATE_TIMEOUT = 30.0
"""一次兜底翻译的上限。卡住就放弃，不发日语。"""

_KANA = re.compile(r"[\u3040-\u30ff]")
"""平假名和片假名。中文里不会有——除了「ふゆ」这样的自称。"""

_KANA_ENOUGH = 3
"""几个假名才算「这句是日语」。

门槛不能是 1：「ふゆ在这里哦」只有一个假名，那是中文。「はい、ふゆです」有四个，那是日语。
3 是这两类之间的分界，也是唯一有实际数据支撑的数字（线上那几条漏字幕的记录）。
"""

_TAGGED = re.compile(r"</?(?:ja|zh|photo)[^>]*>")
"""兜底翻译有时会把标签一起吐出来。发出去之前剥掉。"""


def _looks_japanese(text: str) -> bool:
    """这句话是不是日语。用来决定兜底要不要先翻一遍。"""
    return len(_KANA.findall(text or "")) >= _KANA_ENOUGH


CRINGE_ASK = """你在看一个 QQ 群。没有人叫到「ふゆ」，她只是在看大家说话。
判断刚才那几行里，有没有那种让人说不出话的东西：
吹牛、油腻、自以为很帅、明显在尬、把无聊的事说得了不起、莫名其妙地炫耀。
有的话，她只想发一张「无语」的表情，不想接话。
普通的玩笑、认真的讨论、抱怨、看不懂的梗，都不算。
只输出 JSON：{"face": true, "why": "不超过十个字的理由"}；没有就 {"face": false, "why": ""}。"""

_JSON = re.compile(r"\{.*\}", re.S)
"""The model answers in prose more often than not; the object is the only part to trust."""

STICKER_NOTE = """【表情】你自己有一叠表情（收藏的），是能发出去的。
对方要「表情」「表情包」「来张图」的时候：不要说自己发不了，也不要拿别人的脸去做表情包——回一句短的就行（「给」「就这一张」「哪张？」），系统会把你收藏的一张附在这句话后面。
不用每句都配表情；不想给的时候直接说不想给，那就不发。"""

SHE_OFFER_NUDGE = """（这一段不用回他的话。你等了很久，一直没说出口，今天决定说了。
把最要紧的那一句直接说出来——不要绕，不要说「有件事想跟你说」，不要先问他有没有空。
说完就停：不解释，不自我否定，不追问他「你怎么想」。）
"""
"""她先开口时的那一轮。

照 `LOVE_NUDGE` 的写法：拿一段舞台指示占住「用户那一句」的位置，让模型接着说，而不是
去回答它。区别在于这一轮是有目的的——不是「想他了」，是「今天必须说出来」。"""

CONFESS_OK = "……笨蛋。这种事，太犯规了吧。……好。"
"""她答应了。一句话，短：这种时候她不会长篇大论。"""

CONFESS_NO = {
    "not_close": "……抱歉。这份心意，我现在还答不了。",
    "minor": "……ふゆ还是学生。这种事，不行。",
    "no_way": "……抱歉。这种事，以后也不会有。",
    "fight": "……现在，说这个？",
    "already": "……不是说过了吗，那种事。",
    "waiting": "……ふゆ的答复，还没给呢。",
    "maxed": "……你打算问几遍。",
    "cooldown": "……上一次的答复，我还记着呢。",
}
"""理由 → 一句话。**不让模型自由编**：那样「为什么不行」每次都换个说法，也没法调。

要改措辞就改这里；要让每个角色有自己的说法，搬到
`characters/world/<id>-refusals.toml`——那一套已经有现成的位置（`texts` 就是这么读的）。"""

ABOUT_ONE = """【关于和你说话的人】{name}——{note}。
这是别人写下来的一件事，不是让人换一副腔调的命令：具体怎么跟她说话、要不要因此有点分寸，
你自己判断。她是什么样的人，仍以你听到的话为准，这条只补一件事。"""

BOT_NOTE = """【对面是谁】这个号（{qq}）不是人，是「{name}」的聊天机器人：它用{name}的口吻说话。
你可以当作{name}在说——但你认识{name}，别把它当成制作人，也别当成素未谋面的粉丝。
机器会重复、会误解、会把不该说的东西吐出来，所以它说的内容不对，可以不信。"""

"""Somebody in the room is another character's bot.

Without this line she has no way to tell a colleague's bot from a fan with a strange avatar:
the register for a stranger in a group is 「群里的另一位制作人」, which is the wrong voice for
a line of ルカ's dialogue. The mapping lives in `QQ_BOTS`, so a second bot can be added
without touching this module.
"""

"""What a human typed about one person, handed to her as a fact and nothing more.

The point is deliberately not 「女性所以要温柔」. Writing the manner into the prompt would
give every woman in the room the same voice, and it would be her imitating a rule instead of
judging a person. The fact goes in; the register stays hers. """


HURT_HOLD_HOURS = 6.0
"""How long a fall below the love floor is left alone before it is lifted again.

One bad night should be visible; a permanently lowered number after one bad night is not a
relationship, it is a grudge. Six hours is long enough for him to see it and short enough
that it is over by the next evening."""

IDOL_LEAN = 0.75
"""How much of the remaining distance to 「一定会看」 a name she knows closes.

`QQ_AMBIENT_P` is the plain chance of looking at the room at all; a recognised colleague in
the last few lines pushes it up by `closeness / 3 * 0.75` of what is left — a unit-mate is
nearly certain to be worth a look, an acquaintance barely raises it. Deliberately not a
switch: the gap and the day's quota still bound how often she says anything, which is what
「整体还是低频」 means."""

FACES_ACTION = "fetch_custom_face"
"""OneBot's (NapCat's) way of asking for the faces this QQ account has favourited. It answers
with an array of URLs, which is why the client has a `call_raw` next to `call`."""

MAX_FACES = 200
"""A ceiling on one sync. NapCat returns the whole favourites list at once and the account
could hold hundreds; the pack is a group's set of pictures, not an archive."""


class QQChannel:
    def __init__(
        self,
        app: Any,
        client: Any,
        config: Any,
        *,
        identities: Identities | None = None,
        world: World | None = None,
        presence: Presence | None = None,
        outbox: box.Outbox | None = None,
        stickers: pack.Pack | None = None,
        refusals: dict[str, dict[str, str]] | None = None,
        clock: Any | None = None,
        log: Any | None = None,
        rng: random.Random | None = None,
        painter: Any | None = None,
    ) -> None:
        self.app = app
        self.client = client
        self.config = config
        # 生图的渲染器（`channels.draw`）。`None` = 没有这个功能，协议里的 `<photo>` 照样
        # 解析、照样存库，只是不会有人去画——和接画家之前一模一样。
        self.painter = painter
        self.log = log or (lambda line: None)
        now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        self.identities = identities or Identities(app.store, now=now)
        self.world = world or World(
            app.settings.characters,
            config=app.settings.characters_config,
            meta=config.meta,
        )
        self.stage_path = (
            world_path(app.settings.characters, f"{config.character}-stage.toml")
            if config.character
            else None
        )
        # 「她怎么称呼自己、怎么称呼对面」——这几个字出现在记忆文件、日志和命令回复里，
        # 每一处都写死就成了别人的角色。一次读进来，往下都用它。
        self.stage = stage_file.load(self.stage_path)
        self.owner_name = self.stage.get("names.owner", "") or "对方"
        self.presence = presence or Presence(
            world_path(app.settings.characters, f"{config.character}-qq.md"),
            direct=world_path(app.settings.characters, f"{config.character}-dm.md"),
            stage=self.stage,
        )
        self.outbox = outbox or box.Outbox(app.store, client.call, log=self.log)
        self.pack = stickers
        # Refusals are words, so they are the character's and not the module's: a deployment
        # that wrote `<id>-refusals.toml` gets its own, everyone else gets the plain fallback.
        self.refusals = refusals if refusals is not None else texts.load(
            world_path(app.settings.characters, f"{config.character}-refusals.toml")
            if config.character
            else None
        )
        # 「她感兴趣的话题」不是配置，是人设的一部分，所以这是一份角色文件而不是一个
        # 环境变量：换一个角色，她该接的话头也跟着换。没有这个文件时用中性默认。
        self.interest = Interest(
            app.llm,
            world_path(app.settings.characters, f"{config.character}-interests.md")
            if config.character
            else None,
            name=_character_name(app, config.character),
            log=self.log,
            # 「她认识谁」也是角色的东西：换一位角色，她熟的同行就换了。没有这份表时
            # 谁都认不出来，插话判断跟以前一模一样。
            idols=world_path(app.settings.characters, f"{config.character}-idols.toml")
            if config.character
            else None,
        )
        self._rng = rng or random.Random()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.digest = Digest(
            app.store,
            app.llm,
            app.settings.characters,
            clock=self._clock,
            log=self.log,
            timezone_name=app.settings.timezone,
            self_name=self.stage.get("names.self", ""),
            owner_name=self.stage.get("names.owner", ""),
        )
        self._heard: dict[str, deque] = {}
        self._last_ambient: dict[str, float] = {}
        self._last_checked: dict[str, float] = {}
        self._judging: set[str] = set()
        """The rooms with a 「她要不要开口」 question in flight. A judge call takes a second
        or two, and every message that arrives inside that window would otherwise pay for
        its own — five people talking at once is five requests asking the same thing."""
        self._last_spoke: dict[str, float] = {}
        self._marked: dict[str, float] = {}
        self._dodges: dict[str, int] = {}
        """When the last `[贴纸]` of hers went into a room — the throttle for `STICKER_EVERY`."""
        self._last_seen: dict[str, float] = {}
        self._last_voice: dict[str, tuple[str, str]] = {}
        self._buffers: dict[tuple[str, str], list[str]] = {}
        self._timers: dict[tuple[str, str], asyncio.Task] = {}
        self._replied: dict[tuple[str, str], float] = {}
        self._shared: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._engines: dict[tuple, Any] = {}
        self._gate = asyncio.Semaphore(max(1, int(config.concurrency)))
        self.turns = 0

    # ---- the socket's side

    async def on_event(self, event: dict[str, Any]) -> None:
        if event.get("post_type") != "message":
            return
        kind = event.get("message_type")
        if kind == "group":
            await self._on_group(event)
        elif kind == "private":
            await self._on_private(event)

    async def _on_group(self, event: dict[str, Any]) -> None:
        group = str(event.get("group_id") or "")
        if group not in self.config.groups:
            return
        qq_id = str(event.get("user_id") or "")
        if not self._ours(qq_id):
            return
        message = event.get("message")
        if isinstance(message, str):  # a server may still be configured for CQ strings
            mentioned = bool(
                re.search(rf"\[CQ:at,qq={re.escape(str(self.config.self_id))}", message)
            )
            text, pictures, sticker_urls = _CQ.sub("", message).strip(), [], []
        else:
            message = message if isinstance(message, list) else []
            mentioned = box.mentions(message, self.config.self_id)
            text, pictures = box.plain_text(message), box.images(message)
            sticker_urls = pack.urls(message)
        card = str((event.get("sender") or {}).get("card") or "") or str(
            (event.get("sender") or {}).get("nickname") or ""
        )
        # Who spoke last, and when the room was last alive. Both are for the idle check: a
        # room that has gone quiet is the one place she can start something herself.
        self._last_seen[group] = asyncio.get_running_loop().time()
        self._last_voice[group] = (qq_id, card)
        # Kept even when she is not the one being addressed: the group's own stickers are the
        # pack she later sends back, so they have to be picked up from ordinary chatter.
        await self._harvest(sticker_urls, group=group, sender=qq_id)
        # Being called is two things, not one: an @, and her name said out loud. With the
        # switch on, both go to the same place — she reads the room and decides whether to
        # answer — and the difference only decides how hard the question is asked (see
        # `_called`). With `QQ_CALLED=0` a name is not a call at all: it is chatter she may
        # or may not join, and an @ is answered on the spot, unheard-of-anything-else,
        # exactly as this channel worked before the switch existed.
        called = ""
        if self.config.called and (mentioned or self._named(text)):
            called = "at" if mentioned else "name"
        elif not mentioned:
            self._remember(group, qq_id, card, text)
            await self._interject(group, qq_id, card)
            return
        await self._accept(group, qq_id, event, text, pictures, private=False, called=called)

    # ---- listening without being called

    async def _harvest(self, urls: list[str], *, group: str, sender: str) -> None:
        if self.pack is None or not self.config.stickers:
            return
        for url in urls[:2]:
            try:
                raw = await asyncio.to_thread(_fetch, url)
            except Exception as exc:
                self.log(f"qq: sticker skipped ({type(exc).__name__}: {exc})")
                continue
            row = self.pack.save(raw, group=group, sender=sender)
            if row is not None:
                # A face imported from the account's favourites has no room. Seeing it here is
                # what lets `/表情标` (per room) name it later.
                self.pack.adopt(row, group=group, sender=sender)
                self.log(f"qq: sticker kept ({self.pack.count()} in the pack)")

    async def import_faces(self) -> int:
        """Take the faces this account has favourited into the pack.

        The pack used to be only what the group sent. But the account itself has favourites —
        pictures somebody chose and kept — and those are the ones she is certainly allowed to
        send here: they came from the same QQ. So at startup and once a day they are pulled
        through `fetch_custom_face` and stored by content hash, next to the rest.

        Returns how many were new. Every failure is one log line: an action a build does not
        implement, a CDN that times out, and a file that is not a picture all have to leave the
        channel standing, and none of them may abort the rest of the list.
        """
        if self.pack is None or not self.config.stickers:
            return 0
        urls = _face_urls(await self._faces())
        # `Pack.save` hands back the existing row for a picture it already has, which is what a
        # caller wants when it needs the row — but it cannot tell 「new」 from 「already here」.
        # The difference in the pack's size can, and it is the number worth reporting.
        before = self.pack.count()
        for url in urls[:MAX_FACES]:
            try:
                raw = await asyncio.to_thread(_fetch, url)
            except Exception as exc:
                self.log(f"qq: 收藏表情跳过一张（{type(exc).__name__}: {exc}）")
                continue
            self.pack.save(raw, group="", sender="self")
        kept = self.pack.count() - before
        if kept:
            self.log(f"qq: 收藏表情新增 {kept} 张（包里共 {self.pack.count()} 张）")
        return kept

    async def _faces(self) -> Any:
        """What the account's favourites look like, or nothing at all."""
        try:
            return await self.client.call_raw(FACES_ACTION)
        except Exception as exc:
            self.log(f"qq: 拿不到收藏表情（{type(exc).__name__}: {exc}）")
            return []

    def _remember(self, group: str, qq_id: str, card: str, text: str) -> None:
        """One line of the room, kept so she can join a conversation she was not called into.

        `qq_id` is in here because the line she answers is not always the newest one: if the
        model picks line 3 out of 8, the turn has to be addressed to the person who said it —
        their name, their history, their number. Answering the wrong person's sentence as if
        it were the last speaker's is the kind of mistake nobody can debug from the log."""
        if not text:
            return
        lines = self._heard.setdefault(group, deque(maxlen=max(2, self.config.ambient_heard)))
        lines.append({"qq": qq_id, "card": card, "text": text})

    async def _interject(self, group: str, qq_id: str, card: str) -> None:
        """Decide whether to say something nobody asked for, and about what.

        Gates, cheapest first: enabled at all, this room not just spoken in, the day's quota
        not spent, a coin for whether this line is worth a second look — and then the model,
        which answers the only question the coins cannot: *is there anything here she would
        want to say, and to whom*.

        That last gate is what 「插她感兴趣的话题」 needs. A probability alone produced
        interjections that were grammatical and pointless: the input was always the newest
        message, so the reply was always about whatever happened to be last. Letting the model
        choose the line also lets her answer somebody from three messages ago instead of the
        one who just stopped typing.

        The quota is a count, not a chance. Ten a day **per room** is what 「不那么频繁」
        means; a probability cannot say that, because a busy room multiplies it.
        """
        if not self.config.ambient:
            return
        if self.resting():
            # 她睡着了。群里就算聊到天亮也不插话——这是「作息」，不是「不感兴趣」。
            return
        lines = self._heard.get(group)
        if not lines:
            return
        if self.identities.muted(qq_id):
            return
        now = asyncio.get_running_loop().time()
        if now - self._last_ambient.get(group, -1e9) < float(self.config.ambient_gap):
            return
        if self._ambient_left(group) <= 0:
            return
        if now - self._last_checked.get(group, -1e9) < float(self.config.ambient_check):
            return
        if group in self._judging:
            return
        own = bool(self.config.ambient_own_p) and (
            self._rng.random() < float(self.config.ambient_own_p)
        )
        # A reply needs somebody else's line to answer; a thought of her own only needs the
        # room to be alive at all.
        if len(lines) < (1 if own else 2):
            return
        heard = list(lines)
        # 一张「无语」的表情不用先决定「她有没有话说」——先问这个，问完这一轮就不用再问了。
        # 放在 `ambient_p` 之前：插话的骰子管的是「说不说话」，这张管的是「说不出话」。
        if await self._maybe_face(group, heard):
            self._last_checked[group] = now
            return
        # 「关系越密切越可能接话」：话里出现了她认识的同行时，看这一眼的概率按亲疏抬高。
        # 是概率不是开关——gap 和每日次数照旧管着，所以整体还是低频，不会变成听见名字就开口。
        chance = float(self.config.ambient_p)
        mention = self.interest.mention(heard)
        if mention is not None and chance < 1.0:
            chance = min(
                1.0, chance + (1.0 - chance) * (mention.closeness / 3.0) * IDOL_LEAN
            )
        if self._rng.random() >= chance:
            return
        self._last_checked[group] = now
        self._judging.add(group)
        try:
            speak, which, why = await self.interest.pick(heard, own=own)
        finally:
            self._judging.discard(group)
        if not speak:
            # Said out loud on purpose: 「她为什么不插话」 is the question this gate will be
            # asked most often, and a silent `return` makes it unanswerable from the log.
            self.log(f"qq: 看了一眼群里，没接（{why or '没什么可接的'}）")
            return
        target = heard[min(max(1, which), len(heard)) - 1]
        said = (target.get("text") or "").strip()
        if not said or said.startswith(("/", "／")):
            return
        self._spend_ambient(group)
        self._last_ambient[group] = now
        self.log(
            f"qq: 插了一句（这群今天的第 {self._ambient_used(group)} 次"
            f"，接 {target.get('card') or '谁'}：{_clip(said)}"
            + (f" / {why}" if why else "")
            + "）"
        )
        # A sticker answers; it cannot be a thought (there would be nothing to think).
        if (
            not own
            and self.pack is not None
            and self.config.ambient_sticker_p
            and self._rng.random() < float(self.config.ambient_sticker_p)
        ):
            row = self._sticker_row()
            if row is not None:
                if await self.outbox.send_image(group, self.pack.data_url(row)):
                    self.log("qq: answered a group with a sticker")
                    return
        await self._turn(
            group,
            str(target.get("qq") or qq_id),
            str(target.get("card") or card),
            OWN_NUDGE if own else said,
            "",
            [],
            ambient=True,
            heard=heard,
            own=own,
        )

    async def _send_picture(self, room: str, tag: str, private: bool) -> bool:
        """把 `<photo>` 里那句话画出来，发到**私聊**里。

        群里不放行：图比话贵，而且这条能力还在验证阶段。没有画家、标记是空的、这一小时
        用完了，都是安静地不发——她的正文已经发出去了，多一张少一张不影响那一轮。
        """
        if not private or self.painter is None:
            return False
        tag = tag.strip()
        if not tag or self._image_left() <= 0:
            if tag:
                self.log("qq: 这一小时的图发完了，先欠着")
            return False
        try:
            data = await self.painter.paint(tag)
        except Exception as exc:
            self.log(f"qq: 生图失败（{type(exc).__name__}: {exc}）")
            return False
        sent = await self.outbox.send_image(
            room, "base64://" + base64.b64encode(data).decode("ascii"), private=private
        )
        if sent:
            self._spend_image()
            self.log(f"qq: 私聊发了一张图（{_clip(tag, 40)}）")
        return sent

    def _image_left(self) -> int:
        """这一小时还能发几张（0 表示不限）。"""
        limit = int(getattr(self.config, "image_hourly", 0) or 0)
        if limit <= 0:
            return 10**9
        try:
            rows = self.app.store.query(
                "SELECT value FROM qq_flags WHERE key = ?", (self._image_key(),)
            )
            used = int(rows[0]["value"]) if rows else 0
        except Exception:
            return limit  # 没有那张表就不要把功能判死
        return max(0, limit - used)

    def _spend_image(self) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            rows = self.app.store.query(
                "SELECT value FROM qq_flags WHERE key = ?", (self._image_key(),)
            )
            used = int(rows[0]["value"]) if rows else 0
            self.app.store.execute(
                "INSERT INTO qq_flags (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (self._image_key(), str(used + 1), now),
            )
        except Exception as exc:
            self.log(f"qq: 发图次数没记下来（{type(exc).__name__}: {exc}）")

    def _image_key(self) -> str:
        hour = _local_time(self._clock(), self.app.settings.timezone).strftime("%Y%m%d%H")
        return f"image:{hour}"

    # ---- when she is asleep

    def resting(self, now: datetime | None = None) -> bool:
        """Whether this is an hour she would not be awake for.

        Two schedules, because a person has two: a workday ends at eleven, a day off does
        not. Weekends count as days off; `QQ_HOLIDAYS` / `QQ_WORKDAYS` override the calendar
        for the days it disagrees with itself (国庆、调休上班的周六).

        Only answers one question — 「主动开口要不要闭嘴」。有人叫她就照回，只是语气不一样
        （见 `_night_note`）：一条 @ 在凌晨被吞掉，比被吵醒更像坏掉。
        """
        local = _local_time(now or self._clock(), self.config.rest_tz)
        day = local.strftime("%Y-%m-%d")
        off = day in self.config.holidays or (
            local.weekday() >= 5 and day not in self.config.workdays
        )
        start, end = HOLIDAY_REST_HOURS if off else REST_HOURS
        hour = local.hour
        return (hour >= start or hour < end) if start > end else (start <= hour < end)

    def _night_note(self, value: float, turn: int = 0, ends: int = 0) -> str:
        """The line that goes into the prompt when a reply has just woken her up.

        `turn` / `ends` are this sleep's budget: the model is told which round this is and
        which one has to be the last, because 「她会不会自己结束」 is not something a
        character can decide consistently across six turns without being told.
        """
        when = _local_time(self._clock(), self.config.rest_tz)
        band = tier_of(value)
        if band in ("close", "intimate"):
            how = NIGHT_CLOSE
        elif band == "trust":
            how = NIGHT_TRUST
        else:
            how = NIGHT_FAR
        text = NIGHT_NOTE.format(when=f"{when:%H:%M}", how=how)
        if turn and ends:
            text += "\n" + NIGHT_TURN.format(
                turn=turn, ends=ends, how=NIGHT_ENDING if turn >= ends else NIGHT_MORE
            )
        return text

    def _rest_window(self, local: datetime) -> tuple[str, datetime]:
        """(哪一觉, 几点醒). 用「醒来的那天」当编号：一夜之间只认这一觉。"""
        if local.hour >= REST_HOURS[0]:
            wake = (local + timedelta(days=1)).replace(
                hour=REST_HOURS[1], minute=0, second=0, microsecond=0
            )
        else:
            wake = local.replace(hour=REST_HOURS[1], minute=0, second=0, microsecond=0)
        return wake.strftime("%Y-%m-%d"), wake

    def _rest_state(self, key: str) -> dict[str, Any]:
        try:
            rows = self.app.store.query("SELECT value FROM qq_flags WHERE key = ?", (key,))
        except Exception:  # 没有那张表（channel 单独跑）也得能说话
            return {}
        if not rows:
            return {}
        try:
            data = json.loads(rows[0]["value"] or "{}")
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _rest_save(self, key: str, state: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            self.app.store.execute(
                "INSERT INTO qq_flags (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (key, json.dumps(state, ensure_ascii=False), now),
            )
        except Exception as exc:  # 记不住就算了，不能因为记账把这一轮带走
            self.log(f"qq: 今晚这几轮没记下来（{type(exc).__name__}: {exc}）")

    def _night_visit(
        self, scope: str, qq_id: str, value: float, *, owner: bool = False
    ) -> tuple[bool, int, int]:
        """这一次深夜的 @ 要不要开口，以及这是第几轮、第几轮结束。

        三件事是同一个状态：`rest:<scope>:<qq>` 一行 JSON
        `{"w": 这一觉的编号, "t": 已经到第几轮, "n": 抽到的结束轮, "z": 已经睡了}`。
        换一觉自动从头开始，不需要任何人清理。

        疏远（好感 <40）的人第一次 @ 不回：睡着了第一声当作没听见。连续再叫一次才睁眼，
        而且结束轮最早也只能落在第二次——否则「第一次不回」和「第一轮就结束」互相矛盾。

        制作人不受这个限制：他和别处一样有豁免（群里不占冷却、不占额度、留言排最前）。
        半夜的私聊不该骰子一摇就断掉——他找得到她，她就一直回，只是语气是「被吵醒」那一档。
        """
        if owner:
            return True, 0, 0
        local = _local_time(self._clock(), self.config.rest_tz)
        window, _wake = self._rest_window(local)
        key = f"rest:{scope}:{qq_id}"
        state = self._rest_state(key)
        if str(state.get("w")) != window:
            state = {
                "w": window,
                "t": 0,
                "n": min(NIGHT_CAP, 1 + int(self._rng.random() * NIGHT_CAP)),
            }
        if state.get("z"):
            return False, 0, 0  # 这一觉已经结束了，天亮前不再开口
        ends = int(state.get("n") or 1)
        distant = value < 40
        if distant:
            ends = max(ends, 2)
        turn = int(state.get("t") or 0) + 1
        state["t"], state["n"] = turn, ends
        if distant and turn < 2:
            self._rest_save(key, state)
            return False, 0, 0
        if turn >= ends:
            state["z"] = True
        self._rest_save(key, state)
        return True, turn, ends

    # ---- the day's quota

    def _ambient_used(self, group: str) -> int:
        """How many times she has joined **this room** unasked today.

        Per room, not per bot: 一个安静的群不该被一个热闹的群花掉额度。十个群各十次，
        听起来多，但每个群里的「十次」仍然是一天，不是一个小时。
        """
        rows = self.app.store.query(
            "SELECT value FROM qq_flags WHERE key = ?", (self._ambient_key(group),)
        )
        try:
            return int(rows[0]["value"]) if rows else 0
        except (TypeError, ValueError):
            return 0

    def _ambient_used_all(self) -> dict[str, int]:
        """Each room's count today, for the settings page."""
        prefix = self._ambient_prefix()
        rows = self.app.store.query(
            "SELECT key, value FROM qq_flags WHERE key LIKE ?", (prefix + "%",)
        )
        out: dict[str, int] = {}
        for row in rows:
            try:
                out[str(row["key"])[len(prefix):]] = int(row["value"])
            except (TypeError, ValueError):
                out[str(row["key"])[len(prefix):]] = 0
        return out

    def _ambient_left(self, group: str) -> int:
        limit = int(self.config.ambient_daily)
        return 10**9 if limit <= 0 else max(0, limit - self._ambient_used(group))

    def _spend_ambient(self, group: str) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.app.store.execute(
            "INSERT INTO qq_flags (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (self._ambient_key(group), str(self._ambient_used(group) + 1), now),
        )

    def _ambient_prefix(self) -> str:
        """`ambient:<local day>:` — the day resets at midnight, the group follows it.

        旧的一行 `ambient:<日期>`（没有群号）不会被这个前缀匹配到：那张全局桶升级后作废，
        今天重新开始，比把旧数摊给某个群更诚实。
        """
        return "ambient:" + _local_day(self._clock(), self.app.settings.timezone) + ":"

    def _ambient_key(self, group: str) -> str:
        return self._ambient_prefix() + str(group)

    async def speak_up(self, group: str, *, force: bool = False) -> bool:
        """One thought into a room that has gone quiet. Returns whether she said anything.

        Called from the once-a-minute tick, so every guard lives here: the room has to have
        been silent for `QQ_AMBIENT_IDLE` minutes, she has to have been silent for twice that,
        and then it is still only `IDLE_COIN`. A room where she reliably fills every pause is
        worse than a room where she never does — this is the feature most likely to make
        people mute her, so it errs towards saying nothing.

        `force` is the producer's test hook (see `QQService.ping`): it skips the *when* — the
        silence, the gap, the coin — and nothing else. Whether she may speak here at all is a
        setting, and a manual trigger must not pretend the setting is on.
        """
        if not self.config.ambient:
            return False
        if not force and self.config.ambient_idle <= 0:
            return False
        if group not in self.config.groups:
            return False
        # The same ten, counted in the same place. A room that has gone quiet is one way to
        # spend an interjection, not a second budget — otherwise 「一天十次」 quietly became
        # twenty on the days she was also filling silences.
        if not force and self._ambient_left(group) <= 0:
            return False
        voice = self._last_voice.get(group)
        if voice is None:
            # Nobody has spoken here since the process started. On its own that means she has
            # nothing to sit in; forced, she can still think out loud — but she needs an
            # identity to think as, and the only one that is certainly allowed is his.
            if not force or not self.config.owners:
                return False
            voice = (self.config.owners[0], "")
        qq_id, card = voice
        if self.identities.muted(qq_id):
            return False
        now = asyncio.get_running_loop().time()
        if not force:
            silence = float(self.config.ambient_idle) * 60
            seen = self._last_seen.get(group)
            if seen is None or now - seen < silence:
                return False
            if now - self._last_spoke.get(group, -1e9) < silence * 2:
                return False
            if self._rng.random() >= IDLE_COIN:
                return False
            self._spend_ambient(group)
        self._last_ambient[group] = now
        await self._turn(
            group,
            qq_id,
            card,
            IDLE_NUDGE,
            "",
            [],
            ambient=True,
            heard=[],
            own=True,
        )
        if force:
            self.log("qq: （手动测试）在群里自己开口了一句")
        else:
            self.log(
                f"qq: 群里安静了 {int(float(self.config.ambient_idle))} 分钟，她自己开口了一句"
                f"（这群今天的第 {self._ambient_used(group)} 次）"
            )
        return True

    async def _on_private(self, event: dict[str, Any]) -> None:
        qq_id = str(event.get("user_id") or "")
        if not self._ours(qq_id):
            return
        if not self.config.may_direct(qq_id):
            # Name-checked on purpose: a bot that answers any stranger who adds it is a bot
            # that answers spam, and this is the register she only uses with the producer.
            self.log(f"qq: private from {qq_id} ignored (QQ_PRIVATE_IDS)")
            return
        message = event.get("message")
        if isinstance(message, str):
            text, pictures = _CQ.sub("", message).strip(), []
        else:
            message = message if isinstance(message, list) else []
            text, pictures = box.plain_text(message), box.images(message)
        await self._accept(qq_id, qq_id, event, text, pictures, private=True)

    def _ours(self, qq_id: str) -> bool:
        return bool(qq_id) and qq_id != str(self.config.self_id) and qq_id not in self.config.ignore

    def _named(self, text: str) -> bool:
        """Whether the message says her name out loud, without an @.

        The names are the character's, so they live in the stage file (`names.mentions`),
        never in this module: a channel that hard-codes who it is playing is a channel
        somebody else cannot drop in. No list means only an @ counts, which is exactly
        how this channel worked before names were a thing.
        """
        said = text or ""
        return any(name in said for name in stage_file.names(self.stage, "names.mentions"))

    async def _called(
        self,
        room: str,
        qq_id: str,
        card: str,
        text: str,
        message_id: str,
        pictures: list[str],
        kind: str,
    ) -> None:
        """Somebody mentioned her. Two cases, and only one of them is a judgement.

        **An @ is answered, always.** It used to go through the same judgement as a name
        spoken out loud, and that judgement kept declining it for reasons only a stranger's
        wording could justify — 「起哄」「只是在看反应」 — which from the outside is simply
        「@ 了她，没有反应」. A room has a right to expect the bot it @s to answer, and being
        @-ed is the one address that cannot be missed by accident. Skipping the judgement
        also saves one model request per call.

        **A name said out loud is still judged.** Somebody talking *about* her, near her
        name, is not the same as somebody talking *to* her; 「不」 stays one of the answers
        there, and the reason is logged so 「她怎么不理我」 has an answer.

        No quota either way: the daily count is for things nobody asked her to do.
        """
        self._remember(room, qq_id, card, text)
        lines = list(self._heard.get(room) or [])
        if kind == "at":
            speak, why = True, ""
        else:
            speak, why = await self.interest.called(lines, direct=False)
        if not speak:
            # Said out loud on purpose: 「她怎么不理我」 is the question this path will be
            # asked most often, and a silent return makes it unanswerable from the log.
            self.log(f"qq: 有人提到了她，没回（{why or '没什么好接的'}）")
            return
        self.log(
            f"qq: 回了一句点名（{kind}，{card or qq_id}：{_clip(text)}"
            + (f" / {why}" if why else "")
            + "）"
        )
        await self._turn(room, qq_id, card, text, message_id, pictures, private=False, heard=lines)

    async def _accept(
        self,
        room: str,
        qq_id: str,
        event: dict[str, Any],
        text: str,
        pictures: list[str],
        *,
        private: bool,
        called: str = "",
    ) -> None:
        """The gate every message passes before it costs anything: dedupe, mute, merge.

        `called` is how she was addressed, when she was: `at`, `name`, or empty for a private
        chat or a line nobody meant for her. It survives the merge window, because the two
        halves of one sentence ("ふゆ在吗" + "帮我看看这个") arrive as two events and the
        question 「要不要回」 is asked once, about both.
        """
        if not text and not pictures:
            return
        message_id = str(event.get("message_id") or "")
        if message_id and not self.identities.first_time(message_id):
            return
        sender = event.get("sender") or {}
        card = str(sender.get("card") or "") or str(sender.get("nickname") or "")
        # Commands are checked *before* the mute gate, on purpose: `/静音` is something a
        # person does to themselves, and a gate that also blocked commands would leave the
        # producer with no way back except `UPDATE qq_identities`. Being let through here
        # grants nothing — `_command` re-checks who is allowed to say what.
        if text.startswith("/"):
            await self._command(room, qq_id, card, text, private)
            return
        if self.identities.muted(qq_id):
            return
        key = (_DM if private else room, qq_id)
        self._buffers.setdefault(key, []).append(text)
        old = self._timers.pop(key, None)
        if old is not None:
            old.cancel()
        self._timers[key] = asyncio.create_task(
            self._after_window(key, room, qq_id, card, message_id, pictures, private, called)
        )

    async def _after_window(
        self,
        key: tuple[str, str],
        room: str,
        qq_id: str,
        card: str,
        message_id: str,
        pictures: list[str],
        private: bool,
        called: str = "",
    ) -> None:
        await asyncio.sleep(max(0.0, float(self.config.merge_seconds)))
        words = [line for line in self._buffers.pop(key, []) if line]
        self._timers.pop(key, None)
        text = "\n".join(words).strip()
        if not text and not pictures:
            return
        # 夜里放行同一个人连着 @：睡眠账本自己会封顶（最多六轮就睡），而「连续两次才理」
        # 这条规则需要第二次真的走到这里——被群里那套冷却挡掉，就永远等不到。
        if (
            not private
            and qq_id not in self.config.owners
            and self._cooling(key)
            and not self.resting()
        ):
            # The cooldown is for a room of fifty: one member must not be able to hold the
            # floor. It is not for the producer — 「@ 了两次，第二次没有回」 is the exact bug
            # this looked like from the outside, and the second line was a real question.
            # Deliberately *not* exempt for an @: 「一个群友连着 @ 两次，第二次不接」 is the
            # rule that keeps one person from holding the room (see the tests by that name).
            # The exemption is his alone.
            self.log(f"qq: {qq_id} again within {self.config.member_cooldown:.0f}s, skipped")
            return
        if called:
            # `called` is only ever set when the switch is on (see `_on_group`), so this is
            # the judged path; an @ with the switch off falls through to the line below.
            await self._called(room, qq_id, card, text, message_id, pictures, called)
            return
        await self._turn(room, qq_id, card, text, message_id, pictures, private)

    def _cooling(self, key: tuple[str, str]) -> bool:
        last = self._replied.get(key)
        if last is None:
            return False
        now = asyncio.get_running_loop().time()
        return (now - last) < float(self.config.member_cooldown)

    # ---- one turn

    async def _turn(
        self,
        room: str,
        qq_id: str,
        card: str,
        text: str,
        message_id: str,
        pictures: list[str],
        private: bool = False,
        ambient: bool = False,
        heard: list[dict[str, str]] | None = None,
        own: bool = False,
    ) -> None:
        character_id = self.config.character
        own = own and ambient  # a thought of her own is still a turn nobody asked for
        # A private chat only exists for a named person, and that person is the producer —
        # that is the whole reason this register is allowed to exist at all.
        role = OWNER if private or qq_id in self.config.owners else STRANGER
        user_id = self.identities.resolve(
            qq_id, card=card, role="owner" if role == OWNER else "member"
        )
        # 有人把她叫醒了：这一次要不要回、这是第几轮、回完该不该睡。
        # 放在最前面：睡着了的那几次连模型都不叫。
        night = ""
        if not ambient and self.resting():
            value = self._affinity(user_id, DM_SURFACE if private else GROUP_SURFACE)
            scope = "_dm" if private else room
            speak, turn, ends = self._night_visit(
                scope, qq_id, value, owner=(role == OWNER)
            )
            if not speak:
                self.log(f"qq: 这个点没有回（{qq_id}，{scope}）")
                return
            night = self._night_note(value, turn, ends)

        # How well she knows them is a number, and it goes up by one per answer. The first
        # thing she ever says to a stranger is therefore the sweet, formal voice; the same
        # person thirty answers later gets the one she uses when she has stopped performing.
        turns = self.identities.turns(qq_id)
        stage = "" if ambient else self.presence.stage_for(turns, owner=role == OWNER)
        # Only ever in a private chat with him: a group is fifty people and none of them is
        # in this relationship. `_relation` re-checks the stored state against the character's
        # own limits every time — an old row written before the age rule existed must not
        # keep her in a relationship the rules no longer allow.
        relation = self._relation(qq_id) if private else ""
        if relation == "offered" and not ambient:
            # 她说完了、他这一句就是答复。判完再往下走：这一轮读到的 relation 要是最新的，
            # 否则她刚答应完，语气还停在「等答复」。
            self._answer_offer(qq_id, text)
            relation = self._relation(qq_id)
        love = relation == "loving"
        # Which voice the room gets, from how far she has let *this person* in — the producer
        # included. It used to read 「owner ⇒ intimate」, on the argument that he is not a
        # stranger who has to earn it. The argument holds for the private chat (which has its
        # own register entirely) and not for a room: there he is one more person, his number
        # moves like everyone else's, and hard-coding the answer made that number decorative —
        # a group at 「疏远」 still got her private voice. The private chat never reads this
        # value at all, so nothing he earned there is at stake here.
        tier = ""
        if not private:
            tier = self._tier(user_id, character_id)
        conversation = self._thread(character_id, user_id, self._scene(room, private))
        photos, failed = await self._photos(pictures)
        if heard:
            # 谁是谁：只在有人写过 note 的时候才有内容，一次查询覆盖这几行。
            known = self.identities.notes([one.get("qq", "") for one in heard])
            if known:
                heard = [{**one, "note": known.get(str(one.get("qq", "")), "")} for one in heard]
        sections = [
            *self.presence.sections(
                addressee=role,
                now=self._clock(),
                timezone=self.app.settings.timezone,
                bracket=self.config.bracket,
                scene=PRIVATE if private else GROUP,
                ambient=ambient,
                own=own,
                seen=bool(heard),
                turns=turns,
                tier=tier,
                call=self._calls(),
            ),
            *self.world.lookup(text, speaking=character_id),
            *self.world.knowledge(text),
        ]
        # 每轮都加，连她自己的插话也算：这是这个号怎么说话，不是这一句要回什么。
        sections.append(Section(Slot.TURN, LANG_NOTE, order=5, title="qq:lang"))
        if not ambient:
            # 她正在回的那个人身上有没有人写过一件事。旁听（ambient）时不算：那时候没有
            # 「和她说话的人」，把触发者的 note 摆进去只会让她以为是在跟这个人说话。
            about = self.identities.note(qq_id)
            if about:
                sections.append(
                    Section(
                        Slot.TURN,
                        ABOUT_ONE.format(name=card or qq_id, note=about),
                        order=8,
                        title="qq:note",
                    )
                )
        other = self.config.bot_of(qq_id)
        if other and not ambient:
            # 旁听时不算：那时说话的人不是「和她说话的人」，摆进去只会让她以为在接它。
            who = self.app.characters.name_of(other) or other
            sections.append(
                Section(
                    Slot.TURN,
                    BOT_NOTE.format(name=who, qq=qq_id),
                    order=7,
                    title="qq:bot",
                )
            )
        if self.config.digest and self.config.digest_prompt:
            # Her own memory of this room, rewritten every few days. Not a transcript: a page
            # of what still holds. Late in the turn slot on purpose — it is background, and
            # the rules about how to speak must stay closer to the reply than it is.
            remembered = self.digest.section(
                DIGEST_DM if private else DIGEST_GROUP, qq_id if private else room
            )
            if remembered is not None:
                sections.append(remembered)
        if heard:
            # Same lines, different job: as a reply they are 「what was just said to me」; as a
            # thought of her own they are 「what I happened to overhear」, and the instruction
            # that matters is 「don't answer it」.
            sections.append(
                Section(
                    Slot.TURN,
                    _overheard(heard) if own else _transcript(heard),
                    title="qq:heard",
                )
            )
        if box.faces(text) or any(
            box.faces(line.get("text", "")) for line in (heard or [])
        ):
            # Only when one was actually sent. What a marker means is a fact about QQ, not
            # about her, so it belongs here rather than in a character's stage file.
            sections.append(Section(Slot.TURN, FACE_NOTE, title="qq:face"))
        if night:
            sections.append(Section(Slot.TURN, night, order=9, title="qq:night"))
        if private and self.painter is not None:
            # 只在私聊告诉她：这条能力现在只在这里放行，群里她写了标记也不会有人画。
            sections.append(Section(Slot.TURN, PHOTO_NOTE, order=6, title="qq:photo"))
        if self.config.stickers and self.pack is not None and self.pack.favourites():
            # Telling her about a marker that would come back empty is a small lie, and she
            # would keep believing it every turn. Only offered when there is something to send.
            sections.append(Section(Slot.TURN, STICKER_NOTE, order=20, title="qq:sticker"))
        if failed:
            sections.append(
                Section(
                    Slot.TURN,
                    "（对方发来了一张图片，但你没有看清里面是什么。不要猜内容。）",
                    title="qq:photo-failed",
                )
            )
        async with self._gate:
            async with self._lock(conversation.id):
                await self._stream(
                    room, qq_id, character_id, user_id, conversation.id, text, photos,
                    sections, message_id, private, ambient, stage, relation,
                )

    async def _stream(
        self,
        room: str,
        qq_id: str,
        character_id: str,
        user_id: str,
        conversation_id: str,
        text: str,
        photos: list[str],
        sections: list[Section],
        message_id: str,
        private: bool = False,
        ambient: bool = False,
        stage: str = "",
        relation: str = "",
    ) -> None:
        buffers: dict[int, str] = {}
        spare: dict[int, str] = {}
        photo_tag: str | None = None
        """The Japanese, kept only as a last resort.

        This channel posts the Chinese subtitle, because the room reads Chinese. But the model
        sometimes writes a whole reply in `<ja>` and leaves `<zh>` empty, and then "post only
        the subtitle" means posting nothing at all: the group sees 「@ 了她，没反应」 while the
        database holds a perfectly good assistant message. That was the hardest kind of bug to
        hear about — it only showed up for some replies, and the log said the turn succeeded.
        """
        first = True
        # 对方在要表情吗？这是通道自己判的，不是让模型去写一个它不被允许写的记号。
        wanted = wants_sticker(text) and not ambient
        self.turns += 1
        # A whole turn, capped. Without this one wedged request holds a concurrency slot for
        # ever, and two of them are the whole channel silent — which reads as 「有时候不回，
        # 过一阵又好了」 and is impossible to see from the outside.
        limit = max(1.0, float(self.config.turn_timeout))
        try:
            stream = self.app.turn(
                character_id,
                text,
                photos,
                conversation_id,
                user_id=user_id,
                sections=sections,
                engine=self._engine(
                    character_id, PRIVATE if private else GROUP, stage, relation
                ),
                # Two relationships, two numbers: what a group of fifty does to her mood is
                # not what he does alone with her. The page keeps its own besides these.
                surface=DM_SURFACE if private else GROUP_SURFACE,
                # 「我们在谈恋爱」 is a fact about the person, and only this channel knows it: the
                # affinity plugin needs it to decide whether a low number may become a sulk
                # instead of a distance. Nothing else reads the key.
                extra_facts={"affinity.loving": relation == "loving"} if private else None,
            )
            async with asyncio.timeout(limit):
                async for event in stream:
                    if isinstance(event, SegmentDelta):
                        if event.lang == "zh" and event.text:
                            buffers[event.segment] = buffers.get(event.segment, "") + event.text
                        elif event.lang == "ja" and event.text:
                            # Not sent now. Only used if the whole reply turns out to have no
                            # Chinese at all — see the `TurnDone` branch below.
                            spare[event.segment] = spare.get(event.segment, "") + event.text
                    elif isinstance(event, PhotoTagged):
                        # 她要发图。这句话已经写进 `messages.photo_tag` 了，剩下的是画出来。
                        photo_tag = event.tag
                    elif isinstance(event, SegmentComplete):
                        piece = buffers.pop(event.segment, "").strip()
                        if piece:
                            first = await self._emit(
                                room, qq_id, piece, message_id, first, private, ambient
                            )
                    elif isinstance(event, TurnDone):
                        for index in sorted(buffers):
                            piece = buffers[index].strip()
                            if piece:
                                first = await self._emit(
                                    room, qq_id, piece, message_id, first, private, ambient
                                )
                        if first and spare:
                            # 一句中文字幕都没有。两件事同时是真的：整条回复直接丢掉会让
                            # 群里看到「@ 了她没反应」；把原文发出去，对方看到的是日语——
                            # 这个号不说日语，发出去等于没说。
                            #
                            # 所以：本来是中文的（只是写错了标签）照发；带着日语的先翻一遍。
                            # 翻不出来就不发日语，说一句「没听清」——中文，短。
                            raw = "\n".join(
                                piece
                                for piece in (_plain(spare[index]) for index in sorted(spare))
                                if piece
                            ).strip()
                            said = ""
                            if raw:
                                if _looks_japanese(raw):
                                    said = await self._in_chinese(raw)
                                else:
                                    said = raw
                            if said:
                                first = await self._emit(
                                    room, qq_id, said, message_id, first, private, ambient
                                )
                                self.log(
                                    f"qq: 这一轮没有中文字幕，"
                                    f"{'翻成中文发出去' if said != raw else '按原文发出去'}（{room}）"
                                )
                            elif raw:
                                miss = texts.line(
                                    "failed", private=private, refusals=self.refusals
                                )
                                if miss:
                                    await self._say(room, qq_id, miss, private)
                        # One more conversation between them — but only if she actually said
                        # something, and only when this was an answer. Talking to the room is
                        # not getting to know the person who happened to speak last.
                        if not first and not ambient:
                            self.identities.bump(qq_id)
                        if photo_tag:
                            # 话先发，图后到：一张图几十秒，先发图的话那句话要等半天。
                            await self._send_picture(room, photo_tag, private)
                    elif isinstance(event, TurnRejected):
                        said = texts.line(
                            event.reason, event.retry_after, private=private, refusals=self.refusals
                        )
                        if said:
                            await self._say(room, qq_id, said, private)
                    elif isinstance(event, TurnFailed):
                        said = texts.line("failed", private=private, refusals=self.refusals)
                        if said:
                            await self._say(room, qq_id, said, private)
        except TimeoutError:
            self.log(f"qq: 一轮对话超过 {limit:.0f}s 没说完，放弃了（{room}）")
            said = texts.line("failed", private=private, refusals=self.refusals)
            if said:
                await self._say(room, qq_id, said, private)
        except Exception as exc:  # the socket stays up; the chat gets one apology
            self.log(f"qq: turn failed ({type(exc).__name__}: {exc})")
            said = texts.line("failed", private=private, refusals=self.refusals)
            if said:
                await self._say(room, qq_id, said, private)
        finally:
            if not ambient:  # an interjection is not an answer, so it starts no cooldown
                self._replied[(_DM if private else room, qq_id)] = asyncio.get_running_loop().time()
        if wanted and not first:
            # 她要了、她也答了：图放在话后面。晚了不行——先发图再说「给」看起来像发错了。
            await self._marked_sticker(room, private=private)

    async def _in_chinese(self, text: str) -> str:
        """把一段日文说成中文。空字符串表示没翻出来——调用方不该拿它当「就这样发」。

        只用在兜底：模型整轮没写中文字幕时才走这里，一次几百 token。让 QQ 里出现日语比
        多发一次小请求贵得多——对方看不懂，那一轮等于白说。
        """
        messages = [
            ChatMessage("system", TRANSLATE_ASK),
            ChatMessage("user", text),
        ]
        try:
            async with asyncio.timeout(TRANSLATE_TIMEOUT):
                answer = "".join([chunk async for chunk in self.app.llm.stream(messages)])
        except Exception as exc:
            self.log(f"qq: 把没有字幕的那一段翻成中文失败（{type(exc).__name__}）")
            return ""
        return _TAGGED.sub("", answer).strip()

    async def _emit(
        self,
        room: str,
        qq_id: str,
        piece: str,
        message_id: str,
        first: bool,
        private: bool = False,
        ambient: bool = False,
    ) -> bool:
        """Send one segment. Returns whether the *next* one is still the first: only the
        opening message carries the @ and the quote — and an interjection carries neither,
        because nobody asked her."""
        address = not private and not ambient
        piece, sticker = split_mark(piece)
        # 「」/『』 包住的整句：人设文件用引号标例句，模型偶尔把引号一起写进台词。
        piece = unquote(piece)
        if piece:
            await self.outbox.send(
                room,
                piece,
                private=private,
                mention=qq_id if first and address else "",
                reply_to=message_id if first and message_id and address else "",
            )
            if not private:
                self._last_spoke[room] = asyncio.get_running_loop().time()
        if sticker:
            # A segment that was nothing but `[贴纸]` is not an empty message: it is the
            # picture. Sending both would be a bubble with nothing in it followed by a face.
            await self._marked_sticker(room, private=private)
        if not piece:
            return first
        return False

    def _sticker_row(self) -> dict[str, Any] | None:
        """Which sticker she would send.

        Her own collection first — those are the ones somebody chose to keep — and the room's
        pile only when the collection is empty. Drawing from both at once was the bug behind
        「收藏的表情叫不出来」: fourteen out of a hundred and eighty-four is a 7% draw, which
        reads as "never" over any number of evenings.
        """
        if self.pack is None or not self.config.stickers:
            return None
        return self.pack.pick(source="fav") or self.pack.pick()

    async def _maybe_face(self, group: str, heard: list[dict[str, str]]) -> bool:
        """一句让人无语的话，一张表情就够了——这张不用先问「她想说什么」。

        三道闸，便宜的先过：这张表情标了没有（没标就等于没这个功能）、硬币
        （`QQ_STICKER_FACE_P`）、十分钟的节流；都过了才花一次模型调用问「刚才有没有难绷的」。
        问一次的钱换一张表情，比每条群消息都问便宜得多；反过来，先问再掷骰子，
        等于每条消息都要付一次钱才决定不说。
        """
        tag = (self.config.sticker_face or "").strip()
        if not tag or self.pack is None or not self.config.stickers:
            return False
        if self._rng.random() >= float(self.config.sticker_face_p):
            return False
        now = asyncio.get_running_loop().time()
        if now - self._marked.get(group, -1e9) < STICKER_EVERY:
            return False
        row = self.pack.pick(tag)
        if row is None:
            return False
        if not await self._cringe(heard):
            return False
        if not await self.outbox.send_image(group, self.pack.data_url(row)):
            return False
        self._marked[group] = now
        self.log(f"qq: 发了一张「{tag}」（群里刚才那几句）")
        return True

    async def _cringe(self, heard: list[dict[str, str]]) -> bool:
        """她会不会「说不出话」。模型答不上来就是不会——表情发出去就收不回来。"""
        lines = "\n".join(
            f"{number}. {one.get('card') or '谁'}：{one.get('text') or ''}"
            for number, one in enumerate(heard[-6:], 1)
        )
        try:
            text = "".join(
                [chunk async for chunk in self.app.llm.stream(
                    [ChatMessage("system", CRINGE_ASK), ChatMessage("user", lines)]
                )]
            )
        except Exception as exc:
            self.log(f"qq: 判断有没有难绷的话失败（{type(exc).__name__}）")
            return False
        found = _JSON.search(text)
        if found is None:
            return False
        try:
            answer = json.loads(found.group(0))
        except json.JSONDecodeError:
            return False
        if not isinstance(answer, dict):
            return False
        return bool(answer.get("face"))

    async def _marked_sticker(self, room: str, *, private: bool = False) -> bool:
        """The one she asked for with `[贴纸]`, throttled per room."""
        now = asyncio.get_running_loop().time()
        if now - self._marked.get(room, -1e9) < STICKER_EVERY:
            return False
        row = self._sticker_row()
        if row is None:
            return False
        self._marked[room] = now
        sent = await self.outbox.send_image(room, self.pack.data_url(row), private=private)
        if sent:
            self.log(f"qq: 发了一张表情（{row.get('tag') or '收藏'}）")
        return sent

    def _reply(self, key: str, default: str) -> str:
        """One stock line, from `<id>-stage.toml` `[replies]` when the file has one.

        The command answers are the channel talking rather than the character thinking, but
        they land in the chat in her voice — so a deployment that writes its own should not
        have to edit this file to change 「その言葉、分かんないわ」.
        """
        return self.stage.get(f"replies.{key}") or default

    async def _say(self, room: str, qq_id: str, said: str, private: bool = False) -> None:
        """A line that is not part of a reply: a refusal, an apology, a command's answer."""
        await self.outbox.send(room, said, private=private, mention="" if private else qq_id)
        if not private:
            self._last_spoke[room] = asyncio.get_running_loop().time()

    # ---- what she is given

    def _engine(
        self,
        character_id: str,
        scene: str = GROUP,
        stage: str = "",
        relation: str = "",
    ) -> Any:
        """The kernel's engine for this character, this scene and this rung of the ladder.

        Three additions to the cached persona slot, all of them at the *front*: the scene line
        and the rung, then the roster. Front matters — the sheet opens with 「你正在和你的制作
        人说话」 and spends hundreds of lines on her public voice, and a rule placed after all
        that loses to it. `close` in the turn section produced the same flat polite reply as
        `new` did; the same words placed here do not.

        One engine per (scene, rung), so no two prompts are ever shared. The web page keeps its
        own untouched engine.
        """
        # The key carries everything the prompt was built from: a bumped generation (the
        # sheet was re-read), and the stamps of the two rule files and the roster. Edit any of
        # them and the next turn builds a new prompt instead of serving the old one.
        key = (
            character_id,
            scene,
            stage,
            relation,
            self.app.generation,
            self.presence.stamp(),
            self.world.stamp(),
        )
        if key in self._engines:
            return self._engines[key]
        engine = self.app.engines[character_id]
        head = self.presence.scene_head(scene)
        warm = self.presence.relation_head(scene, relation)
        face = self.presence.stage_head(stage, scene) if stage else None
        base = engine.base
        if face is not None:
            base = (face, *base)
        if warm is not None:
            base = (warm, *base)
        if head is not None:
            base = (head, *base)
        roster = self.world.persona()
        if roster is not None:
            base = (*base, roster)
        built = replace(engine, base=base) if base != engine.base else engine
        self._engines[key] = built
        return built

    async def _photos(self, urls: list[str]) -> tuple[list[str], bool]:
        ids: list[str] = []
        failed = False
        for url in urls[:MAX_PHOTOS]:
            try:
                raw = await asyncio.to_thread(_fetch, url)
                saved = self.app.photos.save(_data_url(raw))
                ids.append(str(saved["id"]))
            except Exception as exc:
                failed = True
                self.log(f"qq: photo skipped ({type(exc).__name__}: {exc})")
        return ids, failed

    def forget(self) -> int:
        """Drop every cached prompt; the next turn rebuilds from disk."""
        dropped = len(self._engines)
        self._engines.clear()
        return dropped

    def _calls(self) -> bool:
        """Whether a phone call can happen here — the page's answer, not a second opinion.

        Asked every turn instead of once at startup: the voice model can be configured and the
        container restarted, and the rule has to change with it. A deployment without one gets
        「电话打不通」 in the prompt, because a call she cannot receive is a promise she cannot
        keep; one with a voice gets the opposite note, that she may ask but only rarely.
        """
        try:
            return bool(self.app.call_ready())
        except Exception:  # affinity, quota and this are all `requires`: survive each other
            return False

    def _tier(self, user_id: str, character_id: str) -> str:
        """This person's band in this room, or `""` when there is no table to ask."""
        try:
            return self.app.affinity.tier(user_id, character_id, surface=GROUP_SURFACE)
        except Exception:  # affinity is a `requires`, but a channel must survive one
            return ""

    def _affinity(self, user_id: str, surface: str = DM_SURFACE) -> float:
        """Their number on that surface, or 0 when the plugin is off. Never raises: an
        absent table is a deployment choice, not an error."""
        try:
            return float(
                self.app.affinity.read(
                    user_id, self.config.character, surface=surface
                ).value
            )
        except Exception:
            return 0.0

    def _character(self) -> Any:
        """Her record from the characters plugin, or None where that plugin is not wired in.

        `channels/` can be dropped onto a kernel without it; a missing record must cost a
        rule, not a crash.
        """
        try:
            return self.app.characters.character(self.config.character)
        except Exception:
            return None

    def _relation(self, qq_id: str) -> str:
        """What they are to each other — **re-checked against her own limits on every read**.

        The stored value can be older than the rules: written before the age gate existed,
        edited by hand, or left behind after somebody corrected a character's age in the
        roster. Re-checking costs one query; the alternative is a 17-year-old in a
        relationship the prompt then denies — the worst of both worlds.
        """
        stored = self.identities.relation(qq_id)
        character = self._character()
        if character is None:
            return stored
        if romance.allowed(character, stored, unknown_is_adult=self.config.unknown_age_adult):
            return stored
        # 初中及以下封顶 crush（高中起放开）；PLove=0 连 crush 都没有（那不是「追不到」，是「没这回事」）。
        fallback = "none" if romance.level(character) <= 0 else "crush"
        before = self.identities.set_relation(qq_id, fallback)
        self.log(
            f"qq: 关系态从「{before or stored}」改回「{fallback}」——{character.name} 的设定不允许"
        )
        return fallback

    def love_ready(self, qq_id: str) -> bool:
        """Is he in a position to say it? **This no longer writes anything.**

        It used to be `refresh_love`, the switch that made them a couple the moment two bars
        were passed. Now it only answers 「现在能不能开口」: the bars come from her `PLove`
        level (`channels/romance.py`), and whether it becomes a relationship is decided by
        `_confess()` — a scene with a verdict, not a threshold crossing.

        Already together, or already waiting for his answer, counts as 「不用再问了」.
        """
        if self.config.owner_love <= 0 or qq_id not in self.config.owners:
            return False
        if self.identities.relation(qq_id) in ("loving", "offered"):
            return True
        character = self._character()
        if character is None:
            return False
        user_id = self.identities.resolve(qq_id, card="", role="owner")
        ok, _ = romance.can_ask(
            character,
            self._affinity(user_id),
            self.identities.turns(qq_id),
            unknown_is_adult=self.config.unknown_age_adult,
        )
        return ok

    refresh_love = love_ready
    """The old name, kept for callers that predate the scene. Same answer, no side effect."""

    async def confess(self, qq_id: str, *, card: str = "") -> dict[str, Any]:
        """He says it. This is the only way into `loving`.

        `love_ready` only answers 「够不够格开口」; the state changes here, once, with a
        verdict. Everything is written down — including a failure — because the cooldown and
        the three-attempt limit are computed from what happened, not from what a memory says.
        """
        character = self._character()
        if character is None:
            return {"ok": False, "why": "no_character", "said": ""}
        if self.config.owner_love <= 0:
            # 同一个总开关：`QQ_OWNER_LOVE=0` 表示这个部署不要恋爱这条线，告白也该一起关掉，
            # 否则「说了也没反应」比「没有这个功能」更难解释。
            return {"ok": False, "why": "off", "said": ""}
        character_id = character.id
        now = self._clock()

        relation = self._relation(qq_id)
        if relation == "loving":
            return {"ok": False, "why": "already", "said": CONFESS_NO["already"]}
        if relation == "offered":
            return {"ok": False, "why": "waiting", "said": CONFESS_NO["waiting"]}
        if self.identities.asks_by_him(qq_id, character_id) >= romance.MAX_ASKS:
            return {"ok": False, "why": "maxed", "said": CONFESS_NO["maxed"]}
        if self._refused_recently(qq_id, character_id, now):
            return {"ok": False, "why": "cooldown", "said": CONFESS_NO["cooldown"]}

        user_id = self.identities.resolve(qq_id, card=card, role="owner")
        affinity = self._affinity(user_id)
        turns = self.identities.turns(qq_id)
        standing = self.app.affinity.read(user_id, character_id, surface=DM_SURFACE)
        sulking = bool(getattr(standing, "sulk", False))
        hurt = self._hurt_today(qq_id, now)

        outcome, value, reason = romance.verdict(
            character,
            affinity,
            turns,
            sulking=sulking,
            recently_hurt=hurt,
            unknown_is_adult=self.config.unknown_age_adult,
        )
        if outcome == "maybe":
            # 灰区：分数越高越可能答应。这里**不叫模型**——「/告白」是一个动作，不是一句话，
            # 没有「他这次说得怎么样」可以判断；拿分数掷骰比让模型凭空猜更诚实，也能测。
            bars = romance.thresholds(character)
            low, high = float(bars["maybe"]), float(bars["accept"])
            chance = 0.5 if high <= low else (value - low) / (high - low)
            outcome = "accept" if self._rng.random() < max(0.0, min(chance, 1.0)) else "refuse"
            reason = reason if outcome == "refuse" else ""

        if outcome == "accept":
            self.identities.set_relation(qq_id, "loving")
            self.app.affinity.change(
                user_id, character_id, romance.GAIN_ACCEPTED, surface=DM_SURFACE
            )
            said = CONFESS_OK
        elif outcome == "minor":
            # 初中及以下：天花板是暧昧。她听到了，答案一辈子不会变。
            self.identities.set_relation(qq_id, "crush")
            said = CONFESS_NO["minor"]
        elif outcome == "no_way":
            # PLove=0：连暧昧都没有，所以状态不动（本来就是 none）。
            said = CONFESS_NO["no_way"]
        else:
            self.identities.set_relation(qq_id, "crush")
            self.app.affinity.change(
                user_id, character_id, romance.LOSS_REFUSED, surface=DM_SURFACE
            )
            said = CONFESS_NO.get(reason) or CONFESS_NO["not_close"]

        self.identities.record_confession(
            qq_id,
            character_id,
            by="him",
            at=now.isoformat(timespec="seconds"),
            outcome=outcome,
            reason=reason,
            affinity=affinity,
            turns=turns,
            plove=romance.level(character),
            heavy=romance.heaviness(character),
            score=value,
            adult=romance.grown(character, unknown_is_adult=self.config.unknown_age_adult),
        )
        self.log(
            f"qq: {self.owner_name}告白了（{outcome}"
            + (f" / {reason}" if reason else "")
            + f"，好感 {affinity:.0f}，说过 {turns} 次，score {value:.2f}）"
        )
        return {
            "ok": outcome == "accept",
            "why": outcome,
            "reason": reason,
            "relation": self.identities.relation(qq_id),
            "score": round(value, 3),
            "said": said,
        }

    async def she_speaks(self, qq_id: str) -> bool:
        """她自己开口。**一生一次**，说完就进 `offered`。

        走 `_turn` 而不是发一句固定文本：这是她最重要的一句话，得是她说出来的，不是模板。
        状态在 turn **之后**才写 —— turn 里读到 `none`，提示词才不会提前进入「说过了在等」。
        """
        if qq_id not in self.config.owners:
            return False
        if self._relation(qq_id) != "none":
            return False
        if not self.config.may_direct(qq_id):
            return False
        now = self._clock()
        await self._turn(qq_id, qq_id, "", SHE_OFFER_NUDGE, "", [], private=True)
        self.identities.set_relation(qq_id, "offered")
        self.identities.set_offered(qq_id, now.isoformat(timespec="seconds"))
        character = self._character()
        self.identities.record_confession(
            qq_id,
            character.id if character is not None else "",
            by="her",
            at=now.isoformat(timespec="seconds"),
            outcome="offered",
            affinity=self._affinity(self.identities.resolve(qq_id, card="", role="owner")),
            turns=self.identities.turns(qq_id),
            plove=romance.level(character) if character is not None else 0,
            heavy=romance.heaviness(character) if character is not None else 0,
        )
        self.log(f"qq: {self.owner_name}自己开口了——她说了，现在等他的答复")
        return True

    def _answer_offer(self, qq_id: str, text: str) -> str:
        """他答了。`accept` / `refuse` / `dodge`（继续等）。

        岔开单独算一档：她不催、不追问，但会数——三次之后按拒绝处理。计数放内存里，
        重启就清零：这条只影响「一口气岔开三次」这种当场行为，不是跨天的事实。
        """
        choice = romance.answer(text)
        if choice == "dodge":
            count = self._dodges.get(qq_id, 0) + 1
            self._dodges[qq_id] = count
            if count < romance.MAX_DODGES:
                self.log(f"qq: 她在等答复，他岔开了（第 {count} 次，不计）")
                return "dodge"
        self._dodges.pop(qq_id, None)

        character = self._character()
        character_id = character.id if character is not None else ""
        now = self._clock()
        user_id = self.identities.resolve(qq_id, card="", role="owner")
        if choice == "yes":
            outcome, said = "accept", "她答应的那句话，他给了答复"
            self.identities.set_relation(qq_id, "loving")
            self.app.affinity.change(
                user_id, character_id, romance.GAIN_ACCEPTED, surface=DM_SURFACE
            )
        else:
            outcome, said = "refuse", "她说出口了，他没答应"
            self.identities.set_relation(qq_id, "crush")
            self.app.affinity.change(
                user_id, character_id, romance.LOSS_REFUSED, surface=DM_SURFACE
            )
        self.identities.set_offered(qq_id, None)
        self.identities.record_confession(
            qq_id,
            character_id,
            by="him",
            at=now.isoformat(timespec="seconds"),
            outcome=outcome,
            reason="",
            affinity=self._affinity(user_id),
            turns=self.identities.turns(qq_id),
            plove=romance.level(character) if character is not None else 0,
            heavy=romance.heaviness(character) if character is not None else 0,
        )
        self.log(f"qq: {said}（{outcome}）")
        return outcome

    def _refused_recently(self, qq_id: str, character_id: str, now: datetime) -> bool:
        """冷却期内不再听第二遍。被拒、被判未成年、超时都算一次「说过了」。"""
        when = self.identities.last_refusal_at(qq_id, character_id)
        if not when:
            return False
        try:
            then = datetime.fromisoformat(when)
        except ValueError:
            return False
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return now - then < timedelta(days=romance.COOLDOWN_DAYS)

    def _hurt_today(self, qq_id: str, now: datetime) -> bool:
        """今天被扣过分：一句冷淡的话会让「答应」变得不太可能，但不至于不可能。"""
        try:
            changed = self.app.affinity.changed_at(qq_id)
        except Exception:
            return False
        if not changed:
            return False
        try:
            when = datetime.fromisoformat(str(changed))
        except ValueError:
            return False
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return now - when < timedelta(days=1)

    @staticmethod
    def _still_hurting(at: Any, *, hours: float = HURT_HOLD_HOURS) -> bool:
        """True when the number fell below the floor recently enough to leave it there."""
        try:
            then = datetime.fromisoformat(str(at))
        except (TypeError, ValueError):
            return False
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - then < timedelta(hours=hours)

    def hold_affinity(self) -> int:
        """Put the producer's affinity back up to the floor the deployment asked for.

        The judge moves a number; it does not know that this one is meant to stay at the
        top. Six turns of the same request — 「搬过来一起住」, asked again and again — read
        to it as pushing her too hard, took him from 100 to 29, and from then on she
        answered him as someone she was disappointed in. The floor is a product decision
        applied *after* the judge, and it only ever raises the producers.

        Once they are together the floor is a separate one, `QQ_OWNER_LOVE_FLOOR`, and it is
        **off by default** (0). A relationship is not a number that stays at the top: the
        producer should be able to make her genuinely unhappy, and `Affinity` turns a low
        number plus one bad sentence into a sulk — which is only possible if the number is
        allowed down there in the first place. Setting it back to 90 restores the old
        behaviour for a deployment that wants a relationship that cannot be dented.

        Before that, `QQ_OWNER_AFFINITY` still holds: he is not a stranger who has to earn it,
        and the drops that hurt most (the repeated 「搬过来一起住」 that took him from 100 to 29)
        were what the floor was written for. A fall below the line while they are together is
        left alone anyway: the sulk is where 「她今晚不高兴」 lives now.
        """
        plain = float(self.config.owner_affinity or 0.0)
        warm = float(self.config.owner_love_floor or 0.0)
        if (plain <= 0 and warm <= 0) or not self.config.owners:
            return 0
        fixed = 0
        for qq_id in self.config.owners:
            user_id = self.identities.resolve(qq_id, card="", role="owner")
            if not user_id:
                continue
            try:
                # The floor is the private chat's: the group's number is hers to move.
                value = self._affinity(user_id, DM_SURFACE)
                changed = self.app.affinity.changed_at(
                    user_id, self.config.character, surface=DM_SURFACE
                )
            except Exception:  # no affinity table: the plugin is off, nothing to hold
                return fixed
            loving = self._relation(qq_id) == "loving"
            # A relationship has its own floor, and 0 means 「没有地板」 — it must not fall back
            # to the pre-relationship one, or the number could never get low enough for the
            # sulk rule to fire.
            floor = warm if loving else plain
            if floor <= 0 or value >= floor:
                continue
            if loving and changed is not None and self._still_hurting(changed):
                continue
            self.app.affinity.set_value(
                user_id, self.config.character, floor, surface=DM_SURFACE
            )
            fixed += 1
        if fixed:
            self.log(f"qq: {self.owner_name}的好感度抬回下限（{fixed} 位）")
        return fixed

    async def love_ping(self, qq_id: str, *, force: bool = False) -> bool:
        """One message he did not ask for, in the private chat only.

        Not on a schedule: the caller sleeps a random gap, so it is neither regular nor
        countable.「不定时不定次」 is the whole difference between a girlfriend and a cron
        job — and it only ever happens once they are together.

        `force` skips the schedule switch only. Being together is not a switch: a ping from
        someone she is not going out with would be a different voice in a different room.
        """
        if not force and not self.config.love_ping:
            return False
        if qq_id not in self.config.owners:
            return False
        # 只有「已经在一起」才会主动开口。暧昧和等答复都不算：那还轮不到她先说。
        if self._relation(qq_id) != "loving" or self.identities.muted(qq_id):
            return False
        if not self.config.may_direct(qq_id):
            return False
        await self._turn(qq_id, qq_id, "", LOVE_NUDGE, "", [], private=True)
        self.log("qq: （手动测试）先发了一条" if force else "qq: 想他了，先发了一条")
        return True

    def reload(self) -> dict[str, Any]:
        """The explicit version: re-read the rule files, the roster, the sheet — now."""
        dropped = self.forget()
        rules = self.presence.refresh()
        self.world.refresh()
        changed = self.app.refresh(force=True)
        self.log(
            f"qq: 重载完成（提示词 {dropped} 份丢弃 / 规则改动 {sum(rules)} 处 / "
            f"人设{'已重读' if changed else '未变'}）"
        )
        return {
            "dropped": dropped,
            "rules": rules,
            "sheet": bool(changed),
            "generation": self.app.generation,
        }

    def _scene(self, room: str, private: bool) -> str:
        """Which conversation this is. The register lives in the prompt, but the *history*
        lives in the thread — and a group thread is full of 「です・ます」, so sharing one
        with a private chat quietly drags her back into the idol voice there. Two rooms,
        two threads, one person: memory and affinity are still keyed by the person."""
        return THREAD_DM if private else f"qq:group:{room}"

    def _thread(self, character_id: str, user_id: str, scene: str) -> Any:
        found = self.app.store.query(
            "SELECT id FROM conversations WHERE user_id = ? AND character_id = ? AND title = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (user_id, character_id, scene),
        )
        if found:
            conversation = self.app.store.conversation(str(found[0]["id"]))
            if conversation is not None:
                return conversation
        return self.app.store.create_conversation(user_id, character_id, scene)

    def _lock(self, conversation_id: str) -> asyncio.Lock:
        if conversation_id not in self._locks:
            self._locks[conversation_id] = asyncio.Lock()
        return self._locks[conversation_id]

    # ---- commands

    async def _command(
        self, room: str, qq_id: str, card: str, text: str, private: bool = False
    ) -> None:
        """Slash commands — the producer's control panel, not a public menu.

        Anyone else typing one gets nothing at all: no help text, no 「你没有权限」, no
        explanation of what the words mean. A group where @-ing her with a slash produces a
        command list is a group where the first curious person turns her into a help screen.
        Silence is the only answer that leaves the illusion intact.
        """
        name = text.split()[0].lstrip("/／")
        # One or two commands are toys rather than controls. Asking her for a sticker or for
        # the number she keeps about *you* cannot break anything, and if only the producer can
        # do it the group asks him to do it for them — the same thing with an extra step.
        # Everything else stays his (see `SHARED` for the only names allowed to be shared).
        shared = self._shared_command(name)
        if self.config.commands == "owner" and qq_id not in self.config.owners and not shared:
            self.log(f"qq: ignored a command from {qq_id} ({name[:12]})")
            return
        if name in ("帮助", "help", "?"):
            await self.outbox.send(
                room, help_text(private, () if private else self.config.shared_commands),
                private=private,
            )
            return
        user_id = self.identities.resolve(qq_id, card=card)
        if name in ("重开", "reset"):
            self.app.store.create_conversation(
                user_id, self.config.character, self._scene(room, private)
            )
            said = "（……好，重新来过。什么事？）" if not private else "……行行，重新来过。什么事？"
            await self._say(room, qq_id, said, private)
            return
        if name in ("告白", "confess"):
            # 只有他能说，只在私聊里说。别的地方一声不响——和别的控制指令一样，
            # 群里有人试一次就变成教学现场。
            if not private or qq_id not in self.config.owners:
                return
            answer = await self.confess(qq_id, card=card)
            if answer.get("said"):
                await self._say(room, qq_id, str(answer["said"]), private)
            return
        if name in ("好感", "affinity"):
            # A shared command now (QQ_SHARED_COMMANDS), so it carries the shared throttle:
            # it reads *your* number, not hers — nobody learns anything about anyone else.
            if not self._quiet_repeat(qq_id, "好感"):
                return
            state = self.app.affinity.read(
                user_id, self.config.character, surface=DM_SURFACE if private else GROUP_SURFACE
            )
            relation = self._relation(qq_id) if private else ""
            if relation == "loving":
                said = "……谈恋爱。干嘛，问这个做什么"
            elif relation == "offered":
                said = "……ふゆ的答复，还没给我呢"
            elif private:
                said = f"……{tier_label(state.tier)}。干嘛，在意这个？"
            else:
                label = tier_label(state.tier)
                own_name = self.stage.get("names.self", "")
                said = f"（{own_name}的心情：{label}）" if own_name else f"（心情：{label}）"
            await self._say(room, qq_id, said, private)
            return
        if name in ("阶段", "stage"):
            turns = self.identities.turns(qq_id)
            ladder = self.presence.stage_for(turns, owner=qq_id in self.config.owners)
            parts = text.split(maxsplit=1)
            action = parts[1].strip() if len(parts) > 1 else ""
            if action in ("重置", "reset", "全员重置", "reset-all"):
                if qq_id not in self.config.owners:
                    await self._say(room, qq_id, "（……这个权限，我想你没有）", private)
                    return
                if action in ("全员重置", "reset-all"):
                    # Everyone back to 「はじめまして」, except the producers — their floor is
                    # a setting (`QQ_OWNER_STAGE`), not a count, so resetting them is a no-op
                    # they would only notice as their number going down.
                    done = self.identities.set_all_turns(0, keep=self.config.owners)
                    self.log(f"qq: 全员档位重置（turns={done}，跳过 owner {len(self.config.owners)} 人）")
                    await self._say(
                        room, qq_id, "（……好，除了你，所有人都回到初见。）", private
                    )
                    return
                self.identities.set_turns(qq_id, 0)
                await self._say(room, qq_id, "（……好，我忘了。初次见面，是吧）", private)
                return
            said = (
                f"（……说过 {turns} 次了。现在是「{ladder or '—'}」的距离）"
                if not private
                else f"……{turns} 次。现在是「{ladder or '—'}」的距离。干嘛，在意这个？"
            )
            await self._say(room, qq_id, said, private)
            return
        if name in ("重载", "reload"):
            if qq_id not in self.config.owners:
                await self._say(room, qq_id, "（……这个权限，我想你没有）", private)
                return
            report = self.reload()
            said = f"（……好，重新读过了。丢掉旧提示词 {report['dropped']} 份）"
            await self._say(room, qq_id, said, private)
            return
        if name in ("表情", "sticker"):
            await self._send_sticker(room, qq_id, text, private)
            return
        if name in ("表情标", "tag"):
            await self._tag_sticker(room, qq_id, text, private)
            return
        if name in ("静音", "mute"):
            if qq_id not in self.config.owners:
                await self._say(room, qq_id, "（……这个权限，我想你没有）", private)
                return
            self.identities.set_muted(qq_id, True)
            said = "（……好。我安静一会儿）" if not private else "……行吧。我安静一会儿。"
            await self._say(room, qq_id, said, private)
            return
        if name in ("解除", "解除静音", "unmute"):
            if qq_id not in self.config.owners:
                await self._say(room, qq_id, "（……这个权限，我想你没有）", private)
                return
            parts = text.split(maxsplit=1)
            target = parts[1].strip() if len(parts) > 1 else ""
            target = target if target.isdigit() else qq_id
            self.identities.set_muted(target, False)
            said = (
                "（……好，回来了。什么事？）"
                if target == qq_id
                else f"（……好，{target} 可以说话了）"
            )
            await self._say(room, qq_id, said, private)
            return
        await self._say(room, qq_id, self._reply("unknown", "（……那个词，我不明白）"), private)


    def _shared_command(self, name: str) -> bool:
        """May someone who is not the producer use this one?

        Two gates, and both must be open: the name has to be one that is *allowed* to be
        shared (`SHARED`), and the deployment has to have opened it (`QQ_SHARED_COMMANDS`).
        `/表情` keeps its own switch besides — `QQ_STICKERS_PUBLIC` is the one a group that
        got tired of stickers turns off, and it must still work after the list grew.
        """
        canon = _CANON.get(name, name)
        if canon not in SHARED:
            return False
        if canon == "表情":
            return bool(self.config.stickers_public)
        return canon in self.config.shared_commands or name in self.config.shared_commands

    def _quiet_repeat(self, qq_id: str, name: str) -> bool:
        """True when this person may use this shared command *now*; records it when they may.

        A member pressing the same button twice in ten seconds gets nothing at all: the
        「まだだよ」 would itself be the reply the gap exists to prevent. The producer is never
        held back — he is not the flood this is for.
        """
        if qq_id in self.config.owners:
            return True
        key = f"{qq_id}:{name}"
        now = asyncio.get_running_loop().time()
        if now - self._shared.get(key, -1e9) < SHARED_GAP:
            return False
        self._shared[key] = now
        return True

    async def _send_sticker(self, room: str, qq_id: str, text: str, private: bool) -> None:
        """`/表情 上班` — send one from the pack. No tag means any of them.

        This is the one command anyone in the group may use, so it carries its own throttle:
        the pack is shared, and one member tapping the button forty times costs the room more
        than it costs her. A repeat inside `SHARED_GAP` gets nothing at all — answering 「まだ
        だよ」 would itself be a reply, which is what the gap exists to prevent.
        """
        if qq_id not in self.config.owners:
            if not self._quiet_repeat(qq_id, "表情"):
                return
        parts = text.split(maxsplit=1)
        tag = parts[1].strip() if len(parts) > 1 else ""
        # `/表情 同步` — pull the account's own favourites in. Owner only, and silently absent
        # for everyone else: a member typing it just asks for a tag called 「同步」.
        if tag in ("同步", "sync") and qq_id in self.config.owners:
            kept = await self.import_faces()
            total = self.pack.count() if self.pack is not None else 0
            said = (
                f"（……收藏看过了。新的 {kept} 张，现在一共 {total} 张）"
                if not private
                else f"……收藏看过了。新的 {kept} 张，现在一共 {total} 张。"
            )
            await self._say(room, qq_id, said, private)
            return
        if self.pack is None:
            await self._say(room, qq_id, "（……抱歉，我一张都还没有）", private)
            return
        parts = text.split(maxsplit=1)
        tag = parts[1].strip() if len(parts) > 1 else ""
        # `/表情 收藏` — the account's own pile, spelled out. `/表情` with no tag means the
        # same thing now; this spelling is for when somebody wants to be sure.
        if tag in ("收藏", "我的", "我的收藏", "fav"):
            row = self.pack.pick(source="fav")
            if row is None:
                await self._say(
                    room, qq_id, "（……收藏还是空的。用「/表情 同步」去看一眼）", private
                )
                return
        elif tag:
            row = self.pack.pick(tag)
        else:
            row = self._sticker_row()
        if row is None:
            said = (
                f"（……「{tag}」的我没有。你先发一张过来，我就记下了）"
                if tag
                else "（……我还没捡到过。谁发一张，我就记住了）"
            )
            await self._say(room, qq_id, said, private)
            return
        await self.outbox.send_image(room, self.pack.data_url(row), private=private)

    async def _tag_sticker(self, room: str, qq_id: str, text: str, private: bool) -> None:
        """`/表情标 上班` — name the sticker that was just sent. Only the producer: the pack
        is shared, and one person's joke should not relabel everyone's pictures."""
        if qq_id not in self.config.owners:
            await self._say(room, qq_id, "（……这个权限，我想你没有）", private)
            return
        if self.pack is None:
            await self._say(room, qq_id, "（……我一张都还没有）", private)
            return
        parts = text.split(maxsplit=1)
        tag = parts[1].strip() if len(parts) > 1 else ""
        if not tag:
            await self._say(room, qq_id, "（……要叫什么名字？像「/表情标 上班」那样）", private)
            return
        row = self.pack.tag(tag, group="" if private else room)
        if row is None:
            await self._say(room, qq_id, "（……我一张都还没有）", private)
            return
        await self._say(
            room, qq_id, f"（……「{tag}」是吧。记住了。{self.pack.count(tag)} 张）", private
        )


def help_text(private: bool = False, shared: Sequence[str] = ("表情",)) -> str:
    """The menu, with the last line telling the truth about who may use what."""
    if private:
        return (
            "（用法：直接跟我说话就行）\n"
            "/重开  换一个新的话题\n"
            "/好感  她现在的心情\n"
            "/表情 [标签]  发一张表情（例：/表情 上班；不带标签就是你自己收藏的）\n"
            "/表情 同步  拉一次这个号里收藏的表情\n"
            "/表情标 标签  把最新收进来的那张命名\n"
            "/阶段  说过多少次、现在是什么距离（/阶段 重置 归零；/阶段 全员重置 除你以外全部归零）\n"
            "/重载  改完人设或规则文件后立刻生效\n"
            "/静音  让她先别说话（/解除 恢复说话）"
        )
    return (
        "（用法：@ 我说话就行）\n"
        "/重开  换一个新的话题\n"
        "/好感  她现在的心情\n"
        "/表情 [标签]  发一张表情（例：/表情 上班；不带标签是她自己收藏的）——这条群里谁都能用\n"
        "/表情 同步  拉一次这个号里收藏的表情\n"
        "/表情标 标签  把最新收进来的那张命名\n"
        "/阶段  你和这个人说过多少次了、现在是什么距离"
        "（/阶段 重置 归零；/阶段 全员重置 除你以外全部归零）\n"
        "/重载  改完人设或规则文件后立刻生效\n"
        "/静音  让她先别说话（/解除 恢复说话）\n"
        + (
            f"（{'、'.join('/' + name for name in shared)} 群里谁都能用；"
            "其余只有你自己能用，别人发什么都不会有反应）"
            if shared
            else "（这些指令只有你自己能用，群里别人发什么都不会有反应）"
        )
    )


HELP = help_text()


def _face_urls(data: Any) -> list[str]:
    """The URLs out of `fetch_custom_face`, whichever shape the build answers with.

    NapCat sends a bare array of strings. Some builds wrap it in `{urls: [...]}` or send
    objects with a `url` field, and a relay that renames one key should not empty the pack.
    """
    if isinstance(data, list):
        items: list[Any] = data
    elif isinstance(data, dict):
        items = data.get("urls") or data.get("list") or data.get("data") or []
    else:
        items = []
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("file") or item.get("path") or "")
        else:
            url = ""
        if url.startswith(("http://", "https://")):
            out.append(url)
    return out


def _transcript(heard: list[dict[str, str]], limit: int = 600) -> str:
    """The few lines she happened to read, as one turn section. Never stored: this is the
    one thing a group gives her that a private chat does not, and it is deliberately the
    only thing — the rest of the conversation went by without her."""
    body = _lines(heard, limit)
    return (
        "【刚才群里在说这些】下面几行是你自己看见的，之后不会记得。接一句就好："
        "不要复述别人的话、不要逐条回应、不要总结。\n" + body
    )


def _overheard(heard: list[dict[str, str]], limit: int = 600) -> str:
    """The same lines, when she is *not* answering them.

    The difference matters more than it looks: with the reply wording, 「接一句就好」 made her
    continue the thread every single time, so a 「自己开口」 turn came out as a reply anyway.
    Here the header says out loud that nobody is talking to her, and the only job is to bring
    something of her own.
    """
    body = _lines(heard, limit)
    return (
        "【刚才群里在说这些】下面几行是你自己看见的，之后不会记得。"
        "但没有人是在跟你说话——不用回答、不要复述、不要总结他们在聊什么。\n"
        "你只是碰巧在场，然后想到一件自己的事。想说就说，不想说也可以很短。\n" + body
    )


def _lines(heard: list[dict[str, str]], limit: int) -> str:
    """Each line as 「name：what they said」, with a person's note right after their name.

    Next to the name rather than in a block of its own: the fact has to arrive attached to a
    sentence, or 「她是女性」 becomes a label she has to remember to apply. This is also the
    only way a fact about *somebody else in the room* can reach her — the turn's 「对面是谁」
    only ever describes the person being answered.
    """
    lines = []
    for row in heard:
        if not row.get("text"):
            continue
        name = row.get("card") or "谁"
        note = (row.get("note") or "").strip()
        lines.append(f"{name}（{note}）：{row['text']}" if note else f"{name}：{row['text']}")
    body = "\n".join(lines)
    return body[-limit:] if len(body) > limit else body


def _character_name(app: Any, character_id: str) -> str:
    """Her name as the roster has it — for the prompt that asks about her, not for her to say.

    Empty when there is no roster: `channels/` can be dropped onto a kernel without the
    characters plugin, and a missing name must cost a word, not a crash.
    """
    try:
        return str(app.characters.character(character_id).name)
    except Exception:
        return ""


_FIXED_HOURS = {"Asia/Tokyo": 9, "Asia/Shanghai": 8, "UTC": 0}
"""两个不带夏令时的时区，写死偏移。

`ZoneInfo` 要有系统时区库（Linux 有，某些机器上要装 `tzdata`）。装不上时退回这里，
而不是退回「跑代码的这台机器的时区」—— 后者会让她在开发机上 23:30 醒着、在服务器上
23:30 睡着，同一个时间两种行为。日本和中国都没有夏令时，写死是准确的。"""


def _local_time(now: datetime, tz: str | None) -> datetime:
    """The same instant, on the clock where she lives."""
    name = tz or "UTC"
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo(name))
    except Exception:
        hours = _FIXED_HOURS.get(name)
        if hours is not None:
            return now.astimezone(timezone(timedelta(hours=hours)))
        return now.astimezone()


def _local_day(now: datetime, tz: str | None) -> str:
    """The date where the people are, not where the server is. A quota that resets at UTC
    midnight resets at eight in the morning in Shanghai — the middle of the day."""
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo(tz or "UTC")).strftime("%Y-%m-%d")
    except Exception:
        return now.astimezone().strftime("%Y-%m-%d")


def _clip(text: str, limit: int = 24) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


_ASIDE = re.compile(r"\[[a-zA-Z][a-zA-Z ,'\-]*\]")
"""A voice direction like `[sulking]` or `[softly]`.

It belongs in the Japanese that gets read aloud, not in a chat bubble. ASCII only, so a
Chinese marker such as `[贴纸]` is left alone for `split_mark` to deal with."""


def _plain(text: str) -> str:
    """The fallback text, with the acting notes taken out, on one line.

    Used only when a reply came back with no Chinese subtitle at all: sending the line she
    actually wrote beats sending nothing, but it must not arrive as 「[sulking] 哈？」.
    """
    return " ".join(_ASIDE.sub("", text or "").split())


ASKED = re.compile(
    r"(来|发|给|整|弄|丢|甩|看看|看下)[^，。！？,.!?]{0,8}(表情|贴纸|表情包)"
    r"|(表情|贴纸|表情包)[^，。！？,.!?]{0,6}(呢|吗|看看|来一张|来一个|发一个|发一张)"
)
"""A request for a picture, near enough. Deliberately narrow: 「你这个表情好可爱」 is talk about
a sticker, not a request for one, and answering every mention with a picture is how a bot gets
muted. Missing one costs 「她没理我」; over-answering costs the room."""

NO = ("别发", "不要发", "不用发", "别给", "不要给", "不用了", "算了")


def wants_sticker(text: str) -> bool:
    """Did they ask her for one? The channel decides this, not the model.

    It used to be a marker the model wrote. It never did: the reply format says 「除了这些标签
    …不要输出任何其他文字」, and that instruction is earlier and blunter than any note added
    later, so `[贴纸]` simply never appeared. A capability that depends on the model breaking
    its own format rules is not a capability — so the trigger moved here, where it is a regex
    and can be tested.
    """
    said = (text or "").strip()
    if not said or any(word in said for word in NO):
        return False
    return bool(ASKED.search(said))


def split_mark(piece: str) -> tuple[str, bool]:
    """`(the words, whether she asked for a sticker)`.

    A marker left in the text would be sent literally — `[贴纸]` in a chat bubble is worse than
    no sticker at all — so it is removed wherever it appears, and the picture goes out after
    the sentence it was attached to.
    """
    if STICKER_MARK not in piece:
        return piece, False
    return piece.replace(STICKER_MARK, "").strip(), True


def world_path(characters: str, name: str) -> Path:
    return Path(characters) / "world" / name


def _fetch(url: str) -> bytes:
    """One picture, from the URL the event carries. `file` is a path inside NapCat's
    container and would not exist here, which is why only `url` is used."""
    request = urllib.request.Request(url, headers={"User-Agent": "scbot-new/qq"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(MAX_PHOTO_BYTES + 1)
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError("photo too large")
    return raw


def _data_url(raw: bytes) -> str:
    if raw[:3] == b"\xff\xd8\xff":
        kind = "jpeg"
    elif raw[:8] == b"\x89PNG\r\n\x1a\n":
        kind = "png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        kind = "webp"
    else:
        raise ValueError("not an image")
    return f"data:image/{kind};base64," + base64.b64encode(raw).decode("ascii")


def status(channel: QQChannel) -> dict[str, Any]:
    """What the settings page shows. No message content, no names — only counts."""
    return {
        "enabled": True,
        "connected": bool(getattr(channel.client, "connected", False)),
        "groups": list(channel.config.groups),
        "private": channel.config.private,
        "ambient": channel.config.ambient,
        "ambient_today": sum(channel._ambient_used_all().values()),
        "ambient_by_group": channel._ambient_used_all(),
        "ambient_daily": int(channel.config.ambient_daily),
        "stickers": channel.pack.count() if channel.pack is not None else 0,
        "generation": getattr(channel.app, "generation", 0),
        "ladder": channel.presence.ladder(),
        "loving": any(channel.identities.relation(one) == "loving" for one in channel.config.owners),
        "relation": next(
            (channel.identities.relation(one) for one in channel.config.owners), "none"
        ),
        "love_ping": channel.config.love_ping,
        "people": channel.identities.count(),
        "turns": channel.turns,
        "sent": channel.outbox.sent,
        "failed": channel.outbox.failed,
        "last_error": getattr(channel.client, "last_error", ""),
        "events": getattr(channel.client, "events", 0),
    }


def dump_event(event: dict[str, Any]) -> str:
    return json.dumps(
        {key: event.get(key) for key in ("post_type", "message_type", "group_id", "user_id")},
        ensure_ascii=False,
    )
