"""Where one character's files live, and what the work itself is called.

Two things live here, and they are the two lines the prompts have always contained without
naming them:

- **the work** (`work.toml`): what this is a work *of* — its title, the agency, the words
  that mean 「someone is talking about the work itself」. Shared by everyone, one file.
- **the pack** (`packs/<id>/`): everything that belongs to one person — her sheet, her facts,
  her view of the office, her voice rules, her names, her memory. One directory per person,
  replaceable without touching code.

The pack is **双路径**: `packs/<id>/voice/qq.md` first, then the flat `world/<id>-qq.md` this
deployment has always used. That is not politeness — it is what makes the move possible in
one step, with fuyuko still running from the old layout while the next character is written
in the new one. `source()` says which of the two answered, so the startup line and the page
can say 「this one is still on the old path」 instead of quietly guessing.

Nothing here reads a file's *content*: a pack is a map from a name to a path, and a missing
file is a fact (`None`), not an error. The caller decides whether it can live without it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

WORK_FILE = "work.toml"
PACKS = "packs"

# kind → (path inside the pack, the flat path this deployment used before)
_LAYOUT: dict[str, tuple[str, str | None]] = {
    "persona": ("persona.md", "sheets/{id}.md"),
    "facts": ("facts.md", "facts/{id}.md"),
    "view": ("view.md", None),
    "world": ("world.md", None),
    "names": ("names.toml", "world/{id}-stage.toml"),
    "qq": ("voice/qq.md", "world/{id}-qq.md"),
    "dm": ("voice/dm.md", "world/{id}-dm.md"),
    "stage": ("voice/stage.toml", "world/{id}-stage.toml"),
    "refusals": ("voice/refusals.toml", "world/{id}-refusals.toml"),
    "interests": ("voice/interests.md", "world/{id}-interests.md"),
    "qzone": ("voice/qzone.md", "world/{id}-qzone.md"),
    "relations": ("relations.toml", "world/{id}-idols.toml"),
    "stories": ("stories.toml", "world/{id}-stories.toml"),
    "story-files": ("facts-stories", "facts/{id}-stories"),
    "avatar": ("assets/avatar.jpg", "avatars/{id}.jpg"),
    "scsp-avatar": ("assets/scsp.jpg", "avatars-scsp/{id}.jpg"),
    "background": ("assets/background.jpg", "backgrounds/{id}.jpg"),
}
REQUIRED = ("persona", "qq", "dm")
"""What a character cannot be played without. Everything else has a neutral fallback:
no view is a character with no opinion of her colleagues, which is worse but not broken."""


@dataclass(frozen=True)
class Work:
    """The work, as data. Every field has a neutral default on purpose: a checkout with no
    `work.toml` must still talk, it just must not claim to be a particular work."""

    title: str = ""
    title_zh: str = ""
    agency: str = ""
    agency_zh: str = ""
    meta_words: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()

    @property
    def named(self) -> bool:
        return bool(self.title or self.title_zh or self.agency or self.agency_zh)

    def name(self, *, chinese: bool = True) -> str:
        """`《偶像大师 闪耀色彩》`, or `""` when this deployment did not say."""
        title = (self.title_zh if chinese else self.title) or self.title or self.title_zh
        return f"《{title}》" if title else ""

    def house(self, *, chinese: bool = True) -> str:
        agency = (self.agency_zh if chinese else self.agency) or self.agency or self.agency_zh
        return agency

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "title_zh": self.title_zh,
            "agency": self.agency,
            "agency_zh": self.agency_zh,
            "named": self.named,
            "words": len(self.meta_words),
        }


def load_work(root: str | Path) -> Work:
    """`<characters>/work.toml`, or an empty `Work`. A broken file is an empty one: the
    roster must not fail to load because somebody's editor saved half a toml."""
    path = Path(root) / WORK_FILE
    if not path.exists():
        return Work()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return Work()
    work = data.get("work") or {}
    aliases = data.get("aliases") or {}
    privacy = data.get("privacy") or {}
    return Work(
        title=str(work.get("title") or "").strip(),
        title_zh=str(work.get("title_zh") or "").strip(),
        agency=str(work.get("agency") or "").strip(),
        agency_zh=str(work.get("agency_zh") or "").strip(),
        meta_words=_strings(aliases.get("words")),
        forbidden=_strings(privacy.get("forbidden")),
    )


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


@dataclass(frozen=True)
class Pack:
    """One character's map of names to paths. Cheap: nothing is read until asked."""

    root: Path
    character_id: str
    work: Work = Work()
    declared: str = ""
    """`pack` from `characters.toml`, relative to the characters directory. Empty means
    `packs/<id>`, which is where nine deployments out of ten will put it anyway."""

    # ---- where things are

    @property
    def home(self) -> Path:
        return self.root / (self.declared or f"{PACKS}/{self.character_id}")

    def relative(self, kind: str, *, writable: bool = False) -> str:
        """The path as a line in a log or a JSON field, relative to the characters dir.

        `writable` is memory: it is created on demand and never falls back, because two
        characters sharing one memory file is the one mistake this whole change exists to
        prevent.
        """
        if kind == "memory":
            return f"memory/{self.character_id}"
        hit = self.path(kind)
        if hit is None:
            return ""
        try:
            return hit.relative_to(self.root).as_posix()
        except ValueError:
            return hit.as_posix()

    def path(self, kind: str, *, writable: bool = False) -> Path | None:
        if kind == "memory":
            target = self.root / "memory" / self.character_id
            if writable:
                target.mkdir(parents=True, exist_ok=True)
            return target if target.is_dir() else None
        layout = _LAYOUT.get(kind)
        if layout is None:
            raise KeyError(f"不知道这个角色包里有什么：{kind}")
        inside, flat = layout
        first = self.home / inside.format(id=self.character_id)
        if first.exists():
            return first
        if flat:
            second = self.root / flat.format(id=self.character_id)
            if second.exists():
                return second
        return None

    def source(self, kind: str) -> str:
        """`packs/<id>/…` when it is in the pack, `world/<id>-…` when it is on the old path,
        `""` when it is not there at all. The startup line prints this verbatim."""
        return self.relative(kind)

    def text(self, kind: str) -> str:
        hit = self.path(kind)
        if hit is None or hit.is_dir():
            return ""
        try:
            return hit.read_text(encoding="utf-8")
        except OSError:
            return ""

    # ---- what is missing

    def fallback(self) -> tuple[str, ...]:
        """Kinds answered by the flat path. Not an error — a migration still in progress."""
        out = []
        for kind in _LAYOUT:
            hit = self.path(kind)
            if hit is None:
                continue
            if self.home not in hit.parents:
                out.append(kind)
        return tuple(out)

    def missing(self) -> tuple[str, ...]:
        """Kinds with no file at all. `persona`/`qq`/`dm` missing means 「this character
        cannot be played」; a missing `view` only means she has no opinion of the office."""
        return tuple(kind for kind in _LAYOUT if self.path(kind) is None)

    def compact(self) -> dict[str, object]:
        """For a list of twenty-eight: ready or not, and what is still on the old path.
        The full map is one character's business, not the roster's."""
        return {
            "home": self.home.relative_to(self.root).as_posix() if self.home.is_dir() else "",
            "ready": all(self.path(kind) is not None for kind in REQUIRED),
            "missing": list(self.missing()),
            "fallback": list(self.fallback()),
        }

    def summary(self) -> dict[str, object]:
        """For the page and the startup line: where each half came from, and what is absent."""
        return {
            "id": self.character_id,
            "home": self.home.relative_to(self.root).as_posix()
            if self.home.is_dir()
            else "",
            "files": {kind: self.relative(kind) for kind in _LAYOUT},
            "fallback": list(self.fallback()),
            "missing": list(self.missing()),
            "ready": all(self.path(kind) is not None for kind in REQUIRED),
        }


def card_tails(character: Any, pack: Pack | None = None) -> tuple[str, ...]:
    """The signatures she leaves at the end of a card title: `# 【カード名】黛冬優子`.

    A pack that says so wins (`names.toml [names] cards`); otherwise it is her full name and
    her Chinese name, which is what every corpus this deployment has seen writes. Empty when
    neither is known — a character with no name gets no stripping, not somebody else's name.
    """
    told: Sequence[str] = ()
    if pack is not None:
        told = _names_table(pack.text("names")).get("cards", ())
    if told:
        return tuple(told)
    out: list[str] = []
    for value in (getattr(character, "name", ""), getattr(character, "name_zh", "")):
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _names_table(text: str) -> dict[str, tuple[str, ...]]:
    try:
        data = tomllib.loads(text or "")
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    names = data.get("names") or {}
    out: dict[str, tuple[str, ...]] = {}
    for key in ("cards", "mentions"):
        value = names.get(key)
        if isinstance(value, str):
            parts = [part.strip() for part in value.replace("，", "、").split("、")]
        elif isinstance(value, (list, tuple)):
            parts = [str(part).strip() for part in value]
        else:
            continue
        out[key] = tuple(part for part in parts if part)
    return out
