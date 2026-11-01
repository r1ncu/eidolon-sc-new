"""夹子的逐句许可 —— 而且**只属于写了这份数据的角色**。

额度（通道数）和许可（判官判）必须都对上才给那一句；但最要紧的一条是：
记号、窗口长度、那句话本身，全都读自 `packs/<id>/voice/tease.toml`。
没有这份文件的角色，这个功能整体不存在——不能把冬優子的招牌戴到别人脸上。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from channels import stickers
from core.messages import Conversation
from core.plugin import TurnContext
from core.store import Store
from plugins.packs import Pack, tease_rule
from plugins.tease import DEFAULT_WINDOW, Tease, flag_key, marks_in, room_of, window_of

NOW = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)

HERS = """\
[tease]
marks = ["♡", "♥", "❤"]
window = 10
hint = "【夹子·这一句】他在逗你——这一句可以夹，说完下一句照旧嫌他。"
"""


def characters(root: Path, *, which: str = "hers") -> Path:
    """一个角色目录：`hers` 有这份规则，`other` 没有。"""
    home = root / "characters"
    (home / "sheets").mkdir(parents=True, exist_ok=True)
    (home / "characters.toml").write_text(
        '[[characters]]\nid = "hers"\nname = "她"\n\n'
        '[[characters]]\nid = "other"\nname = "别人"\n',
        encoding="utf-8",
    )
    pack = home / "packs" / which / "voice"
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "tease.toml").write_text(HERS, encoding="utf-8")
    return home


class TestTheRuleIsHers(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        characters(self.root)

    def pack(self, character_id: str) -> Pack:
        return Pack(self.root / "characters", character_id)

    def test_the_rule_comes_from_her_folder(self):
        rule = tease_rule(self.pack("hers"))
        self.assertTrue(rule.on)
        self.assertEqual(rule.marks, ("♡", "♥", "❤"))
        self.assertEqual(rule.window, 10)
        self.assertIn("这一句可以夹", rule.hint)

    def test_a_character_without_it_has_no_rule(self):
        """没有这份文件 = 这个角色没有「夹」这回事，而不是退回通用写法。"""
        rule = tease_rule(self.pack("other"))
        self.assertFalse(rule.on)
        self.assertEqual(rule.marks, ())
        self.assertEqual(rule.hint, "")

    def test_the_helpers_take_her_numbers(self):
        self.assertEqual(marks_in("……开玩笑的♡", ("♡",)), True)
        self.assertEqual(marks_in("……开玩笑的♡", ()), False, "没有记号就不该数出「夹」")
        self.assertEqual(len(window_of("1" * 30, 4)), 4)
        self.assertEqual(len(window_of("1" * 30)), DEFAULT_WINDOW)
        self.assertEqual(room_of("qq:group:100"), "100")
        self.assertEqual(room_of("qq:dm"), "")


class TestThePermissionIsPerSentence(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        characters(self.root)
        self.store = Store(str(self.root / "db.sqlite"))
        self.addCleanup(self.store.close)
        self.store.migrate(stickers.SCHEMA)
        self.said: list[str] = []
        self.tease = Tease(
            self.store,
            tier=lambda *a, **k: "intimate",
            character=lambda: "hers",
            pack=lambda cid: Pack(self.root / "characters", cid),
            log=self.said.append,
        )

    def turn(self, *, surface: str = "group", facts: dict | None = None, title: str | None = None) -> TurnContext:
        return TurnContext(
            conversation=Conversation(
                id="c1", user_id="u1", character_id="hers",
                created_at="2026-10-22T02:00:00+00:00",
                title="qq:group:123456789" if title is None else title,
            ),
            text="こんばんは", now=NOW, facts=dict(facts or {}), surface=surface,
        )

    async def sections(self, **kwargs) -> tuple:
        return await self.tease.prompt_sections(self.turn(**kwargs))

    async def test_teasing_somebody_she_is_close_to_opens_the_door(self):
        got = await self.sections(facts={"tease.want": True})
        self.assertEqual(len(got), 1)
        self.assertIn("这一句可以夹", got[0].text)
        self.assertEqual(got[0].title, "tease")

    async def test_a_sentence_that_is_not_teasing_closes_it(self):
        self.assertEqual(await self.sections(facts={"tease.want": False}), ())
        self.assertEqual(await self.sections(facts={}), (), "判官没说话 = 不给许可")

    async def test_a_character_without_the_rule_never_gets_it(self):
        """最关键的一条：换了角色，判得再准也不给——那份话术是她的，不是通用的。"""
        other = Tease(
            self.store,
            tier=lambda *a, **k: "intimate",
            character=lambda: "other",
            pack=lambda cid: Pack(self.root / "characters", cid),
        )
        self.assertFalse(other.capabilities()["on"])
        self.assertEqual(await other.prompt_sections(self.turn(facts={"tease.want": True})), ())

    async def test_a_private_chat_keeps_its_own_rule(self):
        self.assertEqual(await self.sections(surface="dm", facts={"tease.want": True}), ())
        self.assertEqual(await self.sections(surface="page", facts={"tease.want": True}), ())

    async def test_a_stranger_is_not_teased_in_the_performing_voice(self):
        tease = Tease(
            self.store, tier=lambda *a, **k: "distant", character=lambda: "hers",
            pack=lambda cid: Pack(self.root / "characters", cid),
        )
        self.assertEqual(await tease.prompt_sections(self.turn(facts={"tease.want": True})), ())

    async def test_the_allowance_beats_the_permission(self):
        self.store.execute(
            "INSERT INTO qq_flags (key, value, updated_at) VALUES (?, ?, ?)",
            (flag_key("123456789"), "0000000001", NOW.isoformat()),
        )
        self.assertEqual(await self.sections(facts={"tease.want": True}), ())

    async def test_a_conversation_that_is_not_a_room_gets_nothing(self):
        self.assertEqual(await self.sections(title="qq:dm", facts={"tease.want": True}), ())

    def test_the_question_is_one_bool(self):
        questions = self.tease.judge_questions()
        self.assertEqual([q.key for q in questions], ["tease.want"])
        self.assertEqual(questions[0].kind, "bool")
        self.assertIn("逗她", questions[0].ask)

    def test_without_a_flags_table_the_rule_just_does_not_fire(self):
        import asyncio

        bare = Tease(
            Store(":memory:"), tier=lambda *a, **k: "intimate", character=lambda: "hers",
            pack=lambda cid: Pack(self.root / "characters", cid),
        )
        self.assertEqual(asyncio.run(bare.prompt_sections(self.turn(facts={"tease.want": True}))), ())


if __name__ == "__main__":
    unittest.main()
