"""`channels/romance.py` 的纯函数测试。

它没有 store、没有时钟、没有模型——所以这里也不需要那些。一件事一个断言，
参数直接从「参数表」抄过来，表改了这里会先红。
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from channels import romance


def who(**changes):
    base = dict(adult=True, age=19, plove=3, heavy=3, name="テスト")
    base.update(changes)
    return SimpleNamespace(**base)


class TestWhoIsGrown(unittest.TestCase):
    def test_an_explicit_answer_beats_the_age(self):
        self.assertTrue(romance.grown(who(adult=True, age=15)))
        self.assertFalse(romance.grown(who(adult=False, age=30)))

    def test_the_age_decides_when_nobody_says(self):
        # 线在「高中生及以上」：15 岁起放开，18 岁这条旧线已经不是分界。
        self.assertTrue(romance.grown(who(adult=None, age=18)))
        self.assertTrue(romance.grown(who(adult=None, age=17)), "高二可以")
        self.assertTrue(romance.grown(who(adult=None, age=15)), "高一可以")
        self.assertFalse(romance.grown(who(adult=None, age=14)), "初三不行")
        self.assertFalse(romance.grown(who(adult=None, age=12)), "小学不行")

    def test_a_middle_schooler_can_be_fifteen(self):
        """15 岁有初三也有高一，所以年龄只是兜底，角色表里的 adult 说了算。"""
        self.assertFalse(romance.grown(who(adult=False, age=15)))
        self.assertTrue(romance.grown(who(adult=True, age=15)))

    def test_a_missing_age_is_underage_by_default(self):
        """漏判成年的代价是少一段玩法；漏判未成年的代价不能出。"""
        self.assertFalse(romance.grown(who(adult=None, age=None)))

    def test_a_deployment_may_choose_otherwise(self):
        self.assertTrue(romance.grown(who(adult=None, age=None), unknown_is_adult=True))


class TestWhatTheNumbersSay(unittest.TestCase):
    def test_the_bars_come_from_her_own_level(self):
        self.assertEqual(romance.can_ask(who(plove=1), 94, 80), (False, "not_close"))
        self.assertEqual(romance.can_ask(who(plove=5), 75, 20), (True, ""))
        self.assertFalse(romance.can_ask(who(plove=3), 90, 10)[0], "话没说过够")

    def test_a_minor_cannot_ask_at_all(self):
        self.assertEqual(romance.can_ask(who(adult=False, plove=5), 100, 99), (False, "minor"))

    def test_plove_zero_may_still_speak(self):
        """L0 允许开口：听完给一个明确的「不会」，比按钮点不动诚实。"""
        self.assertTrue(romance.can_ask(who(plove=0), 100, 99)[0])
        self.assertEqual(romance.verdict(who(plove=0), 100, 80)[0], "no_way")

    def test_the_score_moves_with_all_three_things(self):
        low = romance.score(who(plove=3), 86, 30)
        high = romance.score(who(plove=3), 100, 80)
        self.assertLess(low, high)
        self.assertGreater(romance.score(who(plove=5), 90, 60), romance.score(who(plove=1), 90, 60))

    def test_the_verdicts_come_out_in_order(self):
        self.assertEqual(romance.verdict(who(plove=3), 100, 80)[0], "accept")
        self.assertEqual(romance.verdict(who(plove=3), 86, 30)[0], "refuse")
        self.assertEqual(romance.verdict(who(plove=3), 86, 30, sulking=True)[0], "maybe")

    def test_a_sulk_is_never_a_yes(self):
        """正在生闷气时听到告白：交给模型，不给它一个「好」。"""
        outcome, _, reason = romance.verdict(who(plove=5), 100, 99, sulking=True)
        self.assertEqual((outcome, reason), ("maybe", "fight"))


class TestWhatStateSheMayEnter(unittest.TestCase):
    def test_a_minor_may_be_crushed_but_not_loved(self):
        self.assertTrue(romance.allowed(who(adult=False), "crush"))
        self.assertFalse(romance.allowed(who(adult=False), "loving"))

    def test_plove_zero_does_not_even_get_crush(self):
        self.assertFalse(romance.allowed(who(plove=0), "crush"))
        self.assertFalse(romance.allowed(who(plove=0), "loving"))

    def test_going_back_is_always_allowed(self):
        self.assertTrue(romance.allowed(who(adult=False, plove=0), "none"))

    def test_nonsense_is_refused(self):
        self.assertFalse(romance.allowed(who(), "in_love"))


class TestHerSide(unittest.TestCase):
    def test_she_never_speaks_when_the_line_does_not_exist(self):
        self.assertFalse(romance.she_ready(who(plove=0), 100, "none", 90)[0])
        self.assertEqual(romance.she_chance(who(plove=0), 90), 0.0)

    def test_she_does_not_speak_too_early_or_too_far(self):
        self.assertFalse(romance.she_ready(who(), 100, "none", 5)[0], "还没等够")
        self.assertFalse(romance.she_ready(who(), 60, "none", 90)[0], "还不够近")
        self.assertFalse(romance.she_ready(who(), 100, "crush", 90)[0], "已经没有「没说破」了")
        self.assertTrue(romance.she_ready(who(), 100, "none", 21)[0])

    def test_the_chance_rises_with_plove_heavy_and_waiting(self):
        cool = romance.she_chance(who(plove=1, heavy=1), 0)
        warm = romance.she_chance(who(plove=5, heavy=5), 0)
        late = romance.she_chance(who(plove=5, heavy=5), 60)
        self.assertAlmostEqual(cool, 0.01, places=3)
        self.assertAlmostEqual(warm, 0.29, places=3)
        self.assertGreater(late, warm)
        self.assertLessEqual(late, romance.SHE_CAP)


class TestWhatHeAnswered(unittest.TestCase):
    def test_a_yes(self):
        for said in ("好啊", "嗯，可以", "我也喜欢你"):
            self.assertEqual(romance.answer(said), "yes", said)

    def test_a_no(self):
        for said in ("对不起", "不行，我不喜欢你"):
            self.assertEqual(romance.answer(said), "no", said)

    def test_changing_the_subject_is_its_own_outcome(self):
        for said in ("你说什么呢", "好累啊", "", "今天工作好多"):
            self.assertEqual(romance.answer(said), "dodge", said)

    def test_a_no_inside_a_yes_is_a_no(self):
        self.assertEqual(romance.answer("不行，我不喜欢你"), "no", "先判否再判可")


class TestWaitingTooLong(unittest.TestCase):
    NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)

    def test_seven_days_is_the_line(self):
        self.assertFalse(romance.offer_expired("2026-10-27T00:00:00+00:00", self.NOW))
        self.assertTrue(romance.offer_expired("2026-10-20T00:00:00+00:00", self.NOW))

    def test_an_unreadable_stamp_does_not_end_anything(self):
        self.assertFalse(romance.offer_expired("不是时间", self.NOW))
        self.assertFalse(romance.offer_expired("", self.NOW))

    def test_a_naive_stamp_is_read_as_utc(self):
        when = (self.NOW - timedelta(days=8)).replace(tzinfo=None).isoformat()
        self.assertTrue(romance.offer_expired(when, self.NOW))


if __name__ == "__main__":
    unittest.main()
