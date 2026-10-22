"""What the server needs to know, read once from the environment.

Three variables are required, and they are the same three the old deployment already
uses, so a `.env` from there works here unchanged. The rest have defaults that let the
kernel run on one machine with no arguments; each one belongs to a plugin that is switched
off when its value is empty, which is why there are no "optional feature" flags.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = {
    "SCCORE_HOST": "0.0.0.0",
    "SCCORE_PORT": "8100",
    "SCCORE_DB": "scbot.db",
    "SCCORE_MEDIA": "media",
    "SCCORE_CHARACTERS": "characters",
    "SCCORE_CHARACTERS_CONFIG": "characters.toml",
    "SCCORE_TURNS_PER_HOUR": "60",
    "SCCORE_PHOTOS_PER_HOUR": "8",
    "SCCORE_HISTORY_TURNS": "20",
    "SCCORE_VOICED_BY": "",
    "SCCORE_TIMEZONE": "Asia/Shanghai",
    "SCCORE_RECALL": "off",
    "SCCORE_RECALL_TOP": "4",
    "SCCORE_RECALL_MIN": "0.35",
    "SCCORE_RECALL_GATE": "off",
    "SCCORE_EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-0.6B",
}


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    host: str = "0.0.0.0"
    port: int = 8100
    db: str = "scbot.db"
    media: str = "media"
    characters: str = "characters"
    characters_config: str = "characters.toml"
    turns_per_hour: int = 60
    photos_per_hour: int = 8
    history_turns: int | None = 20
    voiced_by: str | None = None
    judge_model: str = ""
    """The model that answers the plugins' questions. Empty means the chat model: one key
    is enough to run everything, and a second model is a tuning decision."""
    judge_base_url: str = ""
    judge_api_key: str = ""
    """Where the judge model lives, when it is not the chat model's service — upstream's
    JEV (`typesafe/jev-1.13` through OpenRouter) is one such: the chat stays where it is,
    the judgement moves. Both empty means the chat model's own service and key."""
    recall: str = "off"
    """How her facts file (`characters/facts/<id>.md`) reaches the prompt: `off` (the sheet
    only), `whole` (the file, in the cached persona slot) or `vector` (cut up, embedded,
    the nearest few per turn). See `plugins/recall.py`."""
    recall_top: int = 4
    recall_min: float = 0.35
    recall_gate: str = "off"
    """`judge` asks the judge whether the turn needs her facts at all, which is upstream's
    JEV gate; `off` looks on every turn."""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    fish_key: str = ""
    fish_base_url: str = ""
    timezone: str = "Asia/Shanghai"
    """The zone the day begins in for memories and draw chances: upstream keeps everyone on
    Beijing's days, whatever their own zone. Only the extraction prompt's date uses it."""

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, require_credentials: bool = True
    ) -> "Settings":
        env = os.environ if env is None else env
        needed = {
            "CHAT_LLM_BASE_URL": env.get("CHAT_LLM_BASE_URL", "").strip(),
            "CHAT_LLM_API_KEY": env.get("CHAT_LLM_API_KEY", "").strip(),
            "CHAT_LLM_MODEL": env.get("CHAT_LLM_MODEL", "").strip(),
        }
        missing = [name for name, value in needed.items() if not value]
        if missing and require_credentials:
            raise RuntimeError("缺少环境变量：" + "、".join(missing))
        history = _int(env, "SCCORE_HISTORY_TURNS")
        return cls(
            base_url=needed["CHAT_LLM_BASE_URL"],
            api_key=needed["CHAT_LLM_API_KEY"],
            model=needed["CHAT_LLM_MODEL"],
            host=env.get("SCCORE_HOST", DEFAULTS["SCCORE_HOST"]),
            port=_int(env, "SCCORE_PORT"),
            db=env.get("SCCORE_DB", DEFAULTS["SCCORE_DB"]),
            media=env.get("SCCORE_MEDIA", DEFAULTS["SCCORE_MEDIA"]),
            characters=env.get("SCCORE_CHARACTERS", DEFAULTS["SCCORE_CHARACTERS"]),
            characters_config=env.get(
                "SCCORE_CHARACTERS_CONFIG", DEFAULTS["SCCORE_CHARACTERS_CONFIG"]
            ),
            turns_per_hour=_int(env, "SCCORE_TURNS_PER_HOUR"),
            photos_per_hour=_int(env, "SCCORE_PHOTOS_PER_HOUR"),
            history_turns=None if history == 0 else history,
            voiced_by=env.get("SCCORE_VOICED_BY") or None,
            judge_model=env.get("SCCORE_JUDGE_MODEL", "").strip(),
            judge_base_url=env.get("SCCORE_JUDGE_BASE_URL", "").strip(),
            judge_api_key=env.get("SCCORE_JUDGE_API_KEY", "").strip(),
            recall=_choice(env, "SCCORE_RECALL", ("off", "whole", "vector")),
            recall_top=_int(env, "SCCORE_RECALL_TOP"),
            recall_min=_float(env, "SCCORE_RECALL_MIN"),
            recall_gate=_choice(env, "SCCORE_RECALL_GATE", ("off", "judge")),
            embedding_base_url=env.get("SCCORE_EMBEDDING_BASE_URL", "").strip(),
            embedding_api_key=env.get("SCCORE_EMBEDDING_API_KEY", "").strip(),
            embedding_model=env.get("SCCORE_EMBEDDING_MODEL", DEFAULTS["SCCORE_EMBEDDING_MODEL"]).strip()
            or DEFAULTS["SCCORE_EMBEDDING_MODEL"],
            fish_key=env.get("FISH_AUDIO_API_KEY", "").strip(),
            fish_base_url=env.get("FISH_AUDIO_BASE_URL", "").strip(),
            timezone=env.get("SCCORE_TIMEZONE", DEFAULTS["SCCORE_TIMEZONE"]).strip()
            or DEFAULTS["SCCORE_TIMEZONE"],
        )


def _int(env: Mapping[str, str], name: str) -> int:
    raw = env.get(name, DEFAULTS[name]).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 应该是一个整数，而不是 {raw}") from exc


def _float(env: Mapping[str, str], name: str) -> float:
    raw = env.get(name, DEFAULTS[name]).strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 应该是一个小数，而不是 {raw}") from exc


def _choice(env: Mapping[str, str], name: str, allowed: tuple[str, ...]) -> str:
    raw = env.get(name, DEFAULTS[name]).strip().lower()
    if raw not in allowed:
        raise RuntimeError(f"环境变量 {name} 只能是 {'、'.join(allowed)}，而不是 {raw}")
    return raw


def load_env(path: str | Path) -> dict[str, str]:
    """`KEY=VALUE` lines from a `.env`, without overriding what is already set.

    The same shape the old deployment's `.env` has: comments with `#`, no quoting rules
    beyond stripping a matched pair, no interpolation. A `.env` that needs more than this
    is a `.env` that should be environment variables.
    """
    found: dict[str, str] = {}
    file = Path(path)
    if not file.exists():
        return found
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)
        found[name] = value
    return found
