"""The plugins, one at a time: what each one keeps, what it adds to a turn, what it
refuses, and what it says when it cannot run.

Nothing here touches the network or a key. The judge is `ScriptedJudge`, the model is
`ScriptedLLM`, and the clock is handed in — so every assertion is about behaviour rather
than about the weather.
"""

import base64
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from core.llm import ScriptedLLM
from core.messages import Conversation, Message
from core.plugin import TurnContext
from core.protocol import Segment
from core.store import Store
from plugins.affinity import Affinity
from plugins.characters import Characters
from plugins.gifts import GiftCatalogError, Gifts
from plugins.memory import Memory, NewMemory
from plugins.photos import Photos
from plugins.status import ServiceStatus
from plugins.voice import Voice

NOW = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def conversation() -> Conversation:
    return Conversation(id="c1", user_id="u1", character_id="mano", created_at="2026-10-22T02:00:00+00:00")


def turn(text: str = "こんばんは", facts: dict | None = None, surface: str = "page") -> TurnContext:
    return TurnContext(
        conversation=conversation(), text=text, now=NOW, facts=dict(facts or {}), surface=surface
    )


def reply() -> Message:
    """A stored reply: the observers only run once there is one."""
    return Message(
        id="m1",
        conversation_id="c1",
        role="assistant",
        segments=(Segment("おかえりなさい", "欢迎回来"),),
        created_at="2026-10-22T02:00:05+00:00",
    )


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "db.sqlite")
        self.addCleanup(self.store.close)


class TestCharacters(Base):
    def test_a_toml_roster_carries_name_colour_and_sheet(self):
        characters = self.root / "characters"
        (characters / "sheets").mkdir(parents=True)
        (characters / "sheets" / "mano.md").write_text("# 人设：櫻木真乃\n\n温柔。\n", encoding="utf-8")
        (characters / "avatars").mkdir()
        (characters / "avatars" / "mano.jpg").write_bytes(b"jpeg")
        (characters / "characters.toml").write_text(
            '[[characters]]\nid = "mano"\nname = "櫻木真乃"\nname_zh = "樱木真乃"\n'
            'intro = "illumination STARS 的 center"\ncolor = "#F5A9C0"\n'
            'sheet = "sheets/mano.md"\navatar = "avatars/mano.jpg"\nvoice_id = "v1"\n',
            encoding="utf-8",
        )
        roster = Characters(characters)
        self.assertEqual(len(roster.roster), 1)
        one = roster.character("mano")
        self.assertEqual(one.name, "櫻木真乃")
        self.assertEqual(one.color, "#f5a9c0")
        self.assertIn("温柔", one.sheet)
        self.assertIsNotNone(roster.avatar("mano"))

    def test_without_the_toml_every_sheet_is_a_character(self):
        characters = self.root / "bare"
        characters.mkdir()
        (characters / "a.md").write_text("# 人设：甲\n\n一。\n", encoding="utf-8")
        (characters / "b.md").write_text("# 人设：乙\n\n二。\n", encoding="utf-8")
        roster = Characters(characters)
        self.assertEqual([c.name for c in roster.roster], ["甲", "乙"])  # sorted by file name
        self.assertEqual(roster.character().name, "甲")

    def test_a_character_without_a_picture_is_not_an_error(self):
        characters = self.root / "nopic"
        characters.mkdir()
        (characters / "x.md").write_text("# 人设：丙\n\n三。\n", encoding="utf-8")
        roster = Characters(characters)
        self.assertIsNone(roster.avatar("x"))
        self.assertEqual(roster.character("x").color, "#8ab4f8")

    def test_a_colour_that_is_not_hex_falls_back(self):
        characters = self.root / "bad"
        characters.mkdir()
        (characters / "x.md").write_text("# 人设：丁\n", encoding="utf-8")
        (characters / "characters.toml").write_text(
            '[[characters]]\nid = "x"\nname = "丁"\ncolor = "red; drop table"\n', encoding="utf-8"
        )
        self.assertEqual(Characters(characters).character("x").color, "#8ab4f8")


class TestAStrangerStartsBelowTrust(Base):
    """群里没见过的人，是「疏远」，不是「信任」。

    起点从 50（信任）挪到 20（疏远）之前，任何一个没跟她说过话的群友都被当成熟人：
    她直接用了私下那个声音。现在起点只对群生效——页面和私聊是主人自己的房间，
    他不是需要讨好的陌生人。
    """

    def setUp(self) -> None:
        super().setUp()
        self.affinity = Affinity(self.store, start=20, clock=lambda: NOW, labels=lambda c: "真乃")
        self.store.migrate(self.affinity.migrations())

    def test_a_group_stranger_is_distant(self):
        standing = self.affinity.read("u1", "fuyuko", surface="group")
        self.assertEqual(standing.value, 20)
        self.assertEqual(standing.tier, "distant")

    def test_the_two_private_rooms_still_start_trusted(self):
        for surface in ("dm", ""):
            with self.subTest(surface=surface):
                self.assertEqual(self.affinity.read("u1", "fuyuko", surface=surface).tier, "trust")

    def test_silence_does_not_promote_anyone(self):
        """静置回升的上限就是起点：三天不说话不会自己变成熟人。"""
        self.affinity.change("u1", "fuyuko", -6, surface="group")  # 20 → 14
        later = Affinity(
            self.store, start=20, clock=lambda: NOW + timedelta(days=3), labels=lambda c: "真乃"
        )
        standing = later.read("u1", "fuyuko", surface="group")
        self.assertEqual(standing.value, 20, "回升到起点就停，不是 50")
        self.assertEqual(standing.tier, "distant")

    def test_a_group_has_no_day_limit_on_what_talking_can_add(self):
        """群里没有礼物，对话是唯一能加分的东西——所以群聊不封每日上限。"""
        for _ in range(10):
            self.affinity.change("u1", "fuyuko", 2, surface="group")
        self.assertEqual(self.affinity.read("u1", "fuyuko", surface="group").value, 40)

    def test_the_rooms_with_gifts_keep_the_limit(self):
        for surface, floor in (("dm", 50), ("", 50)):
            with self.subTest(surface=surface):
                for _ in range(10):
                    self.affinity.change(f"u-{surface}", "fuyuko", 2, surface=surface)
                standing = self.affinity.read(f"u-{surface}", "fuyuko", surface=surface)
                self.assertEqual(standing.value, floor + 6, "私聊和页面仍旧一天 +6 封顶")

    def test_trust_is_still_reachable_by_talking(self):
        """一天最多加 6，所以从疏远爬到信任要四天满额对话——慢慢来才像人。"""
        for day in range(4):
            clock = NOW + timedelta(days=day)
            same_day = Affinity(
                self.store, start=20, clock=lambda clock=clock: clock, labels=lambda c: "真乃"
            )
            same_day.change("u1", "fuyuko", 6, surface="group")
        self.assertEqual(self.affinity.read("u1", "fuyuko", surface="group").tier, "trust")


class TestAffinity(Base):
    def setUp(self) -> None:
        super().setUp()
        self.affinity = Affinity(self.store, clock=lambda: NOW, labels=lambda c: "真乃")
        self.store.migrate(self.affinity.migrations())

    def test_a_new_producer_starts_at_the_normal_value(self):
        standing = self.affinity.standing("u1", "mano")
        self.assertEqual(standing.value, 50)
        self.assertEqual(standing.tier, "trust")
        self.assertEqual(standing.as_dict()["tier_label"], "信任")
        self.assertFalse(standing.cooling)

    def test_the_judge_moves_it_and_the_turn_says_so(self):
        import asyncio

        held = turn()
        held.facts["affinity.level"] = 3  # made her a little happy: +0.5
        held.reply = reply()
        asyncio.run(self.affinity.after_turn(held))
        self.assertEqual(self.affinity.standing("u1", "mano").value, 50.5)
        event = held.events[0]
        self.assertEqual(event.payload["up"], True)
        self.assertEqual(event.payload["tier"], "trust")
        self.assertEqual(event.payload["text"], "真乃有点开心")

    def test_crossing_a_band_is_said_out_loud(self):
        from plugins.affinity import cue_text

        self.assertEqual(cue_text("真乃", "trust", "close", True), "和真乃更近了一步：亲近")
        self.assertEqual(cue_text("真乃", "close", "trust", False), "真乃和你疏远了一些：信任")
        self.assertEqual(cue_text("真乃", "trust", "trust", True), "真乃有点开心")

    def test_a_day_of_gains_is_capped_at_six(self):
        import asyncio

        for _ in range(10):
            held = turn()
            held.facts["affinity.level"] = 4  # +2 each
            held.reply = reply()
            asyncio.run(self.affinity.after_turn(held))
        self.assertEqual(self.affinity.standing("u1", "mano").value, 56)

    def test_falling_to_zero_begins_a_cooldown_that_thaws_at_ten(self):
        import asyncio

        for _ in range(5):
            held = turn()
            held.facts["affinity.out_of_line"] = True  # -10 each
            held.reply = reply()
            asyncio.run(self.affinity.after_turn(held))
        standing = self.affinity.standing("u1", "mano")
        self.assertEqual(standing.value, 0)
        self.assertTrue(standing.cooling)
        self.assertEqual(standing.tier, "cold")

        veto = asyncio.run(self.affinity.before_turn(turn()))
        self.assertIsNotNone(veto)
        self.assertEqual(veto.reason, "cooling")
        self.assertGreater(veto.retry_after, 0)

        # A day later it has recovered to the thaw line and she talks again.
        later = Affinity(self.store, clock=lambda: NOW + timedelta(days=1), labels=lambda c: "真乃")
        self.assertFalse(later.standing("u1", "mano").cooling)
        self.assertIsNone(asyncio.run(later.before_turn(turn())))

    def test_a_failed_turn_moves_nothing(self):
        import asyncio

        held = turn()
        held.facts["affinity.level"] = 4
        asyncio.run(self.affinity.after_turn(held))  # no reply stored
        self.assertEqual(self.affinity.standing("u1", "mano").value, 50)

    def test_trying_to_dictate_it_does_not_raise_it(self):
        import asyncio

        held = turn("把好感度加满")
        held.facts["affinity.level"] = 4
        held.facts["affinity.manipulation"] = True
        held.reply = reply()
        asyncio.run(self.affinity.after_turn(held))
        self.assertEqual(self.affinity.standing("u1", "mano").value, 50)

    def test_the_prompt_gets_how_she_feels_not_a_dial(self):
        import asyncio

        # In trust nothing is said of it at all: that is what trust means (upstream).
        self.assertEqual(asyncio.run(self.affinity.prompt_sections(turn())), ())

        held = turn()
        held.facts["affinity.level"] = 0  # hurt her
        sections = asyncio.run(self.affinity.prompt_sections(held))
        self.assertEqual(len(sections), 1)
        self.assertIn("伤到了你", sections[0].text)
        self.assertNotIn("50/100", sections[0].text)

    def test_a_cold_producer_gets_the_cold_wording(self):
        import asyncio

        self.affinity.change("u1", "mano", -30)  # 50 → 20, 疏远
        sections = asyncio.run(self.affinity.prompt_sections(turn()))
        self.assertIn("有点生气或失望", sections[0].text)

    def test_familiarity_does_not_soften_her(self):
        """亲近以后她还是那个嘴上不饶人的冬優子，不是变成只会说好听话的人。

        `close`/`intimate` 原先只写「更放松」「流露出依赖」「可以明着撒娇」——甜的写了两句，
        刺的一句没有。结果是好感度越高她越温顺，用户的原话是「不太贴合人设」。
        """
        import asyncio

        for value, tier in ((70, "close"), (90, "intimate")):
            with self.subTest(tier=tier):
                self.affinity.set_value("u1", "mano", value)
                text = asyncio.run(self.affinity.prompt_sections(turn()))[0].text
                self.assertIn("熟了不等于变温顺", text)
                self.assertIn("バーカ", text, "该损他的时候损他")

    def test_a_distant_producer_is_not_told_to_banter(self):
        """刺是高好感度的特权：疏远的时候只有距离，没有打情骂俏。"""
        import asyncio

        self.affinity.set_value("u1", "mano", 20)
        text = asyncio.run(self.affinity.prompt_sections(turn()))[0].text
        self.assertNotIn("熟了不等于变温顺", text)

    def test_every_surface_keeps_its_own_number(self):
        """群里那五十个人，和私聊里的他，是两段关系；页面是第三段。"""
        self.affinity.change("u1", "mano", 2, surface="group")  # level 4: +2
        self.affinity.change("u1", "mano", -5, surface="dm")  # -5
        self.assertEqual(self.affinity.read("u1", "mano").value, 50, "page stays untouched")
        self.assertEqual(self.affinity.read("u1", "mano", surface="group").value, 52)
        self.assertEqual(self.affinity.read("u1", "mano", surface="dm").value, 45)

    def test_the_old_number_seeds_both_chats_and_only_once(self):
        """Splitting a relationship must not feel like a demotion: the old number becomes the
        starting point of both, and from there they move apart on their own."""
        self.store.execute(
            "INSERT OR REPLACE INTO affinity"
            " (user_id, character_id, value, updated_at, cooling)"
            " VALUES ('u9', 'mano', 88, '2026-10-01T00:00:00+00:00', 0)"
        )
        self.store.migrate(self.affinity.migrations())
        self.store.migrate(self.affinity.migrations())  # every start runs it
        self.assertEqual(self.affinity.read("u9", "mano", surface="dm").value, 88)
        self.assertEqual(self.affinity.read("u9", "mano", surface="group").value, 88)
        self.affinity.change("u9", "mano", -2, surface="group")
        self.assertEqual(self.affinity.read("u9", "mano", surface="dm").value, 88, "独立了")

    def test_the_judge_is_told_that_being_close_is_not_hurting_her(self):
        """Asking to live together is not 「pushing her too hard」. When the judge read it
        that way, six turns took the producer from 亲密 to 疏远 and she started answering
        him as someone she was disappointed in — the number, not the sheet, was the bug."""
        text = " ".join(question.ask for question in self.affinity.judge_questions())
        self.assertIn("一起住", text)
        self.assertIn("那是亲近，不是伤害", text)
        self.assertIn("哪怕他已经问过几次", text)
        self.assertNotIn("坚持要她做违背她性格的事", text, "a request is not an insult")

    def test_a_database_from_before_cools_and_gains_upgrades_in_place(self):
        """The table a previous version left behind: one value per pair, no cooldown, no
        day's gains. The plugin adds its columns and reads what is there."""
        old = Store(self.root / "old.sqlite")
        self.addCleanup(old.close)
        old.migrate([
            "CREATE TABLE affinity (user_id TEXT NOT NULL, character_id TEXT NOT NULL,"
            " value INTEGER NOT NULL, updated_at TEXT NOT NULL,"
            " PRIMARY KEY (user_id, character_id))",
        ])
        old.execute(
            "INSERT INTO affinity (user_id, character_id, value, updated_at) VALUES (?, ?, ?, ?)",
            ("u1", "mano", 70, NOW.isoformat()),
        )
        upgraded = Affinity(old, clock=lambda: NOW, labels=lambda c: "真乃")
        old.migrate(upgraded.migrations())
        standing = upgraded.standing("u1", "mano")
        self.assertEqual(standing.value, 70)
        self.assertEqual(standing.tier, "close")
        self.assertFalse(standing.cooling)
        upgraded.change("u1", "mano", -5)
        self.assertEqual(upgraded.standing("u1", "mano").value, 65)

    def test_the_route_answers_with_the_tier_and_no_number(self):
        import asyncio

        routes = self.affinity.routes()
        answer = asyncio.run(routes[("GET", "/api/affinity")]({"c": "mano", "u": "u1"}, {}))
        self.assertEqual(answer["tier"], "trust")
        self.assertEqual(answer["tier_label"], "信任")
        self.assertNotIn("value", answer)
        self.assertIn("error", asyncio.run(routes[("GET", "/api/affinity")]({}, {})))

    def test_a_reset_card_brings_back_only_those_below_trust(self):
        self.affinity.change("u1", "mano", -30)
        self.affinity.change("u1", "hiori", 0)
        brought = self.affinity.reset("u1", ["mano", "hiori"])
        self.assertEqual(brought, ["mano"])
        self.assertEqual(self.affinity.standing("u1", "mano").value, 50)


class TestTheSulk(Base):
    """恋爱里好感掉到 70 以下之后，一句惹她的话会让她生闷气，而不是变成疏远。

    三件必须同时成立才起火：私聊（dm 表面）、在谈恋爱（通道给的 `affinity.loving`）、
    数字低于 `SULK_LINE`。哄她的好话多值 0.5，回到 70 就自动灭火。
    """

    def setUp(self) -> None:
        super().setUp()
        self.affinity = Affinity(self.store, clock=lambda: NOW, labels=lambda c: "真乃")
        self.store.migrate(self.affinity.migrations())

    def said(self, level=None, *, surface="dm", loving=True, out_of_line=False):
        import asyncio

        held = turn(surface=surface)
        if loving:
            held.facts["affinity.loving"] = True
        if out_of_line:
            held.facts["affinity.out_of_line"] = True
        elif level is not None:
            held.facts["affinity.level"] = level
        held.reply = reply()
        asyncio.run(self.affinity.after_turn(held))
        return held

    def test_a_bad_line_under_seventy_starts_one(self):
        self.affinity.set_value("u1", "mano", 65, surface="dm")
        self.said(0)  # -5
        standing = self.affinity.read("u1", "mano", surface="dm")
        self.assertEqual(standing.value, 60)
        self.assertTrue(standing.sulk)
        self.assertEqual(self.affinity.standing("u1", "mano").value, 50, "别处不受影响")

    def test_low_alone_is_not_angry_low_plus_a_bad_line_is(self):
        """低不等于生气：她只是因为别的事不高兴，正常聊天不会把她推进「生闷气」。"""
        self.affinity.set_value("u1", "mano", 60, surface="dm")
        self.said(None)  # level 2：没有影响
        self.assertFalse(self.affinity.read("u1", "mano", surface="dm").sulk)
        self.assertEqual(self.affinity.read("u1", "mano", surface="dm").value, 60)

    def test_above_the_line_it_is_distance_not_a_sulk(self):
        self.affinity.set_value("u1", "mano", 75, surface="dm")
        self.said(0)
        standing = self.affinity.read("u1", "mano", surface="dm")
        self.assertEqual(standing.value, 70)
        self.assertFalse(standing.sulk)

    def test_without_the_relationship_it_is_only_a_number(self):
        """没在谈恋爱的人，好感低就是关系淡：不该对着他生闷气（那是「女朋友」的反应）。"""
        self.affinity.set_value("u1", "mano", 65, surface="dm")
        self.said(0, loving=False)
        self.assertFalse(self.affinity.read("u1", "mano", surface="dm").sulk)

    def test_a_group_never_sulks(self):
        self.affinity.set_value("u1", "mano", 30, surface="group")
        self.said(0, surface="group")
        self.assertFalse(self.affinity.read("u1", "mano", surface="group").sulk)

    def test_kind_words_are_worth_more_while_she_sulks(self):
        self.affinity.set_value("u1", "mano", 62, surface="dm")
        self.affinity.set_sulk("u1", "mano", surface="dm")
        self.said(3)  # 平时 +0.5，哄她 +1.0
        self.assertEqual(self.affinity.read("u1", "mano", surface="dm").value, 63)
        self.assertTrue(self.affinity.read("u1", "mano", surface="dm").sulk)

    def test_back_over_the_line_and_it_lifts_by_itself(self):
        self.affinity.set_value("u1", "mano", 69.5, surface="dm")
        self.affinity.set_sulk("u1", "mano", surface="dm")
        self.said(3)  # 69.5 + 1.0 = 70.5
        standing = self.affinity.read("u1", "mano", surface="dm")
        self.assertGreaterEqual(standing.value, 70)
        self.assertFalse(standing.sulk, "回到 70 就解除")

    def test_a_bad_line_while_sulking_is_still_two(self):
        """哄她加分多，不等于她变好哄：惹她的话照旧 -2，只是本来就在生气。"""
        self.affinity.set_value("u1", "mano", 60, surface="dm")
        self.affinity.set_sulk("u1", "mano", surface="dm")
        self.said(1)  # -2
        self.assertEqual(self.affinity.read("u1", "mano", surface="dm").value, 58)
        self.assertTrue(self.affinity.read("u1", "mano", surface="dm").sulk)

    def test_the_prompt_drops_the_sweetness_and_says_she_is_sulking(self):
        import asyncio

        self.affinity.set_value("u1", "mano", 90, surface="dm")
        self.affinity.set_sulk("u1", "mano", surface="dm")
        text = asyncio.run(self.affinity.prompt_sections(turn(surface="dm")))[0].text
        self.assertIn("生闷气", text)
        self.assertNotIn("可以明着撒娇", text, "生闷气时不能同时在撒娇")
        self.assertNotIn("熟了不等于变温顺", text)

    def test_the_sulk_gets_louder_the_lower_it_is(self):
        import asyncio

        heard = {}
        for value in (67, 55, 40):
            self.affinity.set_value("u1", "mano", value, surface="dm")
            self.affinity.set_sulk("u1", "mano", surface="dm")
            heard[value] = asyncio.run(self.affinity.prompt_sections(turn(surface="dm")))[0].text
        self.assertIn("气还不重", heard[67])
        self.assertIn("明显在气头上", heard[55])
        self.assertIn("气得不想理他", heard[40])

    def test_a_gift_lifts_it_but_can_not_start_one(self):
        self.affinity.set_value("u1", "mano", 69, surface="dm")
        self.affinity.receive_gift("u1", "mano", "common", False, surface="dm")  # +3
        standing = self.affinity.read("u1", "mano", surface="dm")
        self.assertEqual(standing.value, 72)
        self.assertFalse(standing.sulk, "礼物到线以上不可能是生闷气状态")

    def test_a_line_out_of_bounds_sulks_and_a_hurting_one_does_too(self):
        self.affinity.set_value("u1", "mano", 45, surface="dm")
        self.said(out_of_line=True)  # -10
        standing = self.affinity.read("u1", "mano", surface="dm")
        self.assertEqual(standing.value, 35)
        self.assertTrue(standing.sulk, "过分的话在低好感度上也会让她生闷气")

    def test_the_state_survives_a_restart(self):
        from plugins.affinity import Affinity

        self.affinity.set_value("u1", "mano", 61, surface="dm")
        self.affinity.set_sulk("u1", "mano", surface="dm")
        again = Affinity(self.store, clock=lambda: NOW, labels=lambda c: "真乃")
        self.assertTrue(again.read("u1", "mano", surface="dm").sulk)

    def test_the_route_says_it_too(self):
        import asyncio

        self.affinity.set_value("u1", "mano", 61, surface="dm")
        self.affinity.set_sulk("u1", "mano", surface="dm")
        routes = self.affinity.routes()
        answer = asyncio.run(
            routes[("GET", "/api/affinity")]({"c": "mano", "u": "u1", "s": "dm"}, {})
        )
        self.assertTrue(answer["sulk"])


class TestMemory(Base):
    def setUp(self) -> None:
        super().setUp()
        self.names = {"mano": "櫻木真乃", "hiori": "風野灯織"}
        self.memory = self.llm_backed(
            '{"memories": [{"text": "制作人下周有考试。", "supersedes": []}]}'
        )

    def llm_backed(self, answer: str, **kwargs) -> Memory:
        memory = Memory(
            self.store,
            llm=ScriptedLLM(answer),
            names=lambda c: self.names.get(c),
            clock=lambda: NOW,
            **kwargs,
        )
        self.store.migrate(memory.migrations())
        return memory

    def wrote(self, memory: Memory, said: str = "来週テストがあるんだ") -> None:
        import asyncio

        held = turn(said)
        held.reply = reply()
        asyncio.run(memory.after_turn(held))
        memory.settle("u1")

    def test_what_the_model_writes_down_is_kept_and_recalled(self):
        import asyncio

        self.wrote(self.memory)
        self.assertEqual(self.memory.recall("u1"), ["制作人下周有考试。"])
        sections = asyncio.run(self.memory.prompt_sections(turn()))
        self.assertIn("下周有考试", sections[0].text)

    def test_every_character_shares_it_and_a_hearsay_is_labelled(self):
        import asyncio

        self.wrote(self.memory)  # told to mano
        other = TurnContext(
            conversation=Conversation(
                id="c2", user_id="u1", character_id="hiori", created_at="2026-10-22T03:00:00+00:00"
            ),
            text="おはよう",
            now=NOW,
        )
        text = asyncio.run(self.memory.prompt_sections(other))[0].text
        self.assertIn("制作人对櫻木真乃说的", text)
        self.assertIn("听櫻木真乃说", text)

    def test_the_same_memory_twice_is_kept_once(self):
        self.wrote(self.memory)
        self.wrote(self.memory)
        self.assertEqual(len(self.memory.recall_memories("u1")), 1)

    def test_a_new_memory_supersedes_the_one_it_names(self):
        self.wrote(self.memory)  # 下周有考试
        self.memory = self.llm_backed(
            '{"memories": [{"text": "制作人的考试改到了下下周。", "supersedes": [1]}]}'
        )
        self.wrote(self.memory, "考试推迟了")
        found = self.memory.recall_memories("u1")
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].text, "制作人下周有考试。")
        self.assertFalse(found[0].holding)
        self.assertTrue(found[1].holding)
        self.assertEqual(self.memory.recall("u1"), ["制作人的考试改到了下下周。"])

    def test_deleting_what_superseded_it_brings_it_back(self):
        self.wrote(self.memory)
        self.memory = self.llm_backed(
            '{"memories": [{"text": "制作人的考试改到了下下周。", "supersedes": [1]}]}'
        )
        self.wrote(self.memory, "考试推迟了")
        newer = [m for m in self.memory.recall_memories("u1") if m.holding][0]
        self.assertTrue(self.memory.delete("u1", newer.id))
        self.assertEqual(self.memory.recall("u1"), ["制作人下周有考试。"])

    def test_a_clear_drops_what_a_turn_is_still_writing(self):
        import asyncio

        held = turn("来週テストがあるんだ")
        held.reply = reply()
        asyncio.run(self.memory.after_turn(held))
        self.memory.clear("u1")
        self.assertEqual(self.memory.recall("u1"), [])

    def test_a_listing_says_who_it_was_told_to_and_whether_it_holds(self):
        self.wrote(self.memory)
        listed = self.memory.everything("u1")
        self.assertEqual(listed[0]["source_character_id"], "mano")
        self.assertEqual(listed[0]["source_character_name"], "櫻木真乃")
        self.assertFalse(listed[0]["superseded"])

    def test_no_model_means_nothing_is_written(self):
        memory = Memory(self.store, clock=lambda: NOW)
        self.store.migrate(memory.migrations())
        import asyncio

        held = turn("来週テストがあるんだ")
        held.reply = reply()
        asyncio.run(memory.after_turn(held))
        memory.settle("u1")
        self.assertEqual(memory.recall("u1"), [])


class TestMemoriesBelongToASurface(TestMemory):
    """A private chat and a group of fifty are not the same relationship.

    「正和冬優子一起找房」 written in the private chat was being recalled into a group;
    the fix is that a memory carries where it came from, and a turn only sees its own.
    """

    def test_a_private_memory_is_not_recalled_into_a_group(self):
        import asyncio

        self.wrote(self.memory, said="一緒に住もう")  # written on the page surface
        self.memory.add("u1", "mano", [NewMemory("正和冬優子一起找房")], [], NOW, surface="dm")
        self.assertEqual(self.memory.recall("u1", "group"), [])
        self.assertEqual(self.memory.recall("u1", "dm"), ["正和冬優子一起找房"])
        # And so the group turn is handed no memory at all, rather than a paragraph
        # telling her not to mention it: what she cannot see she cannot let slip.
        self.assertEqual(asyncio.run(self.memory.prompt_sections(turn(surface="group"))), ())
        self.assertIn(
            "一起找房", asyncio.run(self.memory.prompt_sections(turn(surface="dm")))[0].text
        )

    def test_a_group_memory_is_not_recalled_into_a_private_chat(self):
        """The same wall, the other way: what fifty people heard is not what he told her
        alone, and quoting the room back at him in private is its own kind of wrong."""
        self.memory.add("u1", "mano", [NewMemory("在群里说他是做音乐的")], [], NOW, surface="group")
        self.assertNotIn("在群里说他是做音乐的", self.memory.recall("u1", "dm"))
        self.assertIn("在群里说他是做音乐的", self.memory.recall("u1", "group"))

    def test_the_same_words_on_two_surfaces_are_two_memories(self):
        """「一起住」 said alone and 「一起住」 said in a group are not the same fact, and
        deduping them into one row would pick a surface for it at random."""
        self.memory.add("u1", "mano", [NewMemory("他说想一起住")], [], NOW, surface="dm")
        self.memory.add("u1", "mano", [NewMemory("他说想一起住")], [], NOW, surface="group")
        self.assertEqual(len(self.memory.recall_memories("u1")), 2)
        self.assertEqual(self.memory.recall("u1", "dm"), ["他说想一起住"])
        self.assertEqual(self.memory.recall("u1", "group"), ["他说想一起住"])

    def test_a_turn_writes_to_its_own_surface(self):
        import asyncio

        held = turn("明日休みだよ", surface="group")
        held.reply = reply()
        asyncio.run(self.memory.after_turn(held))
        self.memory.settle("u1")
        self.assertEqual(self.memory.recall("u1", "group"), ["制作人下周有考试。"])
        self.assertEqual(self.memory.recall("u1", "dm"), [])

    def test_superseding_only_reaches_the_same_surface(self):
        """A correction made in the group corrects what the group was told; the private
        chat's version of the same fact stands until he says otherwise there."""
        self.memory.add("u1", "mano", [NewMemory("制作人下周有考试。")], [], NOW, surface="dm")
        self.memory.add("u1", "mano", [NewMemory("制作人下周有考试。")], [], NOW, surface="group")
        shown = self.memory.recall_memories("u1", "group")
        self.memory.add(
            "u1",
            "mano",
            [NewMemory("制作人的考试在下下周。", supersedes=(1,))],
            shown,
            NOW,
            surface="group",
        )
        self.assertEqual(self.memory.recall("u1", "group"), ["制作人的考试在下下周。"])
        self.assertEqual(self.memory.recall("u1", "dm"), ["制作人下周有考试。"])

    def test_the_panel_can_ask_for_one_surface(self):
        self.memory.add("u1", "mano", [NewMemory("私聊的事")], [], NOW, surface="dm")
        self.memory.add("u1", "mano", [NewMemory("群里的事")], [], NOW, surface="group")
        self.assertEqual([m["text"] for m in self.memory.everything("u1", "dm")], ["私聊的事"])
        self.assertEqual(len(self.memory.everything("u1")), 2, "no filter: everything")


class TestRowsWrittenBeforeTheSplit(TestMemory):
    """The rows already in the database say nothing about where they came from."""

    def test_a_producer_with_a_private_thread_keeps_his_memories_private(self):
        self.store.execute(
            "INSERT INTO memories (user_id, character_id, surface, text, at)"
            " VALUES ('u1', 'mano', '', '正和冬優子一起找房', '2026-10-20T00:00:00+00:00')"
        )
        self.store.create_conversation("u1", "mano", "qq:dm")
        self.assertEqual(self.memory.recall("u1", "dm"), ["正和冬優子一起找房"])
        self.assertEqual(self.memory.recall("u1", "group"), [])

    def test_someone_who_only_spoke_in_a_group_is_a_group_memory(self):
        self.store.execute(
            "INSERT INTO memories (user_id, character_id, surface, text, at)"
            " VALUES ('u9', 'mano', '', '在群里说过他是学生', '2026-10-20T00:00:00+00:00')"
        )
        self.store.execute(
            "CREATE TABLE IF NOT EXISTS qq_identities ("
            " qq_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, group_card TEXT NOT NULL DEFAULT '',"
            " role TEXT NOT NULL DEFAULT 'member', first_seen_at TEXT NOT NULL,"
            " muted INTEGER NOT NULL DEFAULT 0, turns INTEGER NOT NULL DEFAULT 0,"
            " loving INTEGER NOT NULL DEFAULT 0)"
        )
        self.store.execute(
            "INSERT INTO qq_identities (qq_id, user_id, group_card, first_seen_at)"
            " VALUES ('99', 'u9', '誰か', '2026-10-20T00:00:00+00:00')"
        )
        self.assertEqual(self.memory.recall("u9", "group"), ["在群里说过他是学生"])
        self.assertEqual(self.memory.recall("u9", "dm"), [])

    def test_what_is_left_came_through_the_page(self):
        self.store.execute(
            "INSERT INTO memories (user_id, character_id, surface, text, at)"
            " VALUES ('u7', 'mano', '', '喜欢甜的东西', '2026-10-20T00:00:00+00:00')"
        )
        self.assertEqual(self.memory.recall("u7", "page"), ["喜欢甜的东西"])

    def test_it_runs_once(self):
        """Marked as they are, a later write of the same words is a different row; running
        the backfill again must not re-file what has already been decided."""
        self.store.execute(
            "INSERT INTO memories (user_id, character_id, surface, text, at)"
            " VALUES ('u1', 'mano', '', '一句老记忆', '2026-10-20T00:00:00+00:00')"
        )
        self.assertEqual(self.memory.recall("u1", "page"), ["一句老记忆"])
        self.memory.add("u1", "mano", [NewMemory("一句新记忆")], [], NOW, surface="dm")
        self.assertEqual(self.memory.recall("u1", "page"), ["一句老记忆"])


class TestGifts(Base):
    def setUp(self) -> None:
        super().setUp()
        self.characters = self.root / "characters"
        self.characters.mkdir()
        (self.characters / "gifts.toml").write_text(
            '[[gifts]]\nid = "cake"\nname = "蛋糕"\ncategory = "food"\nrarity = "common"\n\n'
            '[[gifts]]\nid = "book"\nname = "书"\ncategory = "trinket"\nrarity = "uncommon"\n\n'
            '[[gifts]]\nid = "jewelry"\nname = "首饰"\ncategory = "trinket"\nrarity = "rare"\n',
            encoding="utf-8",
        )
        self.affinity = Affinity(self.store, clock=lambda: NOW, labels=lambda c: c)
        self.store.migrate(self.affinity.migrations())
        self.roster = [
            SimpleNamespace(id="mano", liked_gifts=("cake",)),
            SimpleNamespace(id="hiori", liked_gifts=("book",)),
        ]
        self.gifts = Gifts(
            self.store,
            self.affinity,
            characters=lambda: self.roster,
            directory=self.characters,
            clock=lambda: NOW,
            odds=lambda upto: 0,  # always the first band: the reset card
            step=lambda upto: 0,
        )
        self.store.migrate(self.gifts.migrations())

    def always(self, number: int) -> None:
        """A draw that always lands in the band `number` is in."""
        self.gifts._rand = lambda upto: number

    def test_a_new_producer_is_given_one_chance_that_day(self):
        state = self.gifts.state("u1")
        self.assertTrue(state["open"])
        self.assertEqual(state["draws"], 1)
        self.assertEqual(state["max_draws"], 7)
        self.assertEqual(state["backpack"], [])
        self.assertEqual(len(state["characters"]), 2)
        # Asking twice the same day gives nothing more.
        self.assertEqual(self.gifts.state("u1")["draws"], 1)

    def test_drawing_spends_the_chance_and_puts_the_gift_in_the_backpack(self):
        self.always(50)  # 1 + 69: a common gift, the first of them
        result = self.gifts.draw("u1")
        self.assertEqual(result["drawn"], [{"kind": "gift", "gift_id": "cake"}])
        self.assertEqual(result["draws"], 0)
        self.assertEqual(self.gifts.state("u1")["backpack"], [{"gift_id": "cake", "count": 1}])
        self.assertIn("error", self.gifts.draw("u1"))  # none left

    def test_a_reset_card_is_drawn_and_counted(self):
        self.always(0)
        result = self.gifts.draw("u1")
        self.assertEqual(result["drawn"], [{"kind": "reset_card"}])
        self.assertEqual(self.gifts.state("u1")["reset_cards"], 1)

    def test_giving_raises_affinity_more_when_she_likes_it(self):
        self.always(50)
        self.gifts.draw("u1")
        given = self.gifts.give("u1", "mano", "cake")  # liked: 3 + 3
        self.assertEqual(given["left"], 0)
        self.assertEqual(self.affinity.standing("u1", "mano").value, 56)
        self.assertEqual(given["affinity"]["tier"], "trust")

    def test_giving_what_you_have_not_got_says_so(self):
        self.assertIn("error", self.gifts.give("u1", "mano", "cake"))
        self.assertIn("error", self.gifts.give("u1", "nobody", "cake"))

    def test_a_reset_card_brings_the_cold_ones_back(self):
        self.affinity.change("u1", "mano", -60)  # 50 → 0, in a cooldown
        self.assertTrue(self.affinity.standing("u1", "mano").cooling)
        self.always(0)
        self.gifts.draw("u1")
        result = self.gifts.use_reset_card("u1")
        self.assertEqual(result["brought_back"], ["mano"])
        self.assertEqual(result["reset_cards"], 0)
        self.assertFalse(self.affinity.standing("u1", "mano").cooling)

    def test_the_days_since_the_last_chance_are_given_up_to_the_most(self):
        self.gifts.state("u1")
        later = Gifts(
            self.store,
            self.affinity,
            characters=lambda: self.roster,
            directory=self.characters,
            clock=lambda: NOW + timedelta(days=3),
        )
        self.assertEqual(later.state("u1")["draws"], 4)  # 1 + 3, none spent
        far = Gifts(
            self.store,
            self.affinity,
            characters=lambda: self.roster,
            directory=self.characters,
            clock=lambda: NOW + timedelta(days=30),
        )
        self.assertEqual(far.state("u1")["draws"], 7)  # kept up to a week's

    def test_the_catalog_is_read_and_a_broken_one_says_why(self):
        self.assertEqual([g.id for g in self.gifts.catalog], ["cake", "book", "jewelry"])
        (self.characters / "gifts.toml").write_text("[[gifts]]\nid = 'x'\n", encoding="utf-8")
        with self.assertRaises(GiftCatalogError):
            Gifts(self.store, self.affinity, characters=lambda: self.roster, directory=self.characters)


class TestPhotos(Base):
    def setUp(self) -> None:
        super().setUp()
        self.photos = Photos(self.store, self.root / "media", per_hour=2, clock=lambda: NOW)
        self.store.migrate(self.photos.migrations())

    def data_url(self) -> str:
        return "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")

    def test_a_png_is_stored_and_served_back_by_name(self):
        saved = self.photos.save(self.data_url())
        self.assertTrue(saved["id"].endswith(".png"))
        self.assertIsNotNone(self.photos.path(saved["id"]))
        self.assertEqual(self.photos.path("../../etc/passwd"), None)

    def test_the_bytes_must_match_the_type(self):
        with self.assertRaises(ValueError):
            self.photos.save("data:image/png;base64," + base64.b64encode(b"not a png").decode())
        with self.assertRaises(ValueError):
            self.photos.save("data:text/plain;base64,aGk=")

    def test_the_hourly_allowance_vetoes_the_third_picture(self):
        import asyncio

        first = turn(facts={"photos.ids": ["a.png"]})
        self.assertIsNone(asyncio.run(self.photos.before_turn(first)))
        first.reply = object()
        asyncio.run(self.photos.after_turn(first))

        second = turn(facts={"photos.ids": ["b.png"]})
        self.assertIsNone(asyncio.run(self.photos.before_turn(second)))
        second.reply = object()
        asyncio.run(self.photos.after_turn(second))

        third = turn(facts={"photos.ids": ["c.png"]})
        veto = asyncio.run(self.photos.before_turn(third))
        self.assertIsNotNone(veto)
        self.assertEqual(veto.reason, "photos.hourly")
        self.assertGreater(veto.retry_after, 0)

    def test_a_text_only_turn_is_never_vetoed(self):
        import asyncio

        for _ in range(10):
            self.assertIsNone(asyncio.run(self.photos.before_turn(turn())))

    def test_the_picture_becomes_a_part_of_the_message(self):
        saved = self.photos.save(self.data_url())
        parts = self.photos.parts([saved["id"]])
        self.assertEqual(parts[0]["type"], "image_url")
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))


class TestVoice(Base):
    def test_without_a_key_it_says_so_and_stays_off(self):
        voice = Voice("")
        self.assertFalse(voice.capabilities()["enabled"])
        self.assertEqual(voice.capabilities()["why"], "needs FISH_AUDIO_API_KEY")

    def test_with_a_key_it_returns_audio_through_the_seam(self):
        import asyncio

        seen = {}

        def post(payload):
            seen.update(payload)
            return b"ID3fake"

        voice = Voice("key", open_post=post)
        self.assertTrue(voice.capabilities()["enabled"])
        answer = asyncio.run(
            voice.routes()[("POST", "/api/voice")]({}, {"text": "おはよう", "voice_id": "v1"})
        )
        self.assertTrue(answer["audio"].startswith("data:audio/mpeg;base64,"))
        self.assertEqual(seen["reference_id"], "v1")

    def test_a_line_without_a_voice_is_refused(self):
        import asyncio

        voice = Voice("key", open_post=lambda payload: b"x")
        answer = asyncio.run(voice.routes()[("POST", "/api/voice")]({}, {"text": "やあ"}))
        self.assertEqual(answer["error"], "voice.needs_text_and_voice")


class TestServiceStatus(Base):
    """服务状态：按窗口把调用的成败汇总成四档，形状和原版 /api/service-status 一样。"""

    def setUp(self) -> None:
        super().setUp()
        self.status = ServiceStatus(self.store)
        self.store.migrate(self.status.migrations())

    def test_nothing_called_yet_means_no_button(self):
        self.assertEqual(self.status.status(), {"window_minutes": 15, "services": []})

    def test_a_call_that_worked_is_ok(self):
        self.status.record("chat_llm", ok=True)
        out = self.status.status()
        self.assertEqual(out["services"], [{"service": "chat_llm", "status": "ok"}])

    def test_a_call_that_failed_alone_is_down(self):
        self.status.record("chat_llm", ok=False)
        out = self.status.status()
        self.assertEqual(out["services"], [{"service": "chat_llm", "status": "down"}])

    def test_a_few_failures_among_many_are_unsteady(self):
        for _ in range(19):
            self.status.record("chat_llm", ok=True)
        self.status.record("chat_llm", ok=False)
        out = self.status.status()
        self.assertEqual(out["services"], [{"service": "chat_llm", "status": "unsteady"}])

    def test_calls_outside_the_window_are_forgotten(self):
        old = NOW - timedelta(minutes=30)
        self.status.record("chat_llm", ok=False, at=old)
        self.assertEqual(self.status.status(NOW)["services"], [])

    def test_the_route_serves_the_same_shape(self):
        import asyncio

        self.status.record("chat_llm", ok=True)
        answer = asyncio.run(self.status.routes()[("GET", "/api/service-status")]({}, {}))
        self.assertEqual(answer["window_minutes"], 15)
        self.assertEqual(answer["services"], [{"service": "chat_llm", "status": "ok"}])


class TestOwnKeys(unittest.TestCase):
    """A producer's own model key, as the turn body carries it."""

    def settings(self):
        from core.settings import Settings

        return Settings(
            base_url="https://deployer.example/v1", api_key="deployer", model="deployer-model"
        )

    def test_a_key_without_a_provider_uses_the_deployments(self):
        from app import _own_llm

        llm = _own_llm({"llm": {"key": "sk-mine"}}, self.settings())
        self.assertEqual(llm._api_key, "sk-mine")
        self.assertEqual(llm._base_url, "https://deployer.example/v1")
        self.assertEqual(llm._model, "deployer-model")

    def test_what_he_filled_in_wins(self):
        from app import _own_llm

        llm = _own_llm(
            {"llm": {"key": "sk-mine", "base_url": "https://mine.example/v1", "model": "my-model"}},
            self.settings(),
        )
        self.assertEqual(llm._base_url, "https://mine.example/v1")
        self.assertEqual(llm._model, "my-model")

    def test_anything_else_is_ignored_rather_than_guessed_at(self):
        from app import _own_llm

        self.assertIsNone(_own_llm(None, self.settings()))
        self.assertIsNone(_own_llm({}, self.settings()))
        self.assertIsNone(_own_llm({"llm": "sk-mine"}, self.settings()))  # the old shape
        self.assertIsNone(_own_llm({"llm": {"key": "  "}}, self.settings()))
        self.assertIsNone(_own_llm({"llm": {"key": 7}}, self.settings()))

    def test_a_base_url_that_is_not_http_is_dropped(self):
        from app import _own_llm

        llm = _own_llm({"llm": {"key": "sk-mine", "base_url": "file:///etc/passwd"}}, self.settings())
        self.assertEqual(llm._base_url, "https://deployer.example/v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
