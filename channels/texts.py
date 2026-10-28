"""What she says when the turn never happens.

A `Veto` carries a stable key (`quota.turns`, `cooling`) because the page translates it. In
a group chat there is no page: the key would be read by fifty people. So every key that can
reach this channel has a line, and the line is in her voice — a refusal should sound like the
character refusing, not like an error code.

Which voice that is cannot live in this file. A module that hard-codes one character's lines
is a module every other character has to be edited out of, so the words come from
`characters/world/<id>-refusals.toml` when a deployment writes one. The table below is only
the neutral fallback: no name, no catchphrase, no particular age. Write the file to sound
like yours; see `templates/refusals.example.toml` in the plugin package.

    [group]                # fifty witnesses: short, and a little more careful
    too_long = "（……太长了，一次说短一点好不好？）"

    [direct]               # just the two of them: blunter is in character
    too_long = "……太长了。短一点。"

`{wait}` may appear in any line; it becomes 「20 秒后」 and the like. A line without it
simply does not say how long. `{wait_raw}` is the same number without the trailing 「后」,
for a language whose grammar puts the verb after the time — 日本語 needs 「20 秒待ってて」,
not 「20 秒後待ってて」.

`None` means say nothing at all: an empty message is not something to apologise for.
"""

import tomllib
from pathlib import Path
from typing import Any

MINUTES = 60

GROUP = "group"
DIRECT = "direct"

DEFAULT: dict[str, dict[str, str]] = {
    GROUP: {
        "too_long": "（……太长了，一次说短一点好不好？）",
        "cooling": "（……还没消气呢，{wait}再说。）",
        "quota": "（今天的力气用完了，{wait}再来。）",
        "quota_none": "（今天的力气用完了，明天再说吧。）",
        "photos": "（照片下次再看吧。）",
        "failed": "（……没听清，再说一次？）",
        "other": "（……现在答不上来，抱歉。）",
    },
    DIRECT: {
        "too_long": "……太长了。短一点。",
        "cooling": "……还没消气呢，{wait}再说。",
        "quota": "今天的力气用完了，{wait}再来。",
        "quota_none": "今天的力气用完了。明天再说。",
        "photos": "照片下次吧。",
        "failed": "……没听清，再说一次。",
        "other": "……现在答不上来，抱歉。",
    },
}
"""The fallback, deliberately plain. Anything with a name in it belongs in the toml."""


def load(path: str | Path | None) -> dict[str, dict[str, str]]:
    """One character's table, merged over the fallback.

    A missing file is normal: a deployment that never wrote one gets the plain lines. A
    broken file is also normal, in the sense that it must not silence her — the fallback is
    kept and the bad file ignored, because a typo in a refusal should cost that refusal, not
    the whole conversation.
    """
    table = {scene: dict(lines) for scene, lines in DEFAULT.items()}
    if not path:
        return table
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return table
    try:
        data: Any = tomllib.loads(raw)
    except (tomllib.TOMLDecodeError, ValueError):
        return table
    if not isinstance(data, dict):
        return table
    for scene in (GROUP, DIRECT):
        lines = data.get(scene)
        if not isinstance(lines, dict):
            continue
        for key, value in lines.items():
            if isinstance(value, str) and value.strip():
                table[scene][str(key)] = value.strip()
    return table


def line(
    reason: str,
    retry_after: int | None = None,
    *,
    private: bool = False,
    refusals: dict[str, dict[str, str]] | None = None,
) -> str | None:
    """The words for one refusal, or `None` to stay quiet."""
    if reason in ("empty", "no_conversation"):
        return None
    table = (refusals or DEFAULT)[DIRECT if private else GROUP]
    if reason.startswith("quota"):
        key = "quota" if retry_after else "quota_none"
    elif reason.startswith("photos"):
        key = "photos"
    else:
        key = reason
    text = table.get(key) or table["other"]
    return _fill(text, retry_after)


def _fill(text: str, retry_after: int | None) -> str:
    if "{wait}" not in text and "{wait_raw}" not in text:
        return text
    return text.replace("{wait}", _wait(retry_after)).replace("{wait_raw}", _wait(retry_after, after=False))


def _wait(seconds: int | None, *, after: bool = True) -> str:
    """`20 秒后` / `3 分钟后`, or `20 秒` / `3 分钟` for a line that puts the verb last.

    The default text and every deployment's `-refusals.toml` are Chinese: this channel posts
    Chinese only. `after=False` still exists because the placeholder is part of the format,
    not because the shipped lines use it.
    """
    if not seconds or seconds <= 0:
        return "过一会儿"
    if seconds < MINUTES:
        span = f"{max(1, seconds)} 秒"
    else:
        span = f"{max(1, round(seconds / MINUTES))} 分"
    return span + "后" if after else span
