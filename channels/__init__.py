"""The QQ side of scbot-new: a group channel that leaves the kernel alone.

Nothing in `core/` imports this package. Everything here is a caller: it reads events from a
OneBot socket, asks `App` for a turn, and sends what comes back.
"""

from .config import QQConfig

__all__ = ["QQConfig"]
