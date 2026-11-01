"""节假日：这一天要不要早起，由这张表回答。"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from channels import calendar as cal


def write(root: Path, body: str) -> None:
    (root / "calendar.toml").write_text(body, encoding="utf-8")


class TestCalendar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_missing_file_is_weekends_only(self):
        one = cal.load(self.root)
        self.assertTrue(one.is_off(date(2026, 10, 31)), "周六")
        self.assertTrue(one.is_off(date(2026, 11, 1)), "周日")
        self.assertFalse(one.is_off(date(2026, 10, 30)), "周五是工作日")
        self.assertEqual(one.as_dict()["count"], 0)

    def test_a_broken_file_is_not_fatal(self):
        write(self.root, "[calendar\n holidays = ")
        self.assertTrue(cal.load(self.root).is_off(date(2026, 10, 31)))
        self.assertFalse(cal.load(self.root).is_off(date(2026, 10, 30)))

    def test_a_listed_holiday_is_a_day_off(self):
        write(
            self.root,
            '[calendar]\ncountry = "JP"\n\n[calendar.holidays]\n'
            '"2026-11-03" = "文化の日"\n',
        )
        one = cal.load(self.root)
        self.assertTrue(one.is_off(date(2026, 11, 3)), "周二，但祝日")
        self.assertEqual(one.name(date(2026, 11, 3)), "文化の日")
        self.assertEqual(one.name(date(2026, 10, 31)), "週末")
        self.assertEqual(one.name(date(2026, 10, 30)), "")

    def test_a_workday_override_beats_the_table(self):
        """调休上班的周六：先看 `.env`，再看祝日，最后才是周末。"""
        write(
            self.root,
            '[calendar.holidays]\n"2026-10-03" = "休み"\n',
        )
        one = cal.load(self.root, workdays=("2026-10-03",))
        self.assertFalse(one.is_off(date(2026, 10, 3)), "写在 workdays 里就是工作日")
        self.assertTrue(one.is_off(date(2026, 10, 4)), "周日不受影响")

    def test_an_extra_holiday_overrides_the_weekday(self):
        one = cal.load(self.root, holidays=("2026-10-01",))
        self.assertTrue(one.is_off(date(2026, 10, 1)), "周四，公司自己放的假")
        self.assertEqual(one.name(date(2026, 10, 1)), "休み")

    def test_the_timezone_comes_from_the_file(self):
        write(self.root, '[calendar]\ntimezone = "Asia/Tokyo"\ncountry = "JP"\n')
        one = cal.load(self.root)
        self.assertEqual(one.timezone, "Asia/Tokyo")
        self.assertEqual(one.country, "JP")

    def test_a_nonsense_date_is_skipped_not_fatal(self):
        write(self.root, '[calendar.holidays]\n"not a date" = "x"\n"2026-05-06" = "休日"\n')
        one = cal.load(self.root)
        self.assertTrue(one.is_off(date(2026, 5, 6)))
        self.assertEqual(one.as_dict()["count"], 1)


class TestTheJapaneseTable(unittest.TestCase):
    """仓库里那张表本身：它错了，她就会在错的日子熬夜。"""

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / "characters"
        self.one = cal.load(self.root)

    def test_it_covers_this_year_and_next(self):
        info = self.one.as_dict()
        self.assertEqual(info["country"], "JP")
        self.assertGreaterEqual(info["count"], 30)
        self.assertLessEqual(info["from"], "2026-01-02")
        self.assertGreaterEqual(info["to"], "2027-11-23")

    def test_the_days_that_are_never_wrong(self):
        for day, name in (
            (date(2026, 1, 1), "元日"),
            (date(2026, 2, 11), "建国記念の日"),
            (date(2026, 5, 3), "憲法記念日"),
            (date(2026, 5, 6), "休日"),  # 振替休日
            (date(2027, 1, 1), "元日"),
        ):
            with self.subTest(day=day):
                self.assertTrue(self.one.is_off(day))
                self.assertEqual(self.one.name(day), name)

    def test_a_regular_weekday_is_not_off(self):
        self.assertFalse(self.one.is_off(date(2026, 6, 10)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
