"""夹子：额度归通道数，许可归判官逐句判。

两件事分开，因为它们本来就是两件事：

- **额度**——她最近十条消息里夹过没夹过。这是事实：通道在她发出每一条时记一格
  （`channels/qq.py::_note_tease`），谁都能读。
- **许可**——*这一句*值不值得夹。这是判断：只有读完对方这句话才知道，所以交给判官，
  每轮问一个问题（`tease.want`），判中了才在提示词里给一句「这一句可以夹」。

为什么要有第二件事：只写规则（人设里「十条最多一次」→「逗他的时候可以披一句」→加例句），
实测四批共二十句、♡ 一条也没出现。规则层的「可以」是弱许可，挤不过她人设主体那句
「嘴上不饶人」。改成逐句许可之后，她才知道是**这一句**可以，而不是「原则上允许」。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.plugin import Plugin, Question, TurnContext
from core.prompt import Section, Slot
from plugins.packs import tease_rule

DEFAULT_WINDOW = 10
"""只有一份规则里没写 `window` 时才用它。真正的数字、记号、话术都在角色包里。"""


TIERS = ("trust", "close", "intimate")
"""只有信任以上（40+）谈得上「夹」。更生疏的两档甜不甜由档位自己管
（`channels/presence.py::_voice`），再给一条规则就是两套规则让她挑。"""

ASK = (
    "这句话是不是在逗她、让她想回嘴调侃：对方在自夸、得意、犯傻、故意欠揍地招她、"
    "起哄她、拿她打趣、求着要她夸，或者刚被她抓到在逞强？"
    "骂她、认真的正事、陌生人的客套、单纯提问都不算。"
)

"""逐句的许可：判官判「这一句是不是在逗她」，通道说「还有没有额度」，两条都对上才给。

给的那句话本身（怎么夹、夹完怎么收）**不在代码里**——它写在 `packs/<id>/voice/tease.toml`
的 `hint` 里。这份文件只留机制和默认值，因为「夹」是某一种人设才有的东西。"""


def flag_key(room: str) -> str:
    """Where a room's window is kept. Two rooms, two windows: one room's ♡ must not
    spend the other room's allowance."""
    return f"tease:{room}"


def room_of(title: str) -> str:
    """`qq:group:123456789` → `123456789`. Any other conversation is not a room."""
    name = (title or "").strip()
    return name.rsplit(":", 1)[-1] if name.startswith("qq:group:") else ""


def window_of(value: str, window: int = DEFAULT_WINDOW) -> str:
    """The stored string, trimmed to the window. Oldest on the left."""
    return (value or "").strip()[-max(1, window):]


def marks_in(text: str, marks: tuple[str, ...] = ()) -> bool:
    """Did this line use the mask? Counting **her** marks, not judging a tone."""
    return any(mark in (text or "") for mark in marks)


class Tease(Plugin):
    """One question for the judge, one line for the prompt — and only when both agree."""

    name = "tease"
    requires = ("affinity",)

    def __init__(
        self,
        store: Any,
        *,
        tier: Callable[..., str] | None = None,
        character: Callable[[], str] | None = None,
        pack: Callable[[str], Any] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._character = character or (lambda: "")
        self._pack = pack
        """`pack(character_id) -> Pack`。「夹」是某一种人设才有的东西，
        规则写在她自己的包里（`packs/<id>/voice/tease.toml`）；没有就没有，
        绝不退回一套通用写法——那等于把她的招牌戴到别人脸上。"""
        self._tier = tier or (lambda *args, **kwargs: "")
        """这个人在这个房间的档位。由装配处注入（`app.py`），插件不自己去认 affinity。"""
        self._log = log or (lambda line: None)

    def rule(self) -> Any:
        return tease_rule(self._pack(self._character()) if self._pack else None)

    def capabilities(self) -> dict[str, Any]:
        rule = self.rule()
        return {"on": rule.on, "window": rule.window}

    def judge_questions(self) -> tuple[Question, ...]:
        # 判官的问句没有角色上下文（`Host.questions()` 每轮一次），所以总是问；
        # 真正的开关在下面——没有她那份规则的字符，许可永远不发。
        return (Question("tease.want", ASK, kind="bool"),)

    async def prompt_sections(self, turn: TurnContext) -> tuple[Section, ...]:
        # 缺答案 = 判官没说（或者挂了）。判官不说话的时候不给许可：许可是一次例外，
        # 不是默认状态。
        rule = self.rule()
        if not rule.on or turn.facts.get("tease.want") is not True:
            return ()
        if turn.surface != "group":
            # 私聊那套【夹子】是人设自己写的，跟这条计数规则不是一回事。
            return ()
        try:
            tier = self._tier(turn.conversation.user_id, turn.conversation.character_id)
        except Exception:  # 没有 affinity 的部署：规则失效，不是崩溃
            return ()
        if tier not in TIERS:
            return ()
        room = room_of(getattr(turn.conversation, "title", "") or "")
        if not room:
            return ()
        try:
            rows = self._store.query(
                "SELECT value FROM qq_flags WHERE key = ?", (flag_key(room),)
            )
        except Exception:
            return ()
        if window_of(rows[0]["value"] if rows else "", rule.window).count("1"):
            # 这十条已经夹过一次：额度用完，许可作废。计数压过判断——
            # 一句一句都夹的人，别人记不住她哪句是真的。
            return ()
        self._log(f"tease: 这一句放她夹（{str(turn.conversation.user_id)[:8]}，{room}）")
        return (Section(Slot.TURN, rule.hint, order=7, title="tease"),)
