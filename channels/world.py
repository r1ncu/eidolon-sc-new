"""What she knows about the office, in three sizes.

She has to recognise twenty-seven colleagues, the office staff, and a handful of things that
are true about her work rather than about her life. Putting all of that in the prompt every
turn would cost more than it is worth; putting none of it in means she misnames people in
front of fifty witnesses. So it is three layers:

    office.md                 everyone, one line each      every turn, cached with the sheet
    idols/<id>.md             one colleague, in detail     when that name is said
    meta.md                   work, events, the people who make them   when that is asked about

and beside each of those a second line that is *hers*, not the world's:

    packs/<id>/view.md        how she calls them, who she is close to   with `office.md`
    packs/<id>/world.md       how she talks about the work itself       with `meta.md`

Facts and her reading of them are injected together, on purpose. Only the facts and she is a
narrator reading a wiki; only her reading and she has nothing to stand on. The pack is per
character, so the second line is the part that changes when somebody else is played — see
`plugins/packs.py`.

The middle layer is looked up by name, not by meaning: a deterministic match on the names in
`characters.toml`. That is deliberate. An embedding lookup can return 三峰結華 for a question
about 幽谷霧子, and being confidently wrong about a colleague is worse than having only the
one-line version. Missing a lookup is safe; a false one is not.

Everything here is read through `core.live`, so these files are editable while she is
running: the roster is re-read on the next turn, and one colleague's file the next time that
name comes up. `core.live` is what makes that safe — a file that is empty or mid-write is
ignored and the previous version is kept, so an editor's autosave cannot blank her memory of
the office for one turn.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

from core.live import Live
from core.prompt import Section, Slot

META_WORDS = (
    # 作品本身：活动、发行、制作方。命中才注入，未命中的话题不带这层。
    "生放送",
    "高山",
    "アニメ",
    "动画",
    "声優",
    "声优",
    "ゲーム",
    "游戏",
    "シングル",
    "单曲",
    "アルバム",
    "专辑",
    "ライブ",
    "演唱会",
    "イベント",
    "活动",
    "コラボ",
    "联动",
    "開発",
    "开发",
    "中の人",
    "シャニマス",
    "シャイニーカラーズ",
    "ブランド",
)

MAX_IDOL = 1600
"""Characters of one colleague's file that may enter a turn. Longer files are cut, not
summarised: a summary written by this file would be a second author of her memory of them."""

MAX_META = 2200

_NOTE = re.compile(r"<!--.*?-->", re.S)
"""文件里的注释是写给改文件的人看的，不是给她看的。

这些层是**整段注入**的：一份 40 行的作品笔记，注释多的时候能有十几行进提示词，而它
一个字的信息量都没有。以前没人管是因为注释少；现在「共享层 + 视角层」成对进来，两份
都带注释就翻倍，所以在入口统一去掉。"""


def _note_free(text: str) -> str:
    """去注释，并收掉注释留下的空行：三个空行和零个空行一样，都不该占提示词。"""
    rough = _NOTE.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", rough).strip()


MAX_HITS = 2
"""Two colleagues in one message is already a strange message; more is a list, not a question."""


class World:
    def __init__(
        self,
        characters: str | Path,
        *,
        config: str | Path | None = None,
        meta: bool = True,
        limit: int = MAX_HITS,
        settle: float = 0.35,
        log: Any | None = None,
        work: Any | None = None,
        pack: Any | None = None,
    ) -> None:
        self._root = Path(characters)
        self._world = self._root / "world"
        self._meta_on = meta
        self._limit = limit
        self._log = log or (lambda line: None)
        self._pack = pack
        """`pack(character_id) -> Pack` 或 None。有它才读得到 `packs/<id>/view.md`
        那一条她自己对世界的看法；没有它，行为和以前一模一样（只有事实）。"""
        self._settle = settle
        self._words = tuple(getattr(work, "meta_words", ()) or META_WORDS)
        """作品话题词：优先她那一个作品的 `works/<id>.toml`，没有就用这份默认表。"""
        self._office = Live(self._world / "office.md", settle=settle, log=self._log)
        self._meta = Live(self._world / "meta.md", settle=settle, log=self._log) if meta else None
        self._world_fact = Live(self._world / "work.md", settle=settle, log=self._log)
        self._extras: dict[str, tuple[Live | None, Live | None]] = {}
        """每人一对：view（她怎么看同事）/ work（她怎么看作品）。惰性建，按角色缓存。"""
        self._names = Live(
            self._root / (Path(config).name if config else "characters.toml"),
            settle=settle,
            log=self._log,
        )
        self._aliases: dict[str, tuple[str, ...]] = {}
        self._aliases_stamp: tuple = ()
        self._idols: dict[str, Live] = {}

    def refresh(self) -> None:
        """Re-read the roster, the work notes and every colleague now."""
        self._office.refresh(force=True)
        if self._meta is not None:
            self._meta.refresh(force=True)
        self._world_fact.refresh(force=True)
        self._names.refresh(force=True)
        for file in self._idols.values():
            file.refresh(force=True)
        for view, about in self._extras.values():
            view.refresh(force=True)
            about.refresh(force=True)
        self._aliases_stamp = ()

    def stamp(self, character_id: str = "") -> tuple:
        """Changes when a file that feeds the *persona* changes. Colleagues' files are not in
        it: they arrive as turn sections, which are rebuilt every turn anyway.

        `character_id` matters because 「她怎么看这些人」 是 persona 的一半：换人必须换提示词，
        改她的 view 也必须换。给空串时只算共享那几份（测试和没有角色在场的调用）。"""
        view, about = self._pair(character_id)
        return (
            self._office.stamp(),
            self._meta.stamp() if self._meta else (0, 0),
            self._world_fact.stamp(),
            self._names.stamp(),
            view.stamp() if view is not None else (0, 0),
            about.stamp() if about is not None else (0, 0),
        )

    @property
    def ready(self) -> bool:
        """Whether the roster file was found. Without it she still talks, from her sheet."""
        return bool(self._office.text())

    def persona(self, character_id: str = "") -> Section | None:
        """The one-line-each roster **and her reading of it**, for the engine she builds.

        One section, not two: both halves go in the persona slot, which is cached with the
        sheet — and the pair is what makes her recognise a colleague *the way she would*
        (「あさひちゃん」, not 「芹沢あさひ」). Without a pack this is exactly what it was.
        """
        roster = _note_free(self._office.text())
        view = self._her(character_id, "view")
        body = "\n\n".join(part for part in (roster, view) if part)
        if not body:
            return None
        return Section(Slot.PERSONA, body, order=10, title="world:office")

    def lookup(self, text: str, *, speaking: str = "") -> list[Section]:
        """Colleagues named in the message, in the order they are named.

        `speaking` is who is answering, so that a message mentioning her own name does not
        drag her own colleague file into the turn. Empty means "nobody in particular": the
        caller that knows the character always passes it.
        """
        said = (text or "").casefold()
        if not said.strip():
            return []
        hits: list[tuple[int, str]] = []
        for character_id, aliases in self._alias_table().items():
            if character_id == speaking:
                continue
            spot = min((said.find(alias) for alias in aliases if alias and alias in said), default=-1)
            if spot >= 0:
                hits.append((spot, character_id))
        sections: list[Section] = []
        for _, character_id in sorted(hits)[: self._limit]:
            detail = self._idol(character_id)
            if detail:
                sections.append(
                    Section(Slot.TURN, detail[:MAX_IDOL], title=f"world:idol:{character_id}")
                )
        return sections

    def knowledge(self, text: str, character_id: str = "") -> list[Section]:
        """The work itself — releases, events, the people who make them — and, beside it,
        what she makes of it.

        `world/work.md` is the shared fact file; `world/meta.md` is the older name for the
        same thing and still answers when the new file is not there yet. Her half comes from
        `packs/<id>/world.md` and is added only when there *is* one: an empty second section
        would read as 「她对这件事没有看法」, which is a different statement from 「没写」.
        """
        if not self._meta_on:
            return []
        fact = self._work_text()
        view = self._her(character_id, "world")
        if not fact and not view:
            return []
        said = (text or "").casefold()
        if not any(word.casefold() in said for word in self._words):
            return []
        sections: list[Section] = []
        if fact:
            sections.append(Section(Slot.TURN, fact[:MAX_META], title="world:work"))
        if view:
            sections.append(
                Section(Slot.TURN, view[:MAX_META], title=f"world:view:{character_id}")
            )
        return sections

    # ---- the two halves of one person

    def _work_text(self) -> str:
        """`world/work.md` first, then the file it replaced."""
        fresh = _note_free(self._world_fact.text())
        if fresh:
            return fresh
        return _note_free(self._meta.text() if self._meta is not None else "")

    def _pair(self, character_id: str) -> tuple[Live | None, Live | None]:
        """Her view of the office and of the work, lazily, one pair per character."""
        if not character_id or self._pack is None:
            return None, None
        found = self._extras.get(character_id)
        if found is None:
            pack = self._pack(character_id)
            view = pack.path("view") if pack is not None else None
            about = pack.path("world") if pack is not None else None
            found = (
                Live(view, settle=self._settle, log=self._log) if view else None,
                Live(about, settle=self._settle, log=self._log) if about else None,
            )
            self._extras[character_id] = found
        return found

    def _her(self, character_id: str, kind: str) -> str:
        view, about = self._pair(character_id)
        file = view if kind == "view" else about
        return _note_free(file.text()) if file is not None else ""

    def _idol(self, character_id: str) -> str:
        file = self._idols.get(character_id)
        if file is None:
            file = Live(
                self._world / "idols" / f"{character_id}.md", settle=0.0, log=self._log
            )
            self._idols[character_id] = file
        return _note_free(file.text())

    def _alias_table(self) -> dict[str, tuple[str, ...]]:
        """The names in `characters.toml`, re-parsed when the file changes."""
        stamp = self._names.stamp()
        if stamp != self._aliases_stamp:
            self._aliases = _parse_aliases(self._names.text())
            self._aliases_stamp = stamp
        return self._aliases


def _parse_aliases(text: str) -> dict[str, tuple[str, ...]]:
    """Every way a name is written, per character, lower-cased.

    Full names only. A given name alone (「透」「凛世」) is left out on purpose: in Chinese
    prose those characters appear inside ordinary words, and a wrong match would put another
    idol's history into a turn about something else entirely.
    """
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for character in data.get("characters", []):
        character_id = str(character.get("id") or "")
        if not character_id:
            continue
        names = [
            character.get("name"),
            character.get("name_zh"),
            character.get("name_romaji"),
            character.get("name_kana"),
            character_id,
        ]
        out[character_id] = tuple(
            str(name).casefold().replace(" ", "") for name in names if name
        )
    return out



