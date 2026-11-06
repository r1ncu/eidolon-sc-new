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


# kind → (path inside the pack, the flat path this deployment used before, inheritable?)
# 继承白名单跟定义走：加一个新 kind 时只要标 `inheritable=...`，就不会忘掉它该不该被派生角色借。
# 默认 `False`——和「不要给数据加魔法默认值」一致：忘标 = 不继承 = 派生角色退回中性，
# 而不是悄悄借到主角色的招牌（`tease`/`tiers`/`stage`/`stories` 那一类）。
@dataclass(frozen=True)
class _Entry:
    inside: str
    flat: str | None = None
    inheritable: bool = False


_LAYOUT: dict[str, _Entry] = {
    "persona": _Entry("persona.md", "sheets/{id}.md", inheritable=True),
    "facts": _Entry("facts.md", "facts/{id}.md", inheritable=True),
    "view": _Entry("view.md", None, inheritable=True),
    "world": _Entry("world.md", None, inheritable=True),
    "names": _Entry("names.toml", "world/{id}-stage.toml", inheritable=True),
    "qq": _Entry("voice/qq.md", "world/{id}-qq.md", inheritable=True),
    "dm": _Entry("voice/dm.md", "world/{id}-dm.md", inheritable=True),
    "stage": _Entry("voice/stage.toml", "world/{id}-stage.toml"),
    "refusals": _Entry("voice/refusals.toml", "world/{id}-refusals.toml", inheritable=True),
    "interests": _Entry("voice/interests.md", "world/{id}-interests.md", inheritable=True),
    "qzone": _Entry("voice/qzone.md", "world/{id}-qzone.md", inheritable=True),
    "relations": _Entry("relations.toml", "world/{id}-idols.toml", inheritable=True),
    "stories": _Entry("stories.toml", "world/{id}-stories.toml"),
    "story-files": _Entry("facts-stories", "facts/{id}-stories"),
    # 头像不继承：派生角色（幼年/if）有自己的图，借主角色的会画出二十岁的她当十岁的。
    "avatar": _Entry("assets/avatar.jpg", "avatars/{id}.jpg"),
    "scsp-avatar": _Entry("assets/scsp.jpg", "avatars-scsp/{id}.jpg"),
    "background": _Entry("assets/background.jpg", "backgrounds/{id}.jpg", inheritable=True),
    "call-background": _Entry("assets/call-background.jpg", "backgrounds/{id}-call.jpg", inheritable=True),
    "tease": _Entry("voice/tease.toml", None),
    "tease-md": _Entry("voice/tease.md", None),
    "tiers": _Entry("voice/tiers.toml", None),
    "night": _Entry("voice/night.toml", None, inheritable=True),
}
"""最后几项没有旧路径：它们是后来才有的、而且**只属于某一种人设**。

`tease` 是「夹子」那套（♡ 这类记号、几个消息里最多一次、那一句的话术），
`tiers` 是群里的档位声线（冬優／冬優子那一层）。别的角色没有这两份文件，
就当这两个功能不存在——不能拿冬優子的招牌去给别人用。

`night` 是深夜那一套：几点睡、几点醒、被叫醒时的语气。它**每个角色都可以有**，
而且和自己的作息绑在一起——十岁的那位九点睡，二十岁的那位二十三点睡，同一个机制。
没有这份文件时，通道用自己那套通用文本（`channels/qq.py` 的 `NIGHT_*`）。"""

_IMAGE_KINDS = {"avatar", "scsp-avatar", "background", "call-background"}
"""这几样是图片。素材给的是 PNG 还是 JPG 是部署者的事，解析时两种都认。"""

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
"""同一个名字常见的四种后缀，写着的那个优先。"""

_INHERITABLE = frozenset(k for k, v in _LAYOUT.items() if v.inheritable)
"""派生角色可以从本体借的那几份。**由 `_LAYOUT` 的 `inheritable` 标记派生**，不再单列维护。

**故意不包括** `tease` / `tease-md` / `tiers` / `stage` / `stories` / `story-files` /
`avatar` / `scsp-avatar`：前五个是「成年那一版的招牌」或者「已经发生过的事」——十岁
的人没有营业声线、没有夹子规则、也还没有剧情，对它们来说「包里没有这份文件」本身就是
「这个功能对她不存在」，继承过来会把它打开。后两个是头像：派生角色有自己的图。

加新 kind 时只要在 `_LAYOUT` 里标 `inheritable=True/False`，这里就跟着走，不再
出现「定义在 `_LAYOUT`、白名单忘改」的脱节。"""


REQUIRED = ("persona", "qq", "dm")
"""What a character cannot be played without. Everything else has a neutral fallback:
no view is a character with no opinion of her colleagues, which is worse but not broken."""

_DEFAULTED = ("night", "refusals", "interests")
"""这几个 kind 缺失时（自己没写、也没从本体继承到），通道会**静默用内置默认顶上**：

- `night` → `channels/qq.py` 的 `REST_HOURS` / `NIGHT_*` 那套通用文本（23 点睡、被吵醒
  那几句日语）。十岁的她九点睡、二十岁的她二十三点睡——这个差异写在 `night.toml` 里；
  没写就一律按 23 点算，而日志里看不出来「这是默认，不是她写的」。
- `refusals` → `channels/texts.py` 的朴素拒答词。
- `interests` → 中性的话题判断。

和 `tease` / `tiers` 不同——那两个缺失是「这个功能对她不存在」（十岁的人没有营业声线），
这三个缺失是「她在用一个不属于她自己的默认」。`compact()` 把它们单列在 `using_defaults`，
启动日志和设置页据此把这件事说清楚，而不是让人从语气里猜。"""


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


@dataclass(frozen=True)
class NightRule:
    """她那一觉：几点睡、几点醒、被吵醒时怎么说（`packs/<id>/voice/night.toml`）。

    每个角色一份，因为「几点该睡」和「被叫醒时什么口气」都是人设的一部分：十岁的那位
    九点就要睡、被叫醒是迷迷糊糊地要回去睡；二十岁的那位二十三点睡、被吵醒先不高兴。
    机制是同一套，数值和文案各自写。

    **-1 / 空字符串 = 没写**，由调用方补默认值（`channels/qq.py` 的 `REST_HOURS` 那一组）。
    这里不填默认值，是因为「没写」和「写了 0 点睡」必须分得开。
    """

    enabled: int = -1
    """她到底睡不睡。-1 = 没写（睡，按下面的钟点）；0 = 不睡（这一套整体不生效）。

    关掉的意思是「这个人没有作息」：机器人号、永远在线的设定、或者只是想先跑起来看
    别的。关掉之后不掷骰子、不记睡眠账本、被 @ 也不会有「被吵醒」的语气。"""
    work: int = -1
    """第二天要上班/上学时，几点上床（24 小时制）。-1 = 没写。"""
    off: int = -1
    """第二天不用去时，几点上床。-1 = 没写（跟 `work` 一样）；0 = 午夜十二点。"""
    late: int = -1
    """在 `off` 之上最多再熬几小时。-1 = 没写（用通道默认）。"""
    wake: int = -1
    """几点醒。-1 = 没写。"""
    cap: int = 0
    """一夜最多几轮（被叫醒的对话）。"""
    note: str = ""
    """被吵醒那一段的正文，`{when}` / `{how}` 是占位符。"""
    turn: str = ""
    """第几轮 / 第几轮结束，`{turn}` / `{ends}` / `{how}`。"""
    ending: str = ""
    more: str = ""
    far: str = ""
    trust: str = ""
    close: str = ""

    def how(self, band: str) -> str:
        """这一档关系听到的那一句。空 = 没写，调用方用通用那句。"""
        if band in ("close", "intimate"):
            return self.close
        if band == "trust":
            return self.trust
        return self.far


_NIGHT_HOURS = ("work", "off", "wake")
_NIGHT_TEXT = ("note", "turn", "ending", "more", "far", "trust", "close")


def night_rule(pack: Any) -> NightRule:
    """`packs/<id>/voice/night.toml`, or an empty rule（= 用通道那套通用文本）。

    读不出来（文件坏了、字段类型不对）就当没写：一个坏掉的作息文件不该让整台部署起不来。
    """
    path = pack.path("night") if pack is not None else None
    if path is None:
        return NightRule()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return NightRule()
    rest = data.get("rest") or {}
    # 文案在 `[lines]` 下；也认扁平写法（字段直接写在根上），免得改一处忘一处。
    lines = data.get("lines") or {}
    found: dict[str, Any] = {}
    for name in _NIGHT_HOURS:
        found[name] = rest.get(name, data.get(name))
    found["late"] = rest.get("late", data.get("late", -1))
    found["cap"] = rest.get("cap", data.get("cap", 0))
    found["enabled"] = rest.get("enabled", data.get("enabled", -1))
    for name in _NIGHT_TEXT:
        found[name] = lines.get(name, data.get(name))

    def hour(value: Any) -> int:
        """0–23 才算写了一个钟点；没写、写坏、越界都是 -1（= 交给调用方补默认值）。

        不能拿 0 当哨兵：0 点是午夜，是个正经钟点。
        """
        try:
            number = int(value)
        except (TypeError, ValueError):
            return -1
        return number if 0 <= number <= 23 else -1

    def count(value: Any, low: int = 0) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return low
        return max(low, number)

    def text(value: Any) -> str:
        return str(value) if isinstance(value, str) else ""

    return NightRule(
        enabled=count(found["enabled"], -1),
        work=hour(found["work"]),
        off=hour(found["off"]),
        late=count(found["late"], -1),
        wake=hour(found["wake"]),
        cap=count(found["cap"]),
        **{name: text(found[name]) for name in _NIGHT_TEXT},
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

    inherit: Path | None = None
    """The pack of the person she is another version of（`derived_from`）。

    Her own directory wins; what she has not written yet comes from here. Same person,
    one folder apart: the ten-year-old does not need a second copy of her grown-up
    memories, tone rules and facts — only the parts that differ.
    """

    inherit_id: str = ""
    """The id to fill `{id}` with when looking inside `inherit`."""

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

    def _local(self, kind: str) -> Path | None:
        """The file **in her own folder** (or on the old flat path). None when she did not
        write this one herself — that is the question `inherited()` answers."""
        layout = _LAYOUT.get(kind)
        if layout is None:
            raise KeyError(f"不知道这个角色包里有什么：{kind}")
        inside, flat = layout.inside, layout.flat
        first = self.home / inside.format(id=self.character_id)
        second = self.root / flat.format(id=self.character_id) if flat else None
        for candidate in _variants(first, kind) + _variants(second, kind):
            if candidate.exists():
                return candidate
        return None

    def path(self, kind: str, *, writable: bool = False) -> Path | None:
        if kind == "memory":
            target = self.root / "memory" / self.character_id
            if writable:
                target.mkdir(parents=True, exist_ok=True)
            return target if target.is_dir() else None
        hit = self._local(kind)
        if hit is not None:
            return hit
        # 她是某个人的另一条人生：自己没写的，用本体那一份。
        # 语气规则、资料、对世界的看法，本体都写好了；十岁版只该覆盖不一样的那几份。
        # 记忆不在此列（上面单独处理）——两个人共用一本记忆正是这套东西要避免的事。
        if self.inherit is not None and kind in _INHERITABLE:
            layout = _LAYOUT[kind]
            inside, flat = layout.inside, layout.flat
            borrowed = []
            if inside:
                borrowed += _variants(self.inherit / inside.format(id=self.inherit_id), kind)
            if flat:
                # 图片走的是旧扁平路径（`backgrounds/<id>.jpg`），那一条也要能借到。
                borrowed += _variants(self.root / flat.format(id=self.inherit_id), kind)
            for candidate in borrowed:
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
        """Kinds answered by the flat path（`<id>-*.md`）. Not an error — a migration in progress.

        只数**她自己那一份**在旧路径上的；从本体借来的不算（那是 `inherited()`）。
        """
        out = []
        for kind in _LAYOUT:
            hit = self._local(kind)
            if hit is None:
                continue
            if self.home not in hit.parents:
                out.append(kind)
        return tuple(out)

    def inherited(self) -> tuple[str, ...]:
        """Kinds she did not write herself: borrowed from the pack of the person she is
        another version of. 「齐了」不等于「写全了」——页面要把这两件事分开说。"""
        if self.inherit is None:
            return ()
        return tuple(
            kind for kind in _LAYOUT if self._local(kind) is None and self.path(kind) is not None
        )

    def missing(self) -> tuple[str, ...]:
        """Kinds with no file at all. `persona`/`qq`/`dm` missing means 「this character
        cannot be played」; a missing `view` only means she has no opinion of the office."""
        return tuple(kind for kind in _LAYOUT if self.path(kind) is None)

    def compact(self) -> dict[str, object]:
        """For a list of twenty-eight: ready or not, and what is still on the old path.
        The full map is one character's business, not the roster's.

        **`ready` 只认她自己写的**（`_local`），不算从本体借来的。一个人设还是抄来的
        十岁版不该显示「齐了」——那会让人以为她已经有自己的东西了。继承来的另列在
        `inherited`；`missing` / `unwritten` 分别是「哪都没有」和「她自己还没写的必填」。

        **`inherited_ready`** 是「含继承能不能演」——派生角色继承齐了就该放行，`web_compat`
        换形态时读这个，而不是 `ready`（读 `ready` 会把继承齐了的十岁版挡在外面）。

        **`using_defaults`** 是「她在用一个不属于她自己的默认」的那几项（`night` /
        `refusals` / `interests`）——自己没写、也没继承到，通道静默顶上了内置默认。和
        `missing` 不同：`missing` 是「哪都没有」，`using_defaults` 是「有默认顶着她不崩，
        但那不是她写的」。启动日志据此说清「这个 23 点是通道默认，不是她写的」。
        """
        return {
            "home": self.home.relative_to(self.root).as_posix() if self.home.is_dir() else "",
            "ready": all(self._local(kind) is not None for kind in REQUIRED),
            "inherited_ready": all(self.path(kind) is not None for kind in REQUIRED),
            "present": [kind for kind in _LAYOUT if self._local(kind) is not None],
            "inherited": list(self.inherited()),
            "missing": list(self.missing()),
            "unwritten": [kind for kind in REQUIRED if self._local(kind) is None],
            "using_defaults": [kind for kind in _DEFAULTED if self.path(kind) is None],
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
            "inherited": list(self.inherited()),
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
