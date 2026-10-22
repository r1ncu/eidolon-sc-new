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


def _turn():
    from datetime import datetime, timezone

    from core.messages import Conversation
    from core.plugin import TurnContext

    return TurnContext(
        Conversation("c1", "u1", "mano", "2026-10-22T00:00:00+00:00"), "在吗", datetime.now(timezone.utc)
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
