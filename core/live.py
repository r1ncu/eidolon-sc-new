"""Files that can change while the process is running.

The rules of a conversation — the register, the roster, the sheet — are data files, and the
whole point of them being data is that they get edited. Restarting a Docker container to
change one sentence is not editing; it is deploying. So the turn loop reads these files
through this cache, which notices a changed mtime and picks the new text up on the next read.

Three details make it safe rather than merely convenient:

- **Settle.** A file touched half a second ago may still be open in an editor that writes as
  you type. A change is only taken once the file has been quiet for `settle` seconds, which
  costs one turn of latency and avoids reading a truncated file.
- **Keep the last good text.** A read that comes back empty is ignored, not applied. An editor
  that truncates before writing, or a `>file` redirect landing between two turns, would
  otherwise leave her with no sheet at all.
- **Stat is cheap.** The check is one `stat` per file per turn, which is nothing next to the
  two seconds a model call takes.
"""

import time
from pathlib import Path
from typing import Any

SETTLE = 0.35
"""Seconds a file must be quiet before its contents are trusted."""


class Live:
    """One file, read when it changes. `text()` never raises: a missing file is empty."""

    def __init__(self, path: str | Path, *, settle: float = SETTLE, log: Any | None = None) -> None:
        self._path = Path(path)
        self._settle = settle
        self._log = log or (lambda line: None)
        self._text = ""
        self._stamp: tuple[int, int] = (0, 0)
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    def text(self) -> str:
        self._refresh()
        return self._text

    def stamp(self) -> tuple[int, int]:
        """Enough to notice a change, and to key a cache by it."""
        self._refresh()
        return self._stamp

    def refresh(self, *, force: bool = True) -> bool:
        """Read now. `force` skips the settle window — used by an explicit reload."""
        return self._refresh(force=force)

    def _refresh(self, *, force: bool = False) -> bool:
        try:
            info = self._path.stat()
        except OSError:
            return False
        stamp = (info.st_mtime_ns, info.st_size)
        if stamp == self._stamp:
            return False
        # The settle window only guards *changes*, and only when the file is older than now:
        # a first read is how the process starts, and a future mtime (a `touch`, a clock a
        # little ahead) is a change to be taken, not one to wait out.
        age = time.time() - info.st_mtime
        if not force and self._loaded and 0 <= age < self._settle:
            return False
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return False
        if not raw.strip() and self._loaded:
            # Probably mid-write, or somebody truncated it. Keep what she already had.
            self._log(f"live: {self._path.name} 读到空内容，先保留上一版")
            return False
        self._text = raw.strip()
        self._stamp = stamp
        self._loaded = True
        self._log(f"live: {self._path.name} 已重新载入（{len(self._text)} 字）")
        return True


class Fixed:
    """Text handed in directly — a test's one-paragraph rule, not a file."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._key = (len(text), abs(hash(text)) % (1 << 62))

    def text(self) -> str:
        return self._text

    def stamp(self) -> tuple[int, int]:
        return self._key


def source(value: str | Path | None, *, settle: float = SETTLE, log: Any | None = None) -> Live | Fixed | None:
    """A path, or the text itself. A string with a newline in it is text; anything else is
    treated as a path, which is how the channel passes `characters/world/*.md`."""
    if value is None:
        return None
    if isinstance(value, Path):
        return Live(value, settle=settle, log=log)
    if "\n" in value or len(value) > 400:
        return Fixed(value)
    return Live(Path(value), settle=settle, log=log)
