"""A few turns with no key at all, so the kernel can be seen working end to end.

    python demo.py

`ScriptedLLM` says what it was told to; everything else is the kernel: the prompt is
assembled from sections, the reply is parsed into segments as it streams, the turn is
stored. Two turns are enough to show the shape of the second prompt — persona, profile,
the history, the cached boundary, the user's words.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

from core.events import SegmentDelta, TurnDone, TurnFailed, TurnRejected
from core.host import Host
from core.llm import ScriptedLLM
from core.plugin import Plugin
from core.prompt import Section, Slot
from core.store import Store
from core.turn import Engine
from plugins.quota import Quota

BASE = (
    Section(Slot.PERSONA, "你是杜野凛世。说话安静、简短，用「……」。", order=100, title="sheet"),
    Section(Slot.PROFILE, "制作人叫小春，习惯晚上来。", order=100, title="producer"),
)


class Clock(Plugin):
    """A plugin in six lines: it adds one line to every turn, and knows nothing else."""

    name = "clock"

    async def prompt_sections(self, turn):
        hour = turn.now.astimezone().hour
        return [Section(Slot.TURN, f"现在是 {hour} 点。", order=100, title="clock")]

TURNS = [
    ("ただいま", "<ja>……おかえりなさい。</ja><zh>……欢迎回来。</zh>"
                 "<ja>[gently] 今日は、少し遅かったですね。</ja><zh>今天，稍微晚了一点呢。</zh>"),
    ("ごめん、残業", "<ja>……うん。[shyly] 待ってた。</ja><zh>……嗯。等着的。</zh>"),
    ("おやすみなさい", "<ja>[sleepy] ……おやすみなさい。</ja><zh>……晚安。</zh>"),
]


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "demo.db")
        user = store.create_user("小春")
        conversation = store.create_conversation(user, "rinze")

        host = Host([Clock(), Quota(store, turns_per_hour=3)])
        host.migrate(store)
        print("插件：", host.capabilities())

        for said, reply in TURNS:
            llm = ScriptedLLM(reply, size=8)
            engine = Engine(store, llm, base=BASE, host=host)
            events = [event async for event in engine.turn(conversation.id, said)]

            print(f"\n制作人：{said}")
            if not llm.seen:
                print("  （这一轮没有发给模型）")
                continue
            print(f"  提示词：{' → '.join(m.role + ('*' if m.cache_breakpoint else '') for m in llm.seen[0])}")
            segments: dict[int, dict[str, str]] = {}
            for event in events:
                if isinstance(event, SegmentDelta):
                    segments.setdefault(event.segment, {"ja": "", "zh": ""})[event.lang] += event.text
                elif isinstance(event, TurnDone):
                    print(f"  已落库：{event.message_id}")
                elif isinstance(event, (TurnFailed, TurnRejected)):
                    print(f"  没成：{event.reason}")
            for segment in segments.values():
                print(f"  {segment['ja']}")
                print(f"  {segment['zh']}")

        print("\n再试一句：限额是每小时 3 轮，前三轮已经把这一小时用完了。")
        gate = Engine(store, ScriptedLLM(TURNS[0][1]), base=BASE, host=host)
        for event in [e async for e in gate.turn(conversation.id, "もう寝るね")]:
            if isinstance(event, TurnRejected):
                print(f"  被拦下：{event.reason}，{event.retry_after} 秒后再来")
                print("  （没有发给模型，也没有落库）")

        print("\n库里现在有：")
        for message in store.messages(conversation.id):
            said = message.text if message.role == "user" else message.ja
            print(f"  {message.role:<9} {said}")
        store.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    asyncio.run(main())
