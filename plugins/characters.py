"""Who she is, and who else you could be talking to.

The roster is data, not code: `characters.toml` beside a `sheets/` directory of Markdown,
the same shape the deployment this replaces already has, so moving to `scbot-new` is
pointing it at the directory rather than exporting anything. A character is her name, her
colour, her avatar, her sheet — the kernel only ever needs the sheet text; everything else
is for the page.

The plugin owns the roster because the roster is a surface: a client asks
`GET /api/characters`, gets what it may draw, and the turn loop stays ignorant of the fact
that there are twenty-eight of them or that a colour exists. Without this plugin the
server still talks to one character, from `SCCORE_CHARACTERS`' first `*.md`.
"""

import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.plugin import Plugin, Route

from . import packs as pack_file

SHEETS = "sheets"
FALLBACK_COLOR = "#8ab4f8"

# 组合（原版 backend/src/eidolon/world.py 的 World.units：官方顺序、组名和平文名读音）。
# 对话列表的筛选面板按它分组，搜索也拿组名和组读音当候选。按角色 id 归属，不按名字——
# 这台部署没有剧情库，World 的那套推导在这里是一张表。
UNITS_FILE = "units.toml"
"""Where her unit lives: `<characters>/units.toml`, next to the roster.

Deliberately **not** a constant in this module. A table of unit names and member ids is
somebody's setting, and a kernel that ships with one baked in is a kernel every other
deployment has to edit out. Without the file every character's `unit` is None: the filter
panel groups nothing, the rest works. See `characters/units.example.toml`.
"""


def _read_units(root: Path) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """The unit table, or nothing. A missing or broken file is not an error — it is a
    deployment that did not write one, and every character simply has no unit."""
    path = root / UNITS_FILE
    if not path.exists():
        return ()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for entry in data.get("units", ()):
        name = str(entry.get("name") or "").strip()
        members = tuple(
            str(one).strip() for one in entry.get("members", ()) if str(one).strip()
        )
        if name and members:
            out.append((name, str(entry.get("reading") or "").strip(), members))
    return tuple(out)


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    name_zh: str
    intro: str
    color: str
    sheet: str
    """The sheet's text, not its path: read once at startup, as upstream does."""
    avatar: Path | None = None
    scsp_avatar: Path | None = None
    background: Path | None = None
    """对话页的壁纸（SCSP 那张）；没有就是没有，页面自己退成印象色。"""
    call_background: Path | None = None
    """通话层单独用的底图。上游没有这个字段，通话层直接铺 `background`；
    这里留一个，是为了让「聊天用 SCSP 那张、通话用另一张」成为可能。
    没配时各处一律回退 `background`（见 `call_art`），行为和上游一致。"""
    voice_id: str = ""
    name_kana: str = ""
    name_romaji: str = ""
    work: str = ""
    """Which work she is from（`characters/works/<id>.toml` 或 `work.toml` 的 id）。

    空字符串是「没人写」：这种角色归到部署自己的那个作品名下，所以单作品的部署永远
    只有一组。写错了 id 也不会把角色弄丢——页面会单开一组，标题就是那个 id。"""
    unit: str | None = None
    """Her unit's name; None where the table above does not place her."""
    unit_reading: str | None = None
    unit_order: int | None = None
    """Where her unit is in the official order; None without one."""
    liked_gifts: tuple[str, ...] = ()
    """The gifts she likes, by identifier in the gift catalog; giving her one raises
    affinity more. Never told to the user."""

    adult: bool | None = None
    """Has she cleared the line? 高中生及以上 = true，初中生・小学生 = false。

    `None` is 「nobody wrote it down」 — **not** 「no」. 这条线是**学段**不是民法成年：
    15 岁既可能是初三也可能是高一，光看 `age` 分不出来，所以这里一个个填。
    The policy for a missing answer (safe by default = treat as underage) belongs to the
    channel that needs it, not to the roster. See `channels/romance.py::grown`."""
    age: int | None = None
    """Her age as the character is written, not as of today. The setting's age does not move
    with the calendar, and neither should this."""
    plove: int = 3
    """0–5: how far she would ever go toward her producer.

    0 is not 「hard」 but 「there is no such line」 — see `channels/romance.py`. 3 is the
    neutral default for a character nobody has judged."""
    heavy: int = 3
    """1–5 of 「重」: how much weight she puts on him — strength only, never direction.

    A 5 is 「把你照顾得无微不至」 or 「两小时没回就发十条」 depending on who she is; that
    difference is written in her own file, not here."""

    derived_from: str = ""
    """She is another age of a character that already exists（`derived_from = "luna"`）。

    **另一个人，不是同一份数据的另一个皮肤。** 好感、睡眠账本、记忆目录都以自己的 id 分开
    存放；全局那些「给主角色的」设定不套在她身上——`QQ_OWNER_AFFINITY` 的地板、`tease`
    （夹子）、`tiers`（营业声线）都属于成年那条线，派生角色一条都不继承。规则写在
    `channels/qq.py`，这里只记「她是从谁来的」。"""

    def brief(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "name_zh": self.name_zh,
            "name_kana": self.name_kana,
            "name_romaji": self.name_romaji,
            "intro": self.intro,
            "color": self.color,
            "avatar": bool(self.avatar),
            "scsp_avatar": bool(self.scsp_avatar),
            "background": bool(self.background),
            "call_background": bool(self.call_background),
            "unit": self.unit,
            "unit_reading": self.unit_reading,
            "unit_order": self.unit_order,
            "work": self.work,
            "derived_from": self.derived_from,
        }


class Characters(Plugin):
    name = "characters"

    def __init__(self, directory: str | Path, *, config: str = "characters.toml") -> None:
        self._root = Path(directory)
        self._config = self._root / config
        self._units = _read_units(self._root)
        # 先读作品，再读角色：角色表里的 `work` 是相对它说的。
        # 作品是平级的：`characters/works/<id>.toml`，谁都不比谁「主」。
        self._works = pack_file.load_works(self._root)
        self._sole_work = (
            next(iter(self._works.values())) if len(self._works) == 1 else None
        )
        # 只有「整台部署只有一个作品」时，没写 work 的人才有唯一答案；有多个作品时留空，
        # 页面单列一组「未指定」——不能默认塞进某一个作品，那等于又造了一个主作品。
        default_work = self._sole_work.id if self._sole_work is not None else ""
        self._all: tuple[Character, ...] = _load(
            self._root, self._config, self._units, default_work
        )
        if not self._all:
            raise RuntimeError(
                f"{self._root} 里没有角色。先建一个角色表：\n"
                f"  cp {self._root}/characters.example.toml {self._root}/characters.toml\n"
                "然后把它改成你自己的人——字段说明见 characters/README.md。"
            )
        self._by_id = {character.id: character for character in self._all}
        self._paths = _sheet_paths(self._root, self._config)
        self._stamp = self._signature()
        # 两条线：作品（共享）与角色包（每人一份）。包只是路径，读不读由用的人决定。
        # 包读哪一份作品，看角色表里她写的是哪一个——不是所有角色都演同一个作品。
        self._packs = {
            character.id: pack_file.Pack(self._root, character.id, self._work_of(character))
            for character in self._all
        }
        # 派生角色（`derived_from`）：自己的包里没写的文件从本体那一份拿。
        # 这样加一个 if 线只要写差异（人设/语气里不一样的那几行），不用复制一整套。
        for character in self._all:
            parent = character.derived_from
            if not parent or parent not in self._packs:
                continue
            mine = self._packs[character.id]
            self._packs[character.id] = pack_file.Pack(
                self._root,
                character.id,
                mine.work,
                declared=mine.declared,
                inherit=self._packs[parent].home,
                inherit_id=parent,
            )
        self._adopt_packs()

    @property
    def work(self) -> pack_file.Work:
        """给「不针对某一位角色」的提示词用的作品（判官开场、记忆抽取）。

        整台部署只有一个作品时，它就是那一个；有多个作品时返回一个空的 `Work`——
        这时候没有「部署的作品」这回事，让措辞退回中性，比随便挑一个当代表更诚实。
        针对具体角色的地方一律走 `work_of(character_id)`。"""
        return self._sole_work or pack_file.Work()

    def _work_of(self, character: Character) -> pack_file.Work:
        found = self._works.get(character.work)
        if found is not None:
            return found
        # 写错了 id（或者根本没写、又有多个作品）：按她写的那个 id 建一个空作品，
        # 页面就会单独显示一组、标题就是那个 id——写错要看得见。
        return pack_file.Work(id=character.work)

    def work_of(self, character_id: str) -> pack_file.Work:
        """Her work, by id. Unknown ids get an empty work rather than somebody else's."""
        character = self._by_id.get(character_id)
        return self._work_of(character) if character is not None else pack_file.Work()

    def _adopt_packs(self) -> None:
        """A `packs/<id>/persona.md` wins over `sheets/<id>.md`, and nothing else changes.

        Only the sheet is taken here: the roster is read before the packs exist, and a
        character whose persona lives in the new place must not be loaded as 「（这个人设还
        没有写。）」 just because the old path is empty."""
        from dataclasses import replace

        adopted = tuple(
            replace(
                character,
                sheet=self._packs[character.id].text("persona") or character.sheet,
                # 壁纸也先看角色包：`packs/<id>/assets/background.*` 赢过表里的路径。
                background=self._packs[character.id].path("background") or character.background,
                call_background=(
                    self._packs[character.id].path("call-background") or character.call_background
                ),
            )
            for character in self._all
        )
        self._all = adopted
        self._by_id = {character.id: character for character in adopted}

    # ---- keeping up with edits on disk

    def stale(self) -> bool:
        """Whether a sheet or the roster table changed since it was last read."""
        return self._signature() != self._stamp

    def reload(self) -> bool:
        """Re-read everything, and keep the old roster if the new one is unusable.

        A half-saved `characters.toml` parses as nothing, and dropping every character
        because of one bad edit would take the page down. So a failed or empty load is
        refused, and the previous roster stays in place until the file is valid again.
        """
        try:
            works = pack_file.load_works(self._root)
            sole = next(iter(works.values())) if len(works) == 1 else None
            default = sole.id if sole is not None else ""
            found = _load(self._root, self._config, self._units, default)
        except Exception:
            return False
        if not found:
            return False
        self._works = works
        self._sole_work = sole
        self._all = found
        self._by_id = {character.id: character for character in found}
        self._paths = _sheet_paths(self._root, self._config)
        self._stamp = self._signature()
        self._packs = {
            character.id: pack_file.Pack(self._root, character.id, self._work_of(character))
            for character in found
        }
        return True

    def _signature(self) -> tuple:
        out: list[tuple[str, int, int]] = []
        # 作品文件也算进来：加一个 `works/<id>.toml`、或者给角色填了 `work`，不用重启。
        work_files = [self._root / pack_file.WORK_FILE]
        folder = self._root / pack_file.WORKS_DIR
        if folder.is_dir():
            work_files.extend(sorted(folder.glob("*.toml")))
        for path in (self._config, self._root / UNITS_FILE, *self._paths.values(), *work_files):
            try:
                info = path.stat()
                out.append((str(path), info.st_mtime_ns, info.st_size))
            except OSError:
                out.append((str(path), 0, 0))
        return tuple(out)

    # ---- what the page and the turn loop ask for

    @property
    def roster(self) -> tuple[Character, ...]:
        return self._all

    def character(self, character_id: str | None = None) -> Character:
        """One of them; the first when nobody said which, so a bare `GET /api/state`
        still answers."""
        if character_id:
            found = self._by_id.get(character_id)
            if found is None:
                raise KeyError(character_id)
            return found
        return self._all[0]

    def avatar(self, character_id: str, *, scsp: bool = False) -> Path | None:
        if character_id not in self._by_id:
            return None
        character = self._by_id[character_id]
        if scsp:
            return character.scsp_avatar or character.avatar
        return character.avatar

    def background(self, character_id: str) -> Path | None:
        """The chat page's wallpaper, or None — the page then keeps her tint."""
        character = self._by_id.get(character_id)
        return character.background if character else None

    def call_background(self, character_id: str) -> Path | None:
        """The call layer's own backdrop, or None when she does not have one."""
        character = self._by_id.get(character_id)
        return character.call_background if character else None

    def call_art(self, character_id: str) -> Path | None:
        """What the call layer actually lays down: her call backdrop, else her chat wallpaper.

        One place for the fallback, so the page and the interface cannot disagree about
        which picture a call shows."""
        return self.call_background(character_id) or self.background(character_id)

    def same_person(self, one: str, other: str) -> bool:
        """这两个 id 是不是同一个人：她自己，或者她的另一个版本（`derived_from` 互相指向）。

        记忆是按 id 分开写的，但「十岁的她」和「十九岁的她」是同一个人——她读到自己
        另一条人生的记忆时，不该被当成「听别人说的」。
        """
        if one == other:
            return True
        here, there = self._by_id.get(one), self._by_id.get(other)
        if here is None or there is None:
            return False
        return here.derived_from == other or there.derived_from == one

    def family(self, character_id: str) -> str:
        """她的好感记在谁名下：本体，还是她自己。

        同一个人的几个形态（`derived_from`）共用一本账——换个年纪不等于换一段关系，
        「十岁的她」不该从零开始认识他。所以派生角色记到本体名下，本体记在自己名下。
        链子（A 派生自 B、B 又派生自 C）一路按到底，遇到环就停在原处，不会转圈。

        这是**好感**的规矩，不是记忆的：记忆按 id 分开写（`same_person` 只用来判断
        「另一条人生的话是梦，不是听别人说的」）。两者故意不一样，别合并。
        """
        seen: set[str] = set()
        current = str(character_id or "")
        while current and current not in seen:
            seen.add(current)
            above = str(getattr(self._by_id.get(current), "derived_from", "") or "")
            if not above:
                return current
            current = above
        return str(character_id or "")

    def other_versions(self, character_id: str) -> tuple[Character, ...]:
        """她的其他版本：她派生出来的那些，加上她所派生的那一位。

        写进人设的那条规则只跟「有没有另一个自己」有关，跟是本体还是分支无关。
        """
        character = self._by_id.get(character_id)
        if character is None:
            return ()
        found = [one for one in self._all if one.derived_from == character_id]
        parent = self._by_id.get(character.derived_from) if character.derived_from else None
        if parent is not None and parent.derived_from != character_id:
            found.append(parent)
        return tuple(found)

    def name_of(self, character_id: str) -> str | None:
        """Her display name, or None for an identifier this deployment does not know —
        what a memory told to a character no longer here is labelled with."""
        character = self._by_id.get(character_id)
        return character.name if character else None

    def pack(self, character_id: str) -> pack_file.Pack:
        """One character's map of files. Unknown ids get an empty map rather than a raise:
        a memory left behind by a character who is no longer here is still shown on the
        page, and it should read as 「no files」, not as an error."""
        found = self._packs.get(character_id)
        if found is None:
            return pack_file.Pack(self._root, character_id, self.work)
        return found

    def card_tails(self, character_id: str) -> tuple[str, ...]:
        """What to strip off the end of a card title — her name, in the spellings her pack
        says. Empty when nothing is known: never somebody else's name."""
        character = self._by_id.get(character_id)
        if character is None:
            return ()
        return pack_file.card_tails(character, self.pack(character_id))

    def tell(self, character: Character) -> dict[str, object]:
        """One character, as the page sees them: the roster fields plus where her files came
        from. The page shows the second half in the character sheet — 「这份人设读的是哪个
        文件、还有什么是旧的」 is the first question when a character sounds off."""
        return {**character.brief(), "pack": self.pack(character.id).compact()}

    def work_groups(self) -> tuple[dict[str, object], ...]:
        """作品 → 它的角色，按角色表里第一次出现的顺序。

        作品是平级的：页面照这个分组画，一个作品一组，谁也不排在「主」的位置上。
        角色写了不存在的 id 也单开一组、标题就是那个 id；多个作品时没写 work 的人
        落在 `id` 为空的那一组，页面显示成「未指定作品」——写漏了要看得见。"""
        groups: dict[str, list[Character]] = {}
        for character in self._all:
            groups.setdefault(character.work, []).append(character)
        out: list[dict[str, object]] = []
        for work_id, members in groups.items():
            work = self._works.get(work_id) or pack_file.Work(id=work_id)
            out.append(
                {
                    **work.as_dict(),
                    # 分组的键就是页面用的 id：没写 work 时是空串，写错了就是那个 id。
                    "id": work_id,
                    "characters": [
                        {
                            "id": one.id,
                            "name": one.name,
                            # 她是哪一位的另一条人生；页面照这个把人收进本体那一行。
                            "derived_from": one.derived_from or None,
                            "pack": self.pack(one.id).compact(),
                        }
                        for one in members
                    ],
                }
            )
        return tuple(out)

    def capabilities(self) -> dict[str, object]:
        return {
            "count": len(self._all),
            "default": self._all[0].id,
            "colors": True,
            "work": self.work.as_dict(),
        }

    def routes(self) -> dict[tuple[str, str], Route]:
        async def characters(query, body):  # noqa: ANN001 - the shape is in plugin.Route
            return {
                "characters": [self.tell(character) for character in self._all],
                "work": self.work.as_dict(),
            }

        async def character(query, body):  # noqa: ANN001
            try:
                found = self.character(_q(query, "c") or None)
            except KeyError:
                return {"error": "没有这个角色"}
            return {**self.tell(found), "files": self.pack(found.id).summary()}

        async def work(query, body):  # noqa: ANN001
            return self.work.as_dict()

        return {
            ("GET", "/api/characters"): characters,
            ("GET", "/api/character"): character,
            ("GET", "/api/work"): work,
        }


def _q(query, name: str) -> str:  # noqa: ANN001 - a flat dict of first values
    return str(query.get(name, ""))


def _load(
    root: Path,
    config: Path,
    units: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    work_id: str = "",
) -> tuple[Character, ...]:
    if config.exists():
        with config.open("rb") as file:
            data = tomllib.load(file)
        return tuple(
            _from_entry(root, entry, units, work_id)
            for entry in data.get("characters", ())
            if str(entry.get("id") or "").strip()
        )
    return _from_sheets(root, work_id)


def _from_entry(
    root: Path,
    entry: dict,
    units: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
    work_id: str = "",
) -> Character:
    """One `[[characters]]` block. Paths in the file are relative to the file, and a
    missing avatar or sheet is a fact rather than an error: a character without a picture
    is a character without a picture."""
    sheet_path = _path(root, entry.get("sheet"))
    sheet = _text(sheet_path) if sheet_path else ""
    if not sheet:
        sheet = _text(root / SHEETS / f"{entry['id']}.md")
    if not sheet:
        sheet = f"# 人设：{entry.get('name', entry['id'])}\n\n（这个人设还没有写。）\n"
    avatar = _path(root, entry.get("avatar"))
    scsp = _path(root, entry.get("scsp_avatar"))
    wallpaper = _path(root, entry.get("background"))
    call_wallpaper = _path(root, entry.get("call_background"))
    character_id = str(entry["id"])
    unit, reading, order = _unit_of(character_id, units)
    return Character(
        id=character_id,
        name=str(entry.get("name") or entry["id"]),
        name_zh=str(entry.get("name_zh") or ""),
        name_kana=str(entry.get("name_kana") or ""),
        name_romaji=str(entry.get("name_romaji") or ""),
        intro=str(entry.get("intro") or ""),
        color=_color(str(entry.get("color") or "")),
        sheet=sheet,
        avatar=avatar if avatar and avatar.exists() else None,
        scsp_avatar=scsp if scsp and scsp.exists() else None,
        background=wallpaper if wallpaper and wallpaper.exists() else None,
        call_background=(
            call_wallpaper if call_wallpaper and call_wallpaper.exists() else None
        ),
        voice_id=str(entry.get("voice_id") or ""),
        work=str(entry.get("work") or "").strip() or work_id,
        unit=unit,
        unit_reading=reading,
        unit_order=order,
        liked_gifts=_liked(entry.get("liked_gifts")),
        adult=_flag(entry.get("adult")),
        age=_int_or_none(entry.get("age")),
        plove=_level(entry.get("plove"), 3, low=0, high=5),
        heavy=_level(entry.get("heavy"), 3, low=1, high=5),
        derived_from=str(entry.get("derived_from") or "").strip(),
    )


def _flag(value: object) -> bool | None:
    """`true` / `false` / absent. A typo is 「nobody wrote it down」, not a silent `false`."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "1", "adult", "成年"):
            return True
        if text in ("false", "no", "0", "minor", "未成年"):
            return False
    return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _level(value: object, default: int, *, low: int, high: int) -> int:
    """A 0–5 / 1–5 grade. Out of range is clamped rather than fatal: a wrong number in the
    roster must not stop a character from talking, and the honest fallback for 「nonsense」
    is the neutral middle."""
    number = _int_or_none(value)
    if number is None:
        return default
    return min(max(number, low), high)


def _liked(value: object) -> tuple[str, ...]:
    """`liked_gifts` from the configuration: a list of catalog identifiers, at most
    `MAX_LIKED` of them, duplicates dropped. Anything else is a configuration mistake the
    gift catalog reports, not a reason to fail here."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return tuple(out[:3])


def _unit_of(
    character_id: str,
    units: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
) -> tuple[str | None, str | None, int | None]:
    """Her unit's name, its reading in hiragana, and where it is in the declared order;
    all None where the deployment's `units.toml` does not place her."""
    for order, (name, reading, members) in enumerate(units):
        if character_id in members:
            return name, reading, order
    return None, None, None


def _sheet_paths(root: Path, config: Path) -> dict[str, Path]:
    """Where each character's sheet lives, so a change can be noticed without re-reading
    every file. Mirrors the fallback order in `_from_entry`: the path in the table, else
    `sheets/<id>.md`, else the flat `*.md` this directory happens to have."""
    paths: dict[str, Path] = {}
    if config.exists():
        try:
            with config.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return paths
        for entry in data.get("characters", ()):
            character_id = str(entry.get("id") or "").strip()
            if not character_id:
                continue
            found = _path(root, entry.get("sheet")) or (root / SHEETS / f"{character_id}.md")
            paths[character_id] = found
        return paths
    for file in sorted(root.glob("*.md")) or sorted((root / SHEETS).glob("*.md")):
        paths[file.stem] = file
    return paths


_NOT_A_CHARACTER = re.compile(
    r"^(readme|changelog|contributing|license|index|example.*|.*\\.example)$", re.I
)
"""Files that sit next to the sheets but are not one.

A directory that ships with a `README.md` used to wake up with a character called 「README」:
a bare deployment looked like it had somebody in it, and the 「no characters」 error — the one
that tells you to copy the example — never fired."""

def _from_sheets(root: Path, work_id: str = "") -> tuple[Character, ...]:
    """No `characters.toml`: every `*.md` is a character, named by its heading. This is
    what a bare directory — a test, a demo, a half-copied deployment — still gives you."""
    files = [
        file
        for file in (sorted(root.glob("*.md")) or sorted((root / SHEETS).glob("*.md")))
        if not _NOT_A_CHARACTER.match(file.stem) and not file.name.startswith(".")
    ]
    return tuple(
        Character(
            id=file.stem,
            name=_name_of(_text(file)) or file.stem,
            name_zh="",
            intro="",
            color=FALLBACK_COLOR,
            sheet=_text(file),
            work=work_id,
        )
        for file in files
        if _text(file).strip()
    )


def _path(root: Path, value: object) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else (root / path)


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _name_of(sheet: str) -> str:
    for line in sheet.splitlines():
        if line.startswith("# 人设："):
            return line[len("# 人设：") :].strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _color(value: str) -> str:
    """`#rrggbb` or the fallback: a colour from the file reaches CSS, so nothing else is
    allowed through."""
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
        except ValueError:
            return FALLBACK_COLOR
        return value.lower()
    return FALLBACK_COLOR


def load_problems(characters: Sequence[Character]) -> list[str]:
    """What a deployment would want to know at startup. Kept tiny on purpose: a sheet that
    is missing is the only thing that changes behaviour."""
    return [character.id for character in characters if not character.sheet.strip()]
