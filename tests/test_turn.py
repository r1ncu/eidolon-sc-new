"""One turn end to end: words in, events out, and what is left in the database.

No network, no key, no channel — `ScriptedLLM` says what it was told to, in small chunks,
the way a real service streams.
"""

import tempfile
import unittest
from pathlib import Path

from core.events import PluginEvent, SegmentDelta, TurnDone, TurnFailed, TurnRejected, UserSaid
from core.host import Host
from core.judge import ScriptedJudge
from core.llm import ScriptedLLM
from core.plugin import Plugin, Question, Veto
from core.prompt import Section, Slot
from core.store import Store
from core.turn import Engine

REPLY = "<ja>おはよう</ja><zh>早上好</zh>"


class TestTurn(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "kernel.db")
        self.addCleanup(self.store.close)
        user = self.store.create_user("制作人")
        self.conversation = self.store.create_conversation(user, "mano")
        self.base = (Section(Slot.PERSONA, "你是真乃。", title="sheet"),)

    def engine(self, llm: ScriptedLLM, **kwargs: object) -> Engine:
        return Engine(self.store, llm, base=self.base, **kwargs)  # type: ignore[arg-type]

    async def say(self, engine: Engine, text: str, **kwargs: object) -> list:
        return [event async for event in engine.turn(self.conversation.id, text, **kwargs)]  # type: ignore[arg-type]

    async def test_a_turn_comes_out_in_order(self):
        events = await self.say(self.engine(ScriptedLLM(REPLY, size=5)), "おはよう")
        self.assertIsInstance(events[0], UserSaid)
        self.assertIsInstance(events[-1], TurnDone)
        shown = "".join(e.text for e in events if isinstance(e, SegmentDelta) and e.lang == "ja")
        self.assertEqual(shown, "おはよう")
        subtitles = "".join(e.text for e in events if isinstance(e, SegmentDelta) and e.lang == "zh")
        self.assertEqual(subtitles, "早上好")

    async def test_what_was_said_is_stored(self):
        events = await self.say(self.engine(ScriptedLLM(REPLY)), "おはよう")
        messages = self.store.messages(self.conversation.id)
        self.assertEqual([m.role for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0].text, "おはよう")
        self.assertEqual(messages[1].ja, "おはよう")
        self.assertEqual(messages[1].zh, "早上好")
        done = events[-1]
        assert isinstance(done, TurnDone)
        self.assertEqual(done.message_id, messages[1].id)

    async def test_the_next_turn_carries_the_last_one(self):
        first = ScriptedLLM(REPLY)
        await self.say(self.engine(first), "おはよう")
        second = ScriptedLLM(REPLY)
        await self.say(self.engine(second), "今日も早いね")
        prompt = second.seen[0]
        self.assertEqual([m.role for m in prompt], ["system", "user", "assistant", "user"])
        self.assertEqual(prompt[1].content, "おはよう")
        self.assertEqual(prompt[2].content, REPLY)
        self.assertEqual(prompt[3].content, "今日も早いね")
        self.assertEqual([m.cache_breakpoint for m in prompt], [True, False, True, False])

    async def test_a_plugin_may_add_a_section_for_this_turn_only(self):
        llm = ScriptedLLM(REPLY)
        await self.say(
            self.engine(llm), "在吗", sections=(Section(Slot.TURN, "现在是晚上十点。", title="clock"),)
        )
        prompt = llm.seen[0]
        self.assertEqual(prompt[-2].role, "system")
        self.assertEqual(prompt[-2].content, "现在是晚上十点。")
        self.assertFalse(prompt[-2].cache_breakpoint)

    async def test_an_aside_goes_before_the_words(self):
        llm = ScriptedLLM(REPLY)
        await self.say(self.engine(llm), "好看吗", aside="（制作人发来一张图片：一只鸟。）")
        self.assertEqual(llm.seen[0][-1].content, "（制作人发来一张图片：一只鸟。）好看吗")

    async def test_an_empty_turn_never_reaches_the_model(self):
        llm = ScriptedLLM(REPLY)
        events = await self.say(self.engine(llm), "   ")
        self.assertEqual([type(e) for e in events], [TurnRejected])
        self.assertEqual(events[0].reason, "empty")
        self.assertEqual(llm.seen, [])
        self.assertEqual(self.store.messages(self.conversation.id), [])

    async def test_a_turn_over_the_limit_never_reaches_the_model(self):
        llm = ScriptedLLM(REPLY)
        events = await self.say(self.engine(llm), "あ" * 2001)
        self.assertEqual(events[0].reason, "too_long")
        self.assertEqual(llm.seen, [])

    async def test_a_unknown_conversation_is_refused(self):
        llm = ScriptedLLM(REPLY)
        events = [e async for e in self.engine(llm).turn("nope", "在吗")]
        self.assertEqual(events[0].reason, "no_conversation")

    async def test_a_failed_turn_stores_nothing(self):
        events = await self.say(self.engine(ScriptedLLM(REPLY, size=5, fail_after=1)), "おはよう")
        self.assertIsInstance(events[-1], TurnFailed)
        self.assertEqual(events[-1].reason, "the line dropped")
        self.assertEqual(self.store.messages(self.conversation.id), [])

    async def test_a_reply_with_nothing_in_it_fails_rather_than_storing_nothing(self):
        events = await self.say(self.engine(ScriptedLLM("")), "おはよう")
        self.assertIsInstance(events[-1], TurnFailed)
        self.assertEqual(events[-1].reason, "empty_reply")
        self.assertEqual(self.store.messages(self.conversation.id), [])

    async def test_a_reply_that_ignores_the_format_is_still_a_reply(self):
        events = await self.say(self.engine(ScriptedLLM("こんにちは")), "おはよう")
        self.assertIsInstance(events[-1], TurnDone)
        reply = self.store.messages(self.conversation.id)[1]
        self.assertEqual(reply.ja, "こんにちは")


class Clock(Plugin):
    name = "clock"

    async def prompt_sections(self, turn):
        return [Section(Slot.TURN, "现在是晚上十点。", title="clock")]


class Quota(Plugin):
    name = "quota"

    def __init__(self) -> None:
        self.calls = 0

    async def before_turn(self, turn):
        self.calls += 1
        return Veto("quota.turns", retry_after=3600)


class Affinity(Plugin):
    name = "affinity"
    requires = ()

    def __init__(self) -> None:
        self.reply = None

    def judge_questions(self):
        return (Question("affect.delta", "好感变化多少？", kind="int", least=-3, most=3),)

    def capabilities(self):
        return {"panel": "affinity"}

    async def prompt_sections(self, turn):
        delta = turn.facts.get("affect.delta")
        if delta is None:
            return ()
        return [Section(Slot.TURN, f"这一轮好感变化 {delta}。", order=200, title="affinity")]

    async def after_turn(self, turn):
        self.reply = turn.reply
        turn.events.append(PluginEvent("affinity", "changed", {"delta": turn.facts.get("affect.delta")}))


class Broken(Plugin):
    name = "broken"

    async def after_turn(self, turn):
        raise RuntimeError("no")


class TestTurnWithPlugins(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "kernel.db")
        self.addCleanup(self.store.close)
        user = self.store.create_user("制作人")
        self.conversation = self.store.create_conversation(user, "mano")
        self.base = (Section(Slot.PERSONA, "你是真乃。", title="sheet"),)

    async def say(self, host, text: str = "おはよう", llm=None) -> list:
        engine = Engine(self.store, llm or ScriptedLLM(REPLY), base=self.base, host=host)
        return [event async for event in engine.turn(self.conversation.id, text)]

    async def test_a_veto_stops_the_turn_before_the_model_is_asked(self):
        llm = ScriptedLLM(REPLY)
        quota = Quota()
        events = await self.say(Host([quota]), llm=llm)
        self.assertIsInstance(events[0], UserSaid)
        self.assertIsInstance(events[1], TurnRejected)
        self.assertEqual(events[1].reason, "quota.turns")
        self.assertEqual(events[1].retry_after, 3600)  # the wait reaches the client
        self.assertEqual(quota.calls, 1)
        self.assertEqual(llm.seen, [])
        self.assertEqual(self.store.messages(self.conversation.id), [])

    async def test_a_plugin_adds_a_line_to_the_prompt(self):
        llm = ScriptedLLM(REPLY)
        await self.say(Host([Clock()]), llm=llm)
        self.assertEqual(llm.seen[0][-2].content, "现在是晚上十点。")

    async def test_a_judged_answer_reaches_the_plugin_that_asked(self):
        llm = ScriptedLLM(REPLY)
        host = Host([Affinity()], judge=ScriptedJudge({"affect.delta": 2}))
        await self.say(host, llm=llm)
        self.assertEqual(llm.seen[0][-2].content, "这一轮好感变化 2。")

    async def test_without_a_judge_the_plugin_simply_says_nothing(self):
        llm = ScriptedLLM(REPLY)
        await self.say(Host([Affinity()]), llm=llm)
        self.assertEqual([m.role for m in llm.seen[0]], ["system", "user"])

    async def test_an_observer_sees_the_stored_reply_and_tells_the_client(self):
        affinity = Affinity()
        events = await self.say(Host([affinity], judge=ScriptedJudge({"affect.delta": -1})))
        self.assertIsNotNone(affinity.reply)
        assert affinity.reply is not None
        self.assertEqual(affinity.reply.ja, "おはよう")
        told = [e for e in events if isinstance(e, PluginEvent)]
        self.assertEqual(told[0].namespace, "affinity")
        self.assertEqual(told[0].name, "changed")
        self.assertIsInstance(events[-1], TurnDone)  # the done event still comes last

    async def test_an_observer_that_raises_says_so_and_the_turn_still_finishes(self):
        events = await self.say(Host([Broken()]))
        self.assertEqual([e.name for e in events if isinstance(e, PluginEvent)], ["failed"])
        self.assertIsInstance(events[-1], TurnDone)
        self.assertEqual(len(self.store.messages(self.conversation.id)), 2)

    async def test_the_same_character_without_plugins_still_works(self):
        """The boundary test, as a test: the host is optional, and nothing else changes."""
        events = await self.say(None)  # type: ignore[arg-type]
        self.assertIsInstance(events[-1], TurnDone)


if __name__ == "__main__":
    unittest.main(verbosity=2)
