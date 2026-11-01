"""The pack resolver: which file answers, and which one is still on the old path."""

import tempfile
import unittest
from pathlib import Path

from plugins import packs


def write(root: Path, name: str, body: str = "x") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


class TestWork(unittest.TestCase):
    def test_a_missing_file_is_a_neutral_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = packs.load_work(tmp)
        self.assertFalse(work.named)
        self.assertEqual(work.name(), "")
        self.assertEqual(work.meta_words, ())

    def test_a_broken_file_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(Path(tmp), "work.toml", "[work\n title = ")
            work = packs.load_work(tmp)
        self.assertFalse(work.named)

    def test_the_title_and_the_house_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(
                Path(tmp),
                "work.toml",
                '[work]\ntitle = "T"\ntitle_zh = "作品"\nagency = "A"\nagency_zh = "事务所"\n'
                '[aliases]\nwords = ["生放送", "live"]\n'
                '[privacy]\nforbidden = ["秘密"]\n',
            )
            work = packs.load_work(tmp)
        self.assertEqual(work.name(), "《作品》")
        self.assertEqual(work.house(), "事务所")
        self.assertEqual(work.meta_words, ("生放送", "live"))
        self.assertEqual(work.forbidden, ("秘密",))

    def test_a_title_without_a_house_does_not_invent_one(self):
        work = packs.Work(title="T")
        self.assertEqual(work.name(), "《T》")
        self.assertEqual(work.house(), "")


class TestPack(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_pack_wins_over_the_flat_name(self):
        write(self.root, "packs/fuyuko/voice/qq.md", "new")
        write(self.root, "world/fuyuko-qq.md", "old")
        pack = packs.Pack(self.root, "fuyuko")
        self.assertEqual(pack.text("qq"), "new")
        self.assertEqual(pack.source("qq"), "packs/fuyuko/voice/qq.md")

    def test_the_flat_name_still_answers(self):
        write(self.root, "world/fuyuko-qq.md", "old")
        pack = packs.Pack(self.root, "fuyuko")
        self.assertEqual(pack.text("qq"), "old")
        self.assertEqual(pack.source("qq"), "world/fuyuko-qq.md")
        self.assertIn("qq", pack.fallback())

    def test_nothing_is_none_not_an_error(self):
        pack = packs.Pack(self.root, "fuyuko")
        self.assertIsNone(pack.path("qq"))
        self.assertEqual(pack.text("qq"), "")
        self.assertIn("qq", pack.missing())

    def test_view_and_world_have_no_legacy_name(self):
        """这两份是新拆出来的：`world/<id>-view.md` 不存在，别去猜一个文件名。"""
        write(self.root, "packs/fuyuko/view.md", "她怎么看他们")
        write(self.root, "packs/fuyuko/world.md", "她怎么看作品")
        pack = packs.Pack(self.root, "fuyuko")
        self.assertEqual(pack.text("view"), "她怎么看他们")
        self.assertEqual(pack.text("world"), "她怎么看作品")
        self.assertEqual(pack.fallback(), ())

    def test_ready_means_the_three_she_cannot_be_played_without(self):
        write(self.root, "world/fuyuko-qq.md")
        write(self.root, "world/fuyuko-dm.md")
        self.assertFalse(packs.Pack(self.root, "fuyuko").compact()["ready"])
        write(self.root, "sheets/fuyuko.md")
        self.assertTrue(packs.Pack(self.root, "fuyuko").compact()["ready"])

    def test_a_declared_home_is_used_and_nothing_else_changes(self):
        write(self.root, "somewhere/else/persona.md", "人设")
        pack = packs.Pack(self.root, "fuyuko", declared="somewhere/else")
        self.assertEqual(pack.text("persona"), "人设")
        self.assertEqual(pack.source("persona"), "somewhere/else/persona.md")

    def test_memory_is_per_character_and_created_on_demand(self):
        pack = packs.Pack(self.root, "fuyuko")
        self.assertIsNone(pack.path("memory"))
        made = pack.path("memory", writable=True)
        self.assertEqual(made, self.root / "memory" / "fuyuko")
        self.assertTrue(made.is_dir())
        self.assertEqual(pack.source("memory"), "memory/fuyuko")

    def test_an_unknown_kind_is_a_loud_mistake(self):
        with self.assertRaises(KeyError):
            packs.Pack(self.root, "fuyuko").path("nonsense")


class TestCardTails(unittest.TestCase):
    """卡名尾：机制保留，内容来自角色包。"""

    class Who:
        name = "黛冬優子"
        name_zh = "黛冬优子"

    def test_the_full_name_is_the_default(self):
        self.assertEqual(packs.card_tails(self.Who()), ("黛冬優子", "黛冬优子"))

    def test_a_pack_may_say_the_spellings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "packs/fuyuko/names.toml", '[names]\ncards = "黛冬優子、黛 冬優子\\n"')
            tails = packs.card_tails(self.Who(), packs.Pack(root, "fuyuko"))
        self.assertEqual(tails, ("黛冬優子", "黛 冬優子"))

    def test_no_name_means_no_stripping(self):
        class Nameless:
            name = ""
            name_zh = ""

        self.assertEqual(packs.card_tails(Nameless()), ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
