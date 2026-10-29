"""The channel, without a QQ account: a fake socket, a scripted model, a temp database.

Everything here runs on the standard library and finishes in milliseconds, which is the
point — the parts that can be wrong about a group chat are the parts that have nothing to do
with QQ being online: who counts as a trigger, how a reply becomes several messages, what a
stranger is told, and what happens when the same event arrives twice.
"""

import asyncio
import base64
import hashlib
import json
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from channels import digest as digest_module
from channels import interests, outbox, presence, stage as stage_file, stickers, texts, ws
from channels.config import QQConfig
from channels.identities import Identities
from channels import plugin_qq
from channels import qq as qq_module
from channels.qq import QQChannel
from channels.world import World
from core.llm import ScriptedLLM
from core.protocol import Segment
from core.settings import Settings
from core.store import Store
from serve import App

SHEET = "# 人设：测试角色\n\n你是测试角色，说话简短。\n"
REPLY = "<ja>おはよう</ja><zh>早上好</zh><ja>今日は早いね</ja><zh>今天真早啊</zh>"
TOML = """\
[[characters]]
id = "test"
name = "テスト"
name_zh = "测试"
name_romaji = "Tesuto"
intro = "测试角色"
sheet = "sheets/test.md"
age = 20
adult = true
plove = 3
heavy = 3
"""
OFFICE = "# 面熟的人\n\n- あさひちゃん：テストの同僚。\n"
IDOL = "# 芹沢あさひ\n\n- 呼び方：「あさひちゃん」。\n"
META = "# 作品のこと\n\n- 生放送もある。\n"
QQ_RULES = "【场合】群聊。\n【对面】{{addressee}}\n【时间】{{time}}\n【距离】只听见这一句。\n"
DM_RULES = "【场合】私聊。只有你们两个。\n【语域】タメ口，「あんた」。\n【时间】{{time}}\n"

WORLD_TOML = """\
[[characters]]
id = "test"
name = "テスト"
name_zh = "测试"
intro = "测试角色"
sheet = "sheets/test.md"

[[characters]]
id = "asahi"
name = "芹沢あさひ"
name_zh = "芹泽朝日"
name_romaji = "Asahi Serizawa"
intro = "同僚"
sheet = "sheets/test.md"
"""


class FakeClient:
    """The OneBot surface the channel uses, recording what would have been sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.connected = True
        self.events = 0
        self.last_error = ""
        self.broken = False
        self.faces: list = []
        self.raw_error: Exception | None = None
        self.login: dict = {"user_id": 999}

    async def call(self, action: str, **params):
        self.calls.append((action, params))
        if self.broken:
            raise RuntimeError("socket is gone")
        if action == "get_login_info":
            return dict(self.login)
        return {}

    async def call_raw(self, action: str, **params):
        """Only `fetch_custom_face` comes back as a list; nothing here counts as a "call"."""
        if self.raw_error is not None:
            raise self.raw_error
        return self.faces if action == "fetch_custom_face" else {}

    @property
    def texts(self) -> list[str]:
        out: list[str] = []
        for _, params in self.calls:
            for segment in params.get("message", []):
                if segment.get("type") == "text" and segment["data"]["text"].strip():
                    out.append(segment["data"]["text"].strip())
        return out


def direct(text: str = "ただいま", *, qq: str = "42", message_id: str = "d1", nickname: str = "制作人"):
    return {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": int(qq),
        "message_id": message_id,
        "sender": {"nickname": nickname},
        "message": [{"type": "text", "data": {"text": text}}],
    }


def event(text: str = "こんばんは", *, qq: str = "42", message_id: str = "m1", at: str = "999"):
    message = []
    if at:
        message.append({"type": "at", "data": {"qq": at}})
    message.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": 100,
        "user_id": int(qq),
        "message_id": message_id,
        "sender": {"card": "制作人" if qq == "42" else "路人", "nickname": "nick"},
        "message": message,
    }


class TestSplit(unittest.TestCase):
    def test_short_text_is_one_message(self):
        self.assertEqual(outbox.split("おはよう"), ["おはよう"])

    def test_empty_is_no_message(self):
        self.assertEqual(outbox.split("   "), [])

    def test_long_text_is_cut_on_a_sentence_end(self):
        text = "あ" * 30 + "。" + "い" * 30 + "。"
        pieces = outbox.split(text, limit=40)
        self.assertTrue(all(len(piece) <= 40 for piece in pieces))
        self.assertEqual("".join(pieces), text)
        self.assertTrue(pieces[0].endswith("。"))

    def test_no_sentence_end_still_cuts_without_losing_a_character(self):
        text = "あ" * 95
        pieces = outbox.split(text, limit=40)
        self.assertEqual("".join(pieces), text)
        self.assertEqual(len(pieces), 3)


class TestFacesAreReadable(unittest.TestCase):
    """Reported by the user: 「群友发送的 [qq表情]，她不会读也不会回复」.

    A face is neither a word nor a picture this process can fetch, so it used to be dropped
    on the floor. Two things followed from that. A face in the middle of a sentence vanished,
    leaving a sentence that said less than the person wrote; and a message that was *only* a
    face arrived as an empty string, which `_accept` discards before it costs anything — so
    somebody who answered her with a sticker got silence, which reads as being ignored.
    """

    def test_a_qq_emoji_keeps_its_place_in_the_sentence(self):
        message = [
            {"type": "text", "data": {"text": "广州邦o "}},
            {"type": "face", "data": {"id": "178"}},
        ]
        self.assertEqual(outbox.plain_text(message), "广州邦o [表情]")

    def test_a_named_face_keeps_its_name(self):
        """Some faces carry 「/偷感」 in `raw`; that half-word is the only meaning there is."""
        message = [{"type": "face", "data": {"id": "1", "raw": "[表情 /偷感]"}}]
        self.assertEqual(outbox.plain_text(message), "[表情 /偷感]")

    def test_a_nameless_raw_is_still_just_a_face(self):
        message = [{"type": "face", "data": {"id": "1", "raw": "[表情 ]"}}]
        self.assertEqual(outbox.plain_text(message), "[表情]")

    def test_a_favourited_face_uses_its_summary(self):
        message = [{"type": "mface", "data": {"summary": "[动画表情]", "url": "http://x/1"}}]
        self.assertEqual(outbox.plain_text(message), "[动画表情]")

    def test_a_favourited_face_without_a_summary_is_still_readable(self):
        self.assertEqual(
            outbox.plain_text([{"type": "mface", "data": {"emoji_id": "abc"}}]),
            "[动画表情]",
        )

    def test_a_run_of_the_same_face_is_one_act_not_forty(self):
        """Somebody mashing one button forty times costs forty times the tokens and says the
        same thing. Three keeps 「他刷了一屏」 without paying for the screen."""
        message = [{"type": "face", "data": {"id": "1"}}] * 40
        self.assertEqual(outbox.plain_text(message), "[表情][表情][表情]")

    def test_different_faces_in_a_row_all_survive(self):
        message = [
            {"type": "face", "data": {"id": "1"}},
            {"type": "face", "data": {"id": "2", "raw": "[表情 /偷感]"}},
        ]
        self.assertEqual(outbox.plain_text(message), "[表情][表情 /偷感]")

    def test_a_message_with_no_face_is_only_its_words(self):
        message = [
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": " 在吗 "}},
        ]
        self.assertEqual(outbox.plain_text(message), "在吗")
        self.assertFalse(outbox.faces("在吗"))

    def test_the_note_is_only_worth_a_section_when_one_was_sent(self):
        self.assertTrue(outbox.faces("[表情]"))
        self.assertTrue(outbox.faces("好啊[动画表情]"))
        self.assertFalse(outbox.faces("[图片]"))


class TestTexts(unittest.TestCase):
    def test_internal_keys_never_reach_the_group(self):
        for reason in ("quota.turns", "cooling", "photos.hourly", "failed", "too_long"):
            said = texts.line(reason)
            self.assertTrue(said)
            self.assertNotIn("quota", said)
            self.assertNotIn("cooling", said)

    def test_empty_is_silence(self):
        self.assertIsNone(texts.line("empty"))

    def test_waiting_puts_seconds_inside_the_line(self):
        self.assertIn("20 秒", texts.line("quota.turns", 20))
        self.assertIn("3 分", texts.line("quota.turns", 180))

    def test_unknown_reason_still_says_something_in_character(self):
        self.assertTrue(texts.line("something.new"))


class TestIdentities(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.store import Store

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.store.close)
        self.ids = Identities(self.store, now="2026-10-25T00:00:00+00:00")
        self.store.migrate(self.ids.migrations())

    def test_first_contact_creates_one_user_with_the_group_card(self):
        first = self.ids.resolve("42", card="制作人")
        again = self.ids.resolve("42", card="改过的名字")
        self.assertEqual(first, again)
        self.assertEqual(self.ids.count(), 1)
        row = self.store.query("SELECT name FROM users WHERE id = ?", (first,))[0]
        self.assertEqual(row["name"], "制作人")

    def test_owner_upgrade_is_sticky(self):
        self.ids.resolve("42")
        self.ids.resolve("42", role="owner")
        self.ids.resolve("42")
        self.assertEqual(self.ids.role("42"), "owner")

    def test_seen_remembers_a_message_exactly_once(self):
        self.assertTrue(self.ids.first_time("m1"))
        self.assertFalse(self.ids.first_time("m1"))
        self.assertTrue(self.ids.first_time("m2"))

    def test_muted_survives_a_round_trip(self):
        self.ids.resolve("42")
        self.ids.set_muted("42", True)
        self.assertTrue(self.ids.muted("42"))

    def test_every_count_can_be_put_back_at_once(self):
        for qq_id in ("1", "2", "3"):
            self.ids.resolve(qq_id)
            self.ids.set_turns(qq_id, 5)
        self.ids.set_all_turns(0, keep=("1",))
        self.assertEqual(self.ids.turns("1"), 5, "kept")
        self.assertEqual(self.ids.turns("2"), 0)
        self.assertEqual(self.ids.turns("3"), 0)

    def test_resetting_everyone_does_not_invent_people(self):
        self.ids.set_all_turns(0)
        self.assertEqual(self.ids.count(), 0)


class TestPresence(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timezone

        self.now = datetime(2026, 10, 24, 16, 22, tzinfo=timezone.utc)  # 00:22 in Shanghai
        self.presence = presence.Presence()

    def test_owner_and_stranger_get_different_instructions(self):
        owner = self.presence.sections(addressee=presence.OWNER, now=self.now)[0].text
        stranger = self.presence.sections(addressee=presence.STRANGER, now=self.now)[0].text
        self.assertIn("括号", owner)
        self.assertIn("不熟", stranger)

    def test_the_clock_says_which_day_and_that_it_is_late(self):
        text = self.presence.sections(addressee=presence.STRANGER, now=self.now)[0].text
        self.assertIn("周日", text)
        self.assertIn("深夜", text)

    def test_bracket_off_forbids_the_aside(self):
        text = self.presence.sections(
            addressee=presence.OWNER, now=self.now, bracket=False
        )[0].text
        self.assertIn("不要在括号里补心里话", text)

    def test_a_call_with_context_hears_the_room(self):
        """被叫到、且给了上文：距离说的是「看见了刚才那几行」。

        旧行为里这一栏写的是「只听见这一句」——那一句和同一段提示词里的对话记录
        自相矛盾，模型两条都得听，行为全看运气。
        """
        text = self.presence.sections(
            addressee=presence.STRANGER, now=self.now, seen=True
        )[0].text
        self.assertIn("看见了", text)
        self.assertNotIn("只听见", text)

    def test_a_call_without_context_still_hears_only_the_one_line(self):
        text = self.presence.sections(addressee=presence.STRANGER, now=self.now)[0].text
        self.assertIn("只听见", text)

    def test_a_missing_file_still_produces_a_section(self):
        self.assertTrue(presence.Presence("/nowhere/none.md").sections(
            addressee=presence.STRANGER, now=self.now
        ))

    def test_a_character_can_say_who_the_room_is(self):
        """Reported by the user: the room is people who play the game, not passers-by.

        That framing is the character's, not the module's — a module that says 「制作人」 is a
        module every other character has to be edited out of. So it now comes from
        `<id>-stage.toml`, and this proves the file reaches the prompt.
        """
        rules = presence.Presence(
            stage={
                "addressee.stranger": "叫你的是群里的另一位制作人，不用解释设定。",
                "addressee.crowd": "对面是一群制作人。",
                "addressee.alone": "群里一群制作人。",
            }
        )
        called = rules.sections(addressee=presence.STRANGER, now=self.now)[0].text
        self.assertIn("另一位制作人", called)
        self.assertIn("不用解释设定", called)
        for kwargs in ({"ambient": True}, {"ambient": True, "own": True}):
            text = rules.sections(addressee=presence.STRANGER, now=self.now, **kwargs)[0].text
            self.assertIn("制作人", text, "a crowd of producers, not of strangers")

    def test_the_neutral_fallback_names_nobody(self):
        """A checkout with no character data must not put a name in somebody's mouth."""
        rules = presence.Presence()
        for scene in (presence.GROUP, presence.PRIVATE):
            for addressee in (presence.OWNER, presence.STRANGER):
                text = rules.sections(addressee=addressee, now=self.now, scene=scene)[0].text
                for word in ("制作人", "ふゆ", "プロデューサー"):
                    self.assertNotIn(word, text, f"{word} leaked into the fallback")

    def test_a_character_stage_file_overrides_the_fallback(self):
        path = Path("characters/world/fuyuko-stage.toml")
        if not path.exists():
            self.skipTest("这个 checkout 没有角色数据")
        rules = presence.Presence(stage=path)
        text = rules.sections(addressee=presence.OWNER, now=self.now)[0].text
        self.assertIn("プロデューサーさん", text)


class TestWorld(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "world" / "idols").mkdir(parents=True)
        (root / "characters.toml").write_text(WORLD_TOML, encoding="utf-8")
        (root / "world" / "office.md").write_text(OFFICE, encoding="utf-8")
        (root / "world" / "meta.md").write_text(META, encoding="utf-8")
        (root / "world" / "idols" / "asahi.md").write_text(IDOL, encoding="utf-8")
        self.world = World(root)

    def test_the_roster_becomes_a_persona_section(self):
        section = self.world.persona()
        self.assertIsNotNone(section)
        self.assertEqual(section.slot.value, "persona")
        self.assertIn("あさひちゃん", section.text)

    def test_a_named_colleague_is_looked_up(self):
        hits = self.world.lookup("芹沢あさひってどんな子？")
        self.assertTrue(hits)
        self.assertIn("呼び方", hits[0].text)

    def test_the_romanised_name_works_too(self):
        self.assertTrue(self.world.lookup("what is Asahi Serizawa like"))

    def test_a_colleague_with_no_file_yields_nothing_rather_than_guessing(self):
        self.assertEqual(self.world.lookup("テストはどう？"), [])

    def test_work_knowledge_only_comes_up_when_asked_about_work(self):
        self.assertEqual(self.world.knowledge("今日は寒いね"), [])
        self.assertTrue(self.world.knowledge("生放送いつだっけ"))


class ChannelCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "characters" / "world" / "idols").mkdir(parents=True)
        (root / "characters" / "sheets").mkdir()
        (root / "characters" / "sheets" / "test.md").write_text(SHEET, encoding="utf-8")
        (root / "characters" / "characters.toml").write_text(TOML, encoding="utf-8")
        (root / "characters" / "world" / "office.md").write_text(OFFICE, encoding="utf-8")
        (root / "characters" / "world" / "fuyuko-qq.md").write_text(QQ_RULES, encoding="utf-8")
        (root / "characters" / "world" / "fuyuko-dm.md").write_text(DM_RULES, encoding="utf-8")
        (root / "characters" / "world" / "meta.md").write_text(META, encoding="utf-8")
        (root / "characters" / "world" / "idols" / "asahi.md").write_text(IDOL, encoding="utf-8")
        settings = Settings(
            base_url="http://unused",
            api_key="unused",
            model="fake",
            db=str(root / "db.sqlite"),
            characters=str(root / "characters"),
            media=str(root / "media"),
            turns_per_hour=0,
        )
        self.app = App(settings, ScriptedLLM(REPLY, size=5))
        self.addCleanup(self.app.close)
        self.client = FakeClient()
        self.outbox = outbox.Outbox(self.app.store, self.client.call, gap=(0.0, 0.0), retries=0)
        self.app.store.migrate([*Identities(None, now="").migrations(), *self.outbox.migrations()])
        self.channel = QQChannel(
            self.app,
            self.client,
            QQConfig(
                enabled=True,
                groups=("100",),
                self_id="999",
                owners=("42",),
                character="test",
                merge_seconds=0.0,
                member_cooldown=0.0,
            ),
            world=World(settings.characters),
            presence=presence.Presence(
                root / "characters" / "world" / "fuyuko-qq.md",
                direct=root / "characters" / "world" / "fuyuko-dm.md",
            ),
            outbox=self.outbox,
        )
        # 把钟钉在周五 15:00（日本时间）：作息闸看的是真的钟，不钉住的话整套用例在半夜
        # 跑就会变成「她睡着了、第一次 @ 不理」。
        self.channel._clock = lambda: datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)
        # Two models, two jobs: the channel's scripted one answers the chat, and this one
        # answers 「她要不要开口、接谁」. A fixture that says yes to everything leaves the
        # gates as the only thing under test, which is what the interjection cases are about;
        # a separate instance so it never shows up in `app.llm.seen`.
        self.channel.interest = interests.Interest(
            ScriptedLLM('{"speak": true, "to": 2, "why": "接得上"}', size=64)
        )

    async def say(self, payload: dict) -> None:
        await self.channel.on_event(payload)
        pending = list(self.channel._timers.values())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


class TestChannel(ChannelCase):
    async def test_a_segment_becomes_a_message_and_only_the_first_at_mentions(self):
        await self.say(event())
        self.assertEqual(self.client.texts, ["早上好", "今天真早啊"])
        first = self.client.calls[0][1]["message"]
        self.assertEqual(first[0]["type"], "reply")
        self.assertEqual(first[1], {"type": "at", "data": {"qq": "42"}})

    async def test_a_wedged_turn_gives_up_instead_of_holding_the_slot(self):
        """一次卡住的请求会一直占着并发位；两个卡住，整条通道就不说话了。

        用户看到的是「有时候不回，过一阵又好了」——从外面看不可能知道是被卡住了。
        """
        from dataclasses import replace

        self.channel.config = replace(self.channel.config, turn_timeout=0.05)

        def wedged(*args, **kwargs):
            async def stream():
                await asyncio.sleep(5)
                if False:  # 永远到不了：这一轮就是不会说完
                    yield None

            return stream()

        self.app.turn = wedged
        started = time.time()
        await self.say(event())
        self.assertLess(time.time() - started, 2.0, "卡住的一轮要有上限")
        self.assertTrue(self.client.texts, "放弃的时候要留一句话，不能静静消失")

    async def test_a_reply_that_takes_a_while_still_arrives(self):
        """上限是防卡死的，不是催她的：正常的一轮不能被它砍掉。"""
        from dataclasses import replace

        self.channel.config = replace(self.channel.config, turn_timeout=30.0)
        await self.say(event())
        self.assertEqual(self.client.texts, ["早上好", "今天真早啊"])

    async def test_the_producers_second_message_is_not_skipped(self):
        """20 秒冷却本来是防「一个群友霸着场」，不该把他算进去。

        日志里真的出现过 `200000002 again within 20s, skipped`——从他那头看就是「@ 了两次，
        第二次没回」，而他问的是一句认真的问题。
        """
        from dataclasses import replace

        self.channel.config = replace(self.channel.config, member_cooldown=60.0)
        await self.say(event("ふゆ在吗", qq="42", message_id="cd1"))
        await self.say(event("我亲我亲", qq="42", message_id="cd2"))
        self.assertEqual(len(self.client.texts), 4, "两条都要回")

    async def test_a_regular_member_still_has_a_cooldown(self):
        from dataclasses import replace

        self.channel.config = replace(self.channel.config, member_cooldown=60.0)
        await self.say(event("ふゆ在吗", qq="43", message_id="ce1"))
        await self.say(event("还有个事", qq="43", message_id="ce2"))
        self.assertEqual(len(self.client.texts), 2, "冷却对群友照旧")

    async def test_a_message_that_does_not_address_her_is_ignored(self):
        await self.say(event(at=""))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.channel.identities.count(), 0)

    async def test_a_sticker_is_an_answer_not_silence(self):
        """Nothing but a face, addressed to her: still a turn.

        This is the reported bug. `_accept` opens with 「no words and no pictures, nothing
        to do」, and a face is neither — so the message never reached her at all.
        """
        payload = event("", message_id="m-face")
        payload["message"] = [
            {"type": "at", "data": {"qq": "999"}},
            {"type": "face", "data": {"id": "178"}},
        ]
        await self.say(payload)
        self.assertTrue(self.client.texts, "a face is something to answer")
        said = self.app.store.query("SELECT text FROM messages WHERE role = 'user'")[0]
        self.assertEqual(said["text"], "[表情]")

    async def test_the_turn_says_what_a_marker_means(self):
        """She cannot see the picture, so she is told that it was a face — and told it is not
        a word, or she would answer 「[动画表情]」 as if somebody had typed that."""
        payload = event("", message_id="m-face2")
        payload["message"] = [
            {"type": "at", "data": {"qq": "999"}},
            {"type": "mface", "data": {"summary": "[动画表情]"}},
        ]
        await self.say(payload)
        prompt = " ".join(
            str(message.content) for batch in self.app.llm.seen for message in batch
        )
        self.assertIn("[动画表情]", prompt)
        self.assertIn("看不到那张图", prompt)

    async def test_the_note_is_not_paid_for_on_every_message(self):
        """A line of explanation on every turn is tokens nobody asked for."""
        await self.say(event())
        prompt = " ".join(
            str(message.content) for batch in self.app.llm.seen for message in batch
        )
        self.assertNotIn("看不到那张图", prompt)

    async def test_another_group_is_not_her_business(self):
        payload = event()
        payload["group_id"] = 200
        await self.say(payload)
        self.assertEqual(self.client.calls, [])

    async def test_the_same_event_twice_is_answered_once(self):
        await self.say(event())
        before = len(self.client.texts)
        await self.say(event())
        self.assertEqual(len(self.client.texts), before)

    async def test_she_answers_the_person_who_called_and_keeps_their_history(self):
        await self.say(event())
        row = self.app.store.query("SELECT user_id FROM qq_identities WHERE qq_id = '42'")[0]
        messages = self.app.store.query(
            "SELECT COUNT(*) AS n FROM messages m JOIN conversations c ON c.id = m.conversation_id"
            " WHERE c.user_id = ?",
            (row["user_id"],),
        )[0]
        self.assertEqual(int(messages["n"]), 2)
        self.assertNotEqual(row["user_id"], self.app.user_id)

    async def test_a_stranger_is_greeted_politely_and_is_not_the_producer(self):
        await self.say(event(qq="43", message_id="m43"))
        self.assertTrue(self.client.texts)
        self.assertEqual(self.channel.identities.role("43"), "member")
        self.assertIn({"type": "at", "data": {"qq": "43"}}, self.client.calls[0][1]["message"])

    async def test_the_cooldown_keeps_one_person_from_driving_her_mad(self):
        """一个群友连着 @ 她两次，第二次不接。

        这里用 43 而不是 42：20 秒冷却防的是「一个群友霸着场」，主人不在其列（见
        `test_the_producers_second_message_is_not_skipped`）。
        """
        self.channel.config = QQConfig(
            enabled=True,
            groups=("100",),
            self_id="999",
            owners=("42",),
            character="test",
            merge_seconds=0.0,
            member_cooldown=3600.0,
        )
        await self.say(event(qq="43"))
        first = len(self.client.texts)
        self.assertTrue(first, "第一次是要回的")
        await self.say(event(qq="43", message_id="m2"))
        self.assertEqual(len(self.client.texts), first)

    async def test_two_messages_in_a_row_are_one_turn(self):
        self.channel.config = QQConfig(
            enabled=True,
            groups=("100",),
            self_id="999",
            owners=("42",),
            character="test",
            merge_seconds=0.05,
            member_cooldown=0.0,
        )
        await self.channel.on_event(event("在吗", message_id="a"))
        await self.channel.on_event(event("在吗？", message_id="b"))
        pending = list(self.channel._timers.values())
        await asyncio.gather(*pending, return_exceptions=True)
        self.assertEqual(len(self.client.texts), 2)  # one reply of two segments
        said = self.app.store.query("SELECT text FROM messages WHERE role = 'user'")[0]
        self.assertEqual(said["text"], "在吗\n在吗？")

    async def test_a_failed_send_does_not_lose_the_rest_of_the_reply(self):
        self.client.broken = True
        await self.say(event())
        pending = self.app.store.query("SELECT COUNT(*) AS n FROM qq_outbox")[0]
        self.assertEqual(int(pending["n"]), 2)  # both kept for the next run

    async def test_reset_command_speaks_and_starts_a_new_conversation(self):
        await self.say(event())
        before = self.app.store.query("SELECT COUNT(*) AS n FROM conversations")[0]["n"]
        await self.say(event("/重开", message_id="m2"))
        conversations = self.app.store.query("SELECT COUNT(*) AS n FROM conversations")[0]
        self.assertEqual(int(conversations["n"]), int(before) + 1)

    async def test_help_lists_the_commands(self):
        await self.say(event("/帮助"))
        self.assertIn("重开", self.client.texts[0])

    async def test_only_the_producer_can_silence_her(self):
        await self.say(event("/静音", qq="43", message_id="m43"))
        self.assertFalse(self.channel.identities.muted("43"))


class TestPrivate(ChannelCase):
    """A private chat is the other scene: nobody watching, so she is not 「ふゆ」."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True,
            groups=(),
            self_id="999",
            owners=("42",),
            private=True,
            character="test",
            merge_seconds=0.0,
            member_cooldown=3600.0,
        )

    async def test_a_private_message_is_answered_through_send_private_msg(self):
        await self.say(direct())
        self.assertEqual(self.client.texts, ["早上好", "今天真早啊"])
        action, params = self.client.calls[0]
        self.assertEqual(action, "send_private_msg")
        self.assertEqual(params["user_id"], "42")
        self.assertNotIn("group_id", params)
        kinds = [segment["type"] for segment in params["message"]]
        self.assertEqual(kinds, ["text"], "a private message carries no @ and no quote")

    async def test_she_is_herself_there_not_fuyuko(self):
        seen: dict = {}
        original = self.app.turn

        def spy(*args, **kwargs):
            seen["sections"] = kwargs.get("sections") or []
            return original(*args, **kwargs)

        self.app.turn = spy
        await self.say(direct())
        text = "\n".join(section.text for section in seen["sections"])
        self.assertIn("タメ口", text)
        self.assertNotIn("群聊", text, "the group rule must not follow her into a private chat")

    async def test_a_private_chat_is_one_conversation_and_keeps_its_history(self):
        await self.say(direct(message_id="d1"))
        await self.say(direct("おかえり", message_id="d2"))
        conversations = self.app.store.query("SELECT COUNT(*) AS n FROM conversations")[0]
        self.assertEqual(int(conversations["n"]), 1, "the same person keeps one thread")
        messages = self.app.store.query("SELECT COUNT(*) AS n FROM messages")[0]
        self.assertEqual(int(messages["n"]), 4)

    async def test_the_group_cooldown_does_not_apply_to_a_private_chat(self):
        self.channel.config = QQConfig(
            enabled=True, groups=(), self_id="999", owners=("42",),
            private=True, character="test", merge_seconds=0.0, member_cooldown=3600.0,
        )
        await self.say(direct(message_id="d1"))
        first = len(self.client.texts)
        await self.say(direct("ねえ", message_id="d2"))
        self.assertGreater(len(self.client.texts), first)

    async def test_a_stranger_cannot_open_a_private_chat(self):
        await self.say(direct(qq="43", message_id="d43"))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.channel.identities.count(), 0)

    async def test_private_chat_stays_off_unless_asked_for(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
        )
        await self.say(direct())
        self.assertEqual(self.client.calls, [])

    async def test_a_failed_private_message_waits_in_the_outbox_and_goes_out_again(self):
        self.client.broken = True
        await self.say(direct())
        rows = self.app.store.query("SELECT group_id, body FROM qq_outbox")
        self.assertTrue(rows)
        self.assertTrue(all(str(row["group_id"]).startswith("dm:") for row in rows))
        self.client.broken = False
        self.outbox._resume_gap = 0.0
        await self.outbox.resend_pending()
        action, params = self.client.calls[-1]
        self.assertEqual(action, "send_private_msg")
        self.assertEqual(params["user_id"], "42")
        self.assertEqual(self.app.store.query("SELECT * FROM qq_outbox"), [])

    async def test_commands_answer_in_private_too(self):
        await self.say(direct("/好感", message_id="d9"))
        self.assertEqual(self.client.calls[0][0], "send_private_msg")
        self.assertTrue(self.client.texts)


class PrivateConfigCase(unittest.TestCase):
    def test_private_ids_override_the_owner_list(self):
        config = QQConfig(owners=("42",), private=True, private_ids=("77",))
        self.assertTrue(config.may_direct("77"))
        self.assertFalse(config.may_direct("42"))

    def test_without_private_ids_only_the_owner_may_write(self):
        config = QQConfig(owners=("42",), private=True)
        self.assertTrue(config.may_direct("42"))
        self.assertFalse(config.may_direct("43"))

    def test_a_channel_with_only_private_chat_can_start(self):
        config = QQConfig(enabled=True, self_id="999", private=True, character="someone")
        self.assertEqual(config.reason_off(), "")

    def test_without_a_character_it_refuses_to_start(self):
        """Guessing which sheet to wear is the one failure that sounds like success: she
        answers in somebody else's voice and nothing looks broken."""
        config = QQConfig(enabled=True, self_id="999", private=True)
        self.assertIn("QQ_CHARACTER", config.reason_off())

    def test_no_target_at_all_is_still_a_problem(self):
        config = QQConfig(enabled=True, self_id="999")
        self.assertIn("QQ_GROUP_IDS", config.reason_off())


class TestSceneHead(unittest.TestCase):
    """The scene line is persona, not a turn rule: it has to be read before the sheet."""

    def setUp(self):
        self.rules = presence.Presence(
            "【场景】你在群里，很多人看着。\n【对面】{{addressee}}\n【时间】{{time}}\n",
            direct="【场景】只有你们两个。\n【时间】{{time}}\n",
        )

    def test_it_is_a_persona_section(self):
        head = self.rules.scene_head(presence.GROUP)
        self.assertIsNotNone(head)
        self.assertEqual(head.slot, "persona")
        self.assertIn("很多人看着", head.text)

    def test_it_does_not_repeat_in_the_turn_section(self):
        from datetime import datetime, timezone

        text = self.rules.sections(
            addressee=presence.OWNER, now=datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)
        )[0].text
        self.assertNotIn("【场景】", text)
        self.assertIn("【对面】", text)

    def test_each_scene_has_its_own_head(self):
        self.assertIn("群里", self.rules.scene_head(presence.GROUP).text)
        self.assertIn("你们两个", self.rules.scene_head(presence.PRIVATE).text)

    def test_a_file_without_a_scene_line_still_works(self):
        plain = presence.Presence("【对面】{{addressee}}\n")
        self.assertIsNone(plain.scene_head(presence.GROUP))


class TestScenesDoNotShareAnEngine(ChannelCase):
    async def test_group_and_private_get_different_prompts(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            private=True, private_ids=("42",), character="test", merge_seconds=0.0,
        )
        group = self.channel._engine("test", presence.GROUP)
        direct = self.channel._engine("test", presence.PRIVATE)
        self.assertIsNot(group, direct, "each scene keeps its own cached prompt")
        self.assertIs(self.channel._engine("test", presence.PRIVATE), direct, "and reuses it")


class TestTemplateHygiene(unittest.TestCase):
    def test_the_editors_comment_never_reaches_the_prompt(self):
        from datetime import datetime, timezone

        source = "<!-- 说明：{{addressee}} 与 {{time}} 由程序填 -->\n【场合】群聊。\n【时间】{{time}}\n"
        text = presence.Presence(source).sections(
            addressee=presence.OWNER, now=datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)
        )[0].text
        self.assertNotIn("说明", text)
        self.assertNotIn("<!--", text)
        self.assertIn("【场合】群聊。", text)

    def test_no_token_survives_any_scene(self):
        from datetime import datetime, timezone

        now = datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)
        rules = presence.Presence()
        for scene in (presence.GROUP, presence.PRIVATE):
            text = rules.sections(addressee=presence.OWNER, now=now, scene=scene)[0].text
            self.assertNotIn("{{", text, scene)


class TestPrivateVoice(unittest.TestCase):
    def test_refusals_speak_her_own_register_there(self):
        for reason in ("too_long", "cooling", "quota.turns", "photos.hourly", "failed"):
            said = texts.line(reason, 30, private=True)
            self.assertTrue(said)
            self.assertNotIn("です", said)
            self.assertNotIn("ふゆ", said)
            self.assertNotIn("quota", said)


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 24
GIF = b"GIF89a" + b"1" * 20


class Coin:
    """A random source that answers from a script, so a probability can be tested."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)
        self.default = 1.0

    def random(self) -> float:
        return self.values.pop(0) if self.values else self.default

    def choice(self, rows):
        return rows[0]


class TestPack(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.store import Store

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "p.db")
        self.addCleanup(self.store.close)
        self.pack = stickers.Pack(self.store, self.root / "stickers", rng=Coin())
        self.store.migrate(self.pack.migrations())

    def test_a_sticker_is_kept_once_and_named_by_its_content(self):
        first = self.pack.save(PNG, group="100", sender="7")
        again = self.pack.save(PNG, group="100", sender="7")
        self.assertIsNotNone(first)
        self.assertEqual(first["sha1"], again["sha1"])
        self.assertEqual(self.pack.count(), 1)
        self.assertTrue(Path(first["path"]).exists())

    def test_a_favourite_seen_in_a_room_becomes_tagglable_there(self):
        """The account's own faces belong to no room, and `/表情标` is per room."""
        imported = self.pack.save(PNG, group="", sender="self")
        self.assertIsNone(self.pack.tag("上班", group="100"), "nothing in this room yet")
        self.pack.adopt(imported, group="100", sender="7")
        row = self.pack.tag("上班", group="100")
        self.assertIsNotNone(row)
        self.assertEqual(self.pack.count("上班"), 1)

    def test_adopting_does_not_move_a_sticker_between_rooms(self):
        self.pack.save(PNG, group="100", sender="7")
        row = self.pack.tag("上班", group="100")
        self.pack.adopt(row, group="200", sender="8")
        self.assertEqual(self.pack.count("上班"), 1, "the row is still there, unchanged")
        self.assertIsNone(self.pack.tag("x", group="200"), "and not moved into 200")

    def test_a_photo_and_a_pdf_are_not_stickers(self):
        self.assertIsNone(self.pack.save(b"%PDF-1.4 x"))
        self.assertEqual(self.pack.count(), 0)

    def test_a_named_tag_never_falls_back_to_another_sticker(self):
        self.pack.save(PNG)
        self.assertIsNone(self.pack.pick("上班"), "a tag with nothing behind it answers nothing")
        self.assertIsNotNone(self.pack.pick())

    def test_tagging_names_the_newest_sticker_and_then_it_can_be_picked(self):
        self.pack.save(PNG)
        self.pack.save(GIF)
        row = self.pack.tag("上班")
        self.assertEqual(row["tag"], "上班")
        picked = self.pack.pick("上班")
        self.assertEqual(picked["sha1"], row["sha1"], "the newest one was the one tagged")
        self.assertEqual(self.pack.count("上班"), 1)

    def test_the_file_is_stored_by_absolute_path(self):
        row = self.pack.save(PNG)
        self.assertTrue(Path(row["path"]).is_absolute(), "nothing may depend on the cwd")

    def test_prune_forgets_rows_whose_file_is_gone(self):
        row = self.pack.save(PNG)
        Path(row["path"]).unlink()
        self.assertEqual(self.pack.prune(), 1)
        self.assertEqual(self.pack.count(), 0)

    def test_the_data_url_is_base64_not_a_path(self):
        row = self.pack.save(PNG)
        url = self.pack.data_url(row)
        self.assertTrue(url.startswith("base64://"))
        self.assertNotIn(str(self.root), url)

    def test_a_flag_round_trips(self):
        self.assertEqual(self.pack.flag("monday:2026-W43"), "")
        self.pack.set_flag("monday:2026-W43", "sent")
        self.pack.set_flag("monday:2026-W43", "sent-again")
        self.assertEqual(self.pack.flag("monday:2026-W43"), "sent-again")


class AmbientCase(ChannelCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "stickers", rng=Coin())
        self.app.store.migrate(self.pack.migrations())
        self.channel.pack = self.pack
        self.channel.config = QQConfig(
            enabled=True,
            groups=("100",),
            self_id="999",
            owners=("42",),
            character="test",
            merge_seconds=0.0,
            member_cooldown=0.0,
            ambient=True,
            ambient_p=1.0,
            ambient_check=0.0,
            ambient_gap=0.0,
            ambient_heard=8,
            ambient_sticker_p=0.0,
        )
        # 作息闸看的是真的钟：不钉住的话，这些用例在半夜跑会全部变成「她睡着了」。
        # 2026-10-30 06:00 UTC = 周五 14:00 北京。
        self.channel._clock = lambda: datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)

    async def quiet(self, text: str, *, qq: str = "43", message_id: str = "q1") -> None:
        """A message that does not @ her."""
        await self.say(event(text, qq=qq, message_id=message_id, at=""))


class TestAmbient(AmbientCase):
    async def test_a_message_nobody_addressed_her_to_is_usually_left_alone(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", character="test",
            merge_seconds=0.0, member_cooldown=0.0, ambient=True, ambient_p=0.0,
        )
        await self.quiet("今日は寒いね", message_id="a")
        await self.quiet("ほんと寒い", message_id="b")
        self.assertEqual(self.client.calls, [])

    async def test_she_sometimes_says_something_without_being_called(self):
        await self.quiet("今日は寒いね", message_id="a")
        await self.quiet("ほんと寒い", message_id="b")
        self.assertTrue(self.client.texts, "she joined in")
        kinds = [segment["type"] for segment in self.client.calls[0][1]["message"]]
        self.assertNotIn("at", kinds, "an interjection does not @ anyone")
        self.assertNotIn("reply", kinds, "and does not quote")

    async def test_she_is_given_the_lines_she_just_read(self):
        seen: dict = {}
        original = self.app.turn

        def spy(*args, **kwargs):
            seen["sections"] = kwargs.get("sections") or []
            return original(*args, **kwargs)

        self.app.turn = spy
        await self.quiet("新曲のMV見た？", message_id="a")
        await self.quiet("まだ見てない", message_id="b")
        text = "\n".join(section.text for section in seen["sections"])
        self.assertIn("まだ見てない", text)
        self.assertIn("刚才群里在说这些", text)
        self.assertIn("之后不会记得", text)

    async def test_the_gap_keeps_her_from_answering_a_burst(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", character="test",
            merge_seconds=0.0, member_cooldown=0.0, ambient=True, ambient_p=1.0,
            ambient_gap=3600.0, ambient_check=0.0,
        )
        for index in range(5):
            await self.quiet(f"メッセージ{index}", message_id=f"m{index}")
        self.assertEqual(len(self.client.texts), 2, "one interjection, not five")

    async def test_a_sticker_can_be_the_whole_answer(self):
        self.pack.save(PNG)
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", character="test",
            merge_seconds=0.0, member_cooldown=0.0, ambient=True, ambient_p=1.0,
            ambient_sticker_p=1.0, ambient_own_p=0.0,
        )
        await self.quiet("それな", message_id="a")
        await self.quiet("それな", message_id="b")
        self.assertEqual(self.client.texts, [], "no words at all")
        action, params = self.client.calls[0]
        self.assertEqual(action, "send_group_msg")
        self.assertEqual(params["message"][0]["type"], "image")

    async def test_a_sticker_from_the_group_is_kept_for_later(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", character="test",
            merge_seconds=0.0, member_cooldown=0.0, ambient=False,
        )
        payload = event(message_id="s1", at="")
        payload["message"] = [
            {"type": "image", "data": {"url": "http://example.invalid/s.png", "sub_type": 1}}
        ]
        original = qq_module._fetch
        qq_module._fetch = lambda url: PNG
        self.addCleanup(setattr, qq_module, "_fetch", original)
        await self.say(payload)
        self.assertEqual(self.pack.count(), 1)

    async def test_the_owner_can_name_a_sticker_and_then_send_it(self):
        self.pack.save(PNG, group="100", sender="42")
        await self.say(event("/表情标 上班", qq="42", message_id="t1"))
        self.assertEqual(self.pack.count("上班"), 1)
        await self.say(event("/表情 上班", qq="42", message_id="t2"))
        last = self.client.calls[-1][1]["message"][0]
        self.assertEqual(last["type"], "image")

    async def test_a_stranger_cannot_rename_the_pack(self):
        self.pack.save(PNG, group="100", sender="42")
        await self.say(event("/表情标 上班", qq="43", message_id="t3"))
        self.assertEqual(self.pack.count("上班"), 0)
        self.assertEqual(self.client.calls, [], "and is not even told the command exists")

    async def test_asking_for_a_tag_she_does_not_have_says_so(self):
        self.pack.save(PNG)
        await self.say(event("/表情 上班", qq="42", message_id="t4"))
        kinds = [segment["type"] for segment in self.client.calls[-1][1]["message"]]
        self.assertNotIn("image", kinds, "she does not send a different sticker instead")
        self.assertIn("上班", self.client.texts[-1])


class TestBotAccounts(ChannelCase):
    """别的角色的机器人：她得知道对面是谁，否则一段ルカ的台词会被当成陌生群友接。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            bots=(("77", "asahi"),), character="test",
            merge_seconds=0.0, member_cooldown=0.0, ambient_p=0.0,
        )

    def capture(self) -> dict:
        seen: dict = {}
        original = self.app.turn

        def spy(*args, **kwargs):
            seen["sections"] = kwargs.get("sections") or []
            return original(*args, **kwargs)

        self.app.turn = spy
        return seen

    async def test_a_marked_account_is_named_as_a_bot(self):
        seen = self.capture()
        await self.say(event("ルカだ。", qq="77", message_id="b1"))
        text = "\n".join(section.text for section in seen["sections"])
        self.assertIn("的聊天机器人", text)
        self.assertIn("不是人", text, "别把它当成一个群友")

    async def test_an_unmarked_account_gets_no_such_line(self):
        seen = self.capture()
        await self.say(event("こんばんは", qq="78", message_id="b2"))
        text = "\n".join(section.text for section in seen["sections"])
        self.assertNotIn("的聊天机器人", text)


class TestIgnoreList(ChannelCase):
    """`QQ_IGNORE_IDS`：另一个 bot 说的话不进上下文，也不回。

    群里第二具机器人不该被当成一个群友：回它一次，它就可能回回来，两边可以这样聊一下午，
    而每一句都是钱。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            ignore=("77",), character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=True, ambient_p=1.0, ambient_gap=0.0, ambient_check=0.0,
            ambient_sticker_p=0.0,
        )

    async def test_a_bot_on_the_list_is_neither_answered_nor_heard(self):
        await self.say(event("我是另一个 bot", qq="77", message_id="b1", at=""))
        self.assertEqual(self.client.calls, [])
        heard = self.channel._heard.get("100") or []
        self.assertEqual([line["qq"] for line in heard], [], "它的话不进上下文")

    async def test_a_person_one_id_over_is_still_heard(self):
        await self.say(event("我是一句普通的话", qq="78", message_id="b2", at=""))
        heard = self.channel._heard.get("100") or []
        self.assertEqual([line["qq"] for line in heard], ["78"])


class TestThreads(ChannelCase):
    """One person, two rooms, two histories."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            private=True, private_ids=("42",), character="test",
            merge_seconds=0.0, member_cooldown=0.0, ambient_p=0.0,
        )

    async def test_a_group_and_a_private_chat_do_not_share_history(self):
        await self.say(event("グループで", message_id="g1"))
        await self.say(direct("ひそひそ", message_id="d1"))
        rows = self.app.store.query("SELECT title FROM conversations ORDER BY rowid")
        titles = [row["title"] for row in rows]
        self.assertEqual(titles, ["qq:group:100", "qq:dm"])

    async def test_the_same_person_stays_one_person(self):
        await self.say(event(message_id="g1"))
        await self.say(direct(message_id="d1"))
        self.assertEqual(self.channel.identities.count(), 1)
        rows = self.app.store.query("SELECT DISTINCT user_id FROM conversations")
        self.assertEqual(len(rows), 1)

    async def test_reset_only_replaces_the_thread_it_was_typed_in(self):
        await self.say(event("グループで", message_id="g1"))
        await self.say(direct("ひそひそ", message_id="d1"))
        await self.say(event("/重开", message_id="g2"))
        rows = self.app.store.query("SELECT title FROM conversations ORDER BY rowid")
        self.assertEqual([row["title"] for row in rows], ["qq:group:100", "qq:dm", "qq:group:100"])


class TestHerOwnThoughts(AmbientCase):
    """Not every interjection is a reply.

    Reported from the live group: when she joined in unasked, it was always a line *back* to
    whatever the last person had said — a very good answer machine, and not a person in a
    room. `QQ_AMBIENT_OWN_P` is the share of interjections that are a thought of her own
    instead: no addressee, no question answered, nothing to reply to.
    """

    def use(self, **overrides):
        base = dict(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=True, ambient_p=1.0, ambient_gap=0.0, ambient_sticker_p=0.0,
            ambient_check=0.0,
        )
        base.update(overrides)
        self.channel.config = QQConfig(**base)

    def spy(self) -> dict:
        seen: dict = {}
        original = self.app.turn

        def wrap(*args, **kwargs):
            seen["text"] = args[1] if len(args) > 1 else kwargs.get("text")
            seen["sections"] = kwargs.get("sections") or []
            return original(*args, **kwargs)

        self.app.turn = wrap
        self.addCleanup(setattr, self.app, "turn", original)
        return seen

    async def test_a_thought_of_her_own_does_not_answer_the_room(self):
        self.use(ambient_own_p=1.0)
        seen = self.spy()
        await self.quiet("新曲のMV見た？", message_id="a")
        text = "\n".join(section.text for section in seen["sections"])
        self.assertIn("没有人是在跟你说话", text)
        self.assertNotIn("接一句就好", text)
        self.assertIn("你自己的事", text)

    async def test_turning_the_share_off_makes_every_interjection_a_reply(self):
        self.use(ambient_own_p=0.0)
        seen = self.spy()
        await self.quiet("新曲のMV見た？", message_id="a")
        await self.quiet("まだ見てない", message_id="b")
        text = "\n".join(section.text for section in seen["sections"])
        self.assertIn("接一句就好", text)
        self.assertNotIn("没有人是在跟你说话", text)

    async def test_the_rooms_chatter_is_not_her_user_message(self):
        self.use(ambient_own_p=1.0)
        seen = self.spy()
        await self.quiet("新曲のMV見た？", message_id="a")
        self.assertNotIn(
            "新曲", seen["text"],
            "a line nobody addressed to her cannot also be the message she answers",
        )

    async def test_a_reply_still_answers_what_was_said(self):
        self.use(ambient_own_p=0.0)
        seen = self.spy()
        await self.quiet("新曲のMV見た？", message_id="a")
        await self.quiet("まだ見てない", message_id="b")
        self.assertIn("まだ見てない", seen["text"])

    async def test_one_overheard_line_is_enough_to_have_a_thought(self):
        self.use(ambient_own_p=1.0)
        await self.quiet("雨だね", message_id="a")
        self.assertTrue(self.client.texts)

    async def test_one_line_is_not_enough_to_join_a_conversation(self):
        self.use(ambient_own_p=0.0)
        await self.quiet("雨だね", message_id="a")
        self.assertEqual(self.client.texts, [], "a reply needs something to reply to")

    async def test_a_thought_never_arrives_as_a_sticker(self):
        self.pack.save(PNG)
        self.use(ambient_own_p=1.0, ambient_sticker_p=1.0)
        await self.quiet("雨だね", message_id="a")
        self.assertTrue(self.client.texts, "a thought is words")
        self.assertEqual(self.client.calls[0][1]["message"][0]["type"], "text")

    async def test_she_still_does_not_at_anyone(self):
        self.use(ambient_own_p=1.0)
        await self.quiet("雨だね", message_id="a")
        kinds = [segment["type"] for segment in self.client.calls[0][1]["message"]]
        self.assertNotIn("at", kinds)
        self.assertNotIn("reply", kinds)


class TestInterestedTopics(AmbientCase):
    """「根据人设自动插她感兴趣的话题」，一天十次左右.

    A coin cannot answer this. It says whether she is willing to speak at all, and that is
    all it says — so before this, the input was always the newest message and the reply was
    always about whatever happened to be last. The model is what knows whether there is
    anything *she* would want to say, and which line she would say it to.

    Ten a day is a count, not a chance: a busy room multiplies a chance, which is why the
    old `QQ_AMBIENT_P` produced a hundred a day in a group that talks constantly.
    """

    def judge(self, answer: str = '{"speak": true, "to": 1}', *, fail: bool = False) -> list[int]:
        """Install a judge that says `answer`, and count how often it was asked."""
        asked: list[int] = []

        class _Model:
            async def stream(self, messages):  # noqa: ANN001, ANN202 - duck-typed LLM
                asked.append(1)
                if fail:
                    raise RuntimeError("the judge is down")
                for piece in (answer,):
                    yield piece

        self.channel.interest = interests.Interest(_Model())
        return asked

    def use(self, **overrides):
        base = dict(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=True, ambient_p=1.0, ambient_gap=0.0, ambient_sticker_p=0.0,
            ambient_check=0.0,
            ambient_own_p=0.0,
        )
        base.update(overrides)
        self.channel.config = QQConfig(**base)

    def last_words(self) -> str:
        rows = self.app.store.query(
            "SELECT text FROM messages WHERE role = 'user' ORDER BY rowid DESC LIMIT 1"
        )
        return str(rows[0]["text"]) if rows else ""

    async def test_an_uninteresting_topic_gets_silence(self):
        self.use()
        self.judge('{"speak": false, "to": 0, "why": "看不懂的梗"}')
        await self.quiet("今日は寒いね", message_id="a")
        await self.quiet("ほんと寒い", message_id="b")
        self.assertEqual(self.client.texts, [])
        self.assertEqual(self.channel._ambient_used("100"), 0, "a refusal costs no quota")

    async def test_she_answers_the_line_worth_answering_not_the_newest(self):
        """The point of letting the model choose: three people answer a question, and the
        line still worth replying to is the question."""
        self.use()
        self.judge('{"speak": true, "to": 1}')
        await self.quiet("新曲のMV見た？", qq="43", message_id="a")
        await self.quiet("まだ見てない", qq="44", message_id="b")
        self.assertEqual(self.last_words(), "新曲のMV見た？")

    async def test_the_turn_belongs_to_whosever_line_she_answered(self):
        """Not the newest speaker: their name, their history, their number."""
        self.use()
        self.judge('{"speak": true, "to": 1}')
        await self.quiet("新曲のMV見た？", qq="43", message_id="a")
        await self.quiet("まだ見てない", qq="44", message_id="b")
        mine = self.app.store.query(
            "SELECT user_id FROM qq_identities WHERE qq_id = '43'"
        )[0]["user_id"]
        rows = self.app.store.query(
            "SELECT user_id FROM conversations ORDER BY rowid DESC LIMIT 1"
        )
        self.assertEqual(rows[0]["user_id"], mine)
        self.assertEqual(self.channel.identities.turns("44"), 0, "44 was only overheard")

    async def test_a_line_number_out_of_range_means_the_newest_line(self):
        self.use()
        self.judge('{"speak": true, "to": 99}')
        await self.quiet("新曲のMV見た？", message_id="a")
        await self.quiet("まだ見てない", message_id="b")
        self.assertEqual(self.last_words(), "まだ見てない")

    async def test_ten_a_day_and_then_she_stops(self):
        self.use()
        self.judge()
        for index in range(14):
            await self.quiet(f"メッセージ{index}", message_id=f"m{index}")
        self.assertEqual(self.channel._ambient_used("100"), 10)
        before = len(self.client.calls)
        await self.quiet("もう一回", message_id="late")
        self.assertEqual(len(self.client.calls), before, "the eleventh is silence")

    async def test_each_room_spends_its_own_budget(self):
        """两个群各有一本账：一个群说满了，另一个群还有自己的十次。"""
        self.use(groups=("100", "200"), ambient_daily=2)
        self.judge()
        # 三条一轮：插话要先有两句「别人的话」垫着，一句时她只看不说话。
        for index in range(3):
            await self.quiet(f"ひとつ{index}", message_id=f"a{index}")
            payload = event(f"ふたつ{index}", qq="44", message_id=f"b{index}", at="")
            payload["group_id"] = 200
            await self.say(payload)
        self.assertEqual(self.channel._ambient_used("100"), 2)
        self.assertEqual(self.channel._ambient_used("200"), 2)
        self.assertEqual(len(self.client.texts), 8, "两个群各插两句，各两段")

    async def test_a_busy_room_cannot_eat_another_room_s_quota(self):
        """「一个群一天十次」。热闹的群说满了，安静的群照旧有自己的那份。"""
        self.use(groups=("100", "200"), ambient_daily=1)
        self.judge()
        for index in range(3):
            await self.quiet(f"ひとつ{index}", message_id=f"a{index}")
        self.assertEqual(self.channel._ambient_used("100"), 1, "这个群用完了")
        for index in range(2):
            payload = event(f"ふたつ{index}", qq="44", message_id=f"b{index}", at="")
            payload["group_id"] = 200
            await self.say(payload)
        self.assertEqual(self.channel._ambient_used("200"), 1, "另一个群不受影响")

    async def test_the_quota_forgets_yesterday(self):
        self.use()
        self.judge()
        for index in range(2):
            await self.quiet(f"メッセージ{index}", message_id=f"m{index}")
        self.assertEqual(self.channel._ambient_used("100"), 1)
        self.channel._clock = lambda: datetime.now(timezone.utc) + timedelta(days=1)
        self.assertEqual(self.channel._ambient_used("100"), 0, "a new local day, a new ten")

    async def test_a_judge_that_is_down_means_she_says_nothing(self):
        """An interjection nobody asked for is the one thing that must never happen by
        accident, so an unreadable answer is 「no」, not a guess."""
        self.use()
        self.judge(fail=True)
        await self.quiet("今日は寒いね", message_id="a")
        await self.quiet("ほんと寒い", message_id="b")
        self.assertEqual(self.client.texts, [])
        self.assertEqual(self.channel._ambient_used("100"), 0)

    async def test_garbage_is_a_no(self):
        self.use()
        self.judge("嗯……我觉得挺好的？")
        await self.quiet("今日は寒いね", message_id="a")
        await self.quiet("ほんと寒い", message_id="b")
        self.assertEqual(self.client.texts, [])

    async def test_a_burst_of_messages_asks_the_judge_once(self):
        """Five people talking at once are not five questions. The second message arrives
        while the first is still being judged; it waits for the next round instead."""
        self.use()
        asked: list[int] = []

        class _Slow:
            async def stream(self, messages):  # noqa: ANN001, ANN202 - duck-typed LLM
                asked.append(1)
                await asyncio.sleep(0.05)
                for piece in ('{"speak": true, "to": 1}',):
                    yield piece

        self.channel.interest = interests.Interest(_Slow())
        first = event("新曲のMV見た？", qq="43", message_id="a", at="")
        second = event("まだ見てない", qq="44", message_id="b", at="")
        await asyncio.gather(self.channel.on_event(first), self.channel.on_event(second))
        for timer in list(self.channel._timers.values()):
            await asyncio.gather(timer, return_exceptions=True)
        self.assertEqual(len(asked), 1)

    async def test_the_judge_is_not_asked_twice_in_a_row(self):
        """`GAP` is about her last *words*, not her last look: without a second clock, a
        topic she just turned down is asked about again on the very next message."""
        self.use(ambient_check=3600.0)
        asked = self.judge('{"speak": false, "to": 0}')
        for index in range(5):
            await self.quiet(f"メッセージ{index}", message_id=f"m{index}")
        self.assertEqual(len(asked), 1, "one look per cooldown, however much is said")

    async def test_the_interests_file_is_what_she_is_judged_against(self):
        """「她感兴趣的话题」 is a fact about a character, so it is a file in her folder, not
        a setting — and the words in it have to reach the prompt."""
        seen: list[str] = []

        class _Watcher:
            async def stream(self, messages):  # noqa: ANN001, ANN202 - duck-typed LLM
                seen.append(" ".join(str(message.content) for message in messages))
                for piece in ('{"speak": false, "to": 0}',):
                    yield piece

        root = Path(self.tmp.name) / "characters" / "world" / "test-interests.md"
        root.write_text("# 她只接吃的\n\n- 拉面、便当、甜食。\n", encoding="utf-8")
        self.channel.interest = interests.Interest(
            _Watcher(), root, name="テスト"
        )
        self.use()
        await self.quiet("今日は寒いね", message_id="a")
        await self.quiet("ほんと寒い", message_id="b")
        self.assertTrue(seen, "the judge was asked")
        self.assertIn("拉面", seen[0])
        self.assertIn("テスト", seen[0])


class TestTheNamesSheKnows(AmbientCase):
    """话里出现她认识的同行时，她更想接一句。

    「越近越可能」是概率：`QQ_AMBIENT_P` 是平时看一眼的机会，认识的同行出现时按
    亲疏把剩下那段距离补上一部分。整体低频由 gap 和每日次数管着，跟这条不冲突。
    """

    def table(self) -> Path:
        path = Path(self.tmp.name) / "characters" / "world" / "test-idols.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '[[group]]\nunit = "Straylight"\ncloseness = 3\n'
            'names = ["あさひ", "芹沢あさひ"]\n\n'
            '[[group]]\nunit = "illumination STARS"\ncloseness = 2\nnames = ["真乃"]\n',
            encoding="utf-8",
        )
        return path

    def test_a_unit_mate_is_close_and_a_colleague_is_merely_known(self):
        names = interests.Idols(self.table())
        self.assertEqual(names.hit("今天あさひ状态很好").closeness, 3)
        self.assertEqual(names.hit("真乃的鸽子好可爱").closeness, 2)
        self.assertIsNone(names.hit("今天天气不错"), "不认识的名字什么都不算")

    def test_the_longer_name_is_the_one_that_matched(self):
        """「芹沢あさひ」不能被「あさひ」抢先——不然日志里永远是那个短的。"""
        names = interests.Idols(self.table())
        self.assertEqual(names.hit("芹沢あさひ的舞台").name, "芹沢あさひ")

    def use(self, **overrides):
        base = dict(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=True, ambient_p=1.0, ambient_gap=0.0, ambient_sticker_p=0.0,
            ambient_check=0.0, ambient_own_p=0.0,
        )
        base.update(overrides)
        self.channel.config = QQConfig(**base)

    def judge(self, answer: str) -> list[int]:
        """装一个只会说 `answer` 的判断模型，并数它被问了几次。"""
        asked: list[int] = []

        class _Model:
            async def stream(self, messages):  # noqa: ANN001, ANN202 - duck-typed LLM
                asked.append(1)
                for piece in (answer,):
                    yield piece

        self._model = _Model()
        return asked

    async def test_a_name_she_knows_raises_the_odds_of_looking(self):
        """概率 0 时，运气永远不会让她看一眼；出现自己人的名字，就会。"""

        class _Low:
            def random(self) -> float:
                return 0.1

        self.channel._rng = _Low()
        asked = self.judge('{"speak": false, "to": 0}')
        self.channel.interest = interests.Interest(self._model, idols=self.table())
        self.use(ambient_p=0.0, ambient_own_p=0.0)
        await self.quiet("最近完全不相关的一句话", message_id="a")
        await self.quiet("还有一句", message_id="b")
        self.assertEqual(len(asked), 0, "没有认识的名字，概率还是 0")

        await self.quiet("今天あさひ状态很好", message_id="c")
        self.assertEqual(len(asked), 1, "自己人的名字把概率抬起来了")

    async def test_the_judge_is_told_who_was_mentioned(self):
        seen: list[str] = []

        class _Watcher:
            async def stream(self, messages):  # noqa: ANN001, ANN202 - duck-typed LLM
                seen.append(" ".join(str(message.content) for message in messages))
                for piece in ('{"speak": false, "to": 0}',):
                    yield piece

        self.channel.interest = interests.Interest(_Watcher(), idols=self.table())
        self.use(ambient_p=1.0, ambient_own_p=0.0)
        await self.quiet("今天あさひ状态很好", message_id="a")
        await self.quiet("真的很好", message_id="b")
        self.assertTrue(seen)
        self.assertIn("あさひ", seen[0])
        self.assertIn("同一个组合", seen[0])


class TestCalled(ChannelCase):
    """有人提到了她：@ 她，或者消息里叫了她的名字。她看着上下文，自己判断要不要回。"""

    def named(self, raw: str = "ふゆ、冬优子") -> None:
        # 名字表在 stage 文件里，频道构造时读一次；这里补的是同一份字典。
        self.channel.stage = {**self.channel.stage, "names.mentions": raw}

    async def said(self, text: str, *, qq: str = "43", message_id: str = "c1") -> list[str]:
        lines: list[str] = []
        self.channel.log = lines.append
        await self.say(event(text, qq=qq, message_id=message_id, at=""))
        return lines

    async def test_a_name_in_the_text_is_a_call_too(self):
        self.named()
        await self.say(event("ふゆ今晚唱什么", qq="43", message_id="n1", at=""))
        self.assertTrue(self.client.texts, "叫了名字，她看得见")

    async def test_without_a_names_list_only_an_at_counts(self):
        await self.say(event("ふゆ今晚唱什么", qq="43", message_id="n2", at=""))
        self.assertEqual(self.client.calls, [], "没有名字表 → 普通群消息 → 插话关着 → 安静")

    async def test_the_room_travels_with_the_reply(self):
        """被叫到不再只听见那一句：前面几句跟着一起来了。"""
        self.named()
        await self.say(event("今天新曲好好听", qq="43", message_id="c0", at=""))
        self.assertEqual(self.client.calls, [], "没人叫她，她只是听着")
        await self.say(event("ふゆ也觉得吧", qq="44", message_id="c1", at=""))
        prompt = " ".join(
            str(message.content) for batch in self.app.llm.seen for message in batch
        )
        self.assertIn("今天新曲好好听", prompt, "上文跟着一起来了")
        self.assertIn("刚才群里在说这些", prompt)

    async def test_she_may_decline_a_call(self):
        self.named()
        self.channel.interest = interests.Interest(
            ScriptedLLM('{"speak": false, "why": "起哄"}', size=64)
        )
        logged = await self.said("ふゆふゆふゆ", message_id="c2")
        self.assertEqual(self.client.calls, [], "不接也是回答的一种")
        self.assertTrue(any("没回" in line for line in logged), "为什么不回，得说得出来")

    async def test_the_producers_at_is_never_declined(self):
        """主人 @ 她不是判断题：他不混在五十个人里，被叫了却没应看起来就是坏了。"""
        self.channel.interest = interests.Interest(
            ScriptedLLM('{"speak": false, "why": "起哄"}', size=64)
        )
        await self.say(event("ふゆ在吗", qq="42", message_id="boss1"))
        self.assertTrue(self.client.texts, "主人的 @ 照回")

    async def test_a_members_at_is_answered_even_when_the_judge_says_no(self):
        """@ 是「在叫她」，不是「在说她的名字」——判断器没资格让它没反应。"""
        self.channel.interest = interests.Interest(
            ScriptedLLM('{"speak": false, "why": "起哄"}', size=64)
        )
        await self.say(event("ふゆ在吗", qq="43", message_id="at1"))
        self.assertTrue(self.client.texts, "@ 了就一定有声")

    async def test_an_at_does_not_cost_a_judgement(self):
        asked: list[bool] = []

        class Spy:
            async def called(self, lines, *, direct: bool = False):
                asked.append(direct)
                return True, ""

        self.channel.interest = Spy()
        await self.say(event("こんばんは", qq="43", message_id="at2"))
        self.assertEqual(asked, [], "直接叫到的，不该再花一次判断")
        self.assertTrue(self.client.texts)

    async def test_a_judge_that_cannot_answer_still_answers(self):
        """被直接叫到却沉默，看起来像坏了——所以这一处是 fail-open。"""
        self.named()
        self.channel.interest = interests.Interest(ScriptedLLM("x", fail_after=0))
        await self.say(event("ふゆ在吗", qq="43", message_id="c3", at=""))
        self.assertTrue(self.client.texts)

    async def test_an_at_is_asked_harder_than_a_name(self):
        """叫名字只是聊到她，@ 才是叫她：同一条判断，两种问法。"""
        seen: dict[bool, str] = {}
        for direct in (True, False):
            llm = ScriptedLLM('{"speak": true}', size=64)
            await interests.Interest(llm, name="ふゆ").called(
                [{"qq": "43", "card": "路人", "text": "ふゆ在吗"}], direct=direct
            )
            seen[direct] = " ".join(str(m.content) for m in llm.seen[-1])
        self.assertIn("@ 了她", seen[True])
        self.assertIn("一般都会回", seen[True])
        self.assertIn("没有 @", seen[False])
        self.assertIn("不接也很自然", seen[False])

    async def test_called_off_is_the_old_behaviour(self):
        """`QQ_CALLED=0`：被 @ 就回，而且只听见叫她的那一句——退路得留着。"""
        self.named()
        self.channel.config = replace(
            self.channel.config, called=False
        )
        await self.say(event("今天新曲好好听", qq="43", message_id="c4", at=""))
        await self.say(event("ふゆ也觉得吧", qq="44", message_id="c5", at=""))
        self.assertEqual(self.client.calls, [], "叫名字不算被叫到，回到旧行为")
        await self.say(event("ふゆ在吗", qq="44", message_id="c6"))
        self.assertTrue(self.client.texts, "@ 还是回")
        prompt = " ".join(
            str(message.content) for batch in self.app.llm.seen for message in batch
        )
        self.assertNotIn("刚才群里在说这些", prompt, "旧行为不带上下文")


class TestTheCallJudgment(unittest.IsolatedAsyncioTestCase):
    """点名那一问自己的答案：会回、不会回、判断不出来。"""

    LINES = [{"qq": "43", "card": "路人", "text": "ふゆ今晚唱什么"}]

    async def judge(self, reply: str, **kwargs: object) -> tuple[bool, str]:
        interest = interests.Interest(ScriptedLLM(reply, size=64), name="ふゆ")
        return await interest.called(self.LINES, **kwargs)  # type: ignore[arg-type]

    async def test_yes_is_yes(self):
        speak, why = await self.judge('{"speak": true, "why": "问她在行的事"}')
        self.assertTrue(speak)
        self.assertEqual(why, "问她在行的事")

    async def test_no_is_no(self):
        speak, why = await self.judge('{"speak": false, "why": "起哄"}')
        self.assertFalse(speak)
        self.assertEqual(why, "起哄")

    async def test_an_unreadable_answer_means_yes(self):
        speak, _ = await self.judge("完全不是 JSON")
        self.assertTrue(speak, "判断不出来也照回：有人叫到她，沉默像坏了")

    async def test_a_dead_model_means_yes(self):
        interest = interests.Interest(ScriptedLLM("x", fail_after=0), name="ふゆ")
        speak, why = await interest.called(self.LINES)
        self.assertTrue(speak)
        self.assertIn("判断失败", why)

    async def test_no_model_at_all_means_yes(self):
        speak, _ = await interests.Interest(None).called(self.LINES)
        self.assertTrue(speak)

    async def test_no_lines_means_yes(self):
        speak, _ = await interests.Interest(ScriptedLLM("x")).called([])
        self.assertTrue(speak, "空房间也回：叫她的那一句就是内容")


class TestNamesList(unittest.TestCase):
    def test_a_mentions_line_becomes_a_list(self):
        self.assertEqual(
            stage_file.names({"names.mentions": "ふゆ、冬優子, 黛冬优子"}, "names.mentions"),
            ("ふゆ", "冬優子", "黛冬优子"),
        )

    def test_no_entry_is_no_names(self):
        self.assertEqual(stage_file.names({}, "names.mentions"), ())
        self.assertEqual(stage_file.names({"names.mentions": "  "}, "names.mentions"), ())


class TestTheDayQuotaResetsAtMidnight(unittest.TestCase):
    def test_the_day_is_the_local_one_not_the_servers(self):
        """A quota keyed on the server's date resets in the middle of the day for the people
        it exists for: 17:00 UTC is already tomorrow in Shanghai.

        Which date it lands on depends on whether this machine has tzdata (a Windows dev box
        does not, and `ZoneInfo` falls back to the local zone), so what is asserted here is
        the thing that must hold everywhere — the answer is a day, not an instant.
        """
        late = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
        self.assertRegex(qq_module._local_day(late, "Asia/Shanghai"), r"^\d{4}-\d{2}-\d{2}$")

    def test_a_zone_that_is_not_installed_still_answers(self):
        late = datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)
        self.assertTrue(qq_module._local_day(late, "Mars/Olympus"))


class TestSpeakingIntoAQuietRoom(AmbientCase):
    """`QQ_AMBIENT_IDLE` — when nobody has said anything for a while, she may start something.

    Off by default and guarded three times over, because this is the one feature that can make
    a group mute her: the room has to be silent long enough, she has to have been silent twice
    that long, and then it is still a coin.
    """

    def use(self, **overrides):
        base = dict(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=True, ambient_p=0.0, ambient_gap=0.0, ambient_idle=5.0,
        )
        base.update(overrides)
        self.channel.config = QQConfig(**base)

    def hours_ago(self, minutes: float, group: str = "100") -> None:
        self.channel._last_seen[group] -= minutes * 60

    async def test_it_is_off_unless_asked_for(self):
        self.use(ambient_idle=0.0)
        await self.quiet("おつかれ", message_id="a")
        self.hours_ago(60)
        self.assertFalse(await self.channel.speak_up("100"))

    async def test_a_room_that_has_gone_quiet_gets_one_thought(self):
        self.use()
        await self.quiet("おつかれ", message_id="a")
        self.assertEqual(self.client.texts, [])
        self.hours_ago(6)
        self.channel._rng = Coin(0.0)
        self.assertTrue(await self.channel.speak_up("100"))
        self.assertTrue(self.client.texts)
        kinds = [segment["type"] for segment in self.client.calls[0][1]["message"]]
        self.assertNotIn("at", kinds)

    async def test_a_room_that_is_still_talking_gets_nothing(self):
        self.use()
        await self.quiet("おつかれ", message_id="a")
        self.channel._rng = Coin(0.0)
        self.assertFalse(await self.channel.speak_up("100"))

    async def test_the_coin_decides_and_usually_says_no(self):
        self.use()
        await self.quiet("おつかれ", message_id="a")
        self.hours_ago(6)
        self.channel._rng = Coin(0.9)
        self.assertFalse(await self.channel.speak_up("100"))
        self.assertEqual(self.client.texts, [])

    async def test_she_does_not_do_it_twice_in_a_row(self):
        self.use()
        await self.quiet("おつかれ", message_id="a")
        self.hours_ago(6)
        self.channel._rng = Coin(0.0, 0.0)
        self.assertTrue(await self.channel.speak_up("100"))
        self.hours_ago(1)
        self.assertFalse(await self.channel.speak_up("100"), "one thought, then she leaves it")

    async def test_filling_a_silence_spends_the_same_day(self):
        """「一天十次」 is one budget. A quiet room is another way to spend it, not a second
        one — otherwise the days she also fills silences quietly become twenty."""
        self.use(ambient_daily=1)
        await self.quiet("おつかれ", message_id="a")
        self.hours_ago(6)
        self.channel._rng = Coin(0.0, 0.0)
        self.assertTrue(await self.channel.speak_up("100"))
        self.assertEqual(self.channel._ambient_used("100"), 1)
        self.hours_ago(6)
        self.assertFalse(await self.channel.speak_up("100"), "the day is spent")

    async def test_the_night_is_quiet(self):
        from datetime import datetime, timezone

        from channels.plugin_qq import QQService

        self.use()
        service = QQService(self.app, self.channel.config, client=self.client, log=lambda _: None)
        service.channel = self.channel
        await self.quiet("おつかれ", message_id="a")
        self.hours_ago(6)
        self.channel._rng = Coin(0.0, 0.0, 0.0)
        # 传进来的是本地时间（`_maybe_idle` 的约定），所以直接写北京的钟点。
        # 周日 23:30：工作日作息的终点，睡了。休息日的延长见 `TestTheHoursSheKeeps`。
        night = datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc)
        self.assertFalse(await service._maybe_idle(now=night), "睡觉了不开口")
        morning = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(await service._maybe_idle(now=morning))


class TestMonday(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.store import Store

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for folder in ("characters/sheets", "characters/world/idols"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "characters/sheets/test.md").write_text(SHEET, encoding="utf-8")
        (self.root / "characters/characters.toml").write_text(TOML, encoding="utf-8")
        self.store = Store(self.root / "m.db")
        self.addCleanup(self.store.close)

    async def service(self, **kwargs):
        from channels.plugin_qq import QQService

        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(self.root / "m.db"), characters=str(self.root / "characters"),
            media=str(self.root / "media"), turns_per_hour=0,
        )
        app = App(settings, ScriptedLLM(REPLY, size=5))
        self.addCleanup(app.close)
        client = FakeClient()
        base = dict(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", monday_hour=9,
        )
        base.update(kwargs)
        config = QQConfig(**base)
        service = QQService(app, config, client=client, log=lambda line: None)
        app.store.migrate(service.migrations())
        return service, client

    async def test_one_sticker_on_a_monday_and_no_more_that_week(self):
        from datetime import datetime, timezone

        service, client = await self.service()
        service.pack.save(PNG)
        service.pack.tag("上班")
        monday = datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc)
        await service._maybe_monday(monday)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1]["message"][0]["type"], "image")
        await service._maybe_monday(monday.replace(hour=18))
        self.assertEqual(len(client.calls), 1, "once a week, not once an hour")

    async def test_nothing_happens_on_other_days_or_before_the_hour(self):
        from datetime import datetime, timezone

        service, client = await self.service()
        service.pack.save(PNG)
        service.pack.tag("上班")
        await service._maybe_monday(datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc))  # Tuesday
        await service._maybe_monday(datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc))  # Monday, too early
        self.assertEqual(client.calls, [])

    async def test_without_a_tagged_sticker_nothing_is_sent(self):
        from datetime import datetime, timezone

        service, client = await self.service()
        service.pack.save(PNG)
        await service._maybe_monday(datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(client.calls, [])

    async def test_it_goes_to_the_group_that_asked_and_no_other(self):
        """QQ_MONDAY_GROUPS：这个要求是一个群提的，另一个群并不在等它。"""
        from datetime import datetime, timezone

        service, client = await self.service(groups=("100", "200"), monday_groups=("200",))
        service.pack.save(PNG)
        service.pack.tag("上班")
        await service._maybe_monday(datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc))
        self.assertEqual([call[1]["group_id"] for call in client.calls], ["200"])

    async def test_without_that_setting_it_still_goes_everywhere(self):
        from datetime import datetime, timezone

        service, client = await self.service(groups=("100", "200"))
        service.pack.save(PNG)
        service.pack.tag("上班")
        await service._maybe_monday(datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(sorted(call[1]["group_id"] for call in client.calls), ["100", "200"])


class TestWhenSheWritesFirst(unittest.IsolatedAsyncioTestCase):
    """主动私聊的时间点存在库里，不是只存在那个任务的内存里。

    宿主每次重启都会把内存里的随机间隔丢掉，于是每一轮都在「15 分钟 + 60–150 分钟」里
    重来一遍；半小时崩一次的机器上，她永远走不到那个间隔的尽头 —— 表现就是「从来不先开口」。
    存在 `qq_flags` 里以后，重启等的是同一个时刻，停机期间到点的会在活过来之后补上。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for folder in ("characters/sheets", "characters/world/idols"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "characters/sheets/test.md").write_text(SHEET, encoding="utf-8")
        (self.root / "characters/characters.toml").write_text(TOML, encoding="utf-8")

    async def service(self, **kwargs):
        from channels.plugin_qq import QQService

        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(self.root / "m.db"), characters=str(self.root / "characters"),
            media=str(self.root / "media"), turns_per_hour=0,
        )
        app = App(settings, ScriptedLLM(REPLY, size=5))
        self.addCleanup(app.close)
        base = dict(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", love_ping=True, owner_love=95.0,
        )
        base.update(kwargs)
        service = QQService(app, QQConfig(**base), client=FakeClient(), log=lambda line: None)
        app.store.migrate(service.migrations())
        return service

    async def test_a_draw_is_stored_and_a_restart_keeps_it(self):
        service = await self.service()
        now = datetime(2026, 10, 26, 14, 0, tzinfo=timezone(timedelta(hours=9)))  # 白天，开着的时段
        due = service._love_draw(now)
        self.assertGreaterEqual(due - now.timestamp(), 60 * 60)
        self.assertLessEqual(due - now.timestamp(), 150 * 60)
        again = await self.service()  # 另一个进程，同一个库
        self.assertAlmostEqual(again._love_due(), due, delta=1, msg="重启不该重新摇一次")

    async def test_the_night_is_pushed_to_the_morning_not_drawn(self):
        service = await self.service()
        night = datetime(2026, 10, 26, 23, 30, tzinfo=timezone(timedelta(hours=9)))
        due = service._love_draw(night, until_window=True)
        when = datetime.fromtimestamp(due, night.tzinfo)
        self.assertEqual((when.day, when.hour), (27, 9), "夜里摇一次等于每晚白扔一个间隔")
        # 到点那一刻还在夜里（停机到半夜才活过来）：不写，推到早上。
        service.pack.set_flag("love:next", f"{night.timestamp() - 60:.0f}")
        self.assertEqual(service._love_step(night), "night")

    async def test_a_gap_that_came_due_while_it_was_down_fires(self):
        service = await self.service()
        now = datetime(2026, 10, 26, 15, 0, tzinfo=timezone(timedelta(hours=9)))
        service.pack.set_flag("love:next", f"{now.timestamp() - 600:.0f}")
        self.assertEqual(service._love_step(now), "fire")

    async def test_a_restart_right_after_a_message_does_not_send_twice(self):
        service = await self.service()
        now = datetime(2026, 10, 26, 15, 0, tzinfo=timezone(timedelta(hours=9)))
        service.pack.set_flag("love:last", f"{now.timestamp() - 60:.0f}")
        service.pack.set_flag("love:next", f"{now.timestamp() - 30:.0f}")
        self.assertEqual(service._love_step(now), "floor", "刚发过的，重启也不能再发一条")

    async def test_a_fresh_install_has_nothing_stored_and_draws(self):
        service = await self.service()
        now = datetime(2026, 10, 26, 15, 0, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(service._love_due(), 0.0)
        self.assertEqual(service._love_step(now), "draw")

    async def test_a_future_time_is_only_waited_for(self):
        service = await self.service()
        now = datetime(2026, 10, 26, 15, 0, tzinfo=timezone(timedelta(hours=9)))
        service.pack.set_flag("love:next", f"{now.timestamp() + 1800:.0f}")
        self.assertEqual(service._love_step(now), "wait")


class TestTheHeartbeat(unittest.IsolatedAsyncioTestCase):
    """socket 开着 ≠ 还登录着。

    QQ 把号挤下线（别的手机登录、改密码、安全验证）的时候，NapCat 的 websocket 还在，
    只是什么都不再送过来。从进程里看不出任何异常——用户看到的就是「有时候不回，过一阵又
    好了」。每五分钟问一次「现在登录的是谁」，是这件事唯一能被看见的地方。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for folder in ("characters/sheets", "characters/world/idols"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "characters/sheets/test.md").write_text(SHEET, encoding="utf-8")
        (self.root / "characters/characters.toml").write_text(TOML, encoding="utf-8")

    async def service(self):
        from channels.plugin_qq import QQService

        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(self.root / "m.db"), characters=str(self.root / "characters"),
            media=str(self.root / "media"), turns_per_hour=0,
        )
        app = App(settings, ScriptedLLM(REPLY, size=5))
        self.addCleanup(app.close)
        self.lines: list[str] = []
        client = FakeClient()
        config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",), character="test"
        )
        service = QQService(app, config, client=client, log=self.lines.append)
        app.store.migrate(service.migrations())
        return service, client

    async def test_the_right_account_says_nothing(self):
        service, _client = await self.service()
        self.assertEqual(await service._check_login(), "ok")
        self.assertEqual(self.lines, [], "正常的时候不要刷屏")

    async def test_another_account_is_said_out_loud(self):
        service, client = await self.service()
        client.login = {"user_id": 1234}
        self.assertEqual(await service._check_login(), "other")
        self.assertTrue(any("被挤下线" in line for line in self.lines), self.lines)

    async def test_a_qq_that_does_not_answer_is_said_out_loud(self):
        service, client = await self.service()
        client.broken = True
        self.assertEqual(await service._check_login(), "down")
        self.assertTrue(any("心跳失败" in line for line in self.lines), self.lines)

    async def test_a_reconnect_replays_what_failed_to_send(self):
        """掉线一小时之后再登录，这段时间说的话不能就这么没了。"""
        service, client = await self.service()
        box = service.channel.outbox
        box._resume_gap = 0.0

        def pending() -> int:
            return service.app.store.query(
                "SELECT COUNT(*) AS n FROM qq_outbox WHERE state = 'pending'"
            )[0]["n"]

        client.broken = True
        await box.send("100", "掉线的时候说的话")
        self.assertEqual(pending(), 1, "发不出去的留着，不抛错")

        client.broken = False
        await service._reconnected()
        self.assertEqual(pending(), 0, "连上的那一刻补发")
        self.assertTrue(any("补发了" in line for line in self.lines), self.lines)


def age(path: Path, seconds: float = 5.0) -> None:
    """Make an edit look old, so the settle window does not hide it from the test."""
    import os

    when = time.time() - seconds
    os.utime(path, (when, when))


class TestHotReload(ChannelCase):
    """Editing a rule file, then talking: no restart in between."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.world_dir = Path(self.tmp.name) / "characters" / "world"
        self.group_rules = self.world_dir / "fuyuko-qq.md"

    def seen_sections(self) -> list:
        """Run one turn and hand back the sections it was given."""
        seen: dict = {}
        original = self.app.turn

        def spy(*args, **kwargs):
            seen["sections"] = kwargs.get("sections") or []
            return original(*args, **kwargs)

        self.app.turn = spy
        return seen

    async def test_an_edited_rule_file_reaches_the_next_turn(self):
        self.group_rules.write_text("【场合】第一版规矩。\n", encoding="utf-8")
        age(self.group_rules)
        seen = self.seen_sections()
        await self.say(event("ひとつめ", message_id="r1"))
        self.assertIn("第一版规矩", "\n".join(s.text for s in seen["sections"]))

        self.group_rules.write_text("【场合】第二版规矩。\n", encoding="utf-8")
        age(self.group_rules)
        seen = self.seen_sections()
        await self.say(event("ふたつめ", message_id="r2"))
        text = "\n".join(s.text for s in seen["sections"])
        self.assertIn("第二版规矩", text)
        self.assertNotIn("第一版规矩", text)

    async def test_the_cached_prompt_is_dropped_when_the_rules_change(self):
        before = self.channel._engine("test", presence.GROUP)
        self.group_rules.write_text("【场合】別の規矩。\n", encoding="utf-8")
        age(self.group_rules)
        after = self.channel._engine("test", presence.GROUP)
        self.assertIsNot(before, after, "the prompt must be rebuilt, not reused")

    async def test_an_edited_roster_reaches_the_persona(self):
        office = self.world_dir / "office.md"
        self.assertIn("あさひちゃん", self.channel.world.persona().text)
        office.write_text("# 面熟的人\n\n- あさひちゃん：同僚。\n- めぐるちゃん：先輩。\n", encoding="utf-8")
        age(office)
        self.assertIn("めぐるちゃん", self.channel.world.persona().text)

    async def test_a_colleagues_file_is_reread_on_its_next_lookup(self):
        roster = Path(self.tmp.name) / "characters" / "characters.toml"
        roster.write_text(WORLD_TOML, encoding="utf-8")
        age(roster)
        idol = self.world_dir / "idols" / "asahi.md"
        idol.write_text("# あさひ\n\n- 第一版。\n", encoding="utf-8")
        age(idol)
        first = self.channel.world.lookup("Asahi Serizawa は？")
        self.assertIn("第一版", first[0].text)
        idol.write_text("# あさひ\n\n- 第二版。\n", encoding="utf-8")
        age(idol)
        second = self.channel.world.lookup("Asahi Serizawa は？")
        self.assertIn("第二版", second[0].text)

    async def test_the_owner_can_ask_for_a_reload(self):
        self.channel._engine("test", presence.GROUP)
        await self.say(event("/重载", qq="42", message_id="rl1"))
        self.assertTrue(self.client.texts)
        self.assertIn("重新读过", self.client.texts[-1])
        self.assertEqual(self.channel._engines, {}, "the cached prompts are gone")

    async def test_a_stranger_cannot_force_a_reload(self):
        self.channel._engine("test", presence.GROUP)
        await self.say(event("/重载", qq="43", message_id="rl2"))
        self.assertEqual(self.client.calls, [])
        self.assertTrue(self.channel._engines, "nothing was dropped")


LADDER = """【场合】群聊。
【对面】{{addressee}}
【距离】{{distance}}
【时间】{{time}}
【阶段 new｜0】
初次见面，语气软一点。
【阶段 familiar｜3】
聊过几次，不那么甜了。
【阶段 close｜10】
熟了，开始挖苦。
【阶段 inner｜30】
自己人，平时的节奏。
"""


class TestStageLadder(unittest.TestCase):
    """How well she knows someone is a count, and the count picks a rung."""

    def rules(self, text: str = LADDER):
        return presence.Presence(text)

    def test_the_rungs_are_read_in_order_of_the_count(self):
        rungs = self.rules().stages()
        self.assertEqual([name for name, _floor, _p in rungs], ["new", "familiar", "close", "inner"])
        self.assertEqual([floor for _n, floor, _p in rungs], [0, 3, 10, 30])

    def test_each_rung_keeps_its_own_prose(self):
        rules = self.rules()
        self.assertIn("初次见面", rules.stage_prose("new"))
        self.assertIn("自己人", rules.stage_prose("inner"))
        self.assertEqual(rules.stage_prose("nope"), "")

    def test_the_count_picks_the_rung_and_the_boundaries_are_inclusive(self):
        rules = self.rules()
        cases = {0: "new", 2: "new", 3: "familiar", 9: "familiar",
                 10: "close", 29: "close", 30: "inner", 500: "inner"}
        for turns, expected in cases.items():
            self.assertEqual(rules.stage_for(turns), expected, f"at {turns} answers")

    def test_the_producer_starts_further_up_but_still_climbs(self):
        rules = self.rules()
        self.assertEqual(rules.stage_for(0, owner=True), "close")
        self.assertEqual(rules.stage_for(0, owner=False), "new")
        self.assertEqual(rules.stage_for(40, owner=True), "inner")

    def test_the_owner_floor_can_be_turned_off_to_test_the_first_meeting_voice(self):
        self.assertEqual(presence.Presence(LADDER, owner_stage="").stage_for(0, owner=True), "new")
        self.assertEqual(presence.Presence(LADDER).stage_for(0, owner=True), "close")

    def test_a_file_without_rungs_says_so_and_the_old_prose_still_works(self):
        from datetime import datetime, timezone

        plain = presence.Presence("【对面】{{addressee}}\\n【时间】{{time}}\\n")
        self.assertEqual(plain.stages(), [])
        self.assertEqual(plain.stage_for(99), "")
        text = plain.sections(
            addressee=presence.STRANGER, now=datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)
        )[0].text
        self.assertIn("不熟", text, "the built-in paragraph is still the fallback")

    def test_the_rung_is_persona_and_sits_before_the_sheet(self):
        # Measured, not assumed: with the rung in the turn section, four rungs one, four and
        # thirty answers apart produced four nearly identical replies.
        head = self.rules().stage_head("inner")
        self.assertEqual(head.slot, "persona")
        self.assertIn("自己人", head.text)

    def test_the_turn_section_carries_no_rung_at_all(self):
        from datetime import datetime, timezone

        text = self.rules().sections(
            addressee=presence.STRANGER, turns=30, now=datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)
        )[0].text
        self.assertNotIn("【阶段", text)
        self.assertNotIn("自己人", text)
        self.assertNotIn("初次见面", text)
        self.assertNotIn("【对面】", text, "the rung replaced that line")
        self.assertNotIn("{{", text)

    def test_a_file_without_rungs_keeps_the_face_line(self):
        from datetime import datetime, timezone

        text = presence.Presence("【对面】{{addressee}}\\n【时间】{{time}}\\n").sections(
            addressee=presence.STRANGER, turns=30, now=datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)
        )[0].text
        self.assertIn("【对面】", text)
        self.assertIn("不熟", text)

    def test_an_interjection_does_not_take_a_rung(self):
        from datetime import datetime, timezone

        rules = self.rules()
        text = rules.sections(
            addressee=presence.OWNER, turns=99, now=datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc),
            ambient=True,
        )[0].text
        self.assertIn("没人叫你", text, "the room gets the crowd paragraph, not a rung")
        self.assertNotIn("【阶段", text)

    def test_a_private_chat_has_no_ladder(self):
        from datetime import datetime, timezone

        rules = presence.Presence(
            LADDER, direct="【场合】私聊。只有你们两个。\\n【时间】{{time}}\\n"
        )
        text = rules.sections(
            addressee=presence.OWNER, turns=0, scene=presence.PRIVATE,
            now=datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc),
        )[0].text
        self.assertIn("只有你们两个", text)
        self.assertNotIn("初次见面", text, "there is nobody new in a private chat")

    def test_the_shipped_rules_have_a_ladder(self):
        shipped = presence.Presence(Path(__file__).resolve().parents[1] / "characters" / "world" / "fuyuko-qq.md")
        names = [name for name, _f, _p in shipped.stages()]
        self.assertEqual(names, ["new", "familiar", "close", "inner"])
        first = shipped.stage_prose("new")
        self.assertIn("♡", first, "the first conversation is the sweet one")
        self.assertIn("ふゆ♡", first, "the signature is allowed there")
        self.assertIn("三四条", first, "and it comes with a budget, not a licence")
        self.assertIn("不写「ふゆ♡」", shipped.stage_prose("close"))

    def test_the_title_line_does_not_reach_the_prompt(self):
        from datetime import datetime, timezone

        shipped = presence.Presence(
            Path(__file__).resolve().parents[1] / "characters" / "world" / "fuyuko-qq.md"
        )
        text = "".join(
            section.text
            for section in shipped.sections(
                addressee=presence.STRANGER, now=datetime.now(timezone.utc)
            )
        )
        self.assertNotIn("群聊语域", text, "the H1 is for the editor, not for her")
        self.assertIn("【时间】", text)

    def test_the_shipped_rules_treat_the_group_as_producers(self):
        from datetime import datetime, timezone

        shipped = presence.Presence(
            Path(__file__).resolve().parents[1] / "characters" / "world" / "fuyuko-qq.md"
        )
        text = "".join(
            section.text
            for section in shipped.sections(
                addressee=presence.STRANGER, now=datetime.now(timezone.utc)
            )
        )
        self.assertIn("都是制作人", shipped.scene_head().text, "the scene line says who is in the room")
        self.assertIn("每一个都是", text, "and the way she addresses them follows")
        self.assertIn("【企划】", text, "she does not have to explain the project to them")
        self.assertIn("别把昵称当身份", text, "screen names are jokes, not identities")

    def test_the_register_survives_into_the_chinese_subtitle(self):
        """The group only ever sees the subtitle — the Japanese is for the voice.

        Reported as 「没聊过的人 @ 她，她还是用本性说话」: the Japanese was です・ます all along,
        but `_stream` emits the `zh` segments only, and Chinese has no keigo. Without samples,
        the model translated politeness into plain speech, and plain speech *is* her private
        voice. So the rules have to say what the Chinese should look like, and show it.

        The first fix over-corrected into a ban list — 「这些私下短句群里不许说」. The producer's
        correction: the register is a dial, not a ban, and for her the dial runs one way —
        the stranger the person, the deeper into the cutesy public 「冬優子」 she goes; with
        people she knows the dry short lines come back on their own. The rule now says that,
        and the test guards both halves.
        """
        from datetime import datetime, timezone

        root = Path(__file__).resolve().parents[1] / "characters" / "world"
        shipped = presence.Presence(root / "fuyuko-qq.md", direct=root / "fuyuko-dm.md")

        text = "".join(
            section.text
            for section in shipped.sections(
                addressee=presence.STRANGER, now=datetime.now(timezone.utc)
            )
        )
        # 「冬優（ふゆ）」是夹子那一面，「冬優子」是本体。这句原先写反了（越陌生越往冬優子），
        # 名字一错，模型就把营业人格和本体对调了。
        self.assertIn("越陌生，你越往「冬優（ふゆ）」那边去", text, "the rule names the dial")
        self.assertIn("「冬優子」才是本体", text, "两个名字谁是面具谁是本人，要写清楚")
        self.assertIn("……不太喜欢", text, "and shows how the polite end is built")
        self.assertIn("不是禁句", text, "私下的短句群里也允许出现，松紧看档位")
        self.assertIn("别日语软、中文冲", text, "日语切到哪一档，中文就落在同一档")
        for rung in ("new", "familiar", "close", "inner"):
            prose = shipped.stage_prose(rung)
            self.assertIn("中文", prose, f"{rung} 档要有中文样本，不然只有日语是对的")

    def test_the_shipped_dm_rules_allow_intimacy_but_draw_a_line(self):
        """The producer asked for this, and it is a product decision, not an accident.

        Private chat is the one place the register is hers: two adults who like each other may
        be close there. What must not quietly disappear is the line — she refuses anything past
        a kiss, and she refuses *in character*, not with a policy statement.
        """
        from datetime import datetime, timezone

        root = Path(__file__).resolve().parents[1] / "characters" / "world"
        shipped = presence.Presence(root / "fuyuko-qq.md", direct=root / "fuyuko-dm.md")

        scene = shipped.scene_head(presence.PRIVATE)
        self.assertEqual(scene.slot, "persona", "the relationship belongs before the sheet")
        self.assertIn("亲近不用藏", scene.text)
        self.assertIn("吻为止", scene.text)

        text = "".join(
            section.text
            for section in shipped.sections(
                addressee=presence.OWNER,
                now=datetime.now(timezone.utc),
                scene=presence.PRIVATE,
            )
        )
        self.assertIn("【亲密】", text)
        self.assertIn("【界线】", text)
        self.assertIn("そこまで", text, "a refusal she would actually say")
        self.assertIn("不写身体细节", text, "affection yes, explicit detail no")
        self.assertIn("不装没听见", text, "and the refusal is not a cold lecture")
        self.assertNotIn("{{", text)

    def test_the_sheet_does_not_ban_being_close_in_private(self):
        """The sheet is read *after* the scene line, so a blanket 「不接受越界的恋爱」 there
        outweighs 「亲近不用藏」 and every reply ends up pushing him away. The sheet must
        leave the private distance to the scene file, and only keep the public one."""
        sheet = (
            Path(__file__).resolve().parents[1] / "characters" / "sheets" / "fuyuko.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("不接受越界的恋爱", sheet)
        self.assertIn("有外人在", sheet, "the public distance is still hers")
        self.assertIn("两个人独处", sheet, "and the private one belongs to the scene file")
        self.assertIn("到吻为止", sheet)

    def test_the_private_rules_tell_her_to_take_it(self):
        """Reluctance is a habit of speech, not a wall: one line of 嘴硬 per turn at most,
        and the line is only about sex — not about being held."""
        root = Path(__file__).resolve().parents[1] / "characters" / "world"
        dm = (root / "fuyuko-dm.md").read_text(encoding="utf-8")
        self.assertIn("接住他的亲近", dm)
        self.assertIn("最多嘴硬一句", dm)
        self.assertIn("不要拿它当挡箭牌", dm, "the limit is about sex, not about closeness")
        self.assertNotIn("はぁ？　あんた何言ってんの", dm, "the refusal template is not how she answers 好き")


    def test_the_facts_file_carries_the_hard_numbers(self):
        """The sheet has her voice; `facts/` has what a fan would ask her about. It goes in
        whole (`SCCORE_RECALL=whole`), so the things below must actually be in it — a character
        who cannot say her own birthday is a character nobody believes."""
        root = Path(__file__).resolve().parents[1] / "characters"
        facts = (root / "facts" / "fuyuko.md").read_text(encoding="utf-8")
        for needle in (
            "12 月 4 日", "163cm", "服装设计", "SOS", "ガガリズム",
            "Landing Point", "Mira♡Mira", "あさひ",
        ):
            self.assertIn(needle, facts)
        self.assertNotIn("{{", facts)
        self.assertNotIn(
            "<!--", facts, "read_facts hands the file over as-is: a comment here is prompt text"
        )
        self.assertIn("永远是「ふゆ」", facts, "the notes must not teach her to call herself 冬優子")

    def test_the_facts_file_knows_the_main_story_chapter_by_chapter(self):
        """A fan can ask 「W.I.N.G. 第5话你干嘛失联」. Chapter titles are the one part of her
        own history that people actually quote, so they are in the notes, one line each."""
        facts = (
            Path(__file__).resolve().parents[1] / "characters" / "facts" / "fuyuko.md"
        ).read_text(encoding="utf-8")
        for needle in (
            "ハートフル・フェアリーテイル",
            "諦めたくないものはひとつだけ",
            "終演後、こちら側",
            "動くかげ",
            "ほら、取り零した",
            "白いツバサ",
            "誘爆ハートビート",
        ):
            self.assertIn(needle, facts)

    def test_the_notes_leave_out_the_failed_routes(self):
        """User's call, and a good one: the losing routes are what the game does to her, not
        what she lived. A memory full of 「落選」 makes her answer as a failure."""
        facts = (
            Path(__file__).resolve().parents[1] / "characters" / "facts" / "fuyuko.md"
        ).read_text(encoding="utf-8")
        for gone in (
            "見えなかった景色",    # W.I.N.G. 落选后的结局
            "オーディション後 敗北",
            "シーズン1（失敗）",
            "敗退コミュ",
            "落選",
            "淘汰",
        ):
            self.assertNotIn(gone, facts, f"{gone} 是失败线，不进她的记忆")

    def test_the_work_layer_knows_the_song_names(self):
        meta = (Path(__file__).resolve().parents[1] / "characters" / "world" / "meta.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("SOS", meta)
        self.assertIn("Landing Point", meta)
        self.assertIn("if(!Straylight)", meta, "the events she carried are outward knowledge too")
        self.assertIn("誘爆ハートビート", meta)
        self.assertLessEqual(len(meta), 2200, "MAX_META cuts longer files; keep it inside")


class TestTheTwoRelationshipsAreSeparate(ChannelCase):
    """群聊和私聊各记各的：五十个人在场时她对你的态度，不是只剩你们两个时的那个。

    一个数字的时候，群里一句难听的话会把私聊里的关系一起压下去 —— 那是错的：
    owner 在群里是「群里的一个制作人」，在私聊里才是那个人。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0, private=True,
        )

    def put(self, surface: str, value: float) -> None:
        user = self.channel.identities.resolve("42", card="", role="owner")
        self.app.store.execute(
            "DELETE FROM affinity_scenes"
            " WHERE user_id = ? AND character_id = 'test' AND surface = ?",
            (user, surface),
        )
        self.app.store.execute(
            "INSERT INTO affinity_scenes"
            " (user_id, character_id, surface, value, updated_at, cooling)"
            " VALUES (?, 'test', ?, ?, ?, 0)",
            # `now`, not a fixed date: a stamp in the past lets the daily recovery move the
            # number and the assertion starts failing at a particular hour of the day.
            (user, surface, value, datetime.now(timezone.utc).isoformat()),
        )

    async def test_a_turn_carries_its_surface(self):
        seen: list = []
        original = self.app.turn

        def spy(*args, **kwargs):
            seen.append(kwargs.get("surface"))
            return original(*args, **kwargs)

        self.app.turn = spy
        self.addCleanup(setattr, self.app, "turn", original)
        await self.say(event(message_id="g1"))
        await self.say(direct(message_id="d1"))
        self.assertEqual(seen, ["group", "dm"])

    async def test_each_room_reads_its_own_number(self):
        self.put("group", 80.0)  # 亲近
        self.put("dm", 40.0)  # 信任
        await self.say(event("/好感", qq="42", message_id="g2"))
        self.assertIn("亲近", self.client.texts[-1])
        await self.say(direct("/好感", message_id="d2"))
        self.assertIn("信任", self.client.texts[-1])

    async def test_the_group_number_is_untouched_by_a_private_floor(self):
        self.put("group", 20.0)  # 疏远，群里确实不太高兴
        self.put("dm", 90.0)
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0, private=True,
            owner_affinity=100.0,
        )
        self.channel.hold_affinity()  # 私聊的地板只抬私聊
        user = self.channel.identities.resolve("42", card="", role="owner")
        group = self.app.affinity.read(user, "test", surface="group").value
        dm = self.app.affinity.read(user, "test", surface="dm").value
        # 差个 2e-7 是「按小时恢复」在几微秒里算出来的，不是地板碰了它
        self.assertAlmostEqual(group, 20.0, places=3, msg="群里那一行是她的，地板不该碰")
        self.assertEqual(dm, 100.0)


class TestTheProducersAffinityFloor(ChannelCase):
    """The judge can only move a number, so the number needs a floor: a producer who asks
    the same thing a few times must not end up in a tier where she sounds disappointed."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            owner_affinity=95.0,
        )

    def standing(self, qq_id: str, value: float, *, role: str = "owner") -> str:
        user = self.channel.identities.resolve(qq_id, card="", role=role)
        # The private chat's row: the floor and the love flag are that relationship's,
        # never the group's (see `affinity_scenes`).
        self.app.store.execute(
            "DELETE FROM affinity_scenes WHERE user_id = ? AND character_id = 'test'", (user,)
        )
        self.app.store.execute(
            "INSERT INTO affinity_scenes"
            " (user_id, character_id, surface, value, updated_at, cooling)"
            " VALUES (?, 'test', 'dm', ?, ?, 0)",
            (user, value, datetime.now(timezone.utc).isoformat()),
        )
        return user

    def value_of(self, user: str) -> float:
        rows = self.app.store.query(
            "SELECT value FROM affinity_scenes"
            " WHERE user_id = ? AND character_id = 'test' AND surface = 'dm'",
            (user,),
        )
        return float(rows[0]["value"])

    async def test_it_lifts_the_producer_back_to_the_floor(self):
        user = self.standing("42", 28.85)
        self.assertEqual(self.channel.hold_affinity(), 1)
        self.assertEqual(self.value_of(user), 95.0)

    async def test_it_never_lowers_him(self):
        user = self.standing("42", 100.0)
        self.assertEqual(self.channel.hold_affinity(), 0)
        self.assertEqual(self.value_of(user), 100.0)

    async def test_it_leaves_everyone_else_alone(self):
        other = self.standing("43", 20.0, role="member")
        self.standing("42", 28.85)
        self.channel.hold_affinity()
        self.assertEqual(self.value_of(other), 20.0, "the floor is the producer's, not a gift to all")

    async def test_zero_turns_it_off(self):
        user = self.standing("42", 10.0)
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0, owner_affinity=0.0,
        )
        self.assertEqual(self.channel.hold_affinity(), 0)
        self.assertEqual(self.value_of(user), 10.0)


class TestTheTwoOfThemAreTogether(ChannelCase):
    """A relationship that changes kind, not degree: two bars to get in, a flag after that,
    a lower floor once in, and messages he did not ask for."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.dm = Path(self.tmp.name) / "characters" / "world" / "fuyuko-dm.md"
        self.dm.write_text(
            "【场景】私聊，只有你们两个。\n"
            "【恋爱】你们已经在谈恋爱了，只是没对外说。\n"
            "【时间】{{time}}\n",
            encoding="utf-8",
        )
        age(self.dm)
        self.channel.presence.refresh()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            private=True, owner_affinity=100.0, owner_love=95.0,
            owner_love_turns=20, owner_love_floor=90.0, love_ping=True,
        )

    def standing(self, value: float, *, minutes_ago: float = 60) -> str:
        user = self.channel.identities.resolve("42", card="", role="owner")
        when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        self.app.store.execute(
            "DELETE FROM affinity_scenes WHERE user_id = ? AND character_id = 'test'", (user,)
        )
        self.app.store.execute(
            "INSERT INTO affinity_scenes"
            " (user_id, character_id, surface, value, updated_at, cooling)"
            " VALUES (?, 'test', 'dm', ?, ?, 0)",
            (user, value, when.isoformat()),
        )
        return user

    def value_of(self, user: str) -> float:
        rows = self.app.store.query(
            "SELECT value FROM affinity_scenes"
            " WHERE user_id = ? AND character_id = 'test' AND surface = 'dm'",
            (user,),
        )
        return float(rows[0]["value"])

    def together(self) -> None:
        """把他们放进「已在一起」。**不再靠 `refresh_love`**：那个函数现在只回答
        「够不够格开口」，把状态改过去的是告白场景（`_confess`）。这组测试测的是
        「在一起之后」的行为，所以直接写状态。"""
        self.channel.identities.resolve("42", card="", role="owner")
        self.channel.identities.set_turns("42", 25)
        self.standing(100.0)
        self.channel.identities.set_relation("42", "loving")
        self.assertTrue(self.channel.refresh_love("42"), "已经在一起，问「能不能说」就没意义了")

    async def test_it_takes_both_bars(self):
        """好感与对话次数两条都要过。门槛现在由她的 PLove 决定（test 角色 = 3 → 85 / 30）。"""
        self.channel.identities.resolve("42", card="", role="owner")
        self.channel.identities.set_turns("42", 5)
        self.standing(100.0)
        self.assertFalse(self.channel.refresh_love("42"), "too few conversations")

        self.channel.identities.set_turns("42", 40)
        self.standing(80.0)
        self.assertFalse(self.channel.refresh_love("42"), "85 is the bar for PLove 3")

        self.standing(96.0)
        self.assertTrue(self.channel.refresh_love("42"))
        self.assertEqual(
            self.channel.identities.relation("42"),
            "none",
            "够格只是「可以开口」，不是「已经在一起」——这一步现在要告白",
        )

    async def test_a_stranger_never_gets_it(self):
        self.channel.identities.resolve("43", card="", role="member")
        self.channel.identities.set_turns("43", 50)
        self.standing(100.0)
        self.assertFalse(self.channel.refresh_love("43"))
        self.assertFalse(self.channel.identities.loving("43"))

    async def test_a_night_below_the_bar_does_not_end_it(self):
        self.together()
        self.channel.identities.set_turns("42", 3)  # even a reset of the count
        self.standing(70.0)
        self.assertTrue(self.channel.refresh_love("42"), "the flag is not a threshold")
        self.assertTrue(self.channel.identities.loving("42"))

    def test_the_sentence_reaches_the_private_engine_and_only_there(self):
        self.together()
        private = "\n".join(
            s.text for s in self.channel._engine("test", presence.PRIVATE, "", "loving").base
        )
        self.assertIn("谈恋爱", private)
        group = "\n".join(s.text for s in self.channel._engine("test", presence.GROUP, "", "").base)
        self.assertNotIn("谈恋爱", group, "fifty people, none of them in this relationship")
        self.assertIsNone(self.channel.presence.love_head(presence.GROUP))
        self.assertIsNotNone(self.channel.presence.love_head(presence.PRIVATE))

    async def test_the_floor_is_lower_once_they_are_together(self):
        self.together()
        user = self.standing(88.0, minutes_ago=12 * 60)
        self.assertEqual(self.channel.hold_affinity(), 1)
        self.assertEqual(self.value_of(user), 90.0, "90 is the line, not 100")

    async def test_a_real_fall_is_left_alone_for_the_evening(self):
        self.together()
        user = self.standing(80.0, minutes_ago=5)
        self.assertEqual(self.channel.hold_affinity(), 0, "let him see it")
        self.assertEqual(self.value_of(user), 80.0)

        user = self.standing(80.0, minutes_ago=8 * 60)  # the next morning
        self.assertEqual(self.channel.hold_affinity(), 1)
        self.assertEqual(self.value_of(user), 90.0)

    async def test_she_writes_first_only_when_they_are_together(self):
        self.assertFalse(await self.channel.love_ping("42"), "not yet")
        self.assertEqual(self.client.texts, [])

        self.together()
        self.assertTrue(await self.channel.love_ping("42"))
        self.assertEqual(len(self.client.texts), 2)  # the scripted reply, in two segments
        self.assertEqual(self.client.calls[0][0], "send_private_msg")

    async def test_off_means_off(self):
        self.together()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            private=True, owner_love=95.0, owner_love_turns=20, love_ping=False,
        )
        self.assertFalse(await self.channel.love_ping("42"))
        self.assertEqual(self.client.texts, [])


class Recorder:
    """一个只会记账的模型：它记下每次被问了什么，回答是写死的。"""

    def __init__(self, reply: str = "## 近况\n\n- 他最近在忙一个新项目") -> None:
        self.reply = reply
        self.prompts: list = []
        self.broken: Exception | None = None

    async def stream(self, messages):
        self.prompts.append(list(messages))
        if self.broken is not None:
            raise self.broken
        for start in range(0, len(self.reply), 7):
            yield self.reply[start : start + 7]

    def asked(self, index: int = -1) -> str:
        return "\n".join(str(m.content) for m in self.prompts[index])


class TestHerMemoryFile(unittest.IsolatedAsyncioTestCase):
    """每三天把私聊写成一份记忆，新的盖掉旧的；群聊一周一次。

    这件事的重点不是「存了聊天记录」：记录会无限变长，最后谁也读不了。所以每轮都是
    **重写整份**——写完之后上一版就不存在了，磁盘上那份文件就是记忆本身。
    """

    NOW = datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "db.sqlite")
        self.addCleanup(self.store.close)
        self.llm = Recorder()
        self.digest = digest_module.Digest(
            self.store,
            self.llm,
            self.root,
            clock=lambda: self.NOW,
            self_name="ふゆ",
            owner_name="制作人",
        )

    # ---- 造一点对话

    def thread(self, title: str, name: str) -> str:
        user = self.store.create_user(name)
        return self.store.create_conversation(user, "test", title).id

    def talk(self, conversation: str, lines) -> None:
        for line in lines:
            if isinstance(line, str):
                self.store.add_turn(conversation, line, [Segment("はい", "嗯")])
            else:
                self.store.add_turn(conversation, line[0], [Segment(line[1], "")])

    def dm(self, *lines) -> str:
        conversation = self.thread("qq:dm", "制作人")
        self.talk(conversation, lines)
        return conversation

    # ---- 私聊

    async def test_the_first_run_writes_a_file_and_says_so(self):
        self.dm("今日ひま？", "コーヒー飲んだ")
        path = await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        self.assertIsNotNone(path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("# 和制作人的记忆", text)
        self.assertIn("上次整理：2026-10-25", text, "她要知道这页是什么时候写的")
        self.assertIn("他最近在忙一个新项目", text)

    async def test_the_transcript_reaches_the_model_in_the_right_shape(self):
        self.dm("今日ひま？", "コーヒー飲んだ")
        await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        asked = self.llm.asked()
        self.assertIn("制作人：今日ひま？", asked)
        self.assertIn("ふゆ：はい", asked, "她说的那句用她自己的话留下来")
        self.assertIn("第一次整理", asked, "第一次没有上一版，要说清楚")

    async def test_the_next_run_carries_the_previous_version(self):
        self.dm("今日ひま？", "コーヒー飲んだ")
        await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        self.dm("明日会える？", "ケーキ買った")
        await self.digest.run(
            digest_module.DM,
            "42",
            heading="和制作人的记忆",
            days=3,
            now=self.NOW + timedelta(days=3, hours=1),
        )
        asked = self.llm.asked()
        self.assertIn("他最近在忙一个新项目", asked, "上一版整份交给模型，它才知道该留什么")
        self.assertIn("制作人：明日会える？", asked)
        self.assertNotIn("制作人：今日ひま？", asked, "已经写进上一版的话不用再读一遍")

    async def test_without_a_name_the_file_signs_nobody(self):
        """A checkout with no character data still writes a memory — it just does not put a
        name on it. 「ふゆ」 in the default would be writing somebody else's diary."""
        neutral = digest_module.Digest(self.store, self.llm, self.root, clock=lambda: self.NOW)
        self.dm("今日ひま？", "コーヒー飲んだ")
        await neutral.run(
            digest_module.DM, "42", heading="记忆", days=3, now=self.NOW
        )
        asked = self.llm.asked()
        self.assertIn("对方：今日ひま？", asked)
        self.assertIn("自己：はい", asked)
        self.assertNotIn("ふゆ", asked)
        self.assertNotIn("制作人", asked)

    async def test_nothing_new_means_no_call_and_no_rewrite(self):
        self.dm("今日ひま？", "コーヒー飲んだ")
        await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        before = len(self.llm.prompts)
        again = await self.digest.run(
            digest_module.DM,
            "42",
            heading="和制作人的记忆",
            days=3,
            now=self.NOW + timedelta(days=4),
        )
        self.assertIsNone(again, "没有新的话就没有可整理的东西")
        self.assertEqual(len(self.llm.prompts), before)

    async def test_a_short_exchange_waits_instead_of_being_dropped(self):
        self.dm("やあ")
        written = await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        self.assertIsNone(written)
        self.dm("おつかれ", "ねむい", "おやすみ")
        written = await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        self.assertIsNotNone(written, "攒够了一起写，不是把那一句丢掉")
        self.assertIn("制作人：やあ", self.llm.asked())

    async def test_the_new_version_replaces_the_old_one(self):
        self.dm("今日ひま？", "コーヒー飲んだ")
        first = await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        self.dm("明日会える？", "ケーキ買った")
        self.llm.reply = "## 近况\n\n- 他说要买蛋糕过来"
        second = await self.digest.run(
            digest_module.DM,
            "42",
            heading="和制作人的记忆",
            days=3,
            now=self.NOW + timedelta(days=3),
        )
        self.assertEqual(first, second, "还是那一份文件")
        text = second.read_text(encoding="utf-8")
        self.assertIn("要买蛋糕过来", text)
        self.assertNotIn("他最近在忙一个新项目", text, "旧的被新的盖掉了，不是追加")

    async def test_the_cadence_is_three_days_for_the_private_chat(self):
        self.dm("1", "2")
        await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        self.assertFalse(self.digest.due(digest_module.DM, "42", 3, self.NOW + timedelta(days=2)))
        self.assertTrue(
            self.digest.due(digest_module.DM, "42", 3, self.NOW + timedelta(days=3, minutes=1))
        )

    async def test_something_never_written_is_due_at_once(self):
        self.assertTrue(
            self.digest.due(digest_module.DM, "42", 3, self.NOW),
            "刚打开就该写一次，不然要等三天才知道它有没有用",
        )

    async def test_a_broken_model_leaves_the_old_file_whole(self):
        self.dm("今日ひま？", "コーヒー飲んだ")
        path = await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        before = path.read_text(encoding="utf-8")
        self.dm("明日会える？", "ケーキ買った")
        self.llm.broken = RuntimeError("service down")
        with self.assertRaises(RuntimeError):
            await self.digest.run(
                digest_module.DM,
                "42",
                heading="和制作人的记忆",
                days=3,
                now=self.NOW + timedelta(days=3),
            )
        self.assertEqual(path.read_text(encoding="utf-8"), before, "旧的那版不能变成半个文件")

    async def test_an_empty_answer_is_not_a_memory(self):
        self.dm("今日ひま？", "コーヒー飲んだ")
        self.llm.reply = "   \n"
        path = await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        self.assertIsNone(path)
        self.assertFalse((self.root / "memory" / "dm-42.md").exists())

    # ---- 群聊

    async def test_the_group_says_who_was_talking(self):
        conversation = self.thread("qq:group:100", "路人甲")
        self.talk(conversation, ["新曲のMV見た？", "まだ見てない"])
        other = self.thread("qq:group:100", "路人乙")  # 同一个人一个会话
        self.talk(other, ["見た見た"])
        conversation2 = self.thread("qq:group:100", "制作人")
        self.talk(conversation2, ["今日ひま？"])
        path = await self.digest.run(
            digest_module.GROUP, "100", heading="群里的记忆", days=7, now=self.NOW
        )
        self.assertIsNotNone(path)
        asked = self.llm.asked()
        self.assertIn("路人甲：新曲のMV見た？", asked, "五十个人的群，名字是重点")
        self.assertIn("路人乙：見た見た", asked)
        self.assertIn("制作人：今日ひま？", asked)
        self.assertTrue(str(path).endswith("group-100.md"))

    async def test_the_group_cadence_is_a_week(self):
        self.dm_group = None
        conversation = self.thread("qq:group:100", "路人甲")
        self.talk(conversation, ["a", "b"])
        await self.digest.run(
            digest_module.GROUP, "100", heading="群里的记忆", days=7, now=self.NOW
        )
        self.assertFalse(self.digest.due(digest_module.GROUP, "100", 7, self.NOW + timedelta(days=5)))
        self.assertTrue(
            self.digest.due(digest_module.GROUP, "100", 7, self.NOW + timedelta(days=8))
        )

    # ---- 进提示词

    async def test_the_file_becomes_a_turn_section(self):
        self.dm("今日ひま？", "コーヒー飲んだ")
        await self.digest.run(
            digest_module.DM, "42", heading="和制作人的记忆", days=3, now=self.NOW
        )
        section = self.digest.section(digest_module.DM, "42")
        self.assertIsNotNone(section)
        self.assertIn("他最近在忙一个新项目", section.text)
        self.assertIn("不要复述", section.text, "记得不等于要背出来")

    async def test_no_file_means_no_section(self):
        self.assertIsNone(self.digest.section(digest_module.DM, "42"))


class TestSpeakingFirstOnDemand(ChannelCase):
    """「现在开口一次」是给部署做验证用的：跳过时间条件，不跳过开关。

    时间条件（安静够久、离上次够远、25% 硬币）是产品的一部分，手动触发绕开它们不算作弊；
    但「这条通道到底开没开」「她在不在一起」是设定，手动触发不能替它撒谎。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=True, ambient_idle=45.0, private=True,
        )

    async def test_a_forced_thought_ignores_the_silence_and_the_coin(self):
        self.channel._rng = Coin(0.99)  # 硬币永远说不
        self.assertTrue(await self.channel.speak_up("100", force=True))
        self.assertTrue(self.client.texts, "她要真的发出去了，不是只返回 True")

    async def test_forcing_does_not_turn_the_feature_on(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=False, ambient_idle=45.0, private=True,
        )
        self.assertFalse(await self.channel.speak_up("100", force=True))
        self.assertEqual(self.client.texts, [])

    async def test_the_private_ping_skips_the_schedule_but_not_the_relationship(self):
        self.assertFalse(
            await self.channel.love_ping("42", force=True), "还没在一起，不能撒娇"
        )
        self.assertEqual(self.client.texts, [])
        self.channel.identities.resolve("42", card="", role="owner")  # 得先有这一行
        self.channel.identities.set_loving("42", True)
        self.assertTrue(await self.channel.love_ping("42", force=True))
        self.assertTrue(self.client.texts)

    async def test_the_endpoint_says_why_when_the_channel_is_not_running(self):
        from channels.plugin_qq import QQService

        service = QQService(
            self.app, self.channel.config, client=self.client, log=lambda line: None
        )
        answer = service.ping("dm")
        self.assertFalse(answer["ok"])
        self.assertIn("没在跑", answer["why"])


class TestTheDigestIsScheduled(unittest.IsolatedAsyncioTestCase):
    """自动任务本身：三天一次、一周一次，不是「每轮都算一遍」。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for folder in ("characters/sheets", "characters/world/idols"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "characters/sheets/test.md").write_text(SHEET, encoding="utf-8")
        (self.root / "characters/characters.toml").write_text(TOML, encoding="utf-8")
        self.store = Store(self.root / "m.db")
        self.addCleanup(self.store.close)

    def talk(self, title: str, name: str, lines) -> None:
        user = self.store.create_user(name)
        conversation = self.store.create_conversation(user, "test", title).id
        for line in lines:
            self.store.add_turn(conversation, line, [Segment("はい", "嗯")])

    async def service(self, **kwargs):
        from channels.plugin_qq import QQService

        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(self.root / "m.db"), characters=str(self.root / "characters"),
            media=str(self.root / "media"), turns_per_hour=0,
        )
        app = App(settings, ScriptedLLM(REPLY, size=5))
        self.addCleanup(app.close)
        config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", **kwargs,
        )
        service = QQService(app, config, client=FakeClient(), log=lambda line: None)
        app.store.migrate(service.migrations())
        service.channel.digest._llm = Recorder()
        return service

    async def test_it_writes_the_private_memory_then_waits_three_days(self):
        self.talk("qq:dm", "制作人", ["a", "b", "c", "d"])
        service = await self.service()
        now = datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)
        written = await service._maybe_digest(now)
        self.assertEqual(len(written), 1, "第一次就该写，不然要等三天才知道它有没有用")
        self.assertTrue(written[0].endswith("dm-42.md"))
        self.assertEqual(await service._maybe_digest(now + timedelta(days=1)), [])
        self.talk("qq:dm", "制作人", ["e", "f", "g", "h"])  # 又过了三天，攒了新的
        self.assertEqual(
            len(await service._maybe_digest(now + timedelta(days=3, minutes=5))), 1
        )

    async def test_the_group_is_seven_days_not_three(self):
        self.talk("qq:group:100", "路人", ["a", "b", "c", "d"])
        service = await self.service()
        now = datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)
        written = await service._maybe_digest(now)
        self.assertTrue(any(path.endswith("group-100.md") for path in written))
        self.assertEqual(
            await service._maybe_digest(now + timedelta(days=4)),
            [],
            "群聊是每周一次，不是每三天",
        )
        self.talk("qq:group:100", "路人", ["e", "f", "g", "h"])
        self.assertTrue(
            any(
                path.endswith("group-100.md")
                for path in await service._maybe_digest(now + timedelta(days=7, minutes=5))
            )
        )

    async def test_the_whole_thing_can_be_switched_off(self):
        self.talk("qq:dm", "制作人", ["a", "b", "c", "d"])
        service = await self.service(digest=False)
        self.assertEqual(
            await service._maybe_digest(datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)), []
        )


class TestHerMemoryInTheTurn(ChannelCase):
    """文件写好了，还得真的进提示词 —— 不然它只是一份档案。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0, private=True,
        )

    def put(self, kind: str, key: str, body: str) -> None:
        folder = Path(self.tmp.name) / "characters" / "memory"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{kind}-{key}.md").write_text(f"# 记忆\n\n{body}\n", encoding="utf-8")

    def sections_of(self) -> list:
        seen: dict = {}
        original = self.app.turn

        def spy(*args, **kwargs):
            seen["sections"] = kwargs.get("sections") or []
            return original(*args, **kwargs)

        self.app.turn = spy
        self.addCleanup(setattr, self.app, "turn", original)
        return seen

    def text_of(self, sections) -> str:
        return "\n".join(section.text for section in sections)

    async def test_the_private_chat_reads_the_private_memory(self):
        self.put("dm", "42", "- 他说过想一起住")
        seen = self.sections_of()
        await self.say(direct(message_id="d1"))
        self.assertIn("想一起住", self.text_of(seen["sections"]))
        self.assertIn("不要复述", self.text_of(seen["sections"]))

    async def test_the_group_does_not_read_the_private_memory(self):
        self.put("dm", "42", "- 他说过想一起住")
        self.put("group", "100", "- 群里最近在聊新曲")
        seen = self.sections_of()
        await self.say(event(message_id="g1"))
        text = self.text_of(seen["sections"])
        self.assertIn("新曲", text)
        self.assertNotIn("想一起住", text, "群里的提示词里不能有私聊的记忆")

    async def test_the_switch_takes_it_out_of_the_prompt(self):
        self.put("dm", "42", "- 他说过想一起住")
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            private=True, digest_prompt=False,
        )
        seen = self.sections_of()
        await self.say(direct(message_id="d1"))
        self.assertNotIn("想一起住", self.text_of(seen["sections"]))


class TestLadderInChannel(ChannelCase):
    """The count lives in the database, so it survives a restart and a rebuild."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        rules = Path(self.tmp.name) / "characters" / "world" / "fuyuko-qq.md"
        rules.write_text(LADDER, encoding="utf-8")
        age(rules)
        self.rules = rules
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0, ambient_p=0.0,
        )

    def spy(self):
        seen: dict = {}
        original = self.app.turn

        def wrapper(*args, **kwargs):
            seen["sections"] = kwargs.get("sections") or []
            seen["engine"] = kwargs.get("engine")
            return original(*args, **kwargs)

        self.app.turn = wrapper
        return seen

    def stage_of(self, engine) -> str:
        """Which rung the prompt was built with — it lives in the persona, not the turn."""
        for section in engine.base:
            if section.title.startswith("qq:stage-"):
                return section.title.removeprefix("qq:stage-")
        return ""

    async def heard(self, text: str, qq: str = "43", message_id: str = "m") -> str:
        """Send one message and hand back the rung her prompt was built on."""
        seen = self.spy()
        await self.say(event(text, qq=qq, message_id=message_id))
        return self.stage_of(seen["engine"])

    async def test_the_first_conversation_is_the_sweet_one(self):
        self.assertEqual(await self.heard("はじめまして", message_id="s1"), "new")

    async def test_the_same_person_climbs_the_ladder_as_she_answers_them(self):
        await self.heard("一", message_id="c1")
        await self.heard("二", message_id="c2")
        await self.heard("三", message_id="c3")
        self.assertEqual(await self.heard("四", message_id="c4"), "familiar")

    async def test_one_reply_is_one_conversation_however_many_bubbles_it_became(self):
        await self.heard("こんばんは", message_id="b1")
        self.assertEqual(len(self.client.texts), 2, "the reply arrived in two segments")
        self.assertEqual(self.channel.identities.turns("43"), 1, "and still counts once")

    async def test_a_reply_that_has_to_be_resent_still_counts(self):
        # The outbox keeps it and sends it again after the socket is back, so the two of
        # them have, in fact, talked. Not counting it would make the ladder depend on
        # whether the network happened to be up.
        self.client.broken = True
        await self.heard("届かない", message_id="f1")
        self.assertEqual(self.channel.identities.turns("43"), 1)

    async def test_an_interjection_does_not_get_to_know_anyone(self):
        # 钉在周五下午：作息闸看的是真的钟，半夜跑用例会被「她睡着了」挡掉。
        self.channel._clock = lambda: datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=True, ambient_p=1.0, ambient_gap=0.0, ambient_check=0.0,
        )
        await self.say(event("それな", qq="43", message_id="a1", at=""))
        await self.say(event("それな", qq="43", message_id="a2", at=""))
        self.assertTrue(self.client.texts, "she did say something")
        self.assertEqual(self.channel.identities.turns("43"), 0)

    async def test_the_count_is_per_person_not_per_group(self):
        await self.heard("ひとつ", qq="43", message_id="p1")
        await self.heard("ひとつ", qq="44", message_id="p2")
        self.assertEqual(self.channel.identities.turns("43"), 1)
        self.assertEqual(self.channel.identities.turns("44"), 1)

    async def test_the_producer_never_gets_the_stranger_voice(self):
        self.assertEqual(await self.heard("おはよう", qq="42", message_id="o1"), "close")

    async def test_the_owner_can_see_the_count_and_start_over(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0, private=True,
            private_ids=("42",),
        )
        await self.say(direct(message_id="d1"))
        await self.say(direct(message_id="d2"))
        await self.say(direct("/阶段", message_id="d3"))
        self.assertIn("2 次", self.client.texts[-1])
        await self.say(direct("/阶段 重置", message_id="d4"))
        self.assertEqual(self.channel.identities.turns("42"), 0)
        self.assertIn("初次见面", self.client.texts[-1])

    async def test_a_stranger_cannot_reset_their_own_count(self):
        await self.heard("さん", message_id="n1")
        before = len(self.client.calls)
        await self.say(event("/阶段 重置", qq="43", message_id="n2"))
        self.assertEqual(self.channel.identities.turns("43"), 1, "unchanged")
        self.assertEqual(len(self.client.calls), before, "and no reply at all")

    async def test_the_status_reports_the_rungs_in_use(self):
        report = qq_module.status(self.channel)
        self.assertEqual([rung["stage"] for rung in report["ladder"]],
                         ["new", "familiar", "close", "inner"])

    async def test_editing_a_threshold_changes_where_the_ladder_bends(self):
        await self.heard("一", message_id="t1")
        await self.heard("二", message_id="t2")
        self.assertEqual(await self.heard("三", message_id="t3"), "new",
                         "still on the first rung after three answers")
        self.rules.write_text(LADDER.replace("｜3】", "｜2】"), encoding="utf-8")
        age(self.rules)
        self.assertEqual(await self.heard("四", message_id="t4"), "familiar",
                         "the new threshold took effect without a restart")


class TestCommandsArePrivate(ChannelCase):
    """Slash commands belong to the producer. Everyone else gets silence.

    Reported from the live group: someone @-ed her with `/帮助` and got the whole command
    list back. That turns her into a help screen for anyone curious enough to type seven
    characters, so the gate is now first thing in the dispatcher.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
        )
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "stickers")
        self.app.store.migrate(self.pack.migrations())
        self.pack.save(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        self.channel.pack = self.pack

    async def test_a_stranger_asking_for_help_gets_nothing(self):
        for text in ("/帮助", "/help", "/?"):
            await self.say(event(text, qq="43", message_id=f"h{text}"))
        self.assertEqual(self.client.calls, [])

    async def test_a_stranger_typing_any_command_gets_nothing(self):
        for text in ("/好感", "/重开", "/阶段", "/静音", "/重载", "/表情标 上班"):
            await self.say(event(text, qq="43", message_id=f"c{text}"))
        self.assertEqual(self.client.calls, [])

    async def test_a_stranger_is_not_even_written_down_for_typing_one(self):
        await self.say(event("/帮助", qq="43", message_id="i1"))
        self.assertEqual(self.channel.identities.count(), 0)

    async def test_the_producer_still_gets_the_help(self):
        await self.say(event("/帮助", qq="42", message_id="o1"))
        self.assertTrue(self.client.texts)
        self.assertIn("重载", self.client.texts[-1])

    async def test_the_producer_can_still_use_every_command(self):
        await self.say(event("/好感", qq="42", message_id="o2"))
        self.assertTrue(self.client.texts, "commands still work for him")

    async def test_the_gate_can_be_opened_on_purpose(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0, commands="all",
        )
        await self.say(event("/帮助", qq="43", message_id="a1"))
        self.assertTrue(self.client.texts, "QQ_COMMANDS=all brings the public menu back")


class TestTheVoiceFollowsTheDistance(ChannelCase):
    """群里听到哪一档声音，取决于她跟*这个人*熟到什么程度，不是说了几次话。

    以前这条线只由「说过几次」决定，而好感度对所有陌生人默认是「信任」——所以一个从没
    见过的人一开口，得到的就是私下的那个声音（「ハ？」「バーカ」都出来了）。
    """

    async def told(self, qq: str, message_id: str) -> str:
        await self.say(event("こんばんは", qq=qq, message_id=message_id))
        return " ".join(str(m.content) for batch in self.app.llm.seen for m in batch)

    async def test_a_stranger_gets_the_performing_voice_only(self):
        """陌生的人见到的是「冬優（ふゆ）」——笑眯眯、挂♡的那一面，不是客气版的本人。"""
        prompt = await self.told("43", "v1")
        self.assertIn("冬優（ふゆ）", prompt)
        self.assertIn("ふゆ♡", prompt, "♡ 是这一档的招牌，不写出来她就不挂")
        self.assertIn("一句都不该有", prompt)

    async def test_the_producer_is_measured_too(self):
        """主人在群里也是一个人：他的数字说了算，不是写死的 intimate。

        写死的时候他在群里的好感度只是个摆设——想验「夹子」有没有真的触发，永远看不到。
        私聊不看这个值，所以他在那边挣到的东西一点没损失。
        """
        prompt = await self.told("42", "v2")
        self.assertIn("冬優（ふゆ）", prompt)

    async def test_the_producer_at_trust_gets_the_private_lines(self):
        user = self.channel.identities.resolve("42", card="制作人")
        self.app.affinity.set_value(user, "test", 45, surface="group")
        prompt = await self.told("42", "v4")
        self.assertIn("冬優子出来了", prompt)
        self.assertNotIn("冬優（ふゆ）", prompt, "熟了就不摆那一面了")

    async def test_a_member_promoted_to_trust_gets_them_too(self):
        user = self.channel.identities.resolve("43", card="路人")
        # 每天最多加 6，所以直接写值：这条测的是「到了信任之后」，不是攒分的过程。
        self.app.affinity.set_value(user, "test", 45, surface="group")
        prompt = await self.told("43", "v3")
        self.assertIn("冬優子出来了", prompt)


class TestTheMaskIsForStrangers(ChannelCase):
    """「冬優（ふゆ）」是夹子，「冬優子」是本体——越陌生越只见到前者。

    这两句曾经写反过（`fuyuko-qq.md` 里写「越陌生越往冬優子那边去」），现在 p 提示词和人设
    口径一致：陌生 = 营业人格拉满（句子短、句尾翘、挂♡），熟 = 本体出来（短句、会怼）。
    """

    async def told(self, qq: str, message_id: str, character_stub: bool = False) -> str:
        await self.say(event("こんばんは", qq=qq, message_id=message_id))
        return " ".join(str(m.content) for batch in self.app.llm.seen for m in batch)

    async def test_the_performing_face_carries_hearts(self):
        prompt = await self.told("43", "m1")
        self.assertIn("每三四条里至少一个", prompt)
        self.assertIn("不交心", prompt)

    async def test_a_cold_room_gets_no_hearts_at_all(self):
        user = self.channel.identities.resolve("44", card="得罪她的人")
        self.app.affinity.set_value(user, "test", 0, surface="group")
        prompt = await self.told("44", "m2")
        self.assertIn("不挂", prompt, "冷淡那一档连♡都收起来")
        self.assertNotIn("每三四条里至少一个", prompt)


class TestThePhoneFollowsTheVoice(ChannelCase):
    """她提不提打电话，取决于这个部署有没有语音模型，不是人设里一句写死的话。

    写进 `fuyuko-dm.md` 就会一直是同一个答案：今天没有语音，那句「电话打不通」是对的，
    等配上语音它还在，就成了「明明能打她却说打不通」。所以答案是每轮现问的
    （`App.call_ready()`，页面画不画电话键也是同一个答案）。
    """

    async def told(self, message_id: str) -> str:
        await self.say(event("我到家了", qq="42", message_id=message_id))
        return " ".join(str(m.content) for batch in self.app.llm.seen for m in batch)

    async def test_without_a_voice_the_phone_does_not_work(self):
        prompt = await self.told("p1")
        self.assertIn("电话打不通", prompt)
        self.assertNotIn("可以通话", prompt)

    async def test_with_a_voice_she_may_ask_for_one(self):
        from plugins.voice import Voice

        self.app.voice = Voice("test-key")
        prompt = await self.told("p2")
        self.assertIn("可以通话", prompt)
        self.assertNotIn("电话打不通", prompt)

    async def test_the_manual_switch_closes_it_even_with_a_voice(self):
        from dataclasses import replace

        from plugins.voice import Voice

        self.app.settings = replace(self.app.settings, call="off")
        self.app.voice = Voice("test-key")
        prompt = await self.told("p3")
        self.assertIn("电话打不通", prompt)


class TestTheSharedCommands(ChannelCase):
    """`QQ_SHARED_COMMANDS`：哪些指令可以交给群里所有人。

    默认只有 /表情——它读的是群友自己填的那包图。/好感 是同一类：读的是*问的人*的数字，
    别人的看不出一点。而 /重载 /静音 /阶段 /表情标 永远只给主人，写进变量也不开。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            shared_commands=("表情", "好感"),
        )
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "stickers")
        self.app.store.migrate(self.pack.migrations())
        self.pack.save(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        self.channel.pack = self.pack

    async def test_a_member_can_ask_about_her_mood(self):
        await self.say(event("/好感", qq="43", message_id="s1"))
        self.assertTrue(self.client.texts, "开了就该答一句")

    async def test_it_is_closed_when_the_list_does_not_name_it(self):
        self.channel.config = replace(self.channel.config, shared_commands=("表情",))
        await self.say(event("/好感", qq="43", message_id="s2"))
        self.assertEqual(self.client.calls, [])

    async def test_the_same_member_cannot_ask_twice_in_a_row(self):
        await self.say(event("/好感", qq="43", message_id="s3"))
        first = len(self.client.texts)
        self.assertTrue(first)
        await self.say(event("/好感", qq="43", message_id="s4"))
        self.assertEqual(len(self.client.texts), first, "十五秒内不重复答，回答本身就是刷屏")

    async def test_the_producer_is_not_throttled(self):
        await self.say(event("/好感", qq="42", message_id="s5"))
        first = len(self.client.texts)
        await self.say(event("/好感", qq="42", message_id="s6"))
        self.assertGreater(len(self.client.texts), first)

    async def test_the_variable_cannot_open_a_control(self):
        self.channel.config = replace(
            self.channel.config, shared_commands=("表情", "好感", "重载", "静音", "阶段")
        )
        for text in ("/重载", "/静音", "/阶段"):
            await self.say(event(text, qq="43", message_id=f"t{text}"))
        self.assertEqual(self.client.calls, [], "白名单之外的名字，写进变量也不开")

    async def test_the_sticker_switch_still_wins(self):
        self.channel.config = replace(self.channel.config, stickers_public=False)
        await self.say(event("/表情", qq="43", message_id="s7"))
        self.assertEqual(self.client.calls, [])

    async def test_the_help_says_who_may_use_what(self):
        await self.say(event("/帮助", qq="42", message_id="s8"))
        said = self.client.texts[-1]
        self.assertIn("/表情", said)
        self.assertIn("/好感", said)
        self.assertIn("谁都能用", said)


class TestTheAccountsOwnFaces(ChannelCase):
    """The face collection that is already in the QQ account.

    The pack used to be only what the group sent. But the account has favourites — pictures
    somebody chose and kept — and those are the ones she is certainly allowed to send here.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
        )
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "stickers")
        self.app.store.migrate(self.pack.migrations())
        self.channel.pack = self.pack
        self.client.faces = ["http://example.invalid/a.png", "http://example.invalid/b.png"]

    def serve_bytes(self, mapping: dict[str, bytes]) -> None:
        original = qq_module._fetch

        def fake(url: str) -> bytes:
            for key, raw in mapping.items():
                if url.endswith(key):
                    return raw
            raise RuntimeError("no such picture")

        qq_module._fetch = fake
        self.addCleanup(setattr, qq_module, "_fetch", original)

    async def test_the_collection_lands_in_the_pack(self):
        self.serve_bytes({"a.png": PNG, "b.png": PNG + b"1"})
        self.assertEqual(await self.channel.import_faces(), 2)
        self.assertEqual(self.pack.count(), 2)

    async def test_syncing_twice_adds_nothing_twice(self):
        self.serve_bytes({"a.png": PNG, "b.png": PNG + b"1"})
        await self.channel.import_faces()
        self.assertEqual(await self.channel.import_faces(), 0, "dedupe is by content")

    async def test_one_broken_url_does_not_stop_the_rest(self):
        self.serve_bytes({"a.png": PNG})  # b raises
        self.assertEqual(await self.channel.import_faces(), 1)

    async def test_a_refused_action_is_not_a_crash(self):
        self.client.raw_error = RuntimeError("retcode 1404")
        self.assertEqual(await self.channel.import_faces(), 0)
        self.assertEqual(self.pack.count(), 0)

    async def test_a_wrapped_answer_is_understood_too(self):
        self.client.faces = {"urls": ["http://example.invalid/a.png"]}
        self.serve_bytes({"a.png": PNG})
        self.assertEqual(await self.channel.import_faces(), 1)

    async def test_the_producer_can_ask_for_a_sync(self):
        self.serve_bytes({"a.png": PNG, "b.png": PNG + b"1"})
        await self.say(event("/表情 同步", qq="42", message_id="f1"))
        self.assertEqual(self.pack.count(), 2)
        self.assertIn("2 张", self.client.texts[-1])

    async def test_a_member_cannot_ask_for_one(self):
        self.serve_bytes({"a.png": PNG, "b.png": PNG + b"1"})
        await self.say(event("/表情 同步", qq="43", message_id="f2"))
        self.assertEqual(self.pack.count(), 0, "it is a tag called 「同步」 to them, and there is none")

    async def test_stickers_off_means_no_sync(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0, stickers=False,
        )
        self.serve_bytes({"a.png": PNG, "b.png": PNG + b"1"})
        self.assertEqual(await self.channel.import_faces(), 0)
        self.assertEqual(self.pack.count(), 0)

    async def test_a_favourite_can_be_named_once_it_has_been_sent_here(self):
        """`/表情标` names the newest picture *in this room*; an imported face has no room."""
        self.serve_bytes({"a.png": PNG})
        await self.channel.import_faces()
        await self.say(event("/表情标 上班", qq="42", message_id="n0"))
        self.assertEqual(self.pack.count("上班"), 0, "not until it has been seen here")
        payload = event(message_id="n1", at="")
        payload["message"] = [
            {"type": "image", "data": {"url": "http://example.invalid/a.png", "sub_type": 1}}
        ]
        await self.say(payload)
        await self.say(event("/表情标 上班", qq="42", message_id="n2"))
        self.assertEqual(self.pack.count("上班"), 1)


class TestTheStickerCommandIsShared(ChannelCase):
    """`/表情` is the one command the group may use, and only it.

    Everything else stays behind the owner gate: a help screen for anyone curious is the
    failure mode this channel was fixed for. A sticker request has no side effect at all —
    it reads the pack the group itself filled — so keeping it private only meant fifty
    people asking the producer to type it for them.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
        )
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "stickers")
        self.app.store.migrate(self.pack.migrations())
        self.pack.save(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        self.channel.pack = self.pack

    async def test_a_member_gets_a_sticker(self):
        await self.say(event("/表情", qq="43", message_id="s1"))
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0][0], "send_group_msg")
        self.assertEqual(self.client.texts, [], "a picture, not words")

    async def test_a_member_still_gets_nothing_for_any_other_command(self):
        await self.say(event("/阶段", qq="43", message_id="s2"))
        self.assertEqual(self.client.calls, [])

    async def test_one_member_cannot_flood_the_room(self):
        await self.say(event("/表情", qq="43", message_id="s3"))
        await self.say(event("/表情", qq="43", message_id="s4"))
        self.assertEqual(len(self.client.calls), 1, "the second one inside the gap is silence")

    async def test_the_producer_is_not_throttled(self):
        await self.say(event("/表情", qq="42", message_id="s5"))
        await self.say(event("/表情", qq="42", message_id="s6"))
        self.assertEqual(len(self.client.calls), 2)

    async def test_the_shared_command_can_be_closed(self):
        self.channel.config = QQConfig(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            stickers_public=False,
        )
        await self.say(event("/表情", qq="43", message_id="s7"))
        self.assertEqual(self.client.calls, [])


class TestMuteAndTheWayBack(ChannelCase):
    """`/静音` used to be a one-way door: the gate that silences her also swallowed the
    command that would have undone it, so the only way back was `UPDATE qq_identities`."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ids = self.channel.identities

    async def test_a_muted_producer_can_still_talk_his_way_out(self):
        self.ids.resolve("42", role="owner")
        self.ids.set_muted("42", True)
        await self.say(event("/解除", message_id="u1"))
        self.assertFalse(self.ids.muted("42"))
        self.assertTrue(self.client.texts)

    async def test_being_muted_still_means_being_silent(self):
        self.ids.resolve("42", role="owner")
        self.ids.set_muted("42", True)
        await self.say(event("こんばんは", message_id="u2"))
        self.assertEqual(self.client.calls, [])

    async def test_a_stranger_cannot_unmute_anyone(self):
        self.ids.resolve("43")
        self.ids.set_muted("43", True)
        await self.say(event("/解除", qq="43", message_id="u3"))
        self.assertTrue(self.ids.muted("43"))
        self.assertEqual(self.client.calls, [])

    async def test_the_producer_can_unmute_someone_by_number(self):
        self.ids.resolve("43")
        self.ids.set_muted("43", True)
        await self.say(event("/解除 43", message_id="u4"))
        self.assertFalse(self.ids.muted("43"))


class TestResettingEveryone(ChannelCase):
    """One correction after the register was rewritten: everyone back to 「はじめまして」,
    the producers left where they are (their floor is a setting, not a count)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ids = self.channel.identities
        for qq_id, turns in (("42", 12), ("43", 7), ("44", 1)):
            self.ids.resolve(qq_id)
            self.ids.set_turns(qq_id, turns)

    async def test_everyone_but_the_producer_goes_back_to_zero(self):
        await self.say(event("/阶段 全员重置", message_id="r1"))
        self.assertEqual(self.ids.turns("43"), 0)
        self.assertEqual(self.ids.turns("44"), 0)
        self.assertEqual(self.ids.turns("42"), 12, "the producer keeps his count")

    async def test_a_stranger_cannot_do_it(self):
        await self.say(event("/阶段 全员重置", qq="43", message_id="r2"))
        self.assertEqual(self.ids.turns("43"), 7)
        self.assertEqual(self.client.calls, [])


class TestWebSocket(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received: list[str] = []
        self.masked = False
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.addCleanup(self.server.close)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            key = ""
            for line in head.decode("latin-1").splitlines():
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(
                hashlib.sha1((key + ws.GUID).encode()).digest()
            ).decode()
            writer.write(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
            )
            await writer.drain()
            head2 = await reader.readexactly(2)
            self.masked = bool(head2[1] & 0x80)
            size = head2[1] & 0x7F
            mask = await reader.readexactly(4) if self.masked else b""
            payload = await reader.readexactly(size)
            if self.masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            self.received.append(payload.decode())
            writer.write(bytes([0x81, len(payload)]) + payload)
            await writer.drain()
            await asyncio.sleep(0.2)
        except Exception:
            pass
        finally:
            writer.close()

    async def test_handshake_send_and_receive(self):
        conn = await ws.connect(f"ws://127.0.0.1:{self.port}")
        await conn.send('{"action": "get_login_info"}')
        self.assertEqual(await conn.recv(), '{"action": "get_login_info"}')
        self.assertEqual(self.received, ['{"action": "get_login_info"}'])
        self.assertTrue(self.masked)
        await conn.close()

    async def test_a_refused_handshake_is_an_error_not_a_hang(self):
        async def refuse(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(refuse, "127.0.0.1", 0)
        self.addCleanup(server.close)
        port = server.sockets[0].getsockname()[1]
        with self.assertRaises(ws.WebSocketError):
            await ws.connect(f"ws://127.0.0.1:{port}")


class TestOneBot(unittest.IsolatedAsyncioTestCase):
    async def test_actions_are_matched_by_echo(self):
        from channels.onebot import OneBot

        class FakeSocket:
            def __init__(self):
                self.sent: list[str] = []
                self.queue: asyncio.Queue[str] = asyncio.Queue()

            async def send(self, text: str) -> None:
                self.sent.append(text)
                echo = json.loads(text)["echo"]
                await self.queue.put(json.dumps({"status": "ok", "retcode": 0, "echo": echo, "data": {"user_id": 999}}))

            async def recv(self) -> str:
                return await self.queue.get()

            async def close(self) -> None:
                pass

        socket = FakeSocket()

        async def connect(*args, **kwargs):
            return socket

        client = OneBot("ws://unused", connect=connect)
        task = asyncio.create_task(client.run(lambda event: asyncio.sleep(0)))
        await asyncio.sleep(0.02)
        data = await client.call("get_login_info")
        self.assertEqual(data["user_id"], 999)
        self.assertTrue(client.connected)
        await client.stop()
        task.cancel()
        with self.assertRaises(Exception):
            await client.call("send_group_msg", group_id=1)

    async def test_on_connect_runs_every_time_the_socket_comes_back(self):
        """掉线期间没送出去的话，要在连上的那一刻补，而不是在进程启动的那一刻。

        启动时补发是个陷阱：那时 socket 还没通，每一行都会失败并消耗一次重试额度，
        五次重启之后整队消息就被悄悄吃掉了。
        """
        from channels.onebot import OneBot

        class FakeSocket:
            def __init__(self):
                self.queue: asyncio.Queue[object] = asyncio.Queue()

            async def send(self, text: str) -> None:
                pass

            async def recv(self) -> str:
                item = await self.queue.get()
                if isinstance(item, Exception):
                    raise item
                return str(item)

            async def close(self) -> None:
                pass

        made: list[FakeSocket] = []

        async def connect(*args, **kwargs):
            socket = FakeSocket()
            made.append(socket)
            return socket

        seen: list[str] = []

        async def on_connect() -> None:
            seen.append("connect")

        client = OneBot("ws://unused", connect=connect)
        task = asyncio.create_task(
            client.run(lambda event: asyncio.sleep(0), on_connect=on_connect)
        )
        await asyncio.sleep(0.02)
        self.assertEqual(seen, ["connect"], "第一次连上就要跑")
        made[0].queue.put_nowait(ConnectionError("socket died"))
        await asyncio.sleep(0.05)
        self.assertEqual(len(seen), 2, "掉线重连之后要再跑一次，补发就是在这里发生")
        await client.stop()
        task.cancel()
        with self.assertRaises(Exception):
            await client.call("send_group_msg", group_id=1)


class TestTheHookCanTalk(unittest.IsolatedAsyncioTestCase):
    """`on_connect` 里要能真的发东西（补发队列就是这样）。

    应答走的是同一个 socket，而那个读循环在 hook 之后才启动：await 掉 hook 的话，
    每一条补发都会等到自己的超时——队列 attempts 一直涨，消息一条也发不出去。
    """

    async def test_an_action_inside_the_hook_comes_back(self):
        from channels.onebot import OneBot

        class EchoSocket:
            def __init__(self):
                self.queue: asyncio.Queue[str] = asyncio.Queue()

            async def send(self, text: str) -> None:
                echo = json.loads(text)["echo"]
                await self.queue.put(
                    json.dumps(
                        {"status": "ok", "retcode": 0, "echo": echo, "data": {"user_id": 999}}
                    )
                )

            async def recv(self) -> str:
                return await self.queue.get()

            async def close(self) -> None:
                pass

        socket = EchoSocket()

        async def connect(*args, **kwargs):
            return socket

        client = OneBot("ws://unused", connect=connect)
        seen: list[int] = []

        async def on_connect() -> None:
            data = await client.call("get_login_info")
            seen.append(int(data["user_id"]))

        task = asyncio.create_task(
            client.run(lambda event: asyncio.sleep(0), on_connect=on_connect)
        )
        await asyncio.sleep(0.15)
        self.assertEqual(seen, [999], "hook 里的调用要答得回来，不能等到超时")
        await client.stop()
        task.cancel()


class TestLoveWindow(unittest.TestCase):
    """The first message of the morning: when the window is closed, wait for it.

    Drawing a gap at 23:00 meant sleeping to 03:00, finding the room shut, drawing again —
    one whole gap thrown away per night, so the morning's first thought drifted later the
    longer it ran. Reported as 「主动搭话触发概率略低」.
    """

    def test_the_night_waits_for_the_morning_instead_of_drawing_a_gap(self):
        from channels.plugin_qq import _until

        late = datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc)
        seconds = _until(9, late)
        self.assertGreaterEqual(seconds, 9.5 * 3600)
        self.assertLess(seconds, 9.5 * 3600 + 600, "a few random minutes, not a fixed minute")

    def test_just_before_the_window_it_waits_only_minutes(self):
        from channels.plugin_qq import _until

        early = datetime(2026, 10, 25, 8, 55, tzinfo=timezone.utc)
        self.assertLess(_until(9, early), 5 * 60 + 600)

    def test_the_defaults_are_the_tuned_ones(self):
        """Sixty to a hundred and fifty minutes: about eight a day inside the window."""
        config = QQConfig(character="someone", love_ping=True)
        self.assertEqual((config.love_ping_min, config.love_ping_max), (60.0, 150.0))


class TestHerOwnStickers(ChannelCase):
    """「我的收藏」里的那叠表情要真的用得上。

    之前的问题不是同步（14 张都进来了，文件也在），是**挑的时候从 184 张里随机**：自己的
    收藏占 7%，看起来就是「从来不用」。而且她除了 `/表情` 命令和偶发的主动插话之外，
    根本没有一句话能带出表情——现在由通道看「对方是不是在要表情」，是就把图附在回答后面。
    这件事不能交给模型：回复格式禁止它在自己的标签之外写任何东西，它也确实没写过。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "stickers", rng=Coin())
        self.app.store.migrate(self.pack.migrations())
        self.channel.pack = self.pack
        self.fav = self.pack.save(PNG, group="", sender="self")
        self.group = self.pack.save(GIF, group="100", sender="7")

    async def test_her_own_collection_is_preferred_over_the_rooms_pile(self):
        row = self.channel._sticker_row()
        self.assertEqual(row["sha1"], self.fav["sha1"], "先挑自己的，再挑别人发的")

    async def test_an_empty_collection_still_falls_back_to_the_pack(self):
        self.app.store.execute(
            "UPDATE qq_stickers SET group_id = '100' WHERE sha1 = ?", (self.fav["sha1"],)
        )
        row = self.channel._sticker_row()
        self.assertIsNotNone(row, "收藏空了不等于没得发")

    async def test_the_pack_counts_the_collection_apart(self):
        self.assertEqual(self.pack.favourites(), 1)
        self.assertEqual(self.pack.count(), 2)

    async def test_a_named_tag_still_wins_over_the_source(self):
        self.pack.tag("上班", sha1=self.group["sha1"])
        row = self.pack.pick("上班", source="fav")
        self.assertEqual(row["sha1"], self.group["sha1"], "「/表情 上班」问的是图，不是来源")

    async def test_the_command_without_a_tag_sends_one_of_hers(self):
        await self.say(event("/表情", qq="42", message_id="k1"))
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0][0], "send_group_msg")
        self.assertEqual(self.client.texts, [], "一张图，不是一句话")

    async def test_the_command_can_ask_for_the_collection_by_name(self):
        await self.say(event("/表情 收藏", qq="42", message_id="k2"))
        self.assertEqual(len(self.client.calls), 1)

    async def test_a_request_for_a_sticker_gets_words_and_a_picture(self):
        await self.channel._turn("100", "43", "群友", "发个表情来看看", "m1", [])
        self.assertTrue(self.client.texts, "她得先回一句话")
        images = [one for _, params in self.client.calls
                  for one in params.get("message", []) if one.get("type") == "image"]
        self.assertEqual(len(images), 1, "话说完，图跟上")

    async def test_an_ordinary_message_gets_no_picture(self):
        await self.channel._turn("100", "43", "群友", "今天好累啊", "m2", [])
        images = [one for _, params in self.client.calls
                  for one in params.get("message", []) if one.get("type") == "image"]
        self.assertEqual(images, [])

    async def test_talking_about_a_sticker_is_not_asking_for_one(self):
        await self.channel._turn("100", "43", "群友", "这个表情好可爱", "m3", [])
        images = [one for _, params in self.client.calls
                  for one in params.get("message", []) if one.get("type") == "image"]
        self.assertEqual(images, [], "每一句提到表情的话都回一张，是会被静音的")

    async def test_no_means_no(self):
        await self.channel._turn("100", "43", "群友", "别发表情了，说正事", "m4", [])
        images = [one for _, params in self.client.calls
                  for one in params.get("message", []) if one.get("type") == "image"]
        self.assertEqual(images, [])

    async def test_one_room_gets_no_second_one_within_ten_minutes(self):
        await self.channel._turn("100", "43", "群友", "发个表情", "m5", [])
        await self.channel._turn("100", "43", "群友", "再来个表情包", "m6", [])
        images = [one for _, params in self.client.calls
                  for one in params.get("message", []) if one.get("type") == "image"]
        self.assertEqual(len(images), 1, "表情是标点，不是刷屏")

    async def test_after_the_gap_it_can_send_again(self):
        loop = asyncio.get_running_loop()
        await self.channel._turn("100", "43", "群友", "发个表情", "m7", [])
        self.channel._marked["100"] = loop.time() - 601
        await self.channel._turn("100", "43", "群友", "再发个表情", "m8", [])
        images = [one for _, params in self.client.calls
                  for one in params.get("message", []) if one.get("type") == "image"]
        self.assertEqual(len(images), 2)

    async def test_a_request_with_no_pack_gets_only_words(self):
        self.channel.pack = None
        await self.channel._turn("100", "43", "群友", "发个表情来看看", "m9", [])
        self.assertTrue(self.client.texts)
        images = [one for _, params in self.client.calls
                  for one in params.get("message", []) if one.get("type") == "image"]
        self.assertEqual(images, [])

    async def test_a_marker_that_slips_through_is_stripped_not_shown(self):
        """她不被要求写记号；万一写了，也不能让 `[贴纸]` 出现在气泡里。"""
        await self.channel._emit("100", "7", "……算了。[贴纸]", "m10", True)
        self.assertEqual(self.client.texts, ["……算了。"])

    async def test_she_is_told_she_has_a_collection(self):
        await self.say(event("在吗", qq="43", message_id="n1"))
        prompt = " ".join(str(one.content) for call in self.app.llm.seen for one in call)
        self.assertIn("【表情】", prompt)
        self.assertIn("收藏", prompt)

    async def test_no_collection_means_she_is_not_told(self):
        """空着的时候不提它：教一件永远做不到的事，是每轮都说一次的小谎。"""
        self.app.store.execute("UPDATE qq_stickers SET group_id = '100'")
        await self.say(event("在吗", qq="43", message_id="n2"))
        prompt = " ".join(str(one.content) for call in self.app.llm.seen for one in call)
        self.assertNotIn("【表情】", prompt)


class TestTheStickerButton(ChannelCase):
    """`POST /api/qq/sticker`：人可以叫她「现在就发一张」，不用等谁开口要。

    人工按钮不过自动那条节流——被人点了一下却被自己的规矩挡住，是那种「功能坏了」的
    错觉里最难查的一种。但它会把时间戳顶上去，免得手发完自动的又紧跟一张。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "stickers", rng=Coin())
        self.app.store.migrate(self.pack.migrations())
        self.channel.pack = self.pack
        self.fav = self.pack.save(PNG, group="", sender="self")
        self.service = plugin_qq.QQService(
            self.app, QQConfig.from_env({"QQ_ENABLED": "1", "QQ_GROUP_IDS": "100"}), client=self.client
        )
        self.service.channel.pack = self.pack
        self.service.pack = self.pack

    async def test_the_button_sends_one_to_the_first_group(self):
        answer = await self.service.send_sticker()
        self.assertTrue(answer["sent"])
        self.assertEqual(answer["group"], "100")
        self.assertEqual(answer["tag"], "收藏")
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0][0], "send_group_msg")

    async def test_the_button_is_not_blocked_by_the_automatic_gap(self):
        loop = asyncio.get_running_loop()
        self.channel._marked["100"] = loop.time()
        answer = await self.service.send_sticker()
        self.assertTrue(answer["sent"], "人是人，节流是给自动用的")

    async def test_an_empty_pack_says_why(self):
        self.app.store.execute("DELETE FROM qq_stickers")
        answer = await self.service.send_sticker()
        self.assertFalse(answer["sent"])
        self.assertIn("空的", answer["why"])
        self.assertEqual(self.client.calls, [])


class TestTheSpeechlessFace(ChannelCase):
    """一句让人说不出话的话，一张「无语」的表情就够——不用先决定她想说什么。

    三道闸的顺序是有意的：标的有没有（没有就是没这个功能）→ 硬币 → 节流，都过了才花
    一次模型调用。先问再掷，等于每条消息都付一次钱来得出「不发」。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "faces", rng=Coin())
        self.app.store.migrate(self.pack.migrations())
        self.channel.pack = self.pack
        self.row = self.pack.save(PNG, group="", sender="self")
        self.pack.tag("无语", sha1=self.row["sha1"])
        self.channel._rng = Coin(0.0)
        self.logs: list[str] = []
        self.channel.log = self.logs.append
        self.heard = [
            {"qq": "7", "card": "群友", "text": "我年薪三百万，你们不懂"},
            {"qq": "8", "card": "另一个人", "text": "哦"},
        ]

    def answered(self, text: str) -> ScriptedLLM:
        llm = ScriptedLLM(text)
        self.app.llm = llm
        return llm

    async def test_a_speechless_line_gets_the_picture(self):
        self.answered('{"face": true, "why": "吹牛"}')
        sent = await self.channel._maybe_face("100", self.heard)
        self.assertTrue(sent)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0][0], "send_group_msg")
        self.assertTrue(any("无语" in line for line in self.logs))

    async def test_an_ordinary_chat_gets_nothing(self):
        self.answered('{"face": false, "why": ""}')
        sent = await self.channel._maybe_face("100", self.heard)
        self.assertFalse(sent)
        self.assertEqual(self.client.calls, [])

    async def test_no_tag_means_the_judge_is_never_asked(self):
        self.pack.tag("", sha1=self.row["sha1"])
        llm = self.answered('{"face": true}')
        sent = await self.channel._maybe_face("100", self.heard)
        self.assertFalse(sent)
        self.assertEqual(llm.seen, [], "没标就没有这个功能，别花那个钱")

    async def test_the_coin_is_flipped_before_the_judge(self):
        self.channel._rng = Coin(0.99)
        llm = self.answered('{"face": true}')
        self.assertFalse(await self.channel._maybe_face("100", self.heard))
        self.assertEqual(llm.seen, [])

    async def test_a_broken_judge_sends_nothing(self):
        self.app.llm = ScriptedLLM('{"face": true}', fail_after=0)
        self.assertFalse(await self.channel._maybe_face("100", self.heard))
        self.assertEqual(self.client.calls, [])

    async def test_an_unreadable_answer_sends_nothing(self):
        self.answered("嗯……还好吧")
        self.assertFalse(await self.channel._maybe_face("100", self.heard))
        self.assertEqual(self.client.calls, [])

    async def test_one_room_gets_no_second_one_within_ten_minutes(self):
        self.answered('{"face": true}')
        self.assertTrue(await self.channel._maybe_face("100", self.heard))
        self.assertFalse(await self.channel._maybe_face("100", self.heard))
        self.assertEqual(len(self.client.calls), 1)

    async def test_the_judge_sees_the_last_few_lines(self):
        llm = self.answered('{"face": true}')
        await self.channel._maybe_face("100", self.heard)
        prompt = " ".join(str(one.content) for one in llm.seen[0])
        self.assertIn("年薪三百万", prompt)
        self.assertIn("说不出话", prompt)


class TestTaggingASticker(ChannelCase):
    """`/表情标` 的远程版：不用先把图发到群里，按 sha1 就能命名。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.pack = stickers.Pack(self.app.store, Path(self.tmp.name) / "faces", rng=Coin())
        self.app.store.migrate(self.pack.migrations())
        self.channel.pack = self.pack
        self.service = plugin_qq.QQService(
            self.app, QQConfig.from_env({"QQ_ENABLED": "1", "QQ_GROUP_IDS": "100"}), client=self.client
        )
        self.service.pack = self.pack
        self.row = self.pack.save(PNG, group="", sender="self")

    async def route(self, body: dict) -> dict:
        return await self.service.routes()[("POST", "/api/qq/sticker/tag")]({}, body)

    async def test_a_sha1_and_a_name_are_enough(self):
        answer = await self.route({"sha1": self.row["sha1"], "tag": "无语"})
        self.assertTrue(answer["tagged"])
        self.assertEqual(self.pack.count("无语"), 1)

    async def test_an_empty_name_untags_it(self):
        await self.route({"sha1": self.row["sha1"], "tag": "无语"})
        await self.route({"sha1": self.row["sha1"], "tag": ""})
        self.assertEqual(self.pack.count("无语"), 0)

    async def test_an_unknown_sha1_says_so(self):
        answer = await self.route({"sha1": "nope", "tag": "无语"})
        self.assertFalse(answer["tagged"])

    async def test_the_button_can_ask_for_one_by_name(self):
        self.pack.tag("无语", sha1=self.row["sha1"])
        answer = await self.service.send_sticker("100", "无语")
        self.assertTrue(answer["sent"])
        self.assertEqual(answer["tag"], "无语")

    async def test_the_button_says_why_when_the_name_is_empty(self):
        answer = await self.service.send_sticker("100", "不存在的标签")
        self.assertFalse(answer["sent"])
        self.assertIn("没有标", answer["why"])


class TestWhatSomebodyIs(ChannelCase):
    """「这个人是女性」这类事实由人写下来，作为事实进提示词，语气由她自己判断。

    不写成「对女性要温柔」这种规矩：那样群里每个女的都会拿到同一副腔调，她是在模仿规则
    而不是在判断一个人。事实进去，说法留给她。
    """

    PERSON = "200000001"
    CARD = "示例群友A"

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.identities.resolve(self.PERSON, card=self.CARD)

    def prompt(self) -> str:
        return " ".join(str(one.content) for call in self.app.llm.seen for one in call)

    async def test_a_note_arrives_with_the_person_she_is_answering(self):
        self.channel.identities.set_note(self.PERSON, "女性")
        await self.channel._turn("100", self.PERSON, self.CARD, "在吗", "n1", [])
        text = self.prompt()
        self.assertIn("关于和你说话的人", text)
        self.assertIn(f"{self.CARD}——女性", text)

    async def test_the_note_does_not_write_her_manner_for_her(self):
        """「所以要用温柔的语气」是规矩；这里只给事实，语气让她自己判断。"""
        self.channel.identities.set_note(self.PERSON, "女性")
        await self.channel._turn("100", self.PERSON, self.CARD, "在吗", "n2", [])
        text = self.prompt()
        self.assertIn("你自己判断", text)
        for command in ("要温柔", "必须用", "一律"):
            self.assertNotIn(command, text)

    async def test_somebody_with_no_note_is_not_described(self):
        await self.channel._turn("100", self.PERSON, self.CARD, "在吗", "n3", [])
        self.assertNotIn("关于和你说话的人", self.prompt())

    async def test_the_note_rides_along_with_the_lines_she_overheard(self):
        """旁听时没有「和她说话的人」，但谁是谁仍要看得见：括在名字后面。"""
        self.channel.identities.set_note(self.PERSON, "女性")
        heard = [
            {"qq": self.PERSON, "card": "示例群友", "text": "今天好热"},
            {"qq": "8", "card": "路人", "text": "是啊"},
        ]
        await self.channel._turn("100", self.PERSON, self.CARD, "今天好热", "n4", [], heard=heard)
        text = self.prompt()
        self.assertIn("示例群友（女性）：今天好热", text)
        self.assertIn("路人：是啊", text, "没写备注的人不该被加括号")

    async def test_someone_else_talking_about_her_is_still_labelled(self):
        """别人叫到她、而带备注的人在同一段记录里——备注跟着那个人走，不是跟着回合走。"""
        self.channel.identities.set_note(self.PERSON, "女性")
        heard = [{"qq": self.PERSON, "card": "示例群友", "text": "ふゆ在吗"}]
        await self.channel._turn("100", "42", "制作人", "在吗", "n5", [], heard=heard)
        self.assertIn("示例群友（女性）", self.prompt())

    async def test_a_thought_of_her_own_does_not_get_the_note_block(self):
        """旁听（ambient）时没有「和她说话的人」，把触发者的备注摆进去会让她以为在回这个人。"""
        self.channel.identities.set_note(self.PERSON, "女性")
        await self.channel._turn(
            "100", self.PERSON, self.CARD, "今天好热", "n6", [], ambient=True, own=True
        )
        self.assertNotIn("关于和你说话的人", self.prompt())

    async def test_an_empty_note_clears_it(self):
        self.channel.identities.set_note(self.PERSON, "女性")
        self.assertEqual(self.channel.identities.note(self.PERSON), "女性")
        self.channel.identities.set_note(self.PERSON, "")
        self.assertEqual(self.channel.identities.note(self.PERSON), "")

    async def test_a_stranger_who_never_spoke_is_not_created(self):
        """手滑打错的号不该变成 `/api/qq/status` 里的一个人。"""
        before = self.channel.identities.count()
        self.assertIsNone(self.channel.identities.set_note("999999", "女性"))
        self.assertEqual(self.channel.identities.count(), before)

    async def test_the_batch_read_matches_the_single_one(self):
        self.channel.identities.set_note(self.PERSON, "女性")
        found = self.channel.identities.notes([self.PERSON, "8", ""])
        self.assertEqual(found, {self.PERSON: "女性"})


class TestTheNoteButton(ChannelCase):
    """`POST /api/qq/note`：写一句话，不用改库。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.identities.resolve("200000001", card="示例群友")
        self.service = plugin_qq.QQService(
            self.app, QQConfig.from_env({"QQ_ENABLED": "1", "QQ_GROUP_IDS": "100"}), client=self.client
        )

    async def route(self, body: dict) -> dict:
        return await self.service.routes()[("POST", "/api/qq/note")]({}, body)

    async def test_it_writes_the_fact_down(self):
        answer = await self.route({"qq": "200000001", "note": "女性"})
        self.assertTrue(answer["named"])
        self.assertEqual(self.channel.identities.note("200000001"), "女性")

    async def test_an_unknown_number_says_so(self):
        answer = await self.route({"qq": "999999", "note": "女性"})
        self.assertFalse(answer["named"])
        self.assertIn("还没说过话", answer["why"])

    async def test_no_number_at_all_says_so(self):
        answer = await self.route({"note": "女性"})
        self.assertFalse(answer["named"])


class Bilingual:
    """按 system 提示分流的假模型：写台词时给 `raw`，被要求翻译时给 `chinese`。

    一个模型干两件事，所以只能靠提示词区分——真实部署里也是同一个模型。
    """

    def __init__(self, raw: str, chinese: str = "", *, fail: bool = False, size: int = 7) -> None:
        self.raw = raw
        self.chinese = chinese
        self.fail = fail
        self.size = size
        self.seen: list[list[Any]] = []
        self.translated = 0

    async def stream(self, messages):  # noqa: ANN001 - the channel's LLM protocol
        self.seen.append(list(messages))
        asked = " ".join(str(one.content) for one in messages)
        if "翻成中文" in asked:
            self.translated += 1
            if self.fail:
                raise RuntimeError("翻译挂了")
            reply = self.chinese
        else:
            reply = self.raw
        for start in range(0, len(reply), self.size):
            yield reply[start : start + self.size]


class TestWhenTheSubtitleIsMissing(ChannelCase):
    """模型偶尔只写 `<ja>`、不写 `<zh>`。这个号只发字幕，于是那一轮等于没说话。

    2026-10-28 线上同时暴露了它的两种表现，其实是同一个原因：群里 @ 了三次一条不回
    （`segments` 第二字段是空串），私聊和群聊里又冒出整句日语（兜底把原文原样发了出去）。
    现在兜底分两路——本来是中国话的照发，带着日语的先翻一遍，翻不出来就说「没听清」。
    **哪一条路都不许把日语发出去**：对方看不懂，发出去等于没说。
    """

    async def reply_to(self, raw: str, *, chinese: str = "", fail: bool = False) -> list[str]:
        model = Bilingual(raw, chinese, fail=fail)
        # engine 记着构造时的那个模型；`app.llm` 是兜底翻译用的那个。两边都得换。
        engine = self.app.engines["test"]
        self.app.engines["test"] = replace(engine, llm=model)
        self.app.llm = model
        self.channel._engines.clear()
        before = len(self.client.texts)
        await self.channel._turn("100", "42", "制作人", "在吗", "s1", [])
        self.model = model
        return self.client.texts[before:]

    async def test_japanese_with_no_subtitle_is_translated_not_posted_raw(self):
        said = await self.reply_to(
            "<ja>おはようございます。今日は早いですね。</ja>", chinese="早上好。今天真早啊。"
        )
        self.assertEqual(said, ["早上好。今天真早啊。"], "日语不许出现在气泡里")

    async def test_a_translation_failure_never_falls_back_to_japanese(self):
        said = await self.reply_to("<ja>おはようございます。</ja>", fail=True)
        self.assertEqual(len(said), 1)
        self.assertIn("没听清", said[0], "翻不出来就说没听清——那是中文")
        self.assertNotIn("おはよう", said[0])

    async def test_chinese_written_into_the_wrong_tag_is_posted_as_is(self):
        """「ふゆ在这里哦」被写进 `<ja>`：它本来就是中文，不该再花一次调用去翻。"""
        said = await self.reply_to("<ja>ふゆ在这里哦。……怎么了吗？</ja>")
        self.assertEqual(said, ["ふゆ在这里哦。……怎么了吗？"])
        self.assertEqual(self.model.translated, 0)

    async def test_the_acting_notes_do_not_reach_the_bubble(self):
        said = await self.reply_to(
            "<ja>[sulking] ふゆ、そういうのはやめてください。</ja>", chinese="……请不要这样。"
        )
        self.assertEqual(said, ["……请不要这样。"])

    async def test_the_acting_notes_are_stripped_before_translating(self):
        await self.reply_to("<ja>[sigh] ふゆ、そういうのはやめてください。</ja>", chinese="……请不要这样。")
        sent = " ".join(str(one.content) for one in self.model.seen[-1])
        self.assertNotIn("[sigh]", sent)

    async def test_the_translation_asks_for_chinese_only(self):
        await self.reply_to("<ja>おはよう</ja>", chinese="早上好")
        asked = " ".join(str(one.content) for one in self.model.seen[-1])
        self.assertIn("只输出中文", asked)

    async def test_labels_in_the_translation_are_peeled_off(self):
        said = await self.reply_to("<ja>おはよう</ja>", chinese="<zh>早上好</zh>")
        self.assertEqual(said, ["早上好"])

    async def test_chinese_still_wins_when_it_is_there(self):
        said = await self.reply_to("<ja>おはよう</ja><zh>早上好</zh>")
        self.assertEqual(said, ["早上好"], "有字幕就只发字幕，不能把原文也倒出来")
        self.assertEqual(self.model.translated, 0)

    async def test_a_reply_with_nothing_in_it_falls_back_to_the_old_answer(self):
        said = await self.reply_to("<ja></ja><zh></zh>")
        self.assertEqual(len(said), 1)
        self.assertIn("没听清", said[0])

    async def test_the_turn_still_counts_as_having_talked(self):
        """兜底发出去的那句也算「她回过他」，否则熟络度永远不涨。"""
        before = self.channel.identities.turns("42")
        await self.reply_to("<ja>おはよう</ja>", chinese="早上好")
        self.assertEqual(self.channel.identities.turns("42"), before + 1)

    async def test_a_missing_subtitle_is_written_down(self):
        logs: list[str] = []
        self.channel.log = logs.append
        await self.reply_to("<ja>おはよう</ja>", chinese="早上好")
        self.assertTrue(any("没有中文字幕" in line for line in logs))


class TestTheConfession(ChannelCase):
    """告白是「在一起」的唯一入口。门槛来自她的 PLove（test 角色 = 3 → 85 / 30）。

    `refresh_love` 已经不再置位了：它只回答「够不够格开口」。谁真的进恋爱，看这里。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.channel.config = replace(self.channel.config, private=True, owner_love=85.0)
        self.channel.identities.resolve("42", card="", role="owner")
        self.logs: list[str] = []
        self.channel.log = self.logs.append

    def standing(self, value: float, turns: int = 40) -> str:
        user = self.channel.identities.resolve("42", card="", role="owner")
        self.channel.identities.set_turns("42", turns)
        self.app.store.execute(
            "DELETE FROM affinity_scenes WHERE user_id = ? AND character_id = 'test'", (user,)
        )
        self.app.store.execute(
            "INSERT INTO affinity_scenes"
            " (user_id, character_id, surface, value, updated_at, cooling)"
            " VALUES (?, 'test', 'dm', ?, ?, 0)",
            (user, value, datetime.now(timezone.utc).isoformat()),
        )
        return user

    def as_character(self, **changes):
        """临时换一个角色设定：未成年 / PLove=0 不该靠改线上数据来测。"""
        from dataclasses import replace as _replace

        base = self.channel._character()
        self.channel._character = lambda: _replace(base, **changes)

    async def test_a_good_shot_is_accepted_and_that_is_the_relationship(self):
        self.standing(100.0, 80)
        answer = await self.channel.confess("42", card="制作人")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["why"], "accept")
        self.assertEqual(self.channel.identities.relation("42"), "loving")
        self.assertTrue(answer["said"])
        self.assertTrue(any("告白了" in line for line in self.logs))

    async def test_a_bare_pass_is_a_refusal_not_a_maybe(self):
        """好感刚够、话刚够：她会听完，然后说不行。门槛不是「会答应」。"""
        self.standing(86.0, 30)
        answer = await self.channel.confess("42")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["why"], "refuse")
        self.assertEqual(self.channel.identities.relation("42"), "crush", "说破了，但没成")

    async def test_a_minor_is_told_no_and_stops_at_crush(self):
        self.as_character(adult=False, age=17)
        self.standing(100.0, 80)
        answer = await self.channel.confess("42")
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["why"], "minor")
        self.assertEqual(self.channel.identities.relation("42"), "crush")
        self.assertEqual(
            self.channel.identities.relation("42") != "loving", True, "未成年永远到不了这里"
        )

    async def test_plove_zero_does_not_even_reach_crush(self):
        """「没有这条线」不是「很难追」：连暧昧都不该有，所以状态不动。"""
        self.as_character(plove=0)
        self.standing(100.0, 80)
        answer = await self.channel.confess("42")
        self.assertEqual(answer["why"], "no_way")
        self.assertEqual(self.channel.identities.relation("42"), "none")

    async def test_a_refusal_has_a_twice_a_week_lock(self):
        self.standing(86.0, 30)
        await self.channel.confess("42")
        second = await self.channel.confess("42")
        self.assertEqual(second["why"], "cooldown")
        self.assertEqual(
            self.channel.identities.asks_by_him("42", "test"), 1, "冷却里那次不该记账"
        )

    async def test_three_attempts_is_the_lot(self):
        self.standing(86.0, 30)
        for _ in range(3):
            self.channel.identities.record_confession(
                "42", "test", by="him", at="2020-01-01T00:00:00+00:00",
                outcome="refuse", reason="not_close",
            )
        answer = await self.channel.confess("42")
        self.assertEqual(answer["why"], "maxed")

    async def test_together_is_not_asked_again(self):
        self.standing(100.0, 80)
        self.channel.identities.set_relation("42", "loving")
        answer = await self.channel.confess("42")
        self.assertEqual(answer["why"], "already")
        self.assertEqual(len(self.channel.identities.confessions("42", "test")), 0)

    async def test_every_attempt_is_written_down_even_a_failure(self):
        self.standing(86.0, 30)
        await self.channel.confess("42")
        rows = self.channel.identities.confessions("42", "test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "refuse")
        self.assertEqual(rows[0]["by"], "him")
        self.assertEqual(rows[0]["plove"], 3)

    async def test_a_stored_relationship_the_rules_forbid_is_rolled_back(self):
        """库里的东西可能比规则老：读的时候对一遍，比相信它便宜。

        场景：关系写在「年龄门禁」之前，或者角色设定后来被改小了。启动自检只能管
        启动那一刻，`_relation` 管的是每一次读。
        """
        self.standing(100.0, 80)
        self.channel.identities.set_relation("42", "loving")
        self.as_character(adult=False, age=17)
        self.assertEqual(self.channel._relation("42"), "crush", "未成年封顶在暧昧")
        self.assertEqual(self.channel.identities.relation("42"), "crush", "库里也要改回来")

    async def test_plove_zero_rolls_back_to_none_not_crush(self):
        self.standing(100.0, 80)
        self.channel.identities.set_relation("42", "loving")
        self.as_character(plove=0)
        self.assertEqual(self.channel._relation("42"), "none", "连暧昧都没有")


class TestSheSpeaksFirst(ChannelCase):
    """她等太久会自己开口——三条门槛、一天一掷、说出口就进 `offered`。

    「等」是连续天数，不是累计：`warm_since` 掉下去就清零，否则「三周里挑出二十一天
    好日子」也能算数。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.logs: list[str] = []
        self.service = plugin_qq.QQService(
            self.app,
            QQConfig(
                enabled=True, groups=(), self_id="999", owners=("42",),
                character="test", private=True, private_ids=("42",),
                owner_love=85.0,
            ),
            client=self.client, log=self.logs.append,
        )
        self.app.store.migrate(self.service.migrations())
        self.channel = self.service.channel
        self.channel.log = self.logs.append
        self.channel.identities.resolve("42", card="", role="owner")
        self.now = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)

    def standing(self, value: float, turns: int = 40) -> None:
        user = self.channel.identities.resolve("42", card="", role="owner")
        self.channel.identities.set_turns("42", turns)
        self.app.store.execute(
            "DELETE FROM affinity_scenes WHERE user_id = ? AND character_id = 'test'", (user,)
        )
        self.app.store.execute(
            "INSERT INTO affinity_scenes"
            " (user_id, character_id, surface, value, updated_at, cooling)"
            " VALUES (?, 'test', 'dm', ?, ?, 0)",
            (user, value, self.now.isoformat()),
        )

    def waited(self, days: float) -> None:
        since = (self.now - timedelta(days=days)).isoformat(timespec="seconds")
        self.channel.identities.mark_warm("42", since)

    async def test_not_long_enough_and_she_says_nothing(self):
        self.standing(100.0)
        self.waited(5)
        await self.service._she_once(self.now)
        self.assertEqual(self.channel.identities.relation("42"), "none")
        self.assertEqual(self.client.texts, [])

    async def test_below_the_bar_resets_the_clock(self):
        """掉下 90 之后再回来：从头数。"""
        self.standing(60.0)
        self.waited(30)
        await self.service._she_once(self.now)
        self.assertEqual(self.channel.identities.warm_since("42"), "", "掉下门槛就得清")

    async def test_one_roll_a_day(self):
        self.standing(100.0)
        self.waited(30)
        self.channel._rng = Coin(0.99)  # 掷不中
        await self.service._she_once(self.now)
        self.assertFalse(self.channel.identities.relation("42") == "offered")
        self.channel._rng = Coin(0.0)  # 就算换成一个必中的骰子
        await self.service._she_once(self.now)
        self.assertEqual(
            self.channel.identities.relation("42"), "none", "今天已经掷过了，不再掷第二次"
        )

    async def test_a_hit_puts_her_in_offered(self):
        self.standing(100.0)
        self.waited(30)
        self.channel._rng = Coin(0.0)  # 必中
        await self.service._she_once(self.now)
        self.assertEqual(self.channel.identities.relation("42"), "offered")
        self.assertTrue(self.channel.identities.offered_at("42"))
        rows = self.channel.identities.confessions("42", "test")
        self.assertEqual(rows[0]["by"], "her")
        self.assertEqual(rows[0]["outcome"], "offered")

    async def test_seven_days_of_silence_ends_it_and_costs_more(self):
        self.standing(100.0)
        self.channel.identities.set_relation("42", "offered")
        self.channel.identities.set_offered(
            "42", (self.now - timedelta(days=8)).isoformat(timespec="seconds")
        )
        await self.service._she_once(self.now)
        self.assertEqual(self.channel.identities.relation("42"), "crush")
        self.assertEqual(self.channel.identities.offered_at("42"), "")
        rows = self.channel.identities.confessions("42", "test")
        self.assertEqual(rows[0]["outcome"], "timeout")
        self.assertTrue(any("收回来了" in line for line in self.logs))

    async def test_he_says_yes_and_that_is_the_relationship(self):
        self.standing(100.0)
        self.channel.identities.set_relation("42", "offered")
        self.channel.identities.set_offered("42", self.now.isoformat(timespec="seconds"))
        self.assertEqual(self.channel._answer_offer("42", "好啊，我也喜欢你"), "accept")
        self.assertEqual(self.channel.identities.relation("42"), "loving")
        self.assertEqual(self.channel.identities.offered_at("42"), "")

    async def test_he_says_no_and_it_falls_back_to_crush(self):
        self.standing(100.0)
        self.channel.identities.set_relation("42", "offered")
        self.channel.identities.set_offered("42", self.now.isoformat(timespec="seconds"))
        self.assertEqual(self.channel._answer_offer("42", "对不起"), "refuse")
        self.assertEqual(self.channel.identities.relation("42"), "crush")

    async def test_three_dodges_count_as_a_no(self):
        self.standing(100.0)
        self.channel.identities.set_relation("42", "offered")
        self.channel.identities.set_offered("42", self.now.isoformat(timespec="seconds"))
        self.assertEqual(self.channel._answer_offer("42", "你说什么呢"), "dodge")
        self.assertEqual(self.channel._answer_offer("42", "今天好累"), "dodge")
        self.assertEqual(self.channel.identities.relation("42"), "offered", "还在等")
        self.assertEqual(self.channel._answer_offer("42", "先吃饭吧"), "refuse")
        self.assertEqual(self.channel.identities.relation("42"), "crush")


class TestTheRelationVoices(ChannelCase):
    """暧昧 / 等答复 / 恋爱三段各归各的，群聊哪一段都不注入。

    这三段必须分开写：crush 的核心是「没说破」，offered 是「说了在等」，loving 是
    「已经在一起」。塞进同一段里，她会按其中最像的那一句演整场。
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.dm = Path(self.tmp.name) / "characters" / "world" / "fuyuko-dm.md"
        self.dm.write_text(
            "【场景】私聊，只有你们两个。\n"
            "【恋爱】你们已经在谈恋爱了。\n"
            "【暧昧】有点什么，但谁都没说破。\n"
            "【等答复】你说出口了，他还没答。\n"
            "【语域】タメ口。\n"
            "【时间】{{time}}\n",
            encoding="utf-8",
        )
        age(self.dm)
        self.channel.presence.refresh()

    def base(self, relation: str, scene: str = presence.PRIVATE) -> str:
        engine = self.channel._engine("test", scene, "", relation)
        return "\n".join(section.text for section in engine.base)

    def test_loving_says_together_and_nothing_else(self):
        text = self.base("loving")
        self.assertIn("谈恋爱", text)
        self.assertNotIn("没说破", text)
        self.assertNotIn("说出口了", text)

    def test_crush_says_unsaid_and_not_together(self):
        text = self.base("crush")
        self.assertIn("没说破", text)
        self.assertNotIn("谈恋爱", text)
        self.assertNotIn("说出口了", text)

    def test_offered_says_spoken_and_not_together(self):
        text = self.base("offered")
        self.assertIn("说出口了", text)
        self.assertNotIn("谈恋爱", text)
        self.assertNotIn("没说破", text)

    def test_none_gets_none_of_them(self):
        text = self.base("")
        for word in ("谈恋爱", "没说破", "说出口了"):
            self.assertNotIn(word, text)

    def test_a_group_never_gets_any_of_them(self):
        for relation in ("loving", "crush", "offered"):
            text = self.base(relation, presence.GROUP)
            for word in ("谈恋爱", "没说破", "说出口了"):
                self.assertNotIn(word, text, relation)


class TestOnlyChineseReachesTheBubble(ChannelCase):
    """这个号只发中文——包括通道自己拼的话，不只是模型写的台词。

    2026-10-28 的线上问题，根因不在模型：`/好感`、`/表情`、`/重载` 这些指令的回复、
    拒绝话术（`-refusals.toml`）、告白的台词，全都是日语写的，直接 `_say` 进气泡。
    「只发中文字幕」这道关只拦了模型输出，从没拦过通道自己写的话。所以现在有一把锁：
    **任何会到达气泡的固定字符串，都不许是日语。**
    """

    def canned(self) -> list[tuple[str, str]]:
        """所有「通道自己写的话」。新加一条话术就该出现在这里，否则这把锁形同虚设。"""
        world = Path(__file__).resolve().parents[1] / "characters" / "world"
        refusals = texts.load(world / "fuyuko-refusals.toml")
        lines: list[tuple[str, str]] = [
            ("CONFESS_OK", qq_module.CONFESS_OK),
            ("help/private", qq_module.help_text(private=True)),
            ("help/group", qq_module.help_text(private=False)),
        ]
        for key, said in qq_module.CONFESS_NO.items():
            lines.append((f"CONFESS_NO[{key}]", said))
        for scene, table in refusals.items():
            for key, said in table.items():
                lines.append((f"refusals/{scene}/{key}", said))
        for scene, table in texts.DEFAULT.items():
            for key, said in table.items():
                lines.append((f"texts/{scene}/{key}", said))
        return lines

    def test_no_canned_line_is_japanese(self):
        bad = [
            f"{where}: {said}"
            for where, said in self.canned()
            if qq_module._looks_japanese(said)
        ]
        self.assertEqual(bad, [], "固定话术里还有日语——它在 QQ 里会原样出现")

    async def test_the_commands_answer_in_chinese(self):
        for command in ("/好感", "/阶段", "/重载", "/重开", "/不认识的指令"):
            before = len(self.client.texts)
            await self.say(event(command, qq="42", message_id=f"c{len(self.client.texts)}"))
            said = self.client.texts[before:]
            self.assertTrue(said, f"{command} 应该有一句回复")
            for line in said:
                self.assertFalse(
                    qq_module._looks_japanese(line), f"{command} 回了一句日语：{line}"
                )

    async def test_a_missing_subtitle_translation_is_chinese_too(self):
        """兜底那条路也要过这把锁（它是这次唯一一处「请求模型翻译」的地方）。"""
        model = Bilingual("<ja>おはようございます。今日は早いですね。</ja>", "早上好。今天真早啊。")
        engine = self.app.engines["test"]
        self.app.engines["test"] = replace(engine, llm=model)
        self.app.llm = model
        self.channel._engines.clear()
        before = len(self.client.texts)
        await self.channel._turn("100", "42", "制作人", "在吗", "cn1", [])
        for line in self.client.texts[before:]:
            self.assertFalse(qq_module._looks_japanese(line), line)

    async def test_the_channel_tells_the_model_that_only_zh_is_seen(self):
        """根因那一条：模型得知道 `<ja>` 对方看不见，否则它会把中文写进 `<ja>`。"""
        await self.channel._turn("100", "42", "制作人", "在吗", "cn2", [])
        prompt = " ".join(str(one.content) for call in self.app.llm.seen for one in call)
        self.assertIn("只有 `<zh>`", prompt)
        self.assertIn("空着就等于", prompt)


class TestTheHoursSheKeeps(AmbientCase):
    """作息：工作日 23–9 睡，休息日拖到 2 点；有人叫她照回，只是语气变成「被吵醒」。

    原来只有「自己开口」那条路径看时间，而且看的是 UTC 的钟：北京时间 11:00 会被当成
    深夜不开口，18:00 反而可以。看到别人说话就插一句的那条路径**根本没有时间闸**，
    所以凌晨三点的热闹群里她照样接话。
    """

    JST = timezone(timedelta(hours=9))

    def local(self, *args) -> datetime:
        """她的钟是日本的（UTC+9）——她是日本人，几点睡不跟着部署地走。"""
        return datetime(*args, tzinfo=self.JST)

    def config(self, **overrides) -> None:
        base = dict(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=True, ambient_p=1.0, ambient_gap=0.0, ambient_check=0.0,
            ambient_own_p=0.0, ambient_sticker_p=0.0,
            holidays=("2026-10-01",), workdays=("2026-10-03",),
        )
        base.update(overrides)
        self.channel.config = QQConfig(**base)

    def at(self, *args) -> None:
        when = self.local(*args).astimezone(timezone.utc)
        self.channel._clock = lambda: when

    def test_a_workday_night_is_asleep(self):
        self.config()
        self.assertTrue(self.channel.resting(self.local(2026, 10, 30, 23, 30)), "周五 23:30")
        self.assertTrue(self.channel.resting(self.local(2026, 10, 30, 8, 59)), "周五早上 8:59")
        self.assertFalse(self.channel.resting(self.local(2026, 10, 30, 22, 30)))
        self.assertFalse(self.channel.resting(self.local(2026, 10, 30, 9, 0)))

    def test_a_day_off_stays_up_until_two(self):
        self.config()
        self.assertFalse(self.channel.resting(self.local(2026, 10, 31, 1, 0)), "周六 1 点还醒着")
        self.assertFalse(self.channel.resting(self.local(2026, 10, 31, 1, 59)))
        self.assertTrue(self.channel.resting(self.local(2026, 10, 31, 2, 30)))

    def test_a_listed_holiday_is_a_day_off_and_a_listed_saturday_is_not(self):
        self.config()
        # 2026-10-01 是周四，但写在 QQ_HOLIDAYS 里 → 按休息日（凌晨一点还醒着）。
        self.assertFalse(self.channel.resting(self.local(2026, 10, 1, 1, 30)))
        # 2026-10-03 是周六，但写在 QQ_WORKDAYS 里（调休）→ 按工作日。
        self.assertTrue(self.channel.resting(self.local(2026, 10, 3, 1, 30)))

    async def test_she_does_not_join_a_room_after_her_bedtime(self):
        self.config()
        self.at(2026, 10, 30, 23, 30)
        await self.quiet("まだ起きてる", message_id="a")
        await self.quiet("眠れない", message_id="b")
        self.assertEqual(self.client.texts, [])
        self.assertEqual(self.channel._ambient_used("100"), 0, "睡着了不花额度")

    async def test_a_call_at_night_is_answered_in_the_woken_up_voice(self):
        self.config()
        self.at(2026, 10, 30, 23, 40)
        # 好感是插件里的数，这里只关心语气，所以直接给一个「亲近」的值。
        self.channel._affinity = lambda *a, **k: 80.0
        seen: dict = {}
        original = self.app.turn

        def spy(*args, **kwargs):
            seen["sections"] = kwargs.get("sections") or []
            return original(*args, **kwargs)

        self.app.turn = spy
        await self.say(event("寝てる？", qq="43", message_id="c"))
        text = "\n".join(section.text for section in seen["sections"])
        self.assertIn("这个点你已经睡了", text, "叫醒她这件事要进提示词")
        self.assertTrue(self.client.texts, "被叫醒也照回")

    def test_the_woken_up_voice_depends_on_how_close_they_are(self):
        self.config()
        self.at(2026, 10, 30, 23, 40)
        far = self.channel._night_note(10)
        mid = self.channel._night_note(50)
        close = self.channel._night_note(80)
        self.assertIn("很不高兴", far, "陌生人：冷、可以直接说不方便")
        self.assertIn("不耐烦", mid)
        self.assertIn("抱怨", close, "亲近的人：抱怨其实是担心")
        self.assertEqual(len({far, mid, close}), 3)


class TestQuoteMarks(ChannelCase):
    """人设文件用「」标例句，模型偶尔把引号写进台词 —— 发出去前剥掉整句外层那一对。"""

    async def test_a_wrapped_line_goes_out_bare(self):
        await self.channel._emit("100", "43", "「……え、なんの話です？」", "m1", True)
        self.assertEqual(self.client.texts, ["……え、なんの話です？"])

    async def test_a_quote_inside_the_line_is_left_alone(self):
        await self.channel._emit("100", "43", "「はぁ？」って言っただけです", "m2", True)
        self.assertEqual(self.client.texts, ["「はぁ？」って言っただけです"])


class TestTheWokenThread(AmbientCase):
    """被叫醒之后的那几个回合：谁第一声不理、几轮之后她自己说「睡了」。

    这三件事是同一个状态（`rest:<群>:<号>` 一行 JSON），所以测的也是同一件事的三种问法。
    """

    JST = timezone(timedelta(hours=9))

    def config(self, **overrides) -> None:
        base = dict(
            enabled=True, groups=("100",), self_id="999", owners=("42",),
            character="test", merge_seconds=0.0, member_cooldown=0.0,
            ambient=False, ambient_idle=0, ambient_p=0.0, ambient_sticker_p=0.0,
        )
        base.update(overrides)
        self.channel.config = QQConfig(**base)

    def night(self, hour: int = 23, minute: int = 40, day: int = 30) -> None:
        when = datetime(2026, 10, day, hour, minute, tzinfo=self.JST).astimezone(timezone.utc)
        self.channel._clock = lambda: when

    def capture(self) -> list[str]:
        seen: list[str] = []
        original = self.app.turn

        def spy(*args, **kwargs):
            seen.append("\n".join(section.text for section in kwargs.get("sections") or []))
            return original(*args, **kwargs)

        self.app.turn = spy
        return seen

    async def test_a_distant_person_is_ignored_the_first_time(self):
        """疏远的人：睡着了第一声当作没听见，连续再叫一次才睁眼。"""
        self.config()
        self.night()
        await self.say(event("ねえ", qq="43", message_id="n1"))
        self.assertEqual(self.client.texts, [], "第一次不理")
        await self.say(event("ねえってば", qq="43", message_id="n2"))
        self.assertTrue(self.client.texts, "第二次才回")

    async def test_somebody_she_trusts_is_answered_at_once(self):
        self.config()
        self.night()
        self.channel._affinity = lambda *a, **k: 60.0
        await self.say(event("起きてる？", qq="43", message_id="n3"))
        self.assertTrue(self.client.texts)

    async def test_she_ends_it_on_the_round_the_dice_says(self):
        self.config()
        self.night()
        self.channel._affinity = lambda *a, **k: 80.0
        self.channel._rng = Coin(0.5)  # 1 + int(0.5 * 6) = 4
        seen = self.capture()

        for index in range(4):
            await self.say(event(f"ねえ{index}", qq="43", message_id=f"t{index}"))
        self.assertIn("第 4 轮", seen[-1])
        self.assertIn("最后一轮", seen[-1])

        before = len(self.client.texts)
        await self.say(event("まだ起きてる？", qq="43", message_id="t9"))
        self.assertEqual(len(self.client.texts), before, "说睡了之后就真的不回")

    async def test_the_budget_never_exceeds_six_rounds(self):
        self.channel._rng = Coin(0.999)
        _speak, _turn, ends = self.channel._night_visit("100", "43", 80.0)
        self.assertLessEqual(ends, 6)
        self.assertEqual(ends, 6, "骰子最大也只到六轮")

    async def test_a_new_sleep_starts_from_zero(self):
        self.config()
        self.night()
        self.channel._affinity = lambda *a, **k: 80.0
        self.channel._rng = Coin(0.0)  # 1 轮就结束
        await self.say(event("ねえ", qq="43", message_id="a1"))
        self.assertTrue(self.client.texts)
        before = len(self.client.texts)
        await self.say(event("もう一声", qq="43", message_id="a2"))
        self.assertEqual(len(self.client.texts), before, "这一觉结束了")

        # 第二天晚上同一觉之外：账本按「醒来的那天」编号，所以重新开始。
        self.channel._rng = Coin(0.5)
        self.night(day=31)
        await self.say(event("こんばんは", qq="43", message_id="a3"))
        self.assertGreater(len(self.client.texts), before, "新的一觉照常")


class TestSheSendsPictures(AmbientCase):
    """`<photo>` 协议位 → 真的有一张图发到私聊里。

    用假画家（不联网、返回 1×1 PNG）：这条链路要验的是**接线**，不是画得好不好。
    """

    def paint(self, reply: str, *, fail=None, hourly: int = 6) -> None:
        from channels.draw import FakePainter

        self.channel.painter = FakePainter(fail=fail)
        self.channel.config = replace(self.channel.config, image_hourly=hourly)
        # 引擎是在 App 构造时建好的，换掉 llm 之后要重建，否则用的还是原来那个。
        self.app.llm = ScriptedLLM(reply)
        self.app._build_engines()

    def images(self) -> list[str]:
        return [
            action
            for action, params in self.client.calls
            if action == "send_private_msg"
            and any(part.get("type") == "image" for part in params.get("message", []))
        ]

    async def test_a_photo_tag_becomes_a_picture_in_the_dm(self):
        self.paint("<photo>夜の街</photo><ja>見て</ja><zh>给你看</zh>")
        await self.channel._turn("42", "42", "制作人", "写真", "", [], private=True)
        self.assertEqual(self.images(), ["send_private_msg"], "私聊里真的发出了一张图")
        self.assertEqual(self.channel.painter.asked, ["夜の街"])
        texts = [t for t in self.client.texts]
        self.assertIn("给你看", texts, "话照常说，图随后到")

    async def test_a_group_reply_never_paints(self):
        self.paint("<photo>夜の街</photo><ja>見て</ja><zh>给你看</zh>")
        await self.channel._turn("100", "43", "路人", "写真", "", [])
        self.assertEqual(self.client.texts, ["给你看"])
        self.assertEqual(self.images(), [], "群里不放行")
        self.assertEqual(self.channel.painter.asked, [], "连画家都不叫")

    async def test_the_hourly_cap_stops_the_second_one(self):
        self.paint("<photo>a</photo><ja>x</ja><zh>好</zh>", hourly=1)
        await self.channel._turn("42", "42", "制作人", "1", "", [], private=True)
        await self.channel._turn("42", "42", "制作人", "2", "", [], private=True)
        self.assertEqual(self.images(), ["send_private_msg"], "一小时一张")

    async def test_a_broken_painter_costs_nothing(self):
        self.paint("<photo>a</photo><ja>x</ja><zh>好</zh>", fail=RuntimeError("服务商挂了"))
        await self.channel._turn("42", "42", "制作人", "1", "", [], private=True)
        self.assertEqual(self.images(), [])
        self.assertEqual(self.client.texts, ["好"], "话不能跟着一起丢")

    async def test_no_painter_means_no_picture(self):
        self.paint("<photo>a</photo><ja>x</ja><zh>好</zh>")
        self.channel.painter = None
        await self.channel._turn("42", "42", "制作人", "1", "", [], private=True)
        self.assertEqual(self.images(), [])
