"""The channel is a plugin you can take away.

Two halves of one promise:

* with `channels/` present it is in the host, with endpoints, without `serve.py` knowing;
* with it absent the kernel still builds an `App` and still serves turns.

Which is what "drop-in" has to mean, because the alternative — an install step that edits
`serve.py` — is the thing that makes people fork.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.llm import ScriptedLLM
from core.settings import Settings
from serve import App

SHEET = "# 人设：测试角色\n\n你是测试角色，说话简短。\n"


def settings_at(root: Path) -> Settings:
    (root / "characters").mkdir(exist_ok=True)
    (root / "characters" / "test.md").write_text(SHEET, encoding="utf-8")
    return Settings(
        base_url="http://unused",
        api_key="unused",
        model="fake",
        db=str(root / "kernel.db"),
        characters=str(root / "characters"),
        media=str(root / "media"),
        turns_per_hour=0,
    )


class TestDropIn(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_channel_is_in_the_host_when_the_package_is_there(self):
        app = App(settings_at(self.root), ScriptedLLM("x", size=1))
        self.addCleanup(app.close)
        self.assertIsNotNone(app.qq)
        self.assertIn("qq", app.host.names)
        self.assertIn(("GET", "/api/qq/status"), app.host.routes())

    def test_an_app_without_the_package_still_builds(self):
        """The import fails the way it would on a checkout that never installed it."""
        hidden = {"channels": None, "channels.plugin_qq": None, "channels.config": None}
        with mock.patch.dict(sys.modules, hidden):
            app = App(settings_at(self.root), ScriptedLLM("x", size=1))
            self.addCleanup(app.close)
            self.assertIsNone(app.qq)
            self.assertNotIn("qq", app.host.names)
            self.assertNotIn(("GET", "/api/qq/status"), app.host.routes())

    def test_a_channel_that_is_configured_off_still_answers_status(self):
        """「为什么她不理我」 must have an answer even when the answer is 「没开」."""
        with mock.patch.dict("os.environ", {"QQ_ENABLED": "0"}, clear=False):
            app = App(settings_at(self.root), ScriptedLLM("x", size=1))
            self.addCleanup(app.close)
            self.assertIsNotNone(app.qq)
            self.assertIn("QQ_ENABLED", app.qq.problem)


if __name__ == "__main__":
    unittest.main(verbosity=2)
