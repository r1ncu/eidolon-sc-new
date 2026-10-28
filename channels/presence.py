"""The one thing a chat surface changes about her: who is she talking to, and who is watching.

The sheet says the rule plainly — the moment an outsider is present she switches to the
register she uses in public. A group
of fifty is never without one. A private chat with the producer is the opposite case: nobody
is watching, so she drops the act and talks the way she really talks. Those are two scenes,
and they are not a matter of degree — the sheet counts roughly 750 lines carrying the
business register against 1970 carrying her own, and only 12 lines mixing the two.

That is why this is a *turn* section and not a persona one. `PromptBuilder` lets a caller
pass `TURN` and `PROFILE` only; `PERSONA` is written once, when the engine is built. The
scene rule therefore cannot leak into the web page, and the page's own text cannot leak into
a group. The cost is a few dozen tokens per turn; the benefit is that the surfaces cannot
contaminate each other.

The outsiders in this group are all producers — people who play the game and follow the
project. That is a fact about the room, not about any one person in it: she still calls them
「プロデューサーさん」, and she still does not tell a stranger what she would tell him. 「外人在
场」 here means 「有别人在」, never 「有粉丝在」.

The prose lives in `characters/world/<id>-qq.md` (group) and `<id>-dm.md` (private) so it
can be edited without touching code. Tokens filled here: `{{addressee}}`, `{{distance}}`,
`{{time}}`.

**The register is not one thing, it is a ladder.** How close she is to the person talking is
counted, not guessed: `qq_identities.turns` goes up by one every time she answers someone. The
rule file defines the rungs — `【阶段 close｜10】` means 「from the tenth answer on, this is how
you talk to them」 — and `Presence` picks the highest rung the count has reached. A first
conversation therefore gets the sweet, formal voice of an idol meeting a fan; the same person
after thirty answers gets the voice she uses with someone she has stopped performing for.

That ladder only ever moves *how familiar* she is. It never moves *how public* the room is: the
scene line stays first in the persona, so even the innermost rung still knows there are fifty
other people reading.

**None of that prose is in this file.** The paragraphs that fill `{{addressee}}` and
`{{distance}}` came from one character's world — a relationship, a nickname, a project to
follow — and a
module that says those words is a module every other character has to be edited out of. They
now live in `characters/world/<id>-stage.toml` under `[addressee]` and `[distance]`, with
`[fallback]` for the two templates. What stays here is a neutral fallback: no name, no
catchphrase, no relationship that was not asked for. Write the toml to say what yours means.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.live import source
from core.prompt import Section, Slot
from plugins.affinity import BELOW_TRUST
from plugins.affinity import label as tier_label

from .stage import load as load_stage
from .stage import word as stage_word

OWNER = "owner"
STRANGER = "stranger"

GROUP = "group"
PRIVATE = "private"

OWNER_FLOOR = "close"
"""Which rung the producer starts on.

He is not a stranger she met in a group — the sheet has them working together — so counting
from 「はじめまして」 would have her politely introducing herself to someone she eats lunch
with. He still climbs to the last rung by talking, like everyone else.
"""

ADDRESSEE = {
    OWNER: (
        "叫你的是跟你最熟的那个人。现在群里有别人在，所以你还是用对外的样子："
        "礼貌、清楚、不交底。"
        "注意：礼貌不等于撒娇。不要每句都带甜腻的尾音、不要拖长音——"
        "你的客气是「清楚、周到」，不是「甜」。"
        "五次里最多两次，可以在句尾用括号补一句真心话——那是只有他能看懂的部分。"
    ),
    STRANGER: (
        "叫你的是群里的另一个人。你不熟的是他这个人，不是他的身份："
        "所以照样客气、亲切、有点分寸，但不交心。"
        "注意分寸：客气不是可爱，别把礼貌说成撒娇。"
        "问到私事、年纪、住在哪、有没有恋人，就笑着绕开。"
        "有人叫错你的名字，就纠正他。"
    ),
}

CROWD = "crowd"
"""Nobody called her: she is simply in the room and chose to speak up."""

ADDRESSEE[CROWD] = (
    "这一次没人叫你，是你自己接的话。"
    "所以不要对着某一个特定的人说，也别回答某个具体的问题——就当是把一句感想丢进群里。"
)

ALONE = "alone"
"""Nobody called her *and* she is not picking up the thread: she just had a thought."""

ADDRESSEE[ALONE] = (
    "这一次没人叫你，你也不打算接谁的话。你只是自己想说一句。"
    "所以这句话没有对象：不用称呼任何人、不用回答任何问题、也不用等谁回应。"
    "说你自己的事就好——手头在忙的、今天的天气、刚才想到的东西、突然冒出来的念头。"
    "一句话，像在自言自语。"
)

DISTANCE = {
    "called": (
        "你只听见叫你的这一句。前面大家在聊什么，你不知道；不要猜、不要编、不要说「刚才你说……」。"
        "不明白就带过，别硬解释。"
    ),
    "ambient": (
        "你一直在群里待着，上一条消息之前的那几行你看见了（只这一次，你之后不会记得）。"
        "所以你接得上话，但只接一句：不要复述别人的话、不要挨个点名回应、不要总结刚才聊了什么。"
        "看不明白他们在说什么的时候，就别硬接。"
    ),
    "own": (
        "他们刚才说的话不是问你的，你不用接，也不用点评——不要复述、不要总结、不要「刚才你们说」"
        "这种把话头拽回去的写法。你只是碰巧在场，然后想到一件自己的事。"
        "和他们在聊的没关系也可以；气氛是安静的，就说一句安静的话。"
        "不要发问等人回答，也不要连发两条。"
    ),
}

FALLBACK = """【场合】群聊，随时有别人在场。
【对面】{{addressee}}
【距离】{{distance}}
【时间】{{time}}
"""

DM_FALLBACK = """【场合】私聊。只有你们两个、没有外人。你现在是私下的样子，不是对外的样子。
【长度】一到三句，口语。不要小标题、不要清单。
【时间】{{time}}
"""

_SCENE = re.compile(r"^【场景】.*?(?=\n【|\Z)", re.S | re.M)
"""The scene line is the one part of these files that is not a turn rule.

It goes into the *persona* slot, at its head, because the sheet opens by saying who she
is talking to — a frame strong enough that a rule at the end of the prompt loses to it. Tried: a turn
section alone had her insisting she was alone with the producer inside a 52-person group. Put
the same sentence first, before the sheet, and she switches register and stays there. The rest
of the file is per-turn detail and stays a turn section."""

_CRUSH = re.compile(r"^【暧昧】.*?(?=\n【|\Z)", re.S | re.M)
"""「有点什么，但谁都没说破」—— 只在 relation='crush' 时注入，和【恋爱】同一个槽位。

必须和【恋爱】分开写：crush 的核心是「没承认」，把它塞进恋爱那段里，她会按「已经在
一起」来演，而那段的第一句就是「你们已经在谈恋爱了」。"""

_OFFERED = re.compile(r"^【等答复】.*?(?=\n【|\Z)", re.S | re.M)
"""「我说了，他还没答」—— 只在 relation='offered' 时注入。

和 crush 的区别只有一件事，但那件事决定整场语气：**话已经说出口了**，收不回来。
crush 是「不说」，offered 是「说了在等」，同一个角色在这两种状态下的沉默完全不是一回事。"""

_LOVE = re.compile(r"^【恋爱】.*?(?=\n【|\Z)", re.S | re.M)
"""The paragraph that only exists once they are together.

Same slot as the scene, same reason: 「我们在谈恋爱」 has to be read before the sheet, or the
sheet's framing of the relationship wins. Kept in the file, not in code, so the relationship can be
rewritten without a deploy — and read only for `PRIVATE`, never for a group."""

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_STAGE_HEAD = re.compile(
    r"^【阶段\s*(?P<name>[\w-]+?)\s*[｜|]\s*(?P<floor>\d+)\s*】[^\n]*$", re.M
)
"""A rung of the ladder: `【阶段 close｜10】`.

The body runs to the next rung or to the end of the file, so the rungs go last. That is the
one structural rule: everything above them is per-turn rules, and a rung must not swallow the
sections that come after it. A file with no rungs at all is still valid and falls back to the
`ADDRESSEE` prose above — which is what the tests and a fresh checkout without character data
get.
"""

_FACE_LINE = re.compile(r"^【对面】.*\n?", re.M)
"""The turn's own 「who is this」 line. Dropped when a rung applies, because the rung is that
paragraph now and it sits at the top of the persona instead of in the middle of the turn."""


class Presence:
    def __init__(
        self,
        template: str | Path | None = None,
        *,
        direct: str | Path | None = None,
        stage: str | Path | dict[str, str] | None = None,
        settle: float = 0.35,
        log: Any | None = None,
        owner_stage: str = OWNER_FLOOR,
    ) -> None:
        """`template` is the group register, `direct` the private one. Either may be a path
        or the text itself.

        Both are read through `core.live`, so editing the file changes the next turn — no
        restart. A file that is missing or empty falls back to the paragraph of rules above,
        which keeps a bare test fixture working without shipping character data.

        `stage` is the character's own wording for the parts that are not a template:
        `addressee.*`, `distance.*`, and the two fallbacks. A path, a dict, or nothing —
        nothing means the neutral lines, which is what a checkout with no character data
        should say.
        """
        self._group = source(template, settle=settle, log=log)
        self._direct = source(direct, settle=settle, log=log)
        self._stage = load_stage(stage)
        # Which rung the producer starts on. 空字符串 means 「 treat him like anyone else」,
        # which is how you test the first-meeting voice on your own account.
        self._owner_stage = owner_stage

    def refresh(self) -> list[bool]:
        """Re-read both files now, skipping the settle window (an explicit reload).
        Returns which of them actually changed, so a reload can say so."""
        return [
            bool(src.refresh(force=True))
            for src in (self._group, self._direct)
            if src is not None and hasattr(src, "refresh")
        ]

    def stamp(self) -> tuple:
        """A value that changes when either rule file does — the channel keys its cached
        engines by it, so an edited rule reaches the prompt on the next turn."""
        return tuple(
            src.stamp() if src is not None else (0, 0) for src in (self._group, self._direct)
        )

    def scene_head(self, scene: str = GROUP) -> Section | None:
        """The persona-side half of the register: one paragraph, read before the sheet."""
        found = _SCENE.search(self._template_for(scene))
        if found is None:
            return None
        return Section(Slot.PERSONA, found.group(0).strip(), title=f"qq:scene-{scene}")

    def love_head(self, scene: str = PRIVATE) -> Section | None:
        """「你们已经在谈恋爱」 — persona, like the scene line, and only ever in private.

        A group is fifty people and none of them is in this relationship, so `love_head`
        refuses anything but `PRIVATE`: the flag is the producer's, and the sentence that
        says it out loud cannot reach a room. Placed right after the scene and before the
        sheet, because everything below it is written for someone who has not said it yet.
        """
        if scene != PRIVATE:
            return None
        found = _LOVE.search(self._template_for(scene))
        if found is None:
            return None
        return Section(Slot.PERSONA, found.group(0).strip(), title="qq:love")

    def relation_head(self, scene: str, relation: str) -> Section | None:
        """The paragraph for whichever state they are in. One place, three answers.

        `loving` keeps using `love_head` so the existing behaviour and its test stay exactly
        as they were; `crush` and `offered` read their own paragraphs from the same file.
        A missing paragraph is not an error — a deployment that only wrote 【恋爱】 still
        works, it just has nothing extra to say in the other two states.
        """
        if scene != PRIVATE or not relation:
            return None
        if relation == "loving":
            return self.love_head(scene)
        pattern = {"crush": _CRUSH, "offered": _OFFERED}.get(relation)
        if pattern is None:
            return None
        found = pattern.search(self._template_for(scene))
        if found is None:
            return None
        return Section(Slot.PERSONA, found.group(0).strip(), title=f"qq:{relation}")

    def _template_for(self, scene: str) -> str:
        src, fallback = (
            (self._direct, DM_FALLBACK) if scene == PRIVATE else (self._group, FALLBACK)
        )
        raw = src.text() if src is not None else ""
        key = "fallback.dm" if scene == PRIVATE else "fallback.group"
        return _strip(raw) or self._word(key, fallback)

    def _word(self, key: str, default: str) -> str:
        """One paragraph of the character's own wording, or the neutral line.

        The toml is the character's; this file is the shape. Filling a key with an empty
        string is the same as leaving it out — a half-blank paragraph in a prompt is worse
        than the neutral one.
        """
        return stage_word(self._stage, key, default)

    # ---- how well she knows the person in front of her

    def stages(self, scene: str = GROUP) -> list[tuple[str, int, str]]:
        """`(name, floor, prose)` per rung, cheapest first. Re-read from the file each call,
        so editing a threshold takes effect on the next turn like every other rule here."""
        text = self._template_for(scene)
        heads = list(_STAGE_HEAD.finditer(text))
        found: list[tuple[str, int, str]] = []
        for index, head in enumerate(heads):
            end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
            found.append((head.group("name"), int(head.group("floor")), text[head.end():end].strip()))
        found.sort(key=lambda rung: rung[1])
        return found

    def stage_for(self, turns: int, *, owner: bool = False, scene: str = GROUP) -> str:
        """The rung this person is on. Empty when the file defines none.

        The producer starts further up — see `OWNER_FLOOR` — but climbs the same ladder.
        """
        rungs = self.stages(scene)
        if not rungs:
            return ""
        reached = 0
        for index, (_name, floor, _prose) in enumerate(rungs):
            if int(turns) >= floor:
                reached = index
        if owner and self._owner_stage:
            floor_name = next(
                (i for i, (name, _f, _p) in enumerate(rungs) if name == self._owner_stage), None
            )
            # A file that renamed its rungs should still have an owner floor: the one below
            # the top, because the last rung is what thirty conversations are for.
            reached = max(reached, floor_name if floor_name is not None else len(rungs) - 2, 0)
        return rungs[reached][0]

    def stage_prose(self, name: str, scene: str = GROUP) -> str:
        for rung_name, _floor, prose in self.stages(scene):
            if rung_name == name:
                return prose
        return ""

    def ladder(self, scene: str = GROUP) -> list[dict[str, Any]]:
        """The rungs as data, for `/api/qq/status`: after editing a threshold, this says what
        the process is actually using."""
        return [
            {"stage": name, "from": floor, "chars": len(prose)}
            for name, floor, prose in self.stages(scene)
        ]

    def stage_head(self, stage: str, scene: str = GROUP) -> Section | None:
        """The rung, as persona rather than as a turn rule.

        Same reason as `scene_head`: a rule placed *after* a sheet that spends seven hundred
        lines on her public voice loses to it. Measured — with the rung only in the turn
        section, four rungs one, four and thirty answers apart produced four nearly identical
        replies, all in the same flat polite voice. In front of the sheet, the rung is what she
        reads first, and it colours everything after it.
        """
        prose = self.stage_prose(stage, scene)
        if not prose:
            return None
        return Section(Slot.PERSONA, f"【对面】{prose}", title=f"qq:stage-{stage}")

    def sections(
        self,
        *,
        addressee: str,
        now: datetime,
        timezone: str = "Asia/Shanghai",
        bracket: bool = True,
        scene: str = GROUP,
        ambient: bool = False,
        own: bool = False,
        seen: bool = False,
        turns: int = 0,
        tier: str = "",
        call: bool = False,
    ) -> list[Section]:
        """The turn's own section: the hour, the distance rule, the mechanics.

        `turns` is how many times she has already answered this person. It picks the rung; the
        rung's prose is *persona* (see `stage_head`), so here it only decides whether the
        「对面是谁」 line is still needed — when a rung applies, that paragraph has already
        replaced it at the top of the prompt.

        `ambient` is 「nobody addressed her」; `own` narrows that to 「and she is not picking up
        the thread either」 — a thought of her own instead of a reply to the room. Both drop the
        rung, because there is no 「对面」 to measure a distance to.

        `seen` is 「somebody did address her, and she was given the room around it」 — a reply
        to a call, with the context the call arrived in. It keeps the rung (there is somebody
        being answered) and takes the ambient distance, because 「you only heard the one line」
        would be a lie the transcript in the same prompt contradicts.

        `tier` is the affinity band for *this person* in *this room*. It decides which of her
        two voices the room gets: below 「信任」 she is entirely in the performing register
        (the 「冬優子」 one), and only from 「信任」 on may the private short lines — 「ハ？」
        「バーカ」 — slip out. Turn counts measure how often he has spoken to her; this
        measures how far she has let him in, and only the second one should be able to open
        her up.

        `call` is whether a phone call can actually happen here — `App.call_ready()`, which is
        the same answer the page uses to decide whether to draw the phone key. It is not in the
        persona file on purpose: a rule written there would say 「电话打不通」 forever, and stay
        there the day a voice model is configured.
        """
        clock = _clock(now, timezone)
        # The rungs are alternatives, not prose: whichever one applies is already in
        # `{{addressee}}`, so the ladder itself is cut before the text becomes a prompt.
        rules = _before_stages(self._template_for(scene))
        if scene == PRIVATE:
            text = (
                _SCENE.sub("", rules)
                .replace("{{time}}", clock)
                .replace("{{addressee}}", "")
                .strip()
            )
            return [Section(Slot.TURN, text + "\n\n" + _call(call), title="qq:presence-dm")]
        ladder = "" if ambient else self.stage_for(turns, owner=addressee == OWNER, scene=scene)
        text = _SCENE.sub("", rules)
        if ladder:
            text = _FACE_LINE.sub("", text).replace("{{addressee}}", "")
        else:
            if own:
                who = self._word("addressee.alone", ADDRESSEE[ALONE])
            elif ambient:
                who = self._word("addressee.crowd", ADDRESSEE[CROWD])
            else:
                who = self._word(
                    "addressee.owner" if addressee == OWNER else "addressee.stranger",
                    ADDRESSEE.get(addressee, ADDRESSEE[STRANGER]),
                )
            text = text.replace("{{addressee}}", who)
        distance = "own" if own else ("ambient" if ambient or seen else "called")
        text = (
            text.replace("{{distance}}", self._word(f"distance.{distance}", DISTANCE[distance]))
            .replace("{{time}}", clock)
            .strip()
        )
        text = re.sub(r"\n{3,}", "\n\n", text)
        if not bracket:
            text += "\n【本条】不要在括号里补心里话，全部放在营业的语气里。"
        if scene == GROUP and tier:
            text += "\n\n" + _voice(tier)
        text += "\n\n" + _call(call)
        return [Section(Slot.TURN, text, title="qq:presence")]


def _voice(tier: str) -> str:
    """Which of her two voices the room gets, as one line in the prompt.

    「冬優（ふゆ）」is the one she puts on — the smile, the soft sentence endings, the ♡.
    「冬優子」is who is underneath, and only people she is used to hear her. So this is not a
    dial from 「polite」 to 「rude」: it is a person taking the mask off, and the mask is
    sweetest for the people she knows least. The ♡ density is stated here because the
    persona file's own rung is chosen by turn count, and a member with thirty turns but a
    distance of twenty was getting neither the mask nor the bite.
    """
    name = tier_label(tier)
    if tier == "cold":
        return (
            f"【距离·语气】你现在收着（{name}）：敬语还在，但短、平、不甜——句尾放平，不拖长音，"
            "♡ 和「ふゆ♡」都不挂。不主动递话、不打听他的事；不高兴就客气地说"
            "（「……そういうところ、ちょっと苦手です」），不甩私下的短句。"
        )
    if tier in BELOW_TRUST:
        return (
            f"【距离·语气】你和他还不熟（{name}），这一档整场都是「冬優（ふゆ）」——人设里那个"
            "笑眯眯、句尾软软的营业人格，比对着镜头还要甜一档：句子短、往上翘、会主动把话递过去。"
            "**这个号只发中文**（日语那段不显示），所以♡要挂在中文里才有用：「ふゆ♡」"
            "「好呀♡」「……是吗♡」这样，每三四条里至少一个——放在自我介绍、被夸、真的笑出来的时候。"
            "**但连着两条不许都挂**：每条都带就是口癖，不是招牌；上一句已经挂过，这一句就干净地说。"
            "不交心：问到私事就笑着绕开，不主动打听他的事。"
            "「ハ？」「バーカ」和中文的「哈？」「笨蛋」一句都不该有——那是冬優子那边才有的。"
            "这层甜是刻意摆出来的，不是真心。"
        )
    return (
        f"【距离·语气】这个人你已经熟了（{name}）：冬優子出来了——私下的那几句可以出来，"
        "偶尔一声「ハ？」「バーカ」，生气的时候（真的假的都算）也会用。"
        "「ふゆ♡」和♡ 到这个距离不必再挂（十条里最多一次，当武器用）。"
        "仍旧是一句，别把私下那一整套搬进群里。"
    )


def _call(can: bool) -> str:
    """Whether she may reach for the phone, as one line in the prompt.

    Both halves are needed and they are not the same instruction: with a voice model she can
    ask him to call, and the risk turns into the opposite one — a bot that suggests calling
    every single turn. Without one, a phone call is a promise she cannot keep.
    """
    if can:
        return (
            "【电话】这个号可以通话。但能打不代表该打：偶尔一句「……声、聞きたい」"
            "「夜、電話してもいい？」就够，一次对话里最多提一回；他真打过来，接起来先短一句，"
            "别一开口就长篇。"
        )
    return (
        "【电话】这个号只能打字：电话打不通，声音传不过去，也不存在「正在通话」。"
        "所以不主动要他打过来，他提了也别答应——「はぁ？　電話？　……やだ、字で十分でしょ」"
        "「声なら、次に会ったときに直接」。想听他的声音就写在字里；不要写「（接通了）」"
        "「（挂断）」这类旁白，也别解释为什么打不通。"
    )


def _clock(now: datetime, timezone_name: str) -> str:
    zone = _zone(timezone_name)
    local = now.astimezone(zone) if zone is not None else now
    hour = local.hour
    if hour < 5:
        part = "深夜"
    elif hour < 11:
        part = "早上"
    elif hour < 14:
        part = "中午"
    elif hour < 18:
        part = "下午"
    elif hour < 23:
        part = "晚上"
    else:
        part = "深夜"
    return f"{local:%Y-%m-%d %H:%M}（{WEEKDAYS[local.weekday()]} {part}）"


def _zone(name: str):
    """The zone, or as close as this machine can get.

    A Windows box has no tzdata, and `ZoneInfo` then fails — which would put the clock eight
    hours behind for a deployment that says Asia/Shanghai, and have her say 「おはよう」 at
    midnight. The fixed offset is the honest fallback for that one case: it is right for the
    half of the year it is right for, and at least never wrong by a whole day part.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        if name in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Harbin", "PRC"):
            from datetime import timedelta, timezone

            return timezone(timedelta(hours=8))
        return None


def _strip(text: str) -> str:
    """The file as prose: comments out, and the title line off.

    The H1 is for whoever opens the file in an editor; in a prompt it is a wasted line that
    reads like a chapter heading. Only the *first* line is dropped — a `#` further down is
    somebody's text, not a title.
    """
    cleaned = re.sub(r"<!--.*?-->", "", text or "", flags=re.S).strip()
    return re.sub(r"\A#[^\n]*\n?", "", cleaned).strip()


def _before_stages(text: str) -> str:
    """Everything above the first rung. The rungs are last by construction, so this is the
    part that belongs in every turn."""
    head = _STAGE_HEAD.search(text or "")
    return (text[: head.start()] if head is not None else text).strip()


def _read(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""
