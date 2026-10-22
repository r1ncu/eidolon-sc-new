"""The prompt: where each part lands, and which parts a service can cache."""

import unittest

from core.messages import Message
from core.prompt import PromptBuilder, Section, Slot, latest_turns, trim_history


def turns(n: int) -> list[Message]:
    """`n` turns, each a user message and her reply."""
    out: list[Message] = []
    for i in range(n):
        out.append(Message(f"u{i}", "c", "user", text=f"第{i}句"))
        out.append(Message(f"a{i}", "c", "assistant"))
    return out


PERSONA = (Section(Slot.PERSONA, "你是真乃。", title="sheet"),)
PROFILE = (Section(Slot.PROFILE, "制作人叫小春。", title="producer"),)


def builder(*groups: tuple[Section, ...]) -> PromptBuilder:
    """A builder whose base is these groups, in this order — the slots sort them."""
    return PromptBuilder(tuple(section for group in groups for section in group))


class TestAssembly(unittest.TestCase):
    def test_roles_and_order_without_history(self):
        prompt = builder(PERSONA, PROFILE).build(text="在吗")
        self.assertEqual([m.role for m in prompt], ["system", "system", "user"])
        self.assertTrue(prompt[0].cache_breakpoint)
        self.assertFalse(prompt[1].cache_breakpoint)
        self.assertEqual(prompt[-1].content, "在吗")

    def test_the_reply_format_goes_in_the_persona_message(self):
        prompt = builder(PERSONA).build(text="在吗")
        self.assertIn("你是真乃。", prompt[0].content)
        self.assertIn("回复格式", prompt[0].content)

    def test_persona_and_profile_are_separate_messages(self):
        prompt = builder(PERSONA, PROFILE).build(text="在吗")
        self.assertNotIn("制作人叫小春", prompt[0].content)
        self.assertEqual(prompt[1].content, "制作人叫小春。")

    def test_history_comes_between_profile_and_this_turn(self):
        prompt = builder(PERSONA, PROFILE).build(history=turns(1)[:2], text="在吗")
        self.assertEqual([m.role for m in prompt], ["system", "system", "user", "assistant", "user"])
        self.assertTrue(prompt[3].cache_breakpoint)  # the last thing the next turn opens with
        self.assertFalse(prompt[-1].cache_breakpoint)

    def test_this_turns_sections_are_one_uncached_message(self):
        sections = (
            Section(Slot.TURN, "现在是晚上十点。", order=100, title="clock"),
            Section(Slot.TURN, "她今天心情不错。", order=200, title="affinity"),
        )
        prompt = builder(PERSONA).build(history=turns(1)[:2], text="在吗", sections=sections)
        self.assertEqual(prompt[-2].role, "system")
        self.assertEqual(prompt[-2].content, "现在是晚上十点。\n\n她今天心情不错。")
        self.assertFalse(prompt[-2].cache_breakpoint)

    def test_sections_are_sorted_by_order_not_by_who_wrote_them(self):
        sections = (
            Section(Slot.TURN, "后面", order=300),
            Section(Slot.TURN, "前面", order=100),
        )
        prompt = builder(PERSONA).build(text="在吗", sections=sections)
        self.assertEqual(prompt[-2].content, "前面\n\n后面")

    def test_aside_goes_before_the_words(self):
        prompt = builder(PERSONA).build(text="好看吗", aside="（制作人发来一张图片：一只鸟。）")
        self.assertEqual(prompt[-1].content, "（制作人发来一张图片：一只鸟。）好看吗")

    def test_her_reply_goes_back_into_the_history_in_the_reply_format(self):
        history = [
            Message("u1", "c", "user", text="早上好"),
            Message("a1", "c", "assistant", segments=(_segment("おはよう", "早上好"),)),
        ]
        prompt = builder(PERSONA).build(history=history, text="今天也早")
        self.assertEqual(prompt[2].role, "assistant")
        self.assertEqual(prompt[2].content, "<ja>おはよう</ja><zh>早上好</zh>")


class TestWhoMayWriteWhere(unittest.TestCase):
    def test_a_turn_section_may_not_be_smuggled_into_the_persona(self):
        with self.assertRaises(ValueError):
            PromptBuilder((Section(Slot.TURN, "每分钟都变"),))

    def test_a_plugin_may_not_write_to_a_cached_slot(self):
        with self.assertRaises(ValueError):
            builder(PERSONA).build(
                text="在吗", sections=(Section(Slot.PERSONA, "来自插件的世界设定"),)
            )

    def test_history_is_not_a_place_a_plugin_writes(self):
        with self.assertRaises(ValueError):
            builder(PERSONA).build(text="在吗", sections=(Section(Slot.HISTORY, "很久以前"),))


class TestTrimming(unittest.TestCase):
    def test_fewer_turns_than_asked_keep_them_all(self):
        self.assertEqual(len(trim_history(turns(5), 20)), 10)

    def test_up_to_one_and_a_half_times_is_left_alone(self):
        for n in (20, 21, 29):
            with self.subTest(turns=n):
                self.assertEqual(len(trim_history(turns(n), 20)), n * 2)

    def test_at_one_and_a_half_times_it_is_cut_back_to_twenty(self):
        self.assertEqual(len(trim_history(turns(30), 20)), 40)

    def test_it_is_cut_in_batches_not_one_turn_at_a_time(self):
        """At 30 turns it is cut back to 20 and then grows again, so between cuts the
        prefix holds — which is what makes the cache worth asking for."""
        self.assertEqual(len(trim_history(turns(30), 20)), 40)
        self.assertEqual(len(trim_history(turns(31), 20)), 42)
        self.assertEqual(len(trim_history(turns(40), 20)), 40)

    def test_what_is_left_starts_with_a_user_message(self):
        kept = trim_history(turns(30), 20)
        self.assertEqual(kept[0].role, "user")
        self.assertEqual(len([m for m in kept if m.role == "user"]), 20)

    def test_none_means_all_of_them(self):
        self.assertEqual(len(trim_history(turns(50), None)), 100)

    def test_latest_turns_counts_turns_not_messages(self):
        self.assertEqual([m.id for m in latest_turns(turns(3), 2)], ["u1", "a1", "u2", "a2"])


def _segment(ja: str, zh: str):
    from core.protocol import Segment

    return Segment(ja, zh)


if __name__ == "__main__":
    unittest.main(verbosity=2)
