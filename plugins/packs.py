"""Where one character's files live, and what the work itself is called.

Two things live here, and they are the two lines the prompts have always contained without
naming them:

- **the work** (`works/<id>.toml`): what this is a work *of* — its title, the agency, the
  words that mean 「someone is talking about the work itself」. One file per work, all of
  them peers; `work.toml` is only the single-work shorthand.
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
WORKS_DIR = "works"
"""`characters/works/<id>.toml`: every work this deployment hosts, one file each, peers.
A deployment with a single work may skip the directory and use `work.toml` instead."""

DEFAULT_WORK = "default"
"""The id `work.toml` gets when it does not declare one. It is a file-name fallback, not a
「main work」 — `works/` never special-cases any of its files."""


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
    "tease": ("voice/tease.toml", None),
    "tease-md": ("voice/tease.md", None),
    "tiers": ("voice/tiers.toml", None),
}
"""最后两项没有旧路径：它们是后来才有的、而且**只属于某一种人设**。

`tease` 是「夹子」那套（♡ 这类记号、几个消息里最多一次、那一句的话术），
`tiers` 是群里的档位声线（冬優／冬優子那一层）。别的角色没有这两份文件，
就当这两个功能不存在——不能拿冬優子的招牌去给别人用。"""

_IMAGE_KINDS = {"avatar", "scsp-avatar", "background"}
"""这三样是图片。素材给的是 PNG 还是 JPG 是部署者的事，解析时两种都认。"""

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
"""同一个名字常见的四种后缀，写着的那个优先。"""


REQUIRED = ("persona", "qq", "dm")
"""What a character cannot be played without. Everything else has a neutral fallback:
no view is a character with no opinion of her colleagues, which is worse but not broken."""


def _variants(path: Path | None, kind: str) -> list[Path]:
    """这个名字在这台机器上可能是哪几个文件：写着的后缀先试，图片再试其余三种。

    背景素材是官方给的 PNG，人设目录里的旧图是 JPG——同一行 `backgrounds/{id}.jpg`
    不该因为后缀对不上就说「没有」。
    """
    if path is None:
        return []
    found = [path]
    if kind in _IMAGE_KINDS and path.suffix:
        found += [
            path.with_suffix(suffix)
            for suffix in _IMAGE_SUFFIXES
            if suffix != path.suffix.lower()
        ]
    return found


@dataclass(frozen=True)
class TeaseRule:
    """「夹」这件事的规则，读自 `packs/<id>/voice/tease.toml`。

    三个字段都是这个角色自己的：记什么记号、几个消息算一轮额度、那一句怎么说。
    空规则 = 这个角色没有「营业声线披上去逗人」这回事，功能整体不生效。
    """

    marks: tuple[str, ...] = ()
    window: int = 10
    hint: str = ""

    @property
    def on(self) -> bool:
        return bool(self.hint) and bool(self.marks)


def tease_rule(pack: Any) -> TeaseRule:
    """This character's own rule, or an empty one. Reading it here rather than in the
    plugin is what keeps the channel and the judge counting the same thing."""
    path = pack.path("tease") if pack is not None else None
    if path is None:
        return TeaseRule()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TeaseRule()
    found = data.get("tease") or {}
    marks = tuple(str(one) for one in (found.get("marks") or ()) if one)
    return TeaseRule(
        marks=marks,
        window=max(1, int(found.get("window") or 10)),
        hint=str(found.get("hint") or ""),
    )


def tier_lines(pack: Any) -> dict[str, str]:
    """The room's voice per distance band: `{"cold": "…", "distant": "…", …}`.

    Which voice a stranger hears and which one somebody close hears is character data —
    it is the whole difference between 「营业人格」 and 「本人」. Without the file there is
    no line at all, which is the honest default for a character nobody wrote it for.
    """
    path = pack.path("tiers") if pack is not None else None
    if path is None:
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        str(tier): str(line)
        for tier, line in (data.get("tiers") or {}).items()
        if tier and line
    }


@dataclass(frozen=True)
class Work:
    """The work, as data. Every field has a neutral default on purpose: a checkout with no
    `work.toml` must still talk, it just must not claim to be a particular work."""

    id: str = ""
    """Which work a character says she is from. A deployment that never writes one gets
    `DEFAULT_WORK`, which is enough while there is only one work."""
    title: str = ""
    title_zh: str = ""
    agency: str = ""
    agency_zh: str = ""
    meta_words: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    source: str = ""
    """`works/<id>.toml`, or `work.toml` for the single-work shorthand. The page shows
    this so 「这个作品的标题是从哪个文件读出来的」 has an answer."""

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
            "id": self.id,
            "title": self.title,
            "title_zh": self.title_zh,
            "agency": self.agency,
            "agency_zh": self.agency_zh,
            "named": self.named,
            "words": len(self.meta_words),
            "source": self.source,
        }


def load_work(root: str | Path) -> Work:
    """`<characters>/work.toml`, the single-work shorthand. Broken or missing is empty."""
    return load_work_file(Path(root) / WORK_FILE, default_id=DEFAULT_WORK)


def load_work_file(path: Path, *, default_id: str = "") -> Work:
    """One work table from one file. Broken or missing is an empty work, never a raise.

    `works/<id>.toml` names itself after the file when it does not declare an id; the
    legacy `work.toml` gets `default_id` instead, because 「work」 is not a work's name."""
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
    declared = str(work.get("id") or "").strip()
    if not declared:
        declared = path.stem if path.stem != "work" else default_id
    source = f"{WORKS_DIR}/{path.name}" if path.parent.name == WORKS_DIR else path.name
    return Work(
        id=declared,
        title=str(work.get("title") or "").strip(),
        title_zh=str(work.get("title_zh") or "").strip(),
        agency=str(work.get("agency") or "").strip(),
        agency_zh=str(work.get("agency_zh") or "").strip(),
        meta_words=_strings(aliases.get("words")),
        forbidden=_strings(privacy.get("forbidden")),
        source=source,
    )


def load_works(root: str | Path) -> dict[str, Work]:
    """Every work this deployment knows — all of them equal, none 「主作品」.

    `characters/works/<id>.toml` is where a work lives. A character says which one she is
    from (`work = "<id>"` in `characters.toml`); this table turns that name into a title.

    `work.toml` is the **single-work shorthand**: read only when there is no `works/`
    directory at all. A deployment with two works keeps both under `works/`, so 「the work
    in work.toml」 never becomes an implicit parent that the others hang off."""
    root = Path(root)
    out: dict[str, Work] = {}
    folder = root / WORKS_DIR
    if folder.is_dir():
        for path in sorted(folder.glob("*.toml")):
            one = load_work_file(path)
            if one.named:
                out[one.id or path.stem] = one
    if out:
        return out
    legacy = load_work(root)
    if legacy.named:
        out[legacy.id or DEFAULT_WORK] = legacy
    return out



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
        second = self.root / flat.format(id=self.character_id) if flat else None
        for candidate in _variants(first, kind) + _variants(second, kind):
            if candidate.exists():
                return candidate
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
            "present": [kind for kind in _LAYOUT if self.path(kind) is not None],
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
