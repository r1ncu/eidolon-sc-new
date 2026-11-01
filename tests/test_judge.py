"""The judge bus: one request, several answers, and nothing guessed when it fails."""

import unittest

from core.host import Host
from core.judge import Judging, ScriptedJudge
from core.llm import ScriptedLLM
from core.plugin import Plugin, Question

AFFECT = Question("affect.delta", "这句话让她的好感变化多少？", kind="int", least=-3, most=3)
PHOTO = Question("photos.wants", "她这一轮想发照片吗？", kind="bool")


class Asking(Plugin):
    name = "asking"
    requires = ()

    def judge_questions(self):
        return (AFFECT, PHOTO)


class AskingMore(Plugin):
    name = "asking_more"
    requires = ("asking",)

    def judge_questions(self):
        return (Question("memory.needed", "他说的值得记住吗？", kind="bool"),)


class TestJudging(unittest.IsolatedAsyncioTestCase):
    async def test_one_request_answers_every_question(self):
        llm = ScriptedLLM('{"affect.delta": 2, "photos.wants": "yes"}', size=9)
        answers = await Judging(llm).ask([AFFECT, PHOTO], said="今天好累")
        self.assertEqual(answers, {"affect.delta": 2, "photos.wants": True})
        self.assertEqual(len(llm.seen), 1)

    async def test_an_out_of_range_number_is_clamped_not_dropped(self):
        llm = ScriptedLLM('{"affect.delta": 9}')
        answers = await Judging(llm).ask([AFFECT], said="太过分了")
        self.assertEqual(answers, {"affect.delta": 3})

    async def test_the_prompt_spells_out_every_key_and_range(self):
        llm = ScriptedLLM("{}")
        await Judging(llm).ask([AFFECT], said="在吗")
        instructions = llm.seen[0][0].content
        self.assertIn('"affect.delta"', instructions)
        self.assertIn("-3 到 3", instructions)

    async def test_an_int_question_may_name_its_own_neutral_value(self):
        """「拿不准 int 给 0」 对 `affinity.level` 的 0 是「明显伤害了她」——最不保守的那个。

        所以结尾那句改成「按问题里写明的默认值」：level 的问题里写着「拿不准时就是对她
        没有影响」（=2），那就该听问题自己的。
        """
        llm = ScriptedLLM("{}")
        await Judging(llm).ask([AFFECT], said="在吗")
        system = llm.seen[0][0].content
        self.assertIn("按问题里写明的默认值", system)
        self.assertNotIn("最保守的值回答", system)

    async def test_prose_instead_of_json_is_no_answers(self):
        llm = ScriptedLLM("我觉得她应该会有点高兴吧。")
        self.assertEqual(await Judging(llm).ask([AFFECT], said="在吗"), {})

    async def test_a_broken_json_body_is_no_answers(self):
        llm = ScriptedLLM('{"affect.delta": }')
        self.assertEqual(await Judging(llm).ask([AFFECT], said="在吗"), {})

    async def test_a_key_the_judge_invented_is_ignored(self):
        llm = ScriptedLLM('{"affect.delta": 1, "made.up": 7}')
        answers = await Judging(llm).ask([AFFECT], said="在吗")
        self.assertEqual(answers, {"affect.delta": 1})

    async def test_no_questions_means_no_request(self):
        llm = ScriptedLLM("{}")
        self.assertEqual(await Judging(llm).ask([], said="在吗"), {})
        self.assertEqual(llm.seen, [])

    async def test_a_model_that_drops_the_line_is_no_answers(self):
        llm = ScriptedLLM('{"affect.delta": 1}', size=4, fail_after=0)
        self.assertEqual(await Judging(llm).ask([AFFECT], said="在吗"), {})


class TestWhatTheJudgeIsTold(unittest.IsolatedAsyncioTestCase):
    """两个新字段：谁在说话（scene）、这句话前面是什么（brief）。

    判官读一句话，判的是「对她有什么影响」。同一句话在五十人的群里和一个私聊里不是
    同一件事；同一句「好感」，接在问题后面和在冷场里也不是同一件事。
    """

    async def test_a_room_member_is_not_called_the_producer(self):
        llm = ScriptedLLM("{}")
        await Judging(llm).ask([AFFECT], said="在吗", scene="group")
        system, user = llm.seen[0][0].content, llm.seen[0][1].content
        self.assertIn("群聊里一个成员", system)
        self.assertNotIn("她的制作人", system)
        self.assertEqual(user, "群友说：在吗")

    async def test_a_private_chat_still_says_producer(self):
        llm = ScriptedLLM("{}")
        await Judging(llm).ask([AFFECT], said="在吗", scene="dm")
        self.assertIn("她的制作人", llm.seen[0][0].content)
        self.assertEqual(llm.seen[0][1].content, "制作人说：在吗")

    async def test_the_page_wording_does_not_move(self):
        """网页那条路一直是他，一个字都不该变：它没有场景可切。"""
        llm = ScriptedLLM("{}")
        await Judging(llm).ask([AFFECT], said="在吗")
        self.assertEqual(
            llm.seen[0][0].content.splitlines()[0],
            "你在判断制作人刚说的一句话对角色有什么影响。",
        )
        self.assertEqual(llm.seen[0][1].content, "制作人说：在吗")

    async def test_the_background_is_marked_as_not_being_judged(self):
        llm = ScriptedLLM("{}")
        await Judging(llm).ask(
            [AFFECT], said="好感", brief="甲：你喜欢吃什么", scene="group"
        )
        user = llm.seen[0][1].content
        self.assertLess(user.index("甲：你喜欢吃什么"), user.index("好感"), "背景在前")
        self.assertIn("不要评判它们", user)
        self.assertIn("要评判的是最后这一句", user)


class TestTheHostAsksOnce(unittest.IsolatedAsyncioTestCase):
    async def test_three_plugins_three_questions_one_call(self):
        judge = ScriptedJudge({"affect.delta": 1})
        host = Host([Asking(), AskingMore()], judge=judge)
        self.assertEqual(len(host.questions()), 3)
        turn = _turn()
        await host.judge(turn)
        self.assertEqual(len(judge.asked), 1)
        self.assertEqual(turn.facts, {"affect.delta": 1})

    async def test_a_judge_that_fails_leaves_the_facts_empty(self):
        host = Host([Asking()], judge=ScriptedJudge(fail=True))
        turn = _turn()
        await host.judge(turn)
        self.assertEqual(turn.facts, {})

    async def test_a_plugin_whose_dependency_is_off_asks_nothing(self):
        host = Host([AskingMore()], judge=ScriptedJudge())
        self.assertEqual(host.questions(), [])

    async def test_no_judge_configured_is_not_an_error(self):
        host = Host([Asking()])
        turn = _turn()
        await host.judge(turn)
        self.assertEqual(turn.facts, {})

    async def test_the_background_and_the_room_travel_with_the_turn(self):
        """通道把这俩放进 facts，总线原样交给判官——插件不用知道它们的来路。"""
        judge = ScriptedJudge({"affect.delta": 1})
        host = Host([Asking()], judge=judge)
        turn = _turn()
        turn.surface = "group"
        turn.facts["judge.brief"] = "甲：在吗"
        await host.judge(turn)
        self.assertEqual(judge.seen[-1], {"said": "在吗", "brief": "甲：在吗", "scene": "group"})


def _turn():
    from datetime import datetime, timezone

    from core.messages import Conversation
    from core.plugin import TurnContext

    return TurnContext(
        Conversation("c1", "u1", "mano", "2026-10-22T00:00:00+00:00"), "在吗", datetime.now(timezone.utc)
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
