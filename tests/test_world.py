"""世界那几层文件里的 HTML 注释不进提示词。

`<!-- ... -->` 是写给改文件的人看的：一份四十行的作品笔记，注释多的时候能有十几行，
而它一个字的信息量都没有。这几层是整段注入的——`office.md` 每轮都在，`idols/<id>.md`
和 `meta.md` 按名字取出来——所以注释会原样变成提示词的一部分，白占位置。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from channels.world import World

WORLD_TOML = """\
[[characters]]
id = "test"
name = "テスト"
intro = "测试角色"

[[characters]]
id = "asahi"
name = "芹沢あさひ"
name_romaji = "Asahi Serizawa"
intro = "同僚"
"""

OFFICE = """\
<!-- 名单：一个组合一行；改之前先看她的 view.md -->
あさひ：テストの同僚。
"""

META = """\
<!-- 作品：活动表还没补完 -->
生放送もある。
"""

IDOL = """\
<!-- あさひ：音楽担当 -->
呼び方：「あさひちゃん」。
"""


class TestNotesStayOutOfThePrompt(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "world" / "idols").mkdir(parents=True)
        (self.root / "characters.toml").write_text(WORLD_TOML, encoding="utf-8")
        (self.root / "world" / "office.md").write_text(OFFICE, encoding="utf-8")
        (self.root / "world" / "meta.md").write_text(META, encoding="utf-8")
        (self.root / "world" / "idols" / "asahi.md").write_text(IDOL, encoding="utf-8")
        self.world = World(self.root)

    def test_the_roster_arrives_without_its_comments(self):
        text = self.world.persona().text
        self.assertIn("あさひ", text)
        self.assertNotIn("<!--", text)
        self.assertNotIn("view.md", text, "注释里的字进了提示词")

    def test_a_colleague_file_arrives_without_its_comments(self):
        hits = self.world.lookup("芹沢あさひってどんな子？")
        self.assertTrue(hits)
        self.assertIn("呼び方", hits[0].text)
        self.assertNotIn("音楽担当", hits[0].text)

    def test_the_work_notes_arrive_without_their_comments(self):
        hits = self.world.knowledge("生放送いつだっけ")
        self.assertTrue(hits)
        self.assertIn("生放送", hits[0].text)
        self.assertNotIn("活動表", hits[0].text)

    def test_a_file_that_is_only_comments_is_empty(self):
        """只剩注释的文件不算「有内容」：人设那边该什么都不注入。"""
        (self.root / "world" / "office.md").write_text("<!-- 待填 -->\n", encoding="utf-8")
        self.world.refresh()
        self.assertIsNone(self.world.persona())

    def test_the_blank_lines_a_comment_leaves_behind_are_squeezed(self):
        (self.root / "world" / "office.md").write_text(
            "あさひ：同僚。\n\n<!-- x -->\n\n\nみずき：先輩。\n", encoding="utf-8"
        )
        self.world.refresh()
        self.assertNotIn("\n\n\n", self.world.persona().text)


if __name__ == "__main__":
    unittest.main()
