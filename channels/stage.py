"""The character's own words, in one file.

Everything a channel has to say that is not a system rule lives in
`characters/world/<id>-stage.toml`: how to address the person in front of her, how close she
is allowed to sound, what she calls herself in a memory file, what she says when a command is
not understood. The channel keeps the *shape* — placeholders, sections, the order of the
prompt — and the file keeps the voice.

Flat keys, because that is how they are used: one lookup, one string. A missing key, an empty
value, an unreadable file and a file that does not parse all mean the same thing: use the
neutral fallback the module was written with. None of those is worth an exception: a typo in a
prompt paragraph should cost that paragraph, not the conversation.

    [addressee]
    owner = "…"        → addressee.owner
    [names]
    self  = "…"        → names.self
"""

import re
import tomllib
from pathlib import Path
from typing import Any, Mapping


def load(source: str | Path | Mapping[str, str] | None) -> dict[str, str]:
    """`addressee.owner` → its paragraph, from a toml path or a dict already flat."""
    if not source:
        return {}
    if isinstance(source, Mapping):
        return {
            str(key): str(value).strip()
            for key, value in source.items()
            if str(value).strip()
        }
    try:
        data: Any = tomllib.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    flat: dict[str, str] = {}
    for section, body in data.items():
        if not isinstance(body, dict):
            continue
        for key, value in body.items():
            if isinstance(value, str) and value.strip():
                flat[f"{section}.{key}"] = value.strip()
    return flat


def names(table: Mapping[str, str], key: str) -> tuple[str, ...]:
    """A `names.*` entry as a list. The file keeps one string (`mentions = "ふゆ、冬優子"`),
    split here on the usual separators — a list in the toml would not survive `load`, which
    keeps strings on purpose. An empty entry gives no names, which is a deployment that
    matches on nothing, not an error."""
    raw = (table.get(key) or "").strip()
    return tuple(part for part in re.split(r"[、,，/\s]+", raw) if part)


def word(table: Mapping[str, str], key: str, default: str) -> str:
    """One paragraph, or the neutral line when the file has nothing to say about it."""
    return (table.get(key) or "").strip() or default
