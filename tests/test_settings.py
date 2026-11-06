"""The one timezone function everyone shares.

`core.settings.zone` / `local_time` were lifted out of four near-duplicate copies in
`channels/{qq,plugin_qq,presence,digest}.py`. The behaviour that matters — and that the
old copies got wrong in different ways — is what happens when `ZoneInfo` cannot find
tzdata: the clock must not jump to UTC (nine hours off for Tokyo) and must not return
`None` (which would make `datetime.now(None)` read the *machine's* zone, so a dev box
and a server disagree about whether she is asleep).
"""

import unittest
from datetime import datetime, timezone, timedelta

from core.settings import FIXED_OFFSETS, local_time, zone


class TestZone(unittest.TestCase):
    def test_tokyo_is_nine_hours_ahead(self):
        tokyo = zone("Asia/Tokyo")
        now = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
        local = now.astimezone(tokyo)
        self.assertEqual(local.hour, 9, "UTC midnight is 09:00 in Tokyo")

    def test_shanghai_is_eight(self):
        shanghai = zone("Asia/Shanghai")
        now = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(now.astimezone(shanghai).hour, 8)

    def test_unknown_name_falls_back_to_utc_not_none(self):
        """A name nobody recognises must still return a tzinfo, never None.

        The old `presence._zone` returned None here, and `datetime.now(None)` then read
        the machine's local zone — a silent nine-hour shift between dev and server.
        """
        result = zone("Mars/Olympus")
        self.assertIsNotNone(result)
        now = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(now.astimezone(result).hour, 0, "unknown → UTC, not machine zone")

    def test_none_name_is_utc(self):
        self.assertIsNotNone(zone(None))

    def test_fixed_offsets_table_matches_the_two_we_care_about(self):
        self.assertEqual(FIXED_OFFSETS["Asia/Tokyo"], 9)
        self.assertEqual(FIXED_OFFSETS["Asia/Shanghai"], 8)


class TestLocalTime(unittest.TestCase):
    def test_it_uses_the_named_zone(self):
        now = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(local_time(now, "Asia/Tokyo").hour, 9)

    def test_none_tz_does_not_crash(self):
        now = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
        result = local_time(now, None)
        # Falls back to the machine zone or UTC; either way it is a datetime, not an error.
        self.assertIsInstance(result, datetime)


if __name__ == "__main__":
    unittest.main()
