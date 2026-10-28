"""Which parts of a room's chatter she would join, if nobody asked her.

Being in a group is not the same as answering every third line. A coin decided *whether* she
spoke at all; this decides *about what*, and that is the difference between a character who
is in the room and a bot that reacts to whatever it could parse. Before this, the input was
always the newest message — so an interjection was whatever happened to be last, which read
as «she answers anything», not as «she has things to say».

Two answers come out of one request: whether anything here is hers to say, and which line she
would answer — not necessarily the last one. Somebody asks a question, three people answer
it, and the line still worth replying to is the question.

The same file, asked a third way, answers the other half of being in a room: somebody said
her name. `pick` is fail-closed because nobody asked; `called` is fail-open because somebody
did — silence at a direct call reads as a broken bot, not as a character holding back.

The model is asked rather than the rules, because 「她感兴趣的话题」 is a fact about a
character, not about a schema. The list lives in `characters/world/<id>-interests.md`;
without one the default below keeps a bare installation working.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.llm import ChatMessage, LLM
from core.live import source

DEFAULT = """她接得上的话头：具体的事（在吃什么、今天的天气、刚才发生的事、手上在忙的东西）、
她懂的领域、有人在抱怨或开玩笑、有人问了个她答得上的问题。
她不接：看不懂的梗和缩写、别人的私事、吵起来的架、刷屏、只发了一个表情、点名要别人回答的问题。"""

"""What she is like when no `-interests.md` says. Deliberately short and unflattering to
nobody: a channel with no character data must not invent somebody's tastes."""

_JSON = re.compile(r"\{.*\}", re.S)


@dataclass(frozen=True)
class Mention:
    """Somebody in the room said a name she knows, and how close that name is to her."""

    name: str
    closeness: int
    unit: str = ""


DEFAULT_IDOLS = (
    (3, "自己人"),
    (2, "同事务所的同行"),
    (1, "认识的人"),
)
"""How the three closeness numbers read in a sentence, when the file only gives numbers."""


class Idols:
    """Who she knows, by name.

    A character in a group hears strangers talk about her colleagues all evening; without
    this, 「有人提到あさひ」 and 「有人提到一个不认识的词」 looked exactly alike to the
    interjection judge. The table turns a name into two facts: *somebody she knows* and *how
    close* — the second is what makes 「关系越密切越可能接话」 a rule instead of a mood.

    Longest match wins, so 「黛冬優子」 is not read as 「冬優子」, and single-character
    aliases are left out of the file on purpose: a false hit is worse than a miss.

    The file is `characters/world/<id>-idols.toml`; without one, nobody is recognised and
    the channel behaves exactly as it did before.
    """

    def __init__(self, text: str | Path | None) -> None:
        self._rows: list[tuple[str, int, str]] = []
        self._load(text)

    def _load(self, text: str | Path | None) -> None:
        if not text:
            return
        raw = ""
        try:
            if isinstance(text, Path) or (isinstance(text, str) and "\n" not in text):
                raw = Path(str(text)).read_text(encoding="utf-8")
            else:
                raw = str(text)
        except OSError:
            return
        try:
            import tomllib

            data = tomllib.loads(raw)
        except Exception:  # a broken table must not take the channel down
            return
        for group in data.get("group") or []:
            if not isinstance(group, dict):
                continue
            try:
                closeness = int(group.get("closeness", 1))
            except (TypeError, ValueError):
                closeness = 1
            unit = str(group.get("unit") or "")
            for name in group.get("names") or []:
                alias = str(name).strip()
                if len(alias) >= 2:
                    self._rows.append((alias, closeness, unit))
        # Longest first: 「黛冬優子」 before 「冬優子」.
        self._rows.sort(key=lambda row: -len(row[0]))

    def hit(self, text: str) -> Mention | None:
        """The best-known name in `text`, or None. Longest match wins."""
        said = text or ""
        if not said or not self._rows:
            return None
        for alias, closeness, unit in self._rows:
            if alias in said:
                return Mention(alias, closeness, unit)
        return None

    def words(self) -> tuple[int, str]:
        """For the log: how many names the table carries."""
        return len(self._rows), str(sorted({row[1] for row in self._rows}))


class Interest:
    """One question about a room, answered by the model that also plays her.

    Same model as the chat, deliberately: 「她对这个话题有没有话说」 is a question about the
    character, and a separate cheap model would answer it about *a* character. The cost is one
    extra request, which is why the caller screens with coins and a quota before asking.
    """

    def __init__(
        self,
        llm: LLM | None,
        text: str | Path | None = None,
        *,
        name: str = "",
        settle: float = 0.35,
        log: Any | None = None,
        idols: str | Path | None = None,
    ) -> None:
        """`text` is the interests file (a path, or the text itself). Empty falls back to
        `DEFAULT`, which is neutral — the file is where one character's tastes belong.

        `idols` is the 「她认识谁」 table. It only ever *adds* a reason to speak; without it
        nothing is recognised and the judgement is the one it always was.
        """
        self._llm = llm
        self._src = source(text, settle=settle, log=log) if text else None
        self._name = (name or "").strip()
        self._log = log
        self._idols = Idols(idols)

    def mention(self, lines: Sequence[dict[str, str]]) -> Mention | None:
        """Who she heard about in these lines, if anybody she knows. For the caller's own
        coin — the probability rises with how close the name is, the judgement is unchanged."""
        return self._idols.hit(" ".join(str(line.get("text") or "") for line in lines))

    async def pick(
        self, lines: Sequence[dict[str, str]], *, own: bool = False
    ) -> tuple[bool, int, str]:
        """`(speak, which line — 1-based, 0 when none, why)`.

        Fail-closed: a model that is down means she says nothing. An interjection nobody
        asked for is the one thing that must never happen by accident.
        """
        if self._llm is None or not lines:
            return False, 0, ""
        mention = self.mention(lines)
        try:
            answer = await _text(self._llm, self._messages(lines, own=own, mention=mention))
        except Exception as exc:
            if self._log is not None:
                self._log(f"qq: 判断要不要插话失败（{type(exc).__name__}: {exc}）")
            return False, 0, ""
        speak, which, why = _read(answer, len(lines))
        if speak and mention is not None:
            why = f"{why}（提到{mention.name}）" if why else f"提到{mention.name}"
        return speak, which, why

    async def called(
        self, lines: Sequence[dict[str, str]], *, direct: bool = False
    ) -> tuple[bool, str]:
        """`(speak, why)` for a message that mentioned her.

        Fail-open, and the one place in this module that is: somebody called her by name,
        and silence there reads as a broken bot rather than as a character holding back.
        A judge that cannot answer means she answers.
        """
        if self._llm is None or not lines:
            return True, ""
        try:
            answer = await _text(self._llm, self._called_messages(lines, direct=direct))
        except Exception as exc:
            if self._log is not None:
                self._log(f"qq: 判断要不要回点名失败（{type(exc).__name__}: {exc}）")
            return True, "（判断失败，照回）"
        return _read_called(answer)

    def _called_messages(
        self, lines: Sequence[dict[str, str]], *, direct: bool
    ) -> list[ChatMessage]:
        heard = "\n".join(
            f"{number}. {line.get('card') or '誰か'}：{line.get('text') or ''}"
            for number, line in enumerate(lines, 1)
        )
        return [
            ChatMessage("system", self._called_instructions(direct=direct)),
            ChatMessage("user", f"【群里刚才说的话】\n{heard}"),
        ]

    def _called_instructions(self, *, direct: bool) -> str:
        who = f"「{self._name}」" if self._name else "这个角色"
        how = "在消息里 @ 了她" if direct else "在消息里叫了她的名字（没有 @）"
        parts = [
            f"你在替{who}看一个 QQ 群。有人刚才{how}。结合下面的上下文，判断她想不想回这一句。",
            "",
            "【她会回的】认真叫她、问她问题、把话递给她、话题她接得上。",
            "【她会不回的】刷屏复读、故意起哄看她反应、点名要别人回答的、"
            "只发一个表情或一串符号、她完全不熟的梗、吵起来的架。",
        ]
        if direct:
            parts.append("她是被直接叫到的：除了上面那几种，一般都会回。")
        else:
            parts.append("只是在聊她，没有叫她：不接也很自然；接，就接一句短的。")
        parts += [
            "",
            '只输出一个 JSON 对象，不要解释：{"speak": true 或 false, "why": "一句中文"}',
        ]
        return "\n".join(parts)

    def _messages(
        self,
        lines: Sequence[dict[str, str]],
        *,
        own: bool,
        mention: Mention | None = None,
    ) -> list[ChatMessage]:
        heard = "\n".join(
            f"{number}. {line.get('card') or '谁'}：{line.get('text') or ''}"
            for number, line in enumerate(lines, 1)
        )
        return [
            ChatMessage("system", self._instructions(own=own, mention=mention)),
            ChatMessage("user", f"【群里刚才说的话】\n{heard}"),
        ]

    def _instructions(self, *, own: bool, mention: Mention | None = None) -> str:
        who = f"「{self._name}」" if self._name else "这个角色"
        parts = [
            f"你在替{who}看一个 QQ 群。没有人叫到她，她只是看见大家在聊。"
            f"现在判断她想不想自己说一句，以及想接谁的话。",
            "",
            f"【{who}会自己接的话头】",
            self._text(),
        ]
        if mention is not None:
            # Only when the table recognised a name. This is a *lean*, not a switch: she is
            # supposed to be the person who happens to know them, not a keyword responder.
            close = {
                3: "是同一个组合的人，她熟得不能再熟",
                2: "是同事务所的同行，一起工作过",
                1: "是工作中会见到的人，认识但没私交",
            }.get(mention.closeness, "她认识")
            parts += [
                "",
                "【注意】刚才的话里提到了一个人：" + mention.name + "——" + close + "。",
                "她对这个名字有反应：可以接一句、可以吐槽、可以问他们在说什么。"
                "但这不等于每个提到熟人的时候都要接——不想说话就不接，"
                "别变成听见关键词就开口的人。",
            ]
        parts += [
            "",
            "【怎么判断】",
            "- 接得上一句就 speak=true：不必很感兴趣，只要说了不像硬凑。",
            "- 优先真正在意、也懂的东西；其次是看不懂、但可以问一句的。",
            "- 要接的那句不一定是最后一句。前面有人说了更值得回的，to 就选那一行。",
            "- 只有明确落在上面「不接」里的才 speak=false。剩下的都算接得上——"
            "她可以只回半句、只问一句「那是什么」，那也算加入聊天。",
            "- 但别把「想接」当成「有话说」：接一句短的，比接一整段强。",
        ]
        if own:
            parts.append(
                "- 这一次她也可以不接任何人的话，只是自己想到了一件相关的事；"
                "那种情况下 to 写 0 也可以，speak=true。"
            )
        parts += [
            "",
            '只输出一个 JSON 对象，不要解释：{"speak": true 或 false, "to": 行号, "why": "一句中文"}',
            'speak=false 时 to 写 0。',
        ]
        return "\n".join(parts)

    def _text(self) -> str:
        raw = self._src.text() if self._src is not None else ""
        return (raw or "").strip() or DEFAULT


async def _text(llm: LLM, messages: Sequence[ChatMessage]) -> str:
    return "".join([chunk async for chunk in llm.stream(messages)])


def _read(answer: str, count: int) -> tuple[bool, int, str]:
    """The three answers that can be trusted. An unreadable one is «no»: nothing that could
    put words in her mouth is worth guessing at."""
    found = _JSON.search(answer)
    if found is None:
        return False, 0, ""
    try:
        parsed = json.loads(found.group(0))
    except json.JSONDecodeError:
        return False, 0, ""
    if not isinstance(parsed, dict):
        return False, 0, ""
    speak = parsed.get("speak")
    if isinstance(speak, str):
        speak = speak.strip().lower() in {"true", "yes", "是", "1"}
    if not speak:
        return False, 0, str(parsed.get("why") or "")[:80]
    which = _line_number(parsed.get("to"))
    if not 1 <= which <= count:
        # A line number out of range means the answer is about a room that is not this one.
        # The newest line is what a plain 「想接一句」 usually means, so that is the repair.
        which = count
    return True, which, str(parsed.get("why") or "")[:80]


def _line_number(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _read_called(answer: str) -> tuple[bool, str]:
    """`(speak, why)` for a mention. An unreadable answer means *yes* — the caller fails
    open on purpose, so this is the opposite of `_read`."""
    found = _JSON.search(answer)
    if found is None:
        return True, ""
    try:
        parsed = json.loads(found.group(0))
    except json.JSONDecodeError:
        return True, ""
    if not isinstance(parsed, dict):
        return True, ""
    speak = parsed.get("speak")
    if isinstance(speak, str):
        speak = speak.strip().lower() in {"true", "yes", "是", "1"}
    if speak is False:
        return False, str(parsed.get("why") or "")[:80]
    return True, str(parsed.get("why") or "")[:80]
