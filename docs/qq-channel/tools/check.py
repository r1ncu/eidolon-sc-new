"""装完跑一下：能不能导入、配置缺什么、角色文件齐不齐。

    python tools/check.py                      # 内核就在上一层（包目录里跑）
    python tools/check.py --kernel /srv/scbot  # 或者指过去
    python tools/check.py --ws                 # 顺便试一下 NapCat 连不连得上

只读，不改任何东西。退出码：0 = 没有阻断性问题，1 = 有。
"""

import argparse
import os
import sys
from pathlib import Path

OK = "[ok]  "
WARN = "[warn]"
BAD = "[bad] "


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 QQ 通道的安装")
    parser.add_argument("--kernel", default="", help="内核目录（有 serve.py 的那一层）")
    parser.add_argument("--ws", action="store_true", help="试着连一次 OneBot")
    options = parser.parse_args()

    kernel = Path(options.kernel).expanduser().resolve() if options.kernel else Path.cwd()
    problems = 0

    def say(mark: str, text: str) -> None:
        nonlocal problems
        if mark == BAD:
            problems += 1
        print(f"{mark} {text}")

    # ---- 1. 这是不是一个内核
    for name in ("serve.py", "app.py"):
        if not (kernel / name).is_file():
            say(BAD, f"找不到 {name} —— {kernel} 不像 scbot-new 的根目录")
            return 1
    say(OK, f"内核目录：{kernel}")

    # ---- 2. 通道在哪
    channels = kernel / "channels"
    if not (channels / "plugin_qq.py").is_file():
        say(BAD, "channels/ 没装在核心里 —— 把包里的 channels/ 整个拷到上面那个目录")
        return 1
    say(OK, f"channels/ 已安装（{len(list(channels.glob('*.py')))} 个模块）")

    sys.path.insert(0, str(kernel))
    try:
        import channels.plugin_qq  # noqa: F401
    except Exception as exc:
        say(BAD, f"import 失败：{type(exc).__name__}: {exc}")
        return 1
    say(OK, "可以 import")

    # ---- 3. 内核支持插件生命周期吗
    try:
        from core.host import Host
        from core.plugin import Plugin
    except Exception as exc:
        say(BAD, f"core 导入失败：{type(exc).__name__}: {exc}")
        return 1
    if hasattr(Plugin, "start") and hasattr(Host, "start"):
        say(OK, "内核有生命周期钩子（start/close）")
    else:
        say(BAD, "内核版本太旧：插件永远不会被启动。看 docs/06-内核补丁.md")

    # ---- 4. 配置
    env_file = kernel / ".env"
    env = _env(env_file)
    if not env_file.is_file():
        say(WARN, "没有 .env —— 用 docker compose 的 env_file 时配置可能不在这一层")
    required = {
        "QQ_ENABLED": "1 才会跑",
        "QQ_SELF_ID": "机器人自己的 QQ 号",
        "QQ_CHARACTER": "用哪位角色（characters.toml 里的 id）",
    }
    for key, why in required.items():
        value = env.get(key, "")
        if not value:
            say(BAD, f"{key} 没填 —— {why}")
        elif key == "QQ_ENABLED" and value not in ("1", "true", "yes", "on"):
            say(BAD, f"QQ_ENABLED={value} —— 要是 1 才会启动")
        else:
            say(OK, f"{key}={value}")

    ws = env.get("QQ_ONEBOT_WS") or "ws://127.0.0.1:3001"
    say(OK, f"QQ_ONEBOT_WS={ws}")

    if not env.get("QQ_GROUP_IDS") and env.get("QQ_PRIVATE", "0") not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        say(BAD, "QQ_GROUP_IDS 和 QQ_PRIVATE 都是空的 —— 不知道它该待在哪儿")

    # ---- 5. 角色文件
    character = env.get("QQ_CHARACTER", "").strip()
    if not character:
        sheets = sorted((kernel / "characters" / "sheets").glob("*.md"))
        if len(sheets) == 1:
            say(WARN, f"characters/sheets 里只有 {sheets[0].stem}"
                       f" —— QQ_CHARACTER 大概应该是 {sheets[0].stem}")
    if character:
        print()
        print("角色文件：")
        base = kernel / "characters"
        sheet = base / "sheets" / f"{character}.md"
        world = base / "world"
        files = [
            ("人设", sheet, True),
            ("群聊规则", world / f"{character}-qq.md", True),
            ("私聊规则", world / f"{character}-dm.md", False),
            ("措辞表", world / f"{character}-stage.toml", False),
            ("拒绝台词", world / f"{character}-refusals.toml", False),
        ]
        for label, path, needed in files:
            exists = path.is_file()
            if exists:
                size = path.stat().st_size
                say(OK, f"  {label:<6} {path.relative_to(kernel)}（{size} 字节）")
            elif needed:
                say(BAD, f"  {label:<6} 缺 {path.relative_to(kernel)} —— 她会用人设裸聊，不像在群里")
            else:
                say(WARN, f"  {label:<6} 缺（可选，会用中性默认）")

        if not _registered(base / "characters.toml", character):
            say(BAD, f"  characters.toml 里没有 id = \"{character}\" 这一段")

        memory = base / "memory"
        if memory.is_dir() and os.access(memory, os.W_OK):
            say(OK, "  memory/ 可写")
        else:
            say(WARN, f"  memory/ 不存在或不可写 —— 记忆文件写不出来（见 docs/05）")

    # ---- 6. OneBot 连得上吗
    if options.ws:
        print()
        print("OneBot：")
        ok = _probe(ws, env.get("QQ_ONEBOT_TOKEN", ""))
        if ok:
            say(OK, "连上了")
        else:
            say(BAD, "连不上 —— 看 docs/03-NapCat.md（十次有九次是端口或 token）")

    print()
    if problems:
        print(f"有 {problems} 个问题要先解决。")
        return 1
    print("没有阻断性问题。启动内核，然后看日志里的「QQ 通道已启动」。")
    return 0


def _env(path: Path) -> dict[str, str]:
    """A .env without a dependency: `K=V`, `#` comments, optional quotes."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _registered(path: Path, character: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return f'id = "{character}"' in text or f"id = '{character}'" in text


def _probe(ws: str, token: str) -> bool:
    import asyncio

    async def run() -> bool:
        try:
            from channels.onebot import OneBot
        except Exception:
            return False
        client = OneBot(ws, token, on_log=lambda line: None)
        task = asyncio.create_task(client.run(lambda event: None))
        try:
            for _ in range(30):
                await asyncio.sleep(0.2)
                if client.connected:
                    return True
            return False
        finally:
            await client.stop()
            task.cancel()

    try:
        return asyncio.run(run())
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
