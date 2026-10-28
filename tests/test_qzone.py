"""QQ 空间的说说：什么时候发、写什么、发不出去的时候算哪一种。

三个场景各占一组：正常发一条、发布失败（含登录、权限、接口不存在），以及不该发的时候
（内容不通过、重复、没到点、骰子没中）。全部离线——模型是脚本，OneBot 是假的，
时间是被交进来的，所以断言的是行为而不是天气。
"""

import asyncio
import tempfile
import unittest
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from channels import qzone as space
from channels.plugin_qq import QQService
from channels.qzone import ContentError, PublishError, Qzone
from core.llm import ChatMessage, ScriptedLLM
from core.store import Store

EVENING = datetime(2026, 10, 27, 21, 30, tzinfo=timezone.utc)
"""周二的九点半：过了默认的二十一点，还没到第二天。"""

GOOD = "今天那句还是没唱顺。明天再来一遍。"
"""一条像她会写的：短、句号多、不说给谁听。"""

PERSONA = "# 空间里的她\n\n句子短。不超过三句。不写具体行程。\n"


class Clock:
    """A clock the test moves. `Qzone` never reads the wall clock in a test."""

    def __init__(self, at: datetime = EVENING) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def later(self, hours: float) -> datetime:
        self.at = self.at + timedelta(hours=hours)
        return self.at


class Bot:
    """OneBot that records instead of sending, and fails the way NapCat fails."""

    def __init__(self, *, tid: str = "tid-1", fail: BaseException | None = None,
                 answer: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.tid = tid
        self.fail = fail
        self.answer = answer

    async def call(self, action: str, **params):
        self.calls.append((action, params))
        if self.fail is not None:
            raise self.fail
        if self.answer is not None:
            return self.answer
        return {"tid": self.tid} if action == "send_qzone_msg" else None


class Rounds:
    """A model that answers differently on each call — for the 「rewrite it once」 path."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.seen: list[Sequence[ChatMessage]] = []

    async def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        self.seen.append(list(messages))
        text = self.answers[min(len(self.seen) - 1, len(self.answers) - 1)]
        for start in range(0, len(text), 7):
            yield text[start : start + 7]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "db.sqlite")
        self.addCleanup(self.store.close)
        self.store.migrate(space.SCHEMA)
        self.persona = self.root / "fuyuko-qzone.md"
        self.persona.write_text(PERSONA, encoding="utf-8")
        self.clock = Clock()
        self.flags = space.Memory()
        self.logs: list[str] = []

    def zone(self, *, bot=None, llm=None, chance=1.0, hour=21, gap=20.0, right=4,
             retry=space.RETRY_GAP) -> Qzone:
        return Qzone(
            bot or Bot(),
            llm or ScriptedLLM(GOOD),
            self.store,
            character="fuyuko",
            persona=self.persona,
            flags=self.flags,
            ugc_right=right,
            hour=hour,
            chance=chance,
            gap=gap,
            retry=retry,
            clock=self.clock,
            rng=lambda: 0.0,
            log=self.logs.append,
        )

    def status(self) -> dict:
        return asyncio.run(self.zone().maybe())


class TestPublishing(Base):
    """正常路径：到点了，写一条，发出去，记下来。"""

    def test_a_normal_evening_posts_exactly_one(self):
        bot = Bot()
        zone = self.zone(bot=bot)
        result = asyncio.run(zone.maybe())

        self.assertTrue(result["posted"])
        self.assertEqual(result["tid"], "tid-1")
        self.assertEqual(len(bot.calls), 1)
        action, params = bot.calls[0]
        self.assertEqual(action, "send_qzone_msg")
        self.assertEqual(params["content"], GOOD)
        self.assertEqual(params["ugc_right"], 4, "默认好友可见")
        self.assertEqual(params["images"], [], "第一版只发文字")

    def test_the_post_is_written_down(self):
        zone = self.zone()
        asyncio.run(zone.maybe())
        rows = self.store.query("SELECT content, tid, ok FROM qzone_posts")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], GOOD)
        self.assertEqual(rows[0]["tid"], "tid-1")
        self.assertTrue(rows[0]["ok"])

    def test_the_day_is_spent_after_one(self):
        bot = Bot()
        zone = self.zone(bot=bot)
        asyncio.run(zone.maybe())
        again = asyncio.run(zone.maybe())
        self.assertFalse(again["posted"])
        self.assertEqual(again["why"], "今天已经发过")
        self.assertEqual(len(bot.calls), 1, "一天一条，不是一分钟一条")

    def test_before_the_hour_she_waits(self):
        self.clock.at = EVENING.replace(hour=20, minute=59)
        result = self.status()
        self.assertFalse(result["posted"])
        self.assertEqual(result["why"], "还不到点")

    def test_nothing_is_written_before_the_hour(self):
        self.clock.at = EVENING.replace(hour=8)
        llm = ScriptedLLM(GOOD)
        asyncio.run(self.zone(llm=llm).maybe())
        self.assertEqual(llm.seen, [], "没到点连模型都不该叫")

    def test_two_posts_never_land_within_twenty_hours(self):
        zone = self.zone()
        asyncio.run(zone.maybe())
        # 第二天同一时间，但只隔了二十小时以内：把她上一条往前挪。
        self.store.execute(
            "UPDATE qzone_posts SET posted_at = ?",
            ((EVENING - timedelta(hours=3)).isoformat(timespec="seconds"),),
        )
        self.flags.set_flag("qzone:day", "")
        result = asyncio.run(zone.maybe())
        self.assertEqual(result["why"], "离上一条太近")

    def test_the_coin_is_flipped_once_a_day(self):
        zone = Qzone(
            Bot(),
            ScriptedLLM(GOOD),
            self.store,
            persona=self.persona,
            flags=self.flags,
            hour=21,
            chance=0.35,
            clock=self.clock,
            rng=lambda: 0.99,
            log=self.logs.append,
        )
        first = asyncio.run(zone.maybe())
        self.assertEqual(first["why"], "今天抽到的签是不写")
        self.assertEqual(self.flags.flag("qzone:roll:2026-10-27"), "0")

        # 换一个「一定中」的骰子，同一个 tick：已经记下来了，不该重摇。
        zone._rng = lambda: 0.0
        second = asyncio.run(zone.maybe())
        self.assertEqual(second["why"], "今天抽到的签是不写")
        self.assertEqual(self.store.query("SELECT id FROM qzone_posts"), [])

    def test_a_manual_post_goes_out_without_the_calendar(self):
        self.clock.at = EVENING.replace(hour=3)
        result = asyncio.run(self.zone().post("睡不着。就这样，反正明天也不用早起。"))
        self.assertTrue(result["posted"])
        self.assertEqual(self.flags.flag("qzone:day"), "2026-10-27", "手发一条也算今天用掉了")


class TestFailing(Base):
    """发不出去：网络、账号、接口不存在，各有各的说法和后果。"""

    def test_a_refused_post_is_recorded_not_raised(self):
        bot = Bot(fail=RuntimeError("send_qzone_msg: retcode 100 权限不足"))
        zone = self.zone(bot=bot)
        result = asyncio.run(zone.maybe())  # 不该抛出去
        self.assertFalse(result["posted"])
        self.assertEqual(result["why"], "rejected")
        rows = self.store.query("SELECT ok, error FROM qzone_posts")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ok"])
        self.assertIn("retcode 100", rows[0]["error"])

    def test_a_failure_is_waited_on_not_retried_at_once(self):
        bot = Bot(fail=RuntimeError("send_qzone_msg: retcode 100"))
        zone = self.zone(bot=bot, retry=1.0)
        asyncio.run(zone.maybe())
        self.assertEqual(len(bot.calls), 1)

        self.clock.later(0.5)
        blocked = asyncio.run(zone.maybe())
        self.assertEqual(blocked["why"], "上次没成，还在等")
        self.assertEqual(len(bot.calls), 1, "等待期内不重试，免得把号打进风控")

        self.clock.later(0.7)
        asyncio.run(zone.maybe())
        self.assertEqual(len(bot.calls), 2, "等够了就再试一次")

    def test_a_lost_login_is_named_out_loud(self):
        bot = Bot(fail=RuntimeError("send_qzone_msg: retcode 100 请先登录"))
        result = asyncio.run(self.zone(bot=bot).maybe())
        self.assertEqual(result["why"], "login")
        self.assertTrue(any("登录态" in line for line in self.logs), self.logs)

    def test_a_napcat_without_qzone_is_remembered_for_a_day(self):
        bot = Bot(fail=RuntimeError("未知的 API 不支持"))
        zone = self.zone(bot=bot)
        result = asyncio.run(zone.maybe())
        self.assertEqual(result["why"], "unsupported")
        self.assertEqual(self.flags.flag("qzone:unsupported")[:10], "2026-10-27")
        self.assertTrue(any("send_qzone_msg" in line for line in self.logs))

        self.clock.later(1)
        self.assertEqual(len(bot.calls), 1, "不支持就不该每分钟再问一遍")

        self.clock.later(24)
        asyncio.run(zone.maybe())
        self.assertEqual(len(bot.calls), 2, "一天后再试：升级 NapCat 后要能自己恢复")

    def test_a_socket_that_is_down_is_network_not_a_crash(self):
        class Down(Exception):
            pass

        down = Down("not connected")
        result = asyncio.run(self.zone(bot=Bot(fail=down)).maybe())
        self.assertEqual(result["why"], "network")

    def test_an_answer_without_a_tid_spends_the_day(self):
        """接口说成了但没给 tid：可能已经发出去了，宁可不发第二条。"""
        bot = Bot(answer={})
        zone = self.zone(bot=bot)
        result = asyncio.run(zone.maybe())
        self.assertEqual(result["why"], "unclear")
        self.assertEqual(self.flags.flag("qzone:day"), "2026-10-27")

        again = asyncio.run(zone.maybe())
        self.assertEqual(again["why"], "今天已经发过")
        self.assertEqual(len(bot.calls), 1)
        self.assertTrue(any("没拿到 tid" in line for line in self.logs))


class TestWhatMayNotBePublished(Base):
    """内容这一侧：泄漏、破功、太长、重复，都是「不发」，不是「发了再补救」。"""

    def refused(self, text: str) -> tuple[dict, Bot]:
        bot = Bot()
        zone = self.zone(bot=bot, llm=ScriptedLLM(text))
        result = asyncio.run(zone.maybe())
        return result, bot

    def test_a_number_in_it_never_leaves(self):
        result, bot = self.refused("今天被 12345678901 吵醒了。真是的。")
        self.assertFalse(result["posted"])
        self.assertIn("写不出来", result["why"])
        self.assertEqual(bot.calls, [], "带号码的句子一个字都到不了空间")
        self.assertEqual(self.store.query("SELECT id FROM qzone_posts"), [])

    def test_breaking_character_never_leaves(self):
        result, bot = self.refused("作为 AI，我今天学习了很多关于偶像的知识。")
        self.assertFalse(result["posted"])
        self.assertEqual(bot.calls, [])

    def test_a_link_never_leaves(self):
        result, bot = self.refused("今天的练习曲在这里 https://example.com/x 听着还行。")
        self.assertFalse(result["posted"])
        self.assertEqual(bot.calls, [])

    def test_an_at_never_leaves(self):
        result, bot = self.refused("刚才 @制作人 说的那句话，我记到现在。")
        self.assertFalse(result["posted"])
        self.assertEqual(bot.calls, [])

    def test_too_long_is_refused(self):
        result, bot = self.refused("今天。这句还是没唱顺。" * 40)
        self.assertFalse(result["posted"])
        self.assertIn("太长", result["why"])
        self.assertEqual(bot.calls, [])

    def test_a_second_attempt_happens_only_once(self):
        """写坏了给一次机会，不给第三次：同一条坏句子不会越写越对。"""
        llm = ScriptedLLM("作为 AI 我今天不该说这个，但是算了。")
        asyncio.run(self.zone(llm=llm).maybe())
        self.assertEqual(len(llm.seen), 2)

    def test_the_same_post_twice_is_rewritten(self):
        self.store.execute(
            "INSERT INTO qzone_posts (tid, content, ok, written_at, posted_at)"
            " VALUES ('t0', ?, 1, ?, ?)",
            (
                GOOD,
                (EVENING - timedelta(days=2)).isoformat(timespec="seconds"),
                (EVENING - timedelta(days=2)).isoformat(timespec="seconds"),
            ),
        )
        other = "走廊的灯关掉以后其实挺安静的。今天有点不想马上回家。"
        bot = Bot()
        zone = self.zone(bot=bot, llm=Rounds(GOOD, other))
        result = asyncio.run(zone.maybe())
        self.assertTrue(result["posted"])
        self.assertEqual(bot.calls[0][1]["content"], other)

    def test_a_repeat_that_never_stops_gives_up(self):
        self.store.execute(
            "INSERT INTO qzone_posts (tid, content, ok, written_at, posted_at)"
            " VALUES ('t0', ?, 1, ?, ?)",
            (
                GOOD,
                (EVENING - timedelta(days=2)).isoformat(timespec="seconds"),
                (EVENING - timedelta(days=2)).isoformat(timespec="seconds"),
            ),
        )
        bot = Bot()
        zone = self.zone(bot=bot, llm=ScriptedLLM(GOOD))
        result = asyncio.run(zone.maybe())
        self.assertFalse(result["posted"])
        self.assertIn("太像", result["why"])
        self.assertEqual(bot.calls, [])

    def test_no_writing_file_means_no_post(self):
        zone = Qzone(
            Bot(),
            ScriptedLLM(GOOD),
            self.store,
            persona=self.root / "missing.md",
            flags=self.flags,
            clock=self.clock,
            log=self.logs.append,
        )
        result = asyncio.run(zone.maybe())
        self.assertFalse(result["posted"])
        self.assertIn("写作口径", result["why"])


class TestTheSmallerPieces(Base):
    """模型调用、撤回、状态页、以及那个 HTTP 入口。"""

    def test_the_prompt_carries_the_voice_and_the_last_posts(self):
        self.store.execute(
            "INSERT INTO qzone_posts (tid, content, ok, written_at, posted_at)"
            " VALUES ('t0', ?, 1, ?, ?)",
            (
                "昨天写了什么不重要，重要的是它出现在提示词里。",
                (EVENING - timedelta(days=2)).isoformat(timespec="seconds"),
                (EVENING - timedelta(days=2)).isoformat(timespec="seconds"),
            ),
        )
        llm = ScriptedLLM(GOOD)
        asyncio.run(self.zone(llm=llm).maybe())
        prompt = llm.seen[0][0].content
        self.assertIn("句子短", prompt, "人设进来了")
        self.assertIn("不要和这些重复", prompt, "上一次发过什么也进来了")
        self.assertIn("周二", prompt)

    def test_deleting_one_takes_the_tid(self):
        bot = Bot()
        asyncio.run(self.zone(bot=bot).delete("tid-9"))
        self.assertEqual(bot.calls, [("delete_qzone_msg", {"tid": "tid-9"})])

    def test_a_delete_that_fails_says_why(self):
        bot = Bot(fail=RuntimeError("retcode 100 没有这条"))
        with self.assertRaises(PublishError) as caught:
            asyncio.run(self.zone(bot=bot).delete("tid-9"))
        self.assertEqual(caught.exception.kind, "rejected")

    def test_the_status_page_has_what_a_person_wants_to_know(self):
        zone = self.zone()
        asyncio.run(zone.maybe())
        status = zone.status()
        self.assertEqual(status["hour"], 21)
        self.assertEqual(status["right"], 4)
        self.assertTrue(status["today"])
        self.assertEqual(len(status["recent"]), 1)
        self.assertTrue(status["recent"][0]["ok"])

    def test_the_endpoint_writes_without_publishing_when_asked(self):
        bot = Bot()
        zone = self.zone(bot=bot)
        channel = SimpleNamespace(qzone=zone, _log=self.logs.append, config=SimpleNamespace(qzone=True))
        answer = asyncio.run(QQService.speak_out(channel, {"publish": False}))
        self.assertFalse(answer["posted"])
        self.assertEqual(answer["text"], GOOD)
        self.assertEqual(bot.calls, [], "只看不发")

    def test_the_endpoint_sends_what_it_is_given(self):
        bot = Bot()
        channel = SimpleNamespace(
            qzone=self.zone(bot=bot), _log=self.logs.append, config=SimpleNamespace(qzone=True)
        )
        answer = asyncio.run(QQService.speak_out(channel, {"text": "今天很累。就这样吧，明天还要早起。"}))
        self.assertTrue(answer["posted"])
        self.assertEqual(bot.calls[0][1]["content"], "今天很累。就这样吧，明天还要早起。")

    def test_the_endpoint_is_closed_when_the_feature_is(self):
        """开关关着的时候，「功能关着」不能只是「定时器关着」。"""
        bot = Bot()
        zone = self.zone(bot=bot)
        channel = SimpleNamespace(
            qzone=zone, _log=self.logs.append, config=SimpleNamespace(qzone=False)
        )
        answer = asyncio.run(QQService.speak_out(channel, {"text": "今天很累。明天还要早起。"}))
        self.assertFalse(answer["posted"])
        self.assertIn("没开", answer["why"])
        self.assertEqual(bot.calls, [])

    def test_the_endpoint_refuses_what_the_calendar_would_refuse(self):
        bot = Bot()
        channel = SimpleNamespace(
            qzone=self.zone(bot=bot), _log=self.logs.append, config=SimpleNamespace(qzone=True)
        )
        answer = asyncio.run(QQService.speak_out(channel, {"text": "打电话 13800000000 给我"}))
        self.assertFalse(answer["posted"])
        self.assertIn("不通过", answer["why"])
        self.assertEqual(bot.calls, [])


class TestTheShapes(unittest.TestCase):
    """单元级的几个纯函数：分档、相似度、清洗。"""

    def test_a_failure_is_classified_by_what_napcat_said(self):
        self.assertEqual(space._kind(RuntimeError("未知的 API")), "unsupported")
        self.assertEqual(space._kind(RuntimeError("请先登录")), "login")
        self.assertEqual(space._kind(RuntimeError("操作太频繁")), "limited")
        self.assertEqual(space._kind(RuntimeError("retcode 100")), "rejected")

    def test_cleaning_strips_scaffolding_not_content(self):
        self.assertEqual(space._clean("```\n说说：「今天很累。」\n```"), "今天很累。")
        self.assertEqual(space._clean("**今天**很累。"), "今天很累。")

    def test_similarity_notices_the_same_sentence(self):
        self.assertGreaterEqual(space._similar("今天那句没唱顺。", "今天那句还是没唱顺。"), 0.6)
        self.assertLess(space._similar("今天那句没唱顺。", "走廊的灯关掉了。"), 0.2)

    def test_the_right_defaults_to_friends_when_the_number_is_wrong(self):
        zone = Qzone(Bot(), ScriptedLLM(GOOD), Store(":memory:"), ugc_right=7)
        self.assertEqual(zone.right, 4)

    def test_content_error_carries_the_reason(self):
        with self.assertRaises(ContentError) as caught:
            raise ContentError("太长")
        self.assertEqual(caught.exception.why, "太长")


if __name__ == "__main__":
    unittest.main()
