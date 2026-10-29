"""她自己的墙：有人评论就回一句，没人评论就不管。

测的是「什么时候不该回」——回自己、回两次、回低俗、把历史一次性回完，都是上线之后
才会发现、删起来又很难看的错误。全部离线：接口是假的，模型是脚本，时间被交进来。
"""

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from channels import qzone as wall
from channels import qzone_reply as reply
from channels.qzone_reply import Wall
from channels.qzone_web import WebError
from core.llm import ScriptedLLM
from core.store import Store

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)

GOOD = "明天再去一次，别急。"
PERSONA = "# 空间里的她\n\n句子短。不客套。\n"
POST = "7a3636c664bbe06ae5550a00"


class Clock:
    def __init__(self, at: datetime = NOW) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def later(self, hours: float = 0.0, minutes: float = 0.0) -> datetime:
        self.at = self.at + timedelta(hours=hours, minutes=minutes)
        return self.at


def cmt(tid: str, uin: str = "10002", text: str = "加油哦，明天再去。",
        name: str = "あさひ", at: datetime | None = None,
        root: str = "", root_uin: str = "", reply_to: str = "") -> dict:
    return {
        "tid": tid,
        "uin": uin,
        "name": name,
        "content": text,
        "at": int((at or NOW).timestamp()),
        "root": root,
        "root_uin": root_uin,
        "reply_to": reply_to,
    }


class FakeWeb:
    """NapCat 那一侧的替身：说说、评论、回复，都只记下来。"""

    def __init__(self, posts=None, comments=None, fail=None) -> None:
        self.self_id = "999"
        self.posts = list(posts if posts is not None else [{"tid": POST, "content": "那句还是差一点。",
                                                            "cmtnum": 0, "at": int(NOW.timestamp())}])
        self.by_post = comments or {}
        self.fail = fail
        self.replied: list[tuple[str, str, str, str, str]] = []
        self.extra: list[dict] = []
        self.commented: list[tuple[str, str]] = []
        self.looked: list[str] = []

    async def my_posts(self, count: int = 3):
        if self.fail is not None:
            raise self.fail
        return self.posts

    async def comments(self, tid: str, count: int = 30):
        if self.fail is not None:
            raise self.fail
        self.looked.append(tid)
        return self.by_post.get(tid, [])

    async def reply(self, tid, uin, comment_tid, comment_uin, text, **extra):
        if self.fail is not None:
            raise self.fail
        self.replied.append((tid, uin, comment_tid, comment_uin, text))
        self.extra.append(extra)
        return "cid"

    async def comment(self, tid, uin, text):
        self.commented.append((tid, text))
        return "cid"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "db.sqlite")
        self.addCleanup(self.store.close)
        self.store.migrate(reply.SCHEMA)
        self.persona = self.root / "fuyuko-qzone.md"
        self.persona.write_text(PERSONA, encoding="utf-8")
        self.clock = Clock()
        self.flags = wall.Memory()
        self.logs: list[str] = []
        self.llm = ScriptedLLM(GOOD)

    def zone(self, *, web=None, hourly=reply.HOURLY, gap=reply.GAP, age=72.0, deny=(), llm=None, on=True,
             bots=None, owners=()) -> Wall:
        return Wall(
            None,
            llm or self.llm,
            self.store,
            web=web or FakeWeb(),
            persona=self.persona,
            flags=self.flags,
            bots=bots,
            self_id="999",
            deny=deny,
            owners=owners,
            hourly=hourly,
            gap=gap,
            age=age,
            clock=self.clock,
            log=self.logs.append,
            on=on,
        )

    def first(self, zone: Wall) -> dict:
        """装好那一刻：把已有的评论记下来，一条都不回。"""
        return asyncio.run(zone.poll())


class TestAnswering(Base):
    def test_a_new_comment_gets_one_answer(self):
        web = FakeWeb()
        zone = self.zone(web=web)
        self.first(zone)

        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        result = asyncio.run(zone.poll())

        self.assertEqual(result["replied"], 1)
        self.assertEqual(web.replied[0][0], POST)
        self.assertEqual(web.replied[0][2], "2", "回的是这条评论")
        self.assertEqual(web.replied[0][3], "10002", "回给这个人")
        self.assertEqual(web.replied[0][4], GOOD)

    def test_the_first_look_only_records(self):
        web = FakeWeb()
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2"), cmt("3")]
        zone = self.zone(web=web)
        result = self.first(zone)
        self.assertEqual(result["replied"], 0)
        self.assertEqual(web.replied, [])
        self.assertEqual(result["new"], 2)

    def test_nobody_commented_means_no_request_at_all(self):
        web = FakeWeb()
        zone = self.zone(web=web)
        self.first(zone)
        web.posts[0]["cmtnum"] = 0
        asyncio.run(zone.poll())
        self.assertEqual(web.looked, [], "没人回过的说说，不该再问一次")

    def test_the_same_post_is_not_fetched_again(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        asyncio.run(zone.poll())
        asyncio.run(zone.poll())
        self.assertEqual(web.looked.count(POST), 1, "评论数没变就不用再看一遍")

    def test_a_new_comment_on_the_same_post_is_fetched(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        asyncio.run(zone.poll())

        web.posts[0]["cmtnum"] = 2
        web.by_post[POST].append(cmt("3", uin="10003", name="まの", text="嗓子还好吗？"))
        self.clock.later(minutes=40)
        result = asyncio.run(zone.poll())
        self.assertEqual(result["replied"], 1)
        self.assertEqual(web.replied[-1][2], "3")

    def test_her_own_comment_is_never_answered(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2", uin="999", name="ふゆ", text="嗯。")]
        result = asyncio.run(zone.poll())
        self.assertEqual(result["replied"], 0)
        self.assertEqual(web.replied, [])

    def test_an_ignored_person_is_left_alone(self):
        web = FakeWeb()
        zone = self.zone(web=web, deny=("10002",), gap=0.0)
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        self.assertEqual(asyncio.run(zone.poll())["replied"], 0)

    def test_an_old_comment_is_not_answered(self):
        old = NOW - timedelta(hours=80)
        web = FakeWeb()
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2", at=old)]
        zone = self.zone(web=web, age=72.0, gap=0.0)
        asyncio.run(zone.poll())  # 第一次仍会记（age 在 _comment 里判，直接跳过）
        self.assertEqual(self.store.query("SELECT id FROM qzone_comments"), [])

    def test_the_manual_door_answers_one_known_comment(self):
        web = FakeWeb()
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        zone = self.zone(web=web)
        self.first(zone)  # 这条算历史，不会自动回
        result = asyncio.run(zone.answer_tid("2", "下次一起去。"))
        self.assertTrue(result["replied"])
        self.assertEqual(web.replied[0][4], "下次一起去。")


class TestNotAnswering(Base):
    def test_a_coarse_comment_is_not_shown_to_the_model(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2", text="约炮吗美女")]
        result = asyncio.run(zone.poll())
        self.assertEqual(result["replied"], 0)
        self.assertEqual(self.llm.seen, [], "这种话不该进模型")
        rows = self.store.query("SELECT state, error FROM qzone_comments WHERE comment_tid = '2'")
        self.assertEqual(rows[0]["state"], reply.SKIPPED)
        self.assertIn("低俗", rows[0]["error"])

    def test_the_model_can_decline(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0, llm=ScriptedLLM("SKIP"))
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2", text="在下输了，甘拜下风。")]
        result = asyncio.run(zone.poll())
        self.assertEqual(result["replied"], 0)
        self.assertEqual(web.replied, [])
        rows = self.store.query("SELECT state FROM qzone_comments WHERE comment_tid = '2'")
        self.assertEqual(rows[0]["state"], reply.SKIPPED)

    def test_the_hourly_cap_holds(self):
        web = FakeWeb()
        zone = self.zone(web=web, hourly=1, gap=0.0)
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        self.assertEqual(asyncio.run(zone.poll())["replied"], 1)

        web.posts[0]["cmtnum"] = 2
        web.by_post[POST].append(cmt("3", uin="10003"))
        self.assertEqual(asyncio.run(zone.poll())["replied"], 0, "一天一条，不是来一条回一条")

    def test_two_answers_never_land_inside_the_gap(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=30.0)
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        asyncio.run(zone.poll())

        web.posts[0]["cmtnum"] = 2
        web.by_post[POST].append(cmt("3", uin="10003"))
        self.clock.later(minutes=10)
        self.assertEqual(asyncio.run(zone.poll())["replied"], 0)
        self.clock.later(minutes=25)
        self.assertEqual(asyncio.run(zone.poll())["replied"], 1, "隔够了就回")


class TestFailing(Base):
    def test_a_login_failure_pauses_for_two_hours(self):
        zone = self.zone(web=FakeWeb(fail=WebError("login", "Cookie 失效")))
        result = asyncio.run(zone.poll())
        self.assertEqual(result["why"], "login")
        self.assertEqual(self.flags.flag("qzone:reply:pause:ttl"), "2")

    def test_no_persona_means_no_poll(self):
        zone = Wall(
            None, self.llm, self.store, web=FakeWeb(),
            persona=self.root / "missing.md", flags=self.flags, clock=self.clock,
            log=self.logs.append,
        )
        result = asyncio.run(zone.poll())
        self.assertFalse(result["ok"])
        self.assertIn("写作口径", result["why"])

    def test_the_switch_being_off_is_a_refusal(self):
        result = asyncio.run(self.zone(on=False).poll())
        self.assertFalse(result["ok"])
        self.assertIn("没开", result["why"])

    def test_a_refused_reply_does_not_fall_back_to_a_plain_comment(self):
        """回不到那个人楼下，就不回 —— 挂在说说下面的那句是更糟的答案，而且删不掉。"""

        class Picky(FakeWeb):
            async def reply(self, tid, uin, comment_tid, comment_uin, text, **extra):
                raise WebError("rejected", "code=-10004 参数错误")

        web = Picky()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        result = asyncio.run(zone.poll())
        self.assertEqual(result["replied"], 0)
        self.assertEqual(web.commented, [], "不再退化成一句新评论")


class TestTheSmallerPieces(Base):
    def test_the_prompt_carries_the_comment_and_the_post(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)  # 先同步：没有评论，只记一次
        web.posts[0]["content"] = "那句还是差一点。"
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2", text="嗓子还好吗？", name="まの")]
        asyncio.run(zone.poll())
        prompt = self.llm.seen[-1][0].content
        self.assertIn("句子短", prompt, "人设进来了")
        self.assertIn("嗓子还好吗", prompt, "评论进来了")
        self.assertIn("那句还是差一点", prompt, "她那条说说也进来了")
        self.assertIn("SKIP", prompt, "退出的方式要写清楚")

    def test_the_status_page_has_what_a_person_wants_to_know(self):
        web = FakeWeb()
        zone = self.zone(web=web)
        self.first(zone)  # 先同步：已有评论算历史，不回
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("2")]
        asyncio.run(zone.poll())
        status = zone.status()
        self.assertTrue(status["on"])
        self.assertEqual(status["transport"], "web")
        self.assertEqual(status["seen"], 1)
        self.assertEqual(status["answered"], 1)
        self.assertEqual(status["hourly"], reply.HOURLY)
        self.assertEqual(len(status["recent"]), 1)

    def test_a_comment_is_shorter_than_a_post(self):
        ok, why = reply.check("嗯")
        self.assertFalse(ok)
        self.assertIn("太短", why)
        ok, why = reply.check("这句话真长。" * 20)
        self.assertFalse(ok)
        self.assertIn("太长", why)


class TestShapes(unittest.TestCase):
    def test_a_refusal_reads_the_same_way_as_a_post(self):
        self.assertTrue(reply.check("明天再去一次。")[0])
        self.assertIn("链接", reply.check("看这个 https://x.cn/a 吧")[1])
        self.assertIn("@", reply.check("问 @制作人 吧")[1])

    def test_coarse_words_are_recognised(self):
        self.assertTrue(reply.coarse("约炮吗"))
        self.assertFalse(reply.coarse("嗓子还好吗？"))


if __name__ == "__main__":
    unittest.main()


class TestTheThread(Base):
    """楼中楼：回复要落在那个人的评论下面，别人的回复也要看得见。"""

    def test_a_reply_makes_its_way_back_in(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)  # 先同步：此时没有评论

        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("3", text="嗓子还好吗？")]
        asyncio.run(zone.poll())
        self.assertEqual(web.replied[0][2], "3", "回的是这条评论")

        # 她又回了一句，楼中楼 —— 不改变 cmtnum
        web.by_post[POST].append(
            cmt("1", uin="999", text="还撑得住呢。", name="ふゆ", root="3", root_uin="10002")
        )
        web.by_post[POST].append(
            cmt("2", uin="10002", text="那我明天再问一次。", name="あさひ",
                root="3", root_uin="10002", reply_to="999")
        )
        self.clock.later(minutes=reply.RECHECK + 1)  # 评论数没变，等复查窗口过去
        result = asyncio.run(zone.poll())

        self.assertEqual(result["replied"], 1, "评论区没变，但楼里有新的一句")
        tid, uin, comment_tid, comment_uin, _text = web.replied[-1]
        self.assertEqual(uin, "999")
        self.assertEqual(comment_uin, "10002")
        self.assertEqual(web.extra[-1]["root_tid"], "3", "锚点是一级评论，不是那条回复")
        self.assertEqual(web.extra[-1]["root_uin"], "10002")

    def test_her_own_reply_in_the_thread_is_not_answered(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [
            cmt("3", text="嗓子还好吗？"),
            cmt("1", uin="999", text="还撑得住呢。", name="ふゆ", root="3", root_uin="10002"),
        ]
        asyncio.run(zone.poll())
        self.assertEqual(len(web.replied), 1, "她自己的楼中楼不触发第二次回复")

    def test_a_thread_between_other_people_is_not_hers_to_join(self):
        """楼里两个人自己聊，她不插嘴 —— 那些话不是对她说的。"""
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [
            cmt("3", uin="10003", text="今天也练习了。"),
            cmt("1", uin="10002", text="加油。", root="3", root_uin="10003", reply_to="10003"),
        ]
        asyncio.run(zone.poll())
        rows = [
            str(row["comment_tid"])
            for row in self.store.query("SELECT comment_tid FROM qzone_comments ORDER BY id")
        ]
        self.assertEqual(rows, ["3"], "只记对她说的一级评论，楼里那半句不记")
        self.assertEqual([one[2] for one in web.replied], ["3"], "回的是找她说话的那句")

    def test_the_same_number_in_two_levels_does_not_collide(self):
        """一级评论和回复各自编号，`1` 两边都有 —— 数据库里不能当成同一条。"""
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)
        web.posts[0]["cmtnum"] = 2
        web.by_post[POST] = [
            cmt("1", text="第一条一级评论。"),
            cmt("1", text="楼里的回复。", root="1", root_uin="10002", reply_to="999"),
        ]
        result = asyncio.run(zone.poll())
        self.assertEqual(result["new"], 2)


class TestTheCoarseWords(Base):
    def test_the_sexual_trollings_do_not_reach_the_model(self):
        for text in ("你真是欠操了", "操你", "约炮吗"):
            self.assertTrue(reply.coarse(text), text)

    def test_ordinary_talk_still_reaches_her(self):
        for text in ("去给我炒俩菜", "asahi唱的比你好听", "嗓子还好吗"):
            self.assertFalse(reply.coarse(text), text)


class TestBotsInTheComments(Base):
    """别的角色的机器人给她留言时，她得知道对面是谁。"""

    def test_a_bot_is_named_in_the_prompt(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0, bots={"10002": "斑鳩ルカ"})
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("3", text="ふゆ、今日はどうだった。")]
        asyncio.run(zone.poll())
        prompt = " ".join(str(message.content) for batch in self.llm.seen for message in batch)
        self.assertIn("斑鳩ルカ", prompt)
        self.assertIn("不是制作人", prompt)

    def test_an_ordinary_person_gets_no_such_line(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0, bots={"10002": "斑鳩ルカ"})
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("3", uin="10009", text="嗓子还好吗？")]
        asyncio.run(zone.poll())
        prompt = " ".join(str(message.content) for batch in self.llm.seen for message in batch)
        self.assertNotIn("不是制作人", prompt)


class TestWhoGetsAnAnswer(Base):
    """找茬要回，只有辱骂和低俗才不回 —— 这条线写错一次，她就会变成只会沉默的客服。"""

    def prompt_for(self, text: str) -> str:
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0)
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("3", text=text)]
        asyncio.run(zone.poll())
        return " ".join(str(message.content) for batch in self.llm.seen for message in batch)

    def test_a_troll_is_something_she_answers(self):
        prompt = self.prompt_for("asahi唱的比你好听")
        self.assertIn("这些要回", prompt)
        self.assertNotIn("纯属找茬", prompt, "旧的「找茬就不回」不能还留着")

    def test_insults_are_named_as_the_reason_to_stay_quiet(self):
        prompt = self.prompt_for("你唱得真难听")
        self.assertIn("骂人、人身攻击", prompt)


class TestTheProducersTurn(Base):
    """制作人的留言不排队：额度是防刷，不是让他的话等到明天。"""

    def test_his_comment_skips_the_daily_cap(self):
        web = FakeWeb()
        zone = self.zone(web=web, hourly=1, gap=0.0, owners=("42",))
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("3", uin="10002", text="嗓子还好吗？")]
        asyncio.run(zone.poll())          # 用掉今天唯一的一条
        web.posts[0]["cmtnum"] = 2
        web.by_post[POST].append(cmt("4", uin="42", name="制作人", text="早点睡。"))
        result = asyncio.run(zone.poll())
        self.assertEqual(result["replied"], 1, "额度满了，他的话仍然要回")

    def test_anybody_else_still_waits(self):
        web = FakeWeb()
        zone = self.zone(web=web, hourly=1, gap=0.0, owners=("42",))
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("3", uin="10002", text="嗓子还好吗？")]
        asyncio.run(zone.poll())
        web.posts[0]["cmtnum"] = 2
        web.by_post[POST].append(cmt("4", uin="10003", name="路人", text="我也来一句。"))
        result = asyncio.run(zone.poll())
        self.assertEqual(result["replied"], 0, "额度用完了，别人明天再来")


class TestWhoseTurnItIs(Base):
    """一次只回一条的时候，制作人排在最前面。"""

    def test_his_line_goes_before_older_trolls(self):
        web = FakeWeb()
        zone = self.zone(web=web, gap=0.0, hourly=1, owners=("42",))
        self.first(zone)
        web.posts[0]["cmtnum"] = 3
        web.by_post[POST] = [
            cmt("5", uin="10003", name="路人", text="唱得一般。", at=NOW - timedelta(minutes=6)),
            cmt("6", uin="10004", name="路人乙", text="换人吧。", at=NOW - timedelta(minutes=4)),
            cmt("7", uin="42", name="制作人", text="早点睡。", at=NOW),
        ]
        asyncio.run(zone.poll())
        self.assertEqual(web.replied[0][3], "42", "先回他的")


class TestTheHourlyWindow(Base):
    """按小时算的是**滚动窗口**：不是整点重置，而是「过去六十分钟」。"""

    def test_capacity_comes_back_after_an_hour(self):
        web = FakeWeb()
        zone = self.zone(web=web, hourly=1, gap=0.0)
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("3", text="嗓子还好吗？")]
        self.assertEqual(asyncio.run(zone.poll())["replied"], 1)

        self.clock.later(minutes=59)
        web.posts[0]["cmtnum"] = 2
        web.by_post[POST].append(cmt("4", text="第二句。"))
        self.assertEqual(asyncio.run(zone.poll())["replied"], 0, "一小时内不再回")

        self.clock.later(minutes=2)
        self.assertEqual(asyncio.run(zone.poll())["replied"], 1, "过了六十分钟就恢复")

    def test_the_window_does_not_reset_on_the_hour(self):
        """21:59 回满、22:00 又满额 —— 那是十分钟回一串，限额等于没有。"""
        web = FakeWeb()
        zone = self.zone(web=web, hourly=1, gap=0.0)
        self.first(zone)
        web.posts[0]["cmtnum"] = 1
        web.by_post[POST] = [cmt("3", text="嗓子还好吗？")]
        asyncio.run(zone.poll())

        self.clock.later(minutes=2)  # 跨过整点
        web.posts[0]["cmtnum"] = 2
        web.by_post[POST].append(cmt("4", text="第二句。"))
        self.assertEqual(asyncio.run(zone.poll())["replied"], 0)
