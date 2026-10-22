"""The first plugin: a sliding hour, charged on the way out, and switchable off."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.events import TurnDone, TurnFailed, TurnRejected
from core.host import Host
from core.llm import ScriptedLLM
from core.prompt import Section, Slot
from core.store import Store
from core.turn import Engine
from plugins.quota import Quota

REPLY = "<ja>おはよう</ja><zh>早上好</zh>"
START = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)


class Clock:
    """A clock that moves only when a test says so."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class TestQuota(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "kernel.db")
        self.addCleanup(self.store.close)
        self.clock = Clock()
        self.conversation = self.store.create_conversation(self.store.create_user("制作人"), "mano")
        self.base = (Section(Slot.PERSONA, "你是真乃。", title="sheet"),)

    def host(self, **kwargs) -> Host:
        quota = Quota(self.store, clock=self.clock, **kwargs)
        host = Host([quota])
        host.migrate(self.store)
        return host

    async def say(self, host: Host, text: str = "おはよう", llm=None) -> list:
        engine = Engine(self.store, llm or ScriptedLLM(REPLY), base=self.base, host=host)
        return [event async for event in engine.turn(self.conversation.id, text)]

    async def test_the_limit_is_a_gate_not_a_hint(self):
        host = self.host(turns_per_hour=2)
        first = ScriptedLLM(REPLY)
        second = ScriptedLLM(REPLY)
        third = ScriptedLLM(REPLY)
        await self.say(host, llm=first)
        await self.say(host, llm=second)
        events = await self.say(host, llm=third)
        self.assertIsInstance(events[-1], TurnRejected)
        self.assertEqual(events[-1].reason, "quota.turns")
        self.assertEqual(third.seen, [])  # the model was never asked
        self.assertEqual(len(self.store.messages(self.conversation.id)), 4)

    async def test_a_failed_turn_costs_the_user_nothing(self):
        host = self.host(turns_per_hour=1)
        broken = ScriptedLLM(REPLY, size=5, fail_after=1)
        events = await self.say(host, llm=broken)
        self.assertIsInstance(events[-1], TurnFailed)
        good = ScriptedLLM(REPLY)
        events = await self.say(host, llm=good)
        self.assertIsInstance(events[-1], TurnDone)  # allowed, and it went through
        self.assertNotEqual(good.seen, [])

    async def test_zero_and_none_both_mean_no_limit(self):
        for limit in (0, None):
            with self.subTest(limit=limit):
                host = self.host(turns_per_hour=limit)
                for _ in range(5):
                    events = await self.say(host)
                    self.assertIsInstance(events[-1], TurnDone)

    async def test_the_hour_slides_rather_than_turning_over(self):
        host = self.host(turns_per_hour=1)
        await self.say(host)
        self.clock.advance(60)
        events = await self.say(host)
        self.assertIsInstance(events[-1], TurnRejected)

        veto = await host.gate(_turn(self.conversation, self.clock.now))
        assert veto is not None
        self.assertIn(veto.retry_after, range(3538, 3542))  # an hour minus the minute gone

        self.clock.advance(3600)
        events = await self.say(host)
        self.assertIsInstance(events[-1], TurnDone)

    async def test_each_user_has_their_own_hour(self):
        other = self.store.create_conversation(self.store.create_user("另一个人"), "mano")
        host = self.host(turns_per_hour=1)
        await self.say(host)
        engine = Engine(self.store, ScriptedLLM(REPLY), base=self.base, host=host)
        events = [e async for e in engine.turn(other.id, "おはよう")]
        self.assertIsInstance(events[-1], TurnDone)

    async def test_capabilities_report_the_limit(self):
        self.assertEqual(self.host(turns_per_hour=30).capabilities()["quota"], {
            "enabled": True, "limits": {"turns_per_hour": 30}
        })
        self.assertEqual(self.host(turns_per_hour=0).capabilities()["quota"], {
            "enabled": True, "limits": {"turns_per_hour": None}
        })

    async def test_a_whole_day_of_rows_is_kept_and_no_longer(self):
        host = self.host(turns_per_hour=0)
        await self.say(host, llm=ScriptedLLM(REPLY, size=5, fail_after=0))  # nothing recorded
        self.store.execute(
            "INSERT INTO quota_uses (user_id, kind, at, exempt) VALUES (?, 'turn', ?, 0)",
            (self.conversation.user_id, (self.clock.now - timedelta(hours=25)).timestamp()),
        )
        await self.say(host)
        rows = self.store.query("SELECT COUNT(*) AS n FROM quota_uses")[0]["n"]
        self.assertEqual(rows, 1)  # the old one gone, this turn's one kept

    async def test_an_exempt_turn_is_neither_counted_nor_stopped(self):
        host = self.host(turns_per_hour=1, exempt=lambda turn: True)
        for _ in range(3):
            events = await self.say(host)
            self.assertIsInstance(events[-1], TurnDone)
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM quota_uses")[0]["n"], 0)

    async def test_turning_the_plugin_off_leaves_the_character_working(self):
        host = Host([Quota(self.store, clock=self.clock)], enabled=[])
        self.assertEqual(host.migrate(self.store), [])  # not even its table is created
        for _ in range(3):
            events = await self.say(host)
            self.assertIsInstance(events[-1], TurnDone)
        self.assertEqual(self.store.query("SELECT name FROM sqlite_master WHERE name = 'quota_uses'"), [])


def _turn(conversation, now):
    from core.plugin import TurnContext

    return TurnContext(conversation, "在吗", now)


if __name__ == "__main__":
    unittest.main(verbosity=2)
