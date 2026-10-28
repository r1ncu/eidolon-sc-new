"""The host: order, switching off, and staying up when a plugin does not."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.events import PluginEvent
from core.host import Host
from core.messages import Conversation
from core.plugin import Plugin, Veto
from core.prompt import PromptBuilder, Section, Slot
from core.store import Store


def ctx(text: str = "在吗"):
    from core.plugin import TurnContext

    return TurnContext(
        Conversation("c1", "u1", "mano", "2026-10-22T00:00:00+00:00"), text, datetime.now(timezone.utc)
    )


class Quiet(Plugin):
    name = "quiet"


class NeedsQuiet(Plugin):
    name = "needs"
    requires = ("quiet",)


class NeedsNothing(Plugin):
    name = "orphan"
    requires = ("nowhere",)


class Gate(Plugin):
    name = "gate"

    def __init__(self, reason: str | None = None, name: str = "gate") -> None:
        self.name = name
        self.reason = reason
        self.turns = 0

    async def before_turn(self, turn):
        self.turns += 1
        return Veto(self.reason, retry_after=60) if self.reason else None


class GateDown(Plugin):
    name = "gate_down"

    async def before_turn(self, turn):
        raise RuntimeError("the counter is on fire")


class Boom(Plugin):
    name = "boom"

    async def before_turn(self, turn):
        raise RuntimeError("no")

    async def prompt_sections(self, turn):
        raise RuntimeError("no")

    async def after_turn(self, turn):
        raise RuntimeError("no")


class Late(Plugin):
    name = "late"

    async def prompt_sections(self, turn):
        return [Section(Slot.TURN, "后", order=200)]


class Early(Plugin):
    name = "early"

    async def prompt_sections(self, turn):
        return [Section(Slot.TURN, "前", order=100)]


class Watcher(Plugin):
    name = "watcher"

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def after_turn(self, turn):
        self.seen.append(turn.text)


class Collector(Plugin):
    name = "collector"

    def migrations(self):
        return ["CREATE TABLE IF NOT EXISTS collector_rows (id TEXT PRIMARY KEY)"]

    def capabilities(self):
        return {"panel": "collector"}


class TestOrder(unittest.TestCase):
    def test_a_plugin_starts_after_the_one_it_requires(self):
        self.assertEqual(Host([NeedsQuiet(), Quiet()]).names, ["quiet", "needs"])

    def test_a_missing_dependency_switches_the_plugin_off_with_a_reason(self):
        host = Host([NeedsNothing()])
        self.assertEqual(host.names, [])
        self.assertEqual(host.capabilities()["orphan"], {"enabled": False, "why": "needs nowhere"})

    def test_a_plugin_that_needs_a_switched_off_one_is_switched_off_too(self):
        host = Host([Quiet(), NeedsQuiet()], enabled=["needs"])
        self.assertEqual(host.names, [])
        self.assertEqual(host.capabilities()["needs"]["why"], "needs quiet")

    def test_enabled_is_an_allow_list(self):
        host = Host([Quiet(), Late()], enabled=["quiet"])
        self.assertEqual(host.names, ["quiet"])
        self.assertEqual(host.capabilities()["late"], {"enabled": False, "why": "not enabled"})

    def test_a_circle_fails_at_startup_not_at_turn_forty(self):
        class A(Plugin):
            name = "a"
            requires = ("b",)

        class B(Plugin):
            name = "b"
            requires = ("a",)

        with self.assertRaises(ValueError):
            Host([A(), B()])

    def test_two_plugins_may_not_share_a_name(self):
        with self.assertRaises(ValueError):
            Host([Quiet(), Quiet()])

    def test_a_plugin_needs_a_name(self):
        with self.assertRaises(ValueError):
            Host([Boom() if False else type("Nameless", (Plugin,), {})()])


class TestSwitchingOff(unittest.IsolatedAsyncioTestCase):
    async def test_a_gate_stops_the_turn(self):
        veto = await Host([Gate("quota.turns")]).gate(ctx())
        assert veto is not None
        self.assertEqual(veto.reason, "quota.turns")
        self.assertEqual(veto.retry_after, 60)

    async def test_the_first_veto_wins_and_the_rest_are_not_asked(self):
        second = Gate("never", name="second")
        host = Host([Gate("first", name="first"), second])
        veto = await host.gate(ctx())
        assert veto is not None
        self.assertEqual(veto.reason, "first")
        self.assertEqual(second.turns, 0)

    async def test_a_gate_that_raises_does_not_stop_the_turn(self):
        turn = ctx()
        veto = await Host([GateDown()]).gate(turn)
        self.assertIsNone(veto)
        self.assertEqual([e.name for e in turn.events], ["failed"])
        self.assertEqual(turn.events[0].payload, {"hook": "before_turn"})

    async def test_an_enricher_that_raises_contributes_nothing(self):
        turn = ctx()
        host = Host([Boom(), Early()])
        sections = await host.enrich(turn)
        self.assertEqual([s.text for s in sections], ["前"])
        self.assertEqual([e.payload["hook"] for e in turn.events], ["prompt_sections"])

    async def test_an_observer_that_raises_does_not_stop_the_others(self):
        watcher = Watcher()
        turn = ctx("こんばんは")
        await Host([Boom(), watcher]).observe(turn)
        self.assertEqual(watcher.seen, ["こんばんは"])
        self.assertEqual([e.payload["hook"] for e in turn.events], ["after_turn"])


class TestEnrich(unittest.IsolatedAsyncioTestCase):
    async def test_sections_keep_plugin_order_and_the_builder_sorts_by_order(self):
        host = Host([Late(), Early()])
        sections = await host.enrich(ctx())
        self.assertEqual([s.text for s in sections], ["后", "前"])
        prompt = PromptBuilder(()).build(text="在吗", sections=sections)
        self.assertEqual(prompt[-2].content, "前\n\n后")


class TestCapabilitiesAndTables(unittest.TestCase):
    def test_a_plugin_says_what_it_offers(self):
        caps = Host([Collector()]).capabilities()
        self.assertEqual(caps["collector"], {"enabled": True, "panel": "collector"})

    def test_migrations_run_for_enabled_plugins_only(self):
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp) / "k.db") as store:
            host = Host([Collector()])
            statements = host.migrate(store)
            self.assertEqual(len(statements), 1)
            rows = store.query("SELECT name FROM sqlite_master WHERE type='table'")
            self.assertIn("collector_rows", [row["name"] for row in rows])

    def test_a_switched_off_plugin_runs_no_migrations(self):
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp) / "k.db") as store:
            host = Host([Collector()], enabled=[])
            self.assertEqual(host.migrate(store), [])


class Recorder(Plugin):
    """A plugin whose only job is to say when it was started and stopped."""

    def __init__(self, name: str, log: list[str], requires: tuple[str, ...] = (), fail: bool = False) -> None:
        self.name = name
        self.requires = requires
        self.log = log
        self.fail = fail

    def start(self) -> None:
        if self.fail:
            raise RuntimeError("no port")
        self.log.append("+" + self.name)

    def stop(self) -> None:
        self.log.append("-" + self.name)


class TestLifecycle(unittest.TestCase):
    """A plugin may own something outside a turn. That is the only reason `start` exists."""

    def test_start_runs_in_dependency_order_and_stop_reverses_it(self):
        log: list[str] = []
        host = Host([Recorder("b", log, ("a",)), Recorder("a", log)])
        self.assertEqual(host.start(), ["a", "b"])
        self.assertEqual(log, ["+a", "+b"])
        host.close()
        self.assertEqual(log, ["+a", "+b", "-b", "-a"])

    def test_a_plugin_that_cannot_start_is_switched_off_with_its_dependants(self):
        log: list[str] = []
        host = Host([Recorder("a", log, fail=True), Recorder("b", log, ("a",))])
        self.assertEqual(host.start(), [])
        self.assertEqual(log, [])
        self.assertFalse(host.capabilities()["a"]["enabled"])
        self.assertIn("start failed", host.capabilities()["a"]["why"])
        self.assertFalse(host.capabilities()["b"]["enabled"])
        self.assertIn("needs a", host.capabilities()["b"]["why"])

    def test_a_plugin_that_cannot_start_takes_no_part_in_a_turn(self):
        host = Host([Recorder("a", [], fail=True)])
        host.start()
        self.assertEqual(host.names, [])

    def test_starting_twice_starts_once(self):
        log: list[str] = []
        host = Host([Recorder("a", log)])
        host.start()
        host.start()
        self.assertEqual(log, ["+a"])

    def test_a_stop_that_raises_does_not_block_the_others(self):
        class Stubborn(Plugin):
            name = "stubborn"

            def stop(self) -> None:
                raise RuntimeError("no")

        log: list[str] = []
        host = Host([Stubborn(), Recorder("a", log)])
        host.start()
        host.close()
        self.assertEqual(log, ["+a", "-a"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
