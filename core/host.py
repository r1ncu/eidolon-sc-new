"""Which plugins run, in what order, and what happens when one falls over.

Two rules the whole file exists to keep:

1. **A plugin that cannot run is switched off, not fatal.** A missing dependency, a name
   that was not enabled, a hook that raises — the plugin stops taking part and the turn
   carries on. A character that stops answering because a gift counter broke is a worse
   failure than a gift counter that stopped counting.

2. **Order is a dependency order, not a registration order.** `requires` is a promise
   about `facts` and tables, so a plugin is started after the ones it reads, and a cycle
   is a programming error that fails loudly at startup rather than quietly at turn 40.
"""

from collections.abc import Iterable, Sequence
from typing import Any

from .events import KernelEvent, PluginEvent
from .judge import Judge
from .plugin import Plugin, Question, Route, TurnContext, Veto
from .prompt import Section
from .store import Store


class Host:
    def __init__(
        self,
        plugins: Sequence[Plugin] = (),
        *,
        judge: Judge | None = None,
        enabled: Iterable[str] | None = None,
    ) -> None:
        self._judge = judge
        self._off: dict[str, str] = {}
        self._started = False
        """Whether `start()` has run. Constructing a host opens nothing; `start()` is what
        makes a plugin with a lifecycle alive, and it may only happen once."""
        by_name: dict[str, Plugin] = {}
        for plugin in plugins:
            if not plugin.name:
                raise ValueError(f"{type(plugin).__name__} has no name")
            if plugin.name in by_name:
                raise ValueError(f"two plugins are called {plugin.name}")
            by_name[plugin.name] = plugin
        if enabled is not None:
            wanted = set(enabled)
            self._off = {name: "not enabled" for name in by_name if name not in wanted}
        self._order = _ordered(list(by_name.values()), self._off)

    def plugin(self, name: str) -> Plugin | None:
        """One plugin by name, for the caller that built it and wants its own methods
        (a directory, a settings page) without the kernel learning what they mean."""
        for plugin in self._order:
            if plugin.name == name:
                return plugin
        return None

    def routes(self) -> dict[tuple[str, str], Route]:
        """Every enabled plugin's endpoints, one table. Claiming a path twice is a startup
        error: silently letting the later one win is how two features fight for a URL."""
        table: dict[tuple[str, str], Route] = {}
        for plugin in self._order:
            for key, handler in plugin.routes().items():
                if key in table:
                    raise ValueError(f"{key[0]} {key[1]} is claimed by two plugins")
                table[key] = handler
        return table

    @property
    def names(self) -> list[str]:
        """The plugins that are running, in the order their hooks are called."""
        return [plugin.name for plugin in self._order]

    def capabilities(self) -> dict[str, dict[str, Any]]:
        """What a client may show, for enabled and switched-off plugins alike: a plugin
        that is off says why, so a settings page can say "needs the judge model"."""
        caps = {plugin.name: {"enabled": True, **plugin.capabilities()} for plugin in self._order}
        for name, why in self._off.items():
            caps[name] = {"enabled": False, "why": why}
        return caps

    def migrate(self, store: Store) -> list[str]:
        """Run every plugin's `CREATE TABLE IF NOT EXISTS`, in dependency order."""
        statements = [statement for plugin in self._order for statement in plugin.migrations()]
        if statements:
            store.migrate(statements)
        return statements

    def start(self) -> list[str]:
        """Bring every enabled plugin up, in the same order the hooks run.

        This is where a plugin that owns something outside a turn — a socket, a thread, a
        timer — becomes alive. Nothing else calls it, and it is separate from `__init__`
        because constructing a plugin must stay free of side effects: a test builds an `App`
        to read a table, and a test must not open a websocket to do it.

        A plugin that raises here is switched off like any other that cannot run, and so are
        the ones that required it. The kernel keeps serving turns. Returns the names that
        made it, which is what `serve.py` prints.
        """
        if self._started:
            return self.names
        self._started = True
        failed: set[str] = set()
        for plugin in list(self._order):
            missing = [need for need in plugin.requires if need in failed]
            if missing:
                failed.add(plugin.name)
                self._off[plugin.name] = "needs " + ", ".join(missing)
                continue
            try:
                plugin.start()
            except Exception as exc:
                failed.add(plugin.name)
                self._off[plugin.name] = f"start failed: {type(exc).__name__}: {exc}"
        if failed:
            self._order = [plugin for plugin in self._order if plugin.name not in failed]
        return self.names

    def close(self) -> None:
        """Stop what `start` started, newest first. Never raises: we are already leaving."""
        for plugin in reversed(self._order):
            try:
                plugin.stop()
            except Exception:
                pass
        self._started = False

    def questions(self) -> list[Question]:
        return [question for plugin in self._order for question in plugin.judge_questions()]

    async def judge(self, turn: TurnContext) -> None:
        """One request for every question any plugin asked. A judge that fails leaves the
        facts empty: the plugins then do without, which they are written to survive."""
        questions = self.questions()
        if not questions or self._judge is None:
            return
        try:
            turn.facts.update(await self._judge.ask(questions, said=turn.text))
        except Exception:
            return

    async def gate(self, turn: TurnContext) -> Veto | None:
        """The first plugin that vetoes wins; the rest are not asked."""
        for plugin in self._order:
            try:
                veto = await plugin.before_turn(turn)
            except Exception:
                turn.events.append(_failed(plugin, "before_turn"))
                continue
            if veto is not None:
                return veto
        return None

    async def enrich(self, turn: TurnContext) -> list[Section]:
        sections: list[Section] = []
        for plugin in self._order:
            try:
                sections.extend(await plugin.prompt_sections(turn))
            except Exception:
                turn.events.append(_failed(plugin, "prompt_sections"))
        return sections

    async def observe(self, turn: TurnContext) -> None:
        for plugin in self._order:
            try:
                await plugin.after_turn(turn)
            except Exception:
                turn.events.append(_failed(plugin, "after_turn"))

    def drain(self, turn: TurnContext) -> list[KernelEvent]:
        """What plugins put in the outbox since last time: failures, and their own news."""
        events, turn.events = turn.events, []
        return events


def _failed(plugin: Plugin, hook: str) -> PluginEvent:
    return PluginEvent(plugin.name, "failed", {"hook": hook})


def _ordered(plugins: list[Plugin], off: dict[str, str]) -> list[Plugin]:
    """The plugins that can run, each after the ones it requires. Those that cannot are
    written into `off` with the reason, and their dependants with it."""
    by_name = {plugin.name: plugin for plugin in plugins}
    changed = True
    while changed:
        changed = False
        for plugin in list(plugins):
            if plugin.name in off:
                continue
            missing = [need for need in plugin.requires if need not in by_name or need in off]
            if missing:
                off[plugin.name] = "needs " + ", ".join(missing)
                changed = True

    alive = [plugin for plugin in plugins if plugin.name not in off]
    done: set[str] = set()
    ordered: list[Plugin] = []
    while alive:
        ready = [plugin for plugin in alive if all(need in done for need in plugin.requires)]
        if not ready:
            names = ", ".join(plugin.name for plugin in alive)
            raise ValueError(f"these plugins require each other in a circle: {names}")
        for plugin in ready:
            ordered.append(plugin)
            done.add(plugin.name)
            alive.remove(plugin)
    return ordered
