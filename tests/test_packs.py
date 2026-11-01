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

    def test_present_says_what_is_actually_there(self):
        # 「世界观」那一页要把二十项摊开：有的打勾，没有的打叉。`missing` 只说缺什么，
        # 所以另给一份 `present`——页面不必自己拿 _LAYOUT 反推。
        write(self.root, "sheets/fuyuko.md", "人设")
        write(self.root, "backgrounds/fuyuko.jpg")
        compact = packs.Pack(self.root, "fuyuko").compact()
        self.assertIn("persona", compact["present"])
        self.assertIn("background", compact["present"])
        self.assertNotIn("qq", compact["present"])
        self.assertEqual(len(compact["present"]) + len(compact["missing"]), len(packs._LAYOUT))

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


class TestTheSettingThatHasItsOwnFile(unittest.TestCase):
    """「夹子」这类只属于某个人设的设定，是包里的一份文件，不是代码里的默认值。

    `tease` 是机制要的数据（记号、窗口、那句话），`tease-md` 是她自己的人设段落：
    语域文件里留一个 `{{extra}}`，那一段就落在那里。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_both_halves_are_found_in_the_pack(self):
        write(self.root, "packs/x/voice/tease.toml", '[tease]\nmarks = ["♡"]\n')
        write(self.root, "packs/x/voice/tease.md", "【夹子】偶尔夹一句。")
        pack = packs.Pack(self.root, "x")
        self.assertEqual(pack.path("tease"), self.root / "packs/x/voice/tease.toml")
        self.assertEqual(pack.path("tease-md"), self.root / "packs/x/voice/tease.md")
        self.assertEqual(pack.text("tease-md"), "【夹子】偶尔夹一句。")

    def test_a_character_without_it_simply_has_none(self):
        write(self.root, "packs/x/voice/qq.md", "群聊语域")
        pack = packs.Pack(self.root, "x")
        self.assertIsNone(pack.path("tease-md"))
        self.assertEqual(pack.text("tease-md"), "")
        self.assertIn("tease-md", pack.missing())


class TestAnImageInEitherFormat(unittest.TestCase):
    """图片类的条目认后缀：写着的先试，`.png` / `.jpg` / `.jpeg` / `.webp` 都算。

    背景素材是官方给的 PNG，而条目里写着 `.jpg`（roster 里那条老路径就是 jpg）。要是
    解析器只认写着的后缀，一份 PNG 就会被判成「没有背景」——文件明明在那儿。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def touch(self, name: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return path

    def test_a_png_answers_for_a_jpg_entry(self):
        path = self.touch("backgrounds/x.png")
        pack = packs.Pack(self.root, "x")
        self.assertEqual(pack.path("background"), path)

    def test_the_declared_suffix_still_wins(self):
        jpg = self.touch("backgrounds/x.jpg")
        self.touch("backgrounds/x.png")
        self.assertEqual(packs.Pack(self.root, "x").path("background"), jpg)

    def test_the_pack_comes_before_the_flat_name(self):
        inside = self.touch("packs/x/assets/background.png")
        self.touch("backgrounds/x.jpg")
        self.assertEqual(packs.Pack(self.root, "x").path("background"), inside)

    def test_a_text_entry_does_not_grow_suffixes(self):
        """只有图片这么放宽：`persona.md` 不会因为有个 `persona.png` 就被认走。"""
        self.touch("packs/x/persona.png")
        self.assertIsNone(packs.Pack(self.root, "x").path("persona"))


class TestTheSpecialSettings(unittest.TestCase):
    """「夹子」和「档位声线」是某一种人设才有的东西，三份文件互不牵连。

    没有哪一份就等于没有那件事：规则不生效、提示词里不出现那一段，也不报错。
    不能因为某位角色写了夹子，就给别的角色配上一套通用写法。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pack = packs.Pack(self.root, "someone")

    def test_a_character_without_them_is_simply_without(self):
        self.assertFalse(packs.tease_rule(self.pack).on)
        self.assertEqual(packs.tier_lines(self.pack), {})
        self.assertEqual(self.pack.text("tease-md"), "")

    def test_the_rule_and_the_voice_are_written_separately(self):
        write(
            self.root,
            "packs/someone/voice/tease.toml",
            '[tease]\nmarks = ["♡"]\nwindow = 4\nhint = "这一句可以夹"\n',
        )
        self.assertTrue(packs.tease_rule(self.pack).on)
        self.assertEqual(packs.tier_lines(self.pack), {}, "写夹子不会连带出档位声线")

        write(
            self.root,
            "packs/someone/voice/tiers.toml",
            '[tiers]\ntrust = "信任：{name}"\n',
        )
        self.assertEqual(packs.tier_lines(self.pack), {"trust": "信任：{name}"})
        self.assertEqual(packs.tease_rule(self.pack).window, 4, "写声线不会改掉夹子的额度")

    def test_the_wording_is_its_own_file_too(self):
        """`tease.md` 是人设段落，`tease.toml` 是计数规则：可以只写其中一份。"""
        write(self.root, "packs/someone/voice/tease.md", "【夹子】偶尔夹一句。\n")
        self.assertIn("偶尔夹一句", self.pack.text("tease-md"))
        self.assertFalse(packs.tease_rule(self.pack).on, "只有话术没有规则，计数不启动")
