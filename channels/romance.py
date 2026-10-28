"""The one relationship that changes kind, and the wall around it.

Everything here is a pure function of numbers and a `Character`: no store, no clock, no
model. That is deliberate — 「她会不会答应」 is the kind of decision that has to be testable
and explainable afterwards (「她还差 4 点好感」), not something to be discovered by watching
what she says.

Three walls, and they are not the same wall:

    初中及以下  her school year. 高中生及以上可以通过；初中生与小学生不行。她被搭话、被喜欢、
                甚至自己动心都成立 —— 只是不能进关系。Ceiling: `crush`。
                The line is drawn by school year (see `SCHOOL_AGE`), not by 18 岁。
    PLove = 0   the setting. There is no such line, ever. Ceiling: `none` — not even
                `crush`, because 「暧昧」 would itself be progress.
    好感不够    the number. Nothing happens yet; this is not a wall, it is a distance.

The numbers live here rather than in `Settings` because they are not deployment choices:
they are what this design *is*. A deployment that wants different ones changes the file.
"""

from __future__ import annotations

from typing import Any

SCHOOL_AGE = 15
"""The age at which the wall comes down, **in school terms** — not the legal age of majority.

The line is 「高中生及以上」: 小学 6–11、中学 12–14、高校 15–17. So 15 is the floor, and
nothing under it gets through. It is deliberately not 18: a 17-year-old high schooler may be
in a relationship here, a 15-year-old middle schooler may not — and that distinction cannot be
made from the age alone (15 is either 初三 or 高一).

That is why the character's own `adult` flag wins over this number: it is filled per person by
school year, so a 15-year-old middle schooler stays closed even though she clears the age.
`unknown_is_adult` (default False) covers the case where neither is written down."""

RELATIONS = ("none", "crush", "offered", "loving")
"""The four states, in the order they can be reached. `offered` is 「她说了，等他答」."""

PLOVE_LEVELS: dict[int, dict[str, Any]] = {
    0: {"name": "不可能", "ask": 95.0, "turns": 60, "accept": 1.01, "maybe": 1.01},
    1: {"name": "绝缘", "ask": 95.0, "turns": 60, "accept": 0.72, "maybe": 0.40},
    2: {"name": "迟钝", "ask": 90.0, "turns": 45, "accept": 0.66, "maybe": 0.35},
    3: {"name": "普通", "ask": 85.0, "turns": 30, "accept": 0.60, "maybe": 0.30},
    4: {"name": "易动心", "ask": 80.0, "turns": 25, "accept": 0.52, "maybe": 0.25},
    5: {"name": "早就动心", "ask": 75.0, "turns": 20, "accept": 0.44, "maybe": 0.18},
}
"""Level → the门槛 he must clear and the two lines his score is read against.

`0` carries `ask`/`turns` because he is still allowed to say it — she listens and then gives
a clear 「没有这回事」. Its `accept`/`maybe` are 1.01 on purpose: nothing can reach them, and a
score is never computed for it anyway (`verdict` short-circuits)."""

W_AFFINITY = 0.45
W_TURNS = 0.30
W_PLOVE = 0.25
"""How the score is split. They must sum to 1, or every score is silently scaled."""

TURNS_FULL = 80.0
"""Turns at which 「他了解我」 is full marks. Beyond it, nothing more to earn."""

HURT_SULK = -0.25
HURT_RECENT = -0.10
"""The two deductions: she is sulking right now, or she was hurt in the last day."""

COOLDOWN_DAYS = 14
MAX_ASKS = 3
"""After a refusal: how long before he may ask again, and how many times in total."""

SHE_FLOOR = 90.0
SHE_DAYS = 21
"""Where her side starts: the number, and how many **consecutive** days above it."""

SHE_BASE = 0.01
SHE_PER_PLOVE = 0.035
SHE_PER_HEAVY = 0.035
SHE_PER_DAY = 0.004
SHE_CAP = 0.40
"""Her daily chance. The waiting term is the point: without it the coolest pairing never
speaks, and 「憋到某天突然开口」 is exactly what a 重 character does."""

OFFER_TIMEOUT_DAYS = 7
GAIN_ACCEPTED = 3.0
LOSS_REFUSED = -8.0
LOSS_SILENCE = -12.0
"""Silence costs more than a refusal. A refusal is an answer; silence is not."""

MAX_DODGES = 3
"""Times he may change the subject before 「岔开」 counts as a no."""


def grown(character: Any, *, unknown_is_adult: bool = False) -> bool:
    """Has she cleared the wall? Explicit answer first, then the age, then the policy.

    The explicit `adult` in the roster is the答案, because 「高中生」 is a school year and the
    age alone cannot tell 高一 from 初三. `age >= SCHOOL_AGE` is only the fallback for a
    character nobody wrote the flag for.

    `unknown_is_adult` defaults to False at every call site: a character nobody wrote either
    for is treated as underage. Getting that wrong the other way is not a bug you can fix
    after it ships.
    """
    adult = getattr(character, "adult", None)
    if adult is not None:
        return bool(adult)
    age = getattr(character, "age", None)
    if isinstance(age, int):
        return age >= SCHOOL_AGE
    return unknown_is_adult


def level(character: Any) -> int:
    return max(0, min(int(getattr(character, "plove", 3) or 0), 5))


def heaviness(character: Any) -> int:
    return max(1, min(int(getattr(character, "heavy", 3) or 3), 5))


def thresholds(character: Any) -> dict[str, Any]:
    return PLOVE_LEVELS[level(character)]


def can_ask(character: Any, affinity: float, turns: int, *, unknown_is_adult: bool = False) -> tuple[bool, str]:
    """May he say it at all? `(yes/no, why not)`.

    Two different reasons can stop him and they are not interchangeable: 「你还没到」 is a
    number he can move, 「没有这条线」 is not. The caller decides what to say; this only
    names which one it is.
    """
    if not grown(character, unknown_is_adult=unknown_is_adult):
        return False, "minor"
    bars = thresholds(character)
    if affinity < bars["ask"]:
        return False, "not_close"
    if turns < int(bars["turns"]):
        return False, "not_close"
    return True, ""


def score(
    character: Any,
    affinity: float,
    turns: int,
    *,
    sulking: bool = False,
    recently_hurt: bool = False,
) -> float:
    """0–1. Read against the two lines of her level.

    Deliberately *not* clamped at 0: a bad night can put it under the floor, and that is a
    true answer about how the conversation went.
    """
    bars = thresholds(character)
    ask = float(bars["ask"])
    room = max(100.0 - ask, 1.0)
    margin = min(max((affinity - ask) / room, 0.0), 1.0)
    value = (
        W_AFFINITY * margin
        + W_TURNS * min(max(turns / TURNS_FULL, 0.0), 1.0)
        + W_PLOVE * max(level(character) - 1, 0) / 4
    )
    if sulking:
        value += HURT_SULK
    if recently_hurt:
        value += HURT_RECENT
    return value


def verdict(
    character: Any,
    affinity: float,
    turns: int,
    *,
    sulking: bool = False,
    recently_hurt: bool = False,
    unknown_is_adult: bool = False,
) -> tuple[str, float, str]:
    """`(outcome, score, reason)`. Outcomes: accept / maybe / refuse / minor / no_way.

    `maybe` is 「真的要看她那句话了」: the numbers put him in the band where wording decides.
    Everything else is decided here, so that most answers can be explained without a model.
    """
    if level(character) <= 0:
        return "no_way", 0.0, "no_way"
    if not grown(character, unknown_is_adult=unknown_is_adult):
        return "minor", 0.0, "minor"
    bars = thresholds(character)
    if affinity < bars["ask"] or turns < int(bars["turns"]):
        return "refuse", 0.0, "not_close"
    if sulking:
        # A sulking 「好」 would be a lie: she is not in a state to mean it.
        return "maybe", score(character, affinity, turns, sulking=True), "fight"
    value = score(character, affinity, turns, recently_hurt=recently_hurt)
    if value >= float(bars["accept"]):
        return "accept", value, ""
    if value >= float(bars["maybe"]):
        return "maybe", value, ""
    return "refuse", value, "not_close"


def allowed(character: Any, target: str, *, unknown_is_adult: bool = False) -> bool:
    """May she be *put into* this state? The last gate before a write.

    Three separate rules, checked here so that no caller can forget one:

    - `loving` / `offered` need an adult with `PLove ≥ 1`.
    - `crush` needs `PLove ≥ 1` but not adulthood — a 17-year-old can absolutely have
      something unsaid between her and her producer.
    - `none` is always allowed: going backwards is never blocked by a wall.
    """
    if target not in RELATIONS:
        return False
    if target == "none":
        return True
    if level(character) <= 0:
        return False
    if target == "crush":
        return True
    return grown(character, unknown_is_adult=unknown_is_adult)


def she_ok(character: Any, *, unknown_is_adult: bool = False) -> bool:
    """Can she ever be the one who speaks? Both walls say no, for different reasons."""
    return grown(character, unknown_is_adult=unknown_is_adult) and level(character) >= 1


def she_chance(character: Any, waited_days: float) -> float:
    """Her chance per day of finally saying it. Zero when the line does not exist.

    Callers must have passed `she_ready` first — a number here is not permission.
    """
    if level(character) <= 0:
        return 0.0
    per_day = (
        SHE_BASE
        + SHE_PER_PLOVE * (level(character) - 1)
        + SHE_PER_HEAVY * (heaviness(character) - 1)
        + SHE_PER_DAY * max(waited_days, 0.0)
    )
    return min(max(per_day, 0.0), SHE_CAP)


def she_ready(
    character: Any,
    affinity: float,
    relation: str,
    waited_days: float,
    *,
    unknown_is_adult: bool = False,
) -> tuple[bool, str]:
    """Is she in the running tonight? `(yes/no, why not)`.

    `waited_days` is **consecutive** days above `SHE_FLOOR` — the caller owns that clock
    (`qq_identities.warm_since`), because a counter in memory restarts on every reboot and
    this machine reboots.
    """
    if not she_ok(character, unknown_is_adult=unknown_is_adult):
        return False, "no_way" if level(character) <= 0 else "minor"
    if relation != "none":
        return False, relation
    if affinity < SHE_FLOOR:
        return False, "not_close"
    if waited_days < SHE_DAYS:
        return False, "not_long_enough"
    return True, ""


YES_WORDS = (
    "好啊", "可以啊", "可以", "我也是", "我也喜欢", "喜欢你", "在一起", "答应",
    "愿意", "当然", "那当然", "嗯嗯", "行啊", "好啊好啊", "要",
)
NO_WORDS = (
    "对不起", "抱歉", "不行", "不可以", "不要", "算了", "不喜欢", "拒绝",
    "没这回事", "别这样", "不可以的",
)
"""词表比模型便宜，也比模型稳定。**先判否再判可**： 「不行，我不喜欢你」 里有「喜欢」。"""


def answer(text: str) -> str:
    """How he answered her: `yes` / `no` / `dodge`.

    `dodge` is its own outcome, not a soft no: 「你说什么呢」 is neither an answer nor a
    refusal, and she is exactly the kind of person to keep waiting on it. Three of those and
    the caller counts it as a no.
    """
    said = "".join((text or "").split())
    if not said:
        return "dodge"
    if any(word in said for word in NO_WORDS):
        return "no"
    if len(said) <= 14 and any(word in said for word in YES_WORDS):
        return "yes"
    return "dodge"


def offer_expired(offered_at: str, now: Any, *, days: int = OFFER_TIMEOUT_DAYS) -> bool:
    """Has she been waiting too long? An unreadable timestamp counts as 「no」 — better to
    keep waiting than to end something on a parse error."""
    from datetime import datetime, timedelta, timezone

    try:
        when = datetime.fromisoformat(str(offered_at))
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return now - when >= timedelta(days=days)
