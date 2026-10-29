"""The channel's own settings, read from the environment.

Deliberately separate from `core.settings`: the kernel does not know a QQ exists, and a
deployment that never turns this on should not have to read a `QQ_*` variable to understand
its configuration. Defaults are the boring ones — off, one group, no owners — because the
failure mode of a chat bot is being switched on by accident.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class QQConfig:
    enabled: bool = False
    ws: str = "ws://127.0.0.1:3001"
    token: str = ""
    self_id: str = ""
    groups: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    private: bool = False
    private_ids: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    rest_tz: str = "Asia/Tokyo"
    """她的作息按哪个时区算。默认日本（UTC+9）：她是日本人，几点睡是她自己的事，
    不是部署地的事。

    只影响「作息」这一件事（工作日 23–9、休息日 2–9 不主动开口），不影响额度、空间日历
    这些跟这边的人有关的东西——那些仍然按 `SCCORE_TIMEZONE`。"""
    holidays: tuple[str, ...] = ()
    workdays: tuple[str, ...] = ()
    """哪些日子按「节假日」算、哪些按「上班」算（`YYYY-MM-DD`，逗号分隔）。

    周末自动算休息日；这两个列表只用来纠正日历说不清的那几天：国庆、春节这类连休，
    以及调休上班的周六。它只影响**她主动开口的时段**——工作日 23:00 之后闭嘴，休息日
    可以拖到 02:00，像一个人第二天不用早起。有人 @ 她、或者私聊，任何时间都回，
    只是语气会变成「被吵醒」那一档。"""
    bots: tuple[tuple[str, str], ...] = ()
    """哪些 QQ 号是别的角色的机器人：`QQ_BOTS=3824843517=luca`（号码=角色 id，逗号分隔）。

    它们是**另一个角色**在说话，不是制作人也不是路人。标出来以后，她在这几个号说话时会
    被告知「这是〇〇的机器人」，语气按认识的人走；不标的话，一段ルカ的台词会被她当成
    一位陌生群友的寒暄来接。"""
    character: str = ""
    """谁在说话：`characters/characters.toml` 里的 id，也决定了 `characters/world/<id>-qq.md`
    和 `<id>-dm.md` 这两个文件叫什么。没有默认值——猜一个角色名字，猜错了她不会报错，
    只会用另一个人的口吻说话，那是最难发现的一种坏。"""
    merge_seconds: float = 1.2
    member_cooldown: float = 20.0
    concurrency: int = 2
    ambient: bool = False
    ambient_p: float = 1.0
    """每条群消息有多大比例会被「认真看一眼」——这是一道成本闸，不是「说不说」的骰子。

    默认 1（每条都看）：说不说由 `interests` 判断，多久看一次由 `ambient_check` 管。
    设成 0.1 的意思是「十条抽一条看」，群里消息少的时候会抽不到——那看起来就像她不理人，
    而原因只是运气。只有真的在乎每次判断的钱时才调它。"""
    ambient_check: float = 300.0
    """两次「要不要插话」的判断之间至少隔多少秒。

    判断是一次模型请求，而门槛里没有它的位置：`GAP` 管的是「她上次开口」，不是「上次
    动过这个脑子」。所以一个刚被她拒掉的话题，下一条消息还会再问一遍。这个数字是那件
    事的成本，和「说不说」无关——它只决定她多勤快地看。"""
    ambient_gap: float = 240.0
    ambient_heard: int = 8
    ambient_sticker_p: float = 0.3
    ambient_own_p: float = 0.35
    ambient_daily: int = 10
    """**每个群**一天最多主动插话几次（0 = 不限）。

    概率管不住频率：群里一分钟十条消息，5% 就是每十几条一次，一天下来一百多次。
    「不多不少」是一个**次数**，不是一个概率，所以这里按天封顶。它管的是上限——
    真正决定说不说下面那一句的，是「这个话题她有没有话接」（`channels/interests.py`）。

    **按群算，不是所有群共用一份**：一个热闹的群不该把安静的群的额度吃掉。十个群各十次
    听起来多，但每个群的「十次」仍然是一天；真嫌吵就把 `QQ_AMBIENT` 关掉或调小这个数。"""
    ambient_idle: float = 0.0
    called: bool = True
    """群里有人提到她时怎么办：@ 她、或者消息里叫了她的名字（名字表在 stage 文件的
    `names.mentions`）。开着的时候，她结合上下文自己判断要不要回——不回也是回答的一种。
    `QQ_CALLED=0` 回到旧行为：被 @ 就回，而且只看见叫她的那一句。"""
    stickers: bool = True
    sticker_dir: str = ""
    stickers_public: bool = True
    sticker_face: str = "无语"
    """群聊里那张「说不出话」的表情叫什么（`/表情标` 给的标签）。空 = 关掉这个反应。
    不是猜出来的：标签是人给的，插件不认识像素。没标就等于没这个功能，启动日志会说。"""
    sticker_face_p: float = 0.2
    """看到一句难绷的话之后，真发出去的概率。判断一次要不要花模型调用之前先掷它——
    先问再掷，等于每条消息都付一次钱来得出「不发」。"""
    shared_commands: tuple[str, ...] = ("表情",)
    """群里谁都能用的指令（`QQ_SHARED_COMMANDS`，逗号分隔）。默认只开 `/表情`：它读的是群友
    自己填的那包图，没有副作用。`/好感` 是同一类——读的是问的人自己的数字，别人的看不出来。
    其余的（`/重开` `/阶段` `/重载` `/静音` `/表情标`）永远只给主人。"""
    owner_stage: str = "close"
    owner_affinity: float = 0.0
    """做主人的好感度下限（0–100，0 = 不管）。判官按「这句话让她多难受」加减，
    但它不知道「这一位是满好感」；他连着几次提同一个要求就会被判成「在逼她」，
    分数掉到「疏远」之后，她开始用对失望的人的语气说话。这个地板在判官之后生效，
    只抬主人，不动别人。"""
    commands: str = "owner"
    unknown_age_adult: bool = False
    """角色表里没写年龄也没写过线标记时，算不算过线。默认**不算**。

    这条线是「高中生及以上」（见 `romance.SCHOOL_AGE`），不是 18 岁的民法成年。
    漏判一个高中生的代价是少一段玩法；漏判一个初中生的代价是不能出的东西出去了。
    写错一边可以改回来，另一边改不回来。"""
    owner_love: float = 0.0
    """进入「恋爱」的好感度门槛（0–100，0 = 不启用）。比「亲密」的 85 高，默认 95：
    光是很喜欢还不够，得高到没有余地。还要同时满足 `owner_love_turns`。"""
    owner_love_turns: int = 20
    """进入「恋爱」还需要说过多少次话。数字和次数两道门，缺一个都不算。"""
    owner_love_floor: float = 0.0
    """恋爱之后的好感度下限，0 = 不设地板（默认）。

    以前默认 90：恋爱里也压着，不让掉出「亲密」。现在改成没有地板——恋爱里的人也必须
    能真的不高兴，`Affinity` 的「生闷气」（低于 70 又被戳一下）才有机会发生。想恢复
    旧行为就把它设回 90。恋爱前那条 `owner_affinity` 不受影响。"""
    love_ping: bool = False
    """恋爱之后，她会不会不定时地先发消息过来（只在私聊里）。"""
    love_ping_min: float = 60.0
    love_ping_max: float = 150.0
    """两条主动消息之间的随机间隔（分钟）。真正的开关是随机：不固定时间、不固定条数。"""
    monday_hour: int = 9
    """周一几点之后发那张「上班」表情（-1 = 不发）。"""
    monday_groups: tuple[str, ...] = ()
    """这张表情发到哪几个群。空 = 所有群。要求往往来自某一个群，别的地方并不在等它。"""
    qzone: bool = False
    """要不要自己发 QQ 空间说说。默认关：这是会出现在别人好友动态里、发出去收不回来的一句话。
    开之前要先有 `characters/world/<id>-qzone.md`——没有那份写作口径，插件会拒绝发，
    而不是拿群聊的语气硬写一条。"""
    qzone_hour: int = 21
    """几点之后可以发。过了这个点才轮到骰子，不是「九点整准时发」。"""
    qzone_p: float = 0.35
    """过了点之后，今天到底写不写的概率。一天只掷一次并记下来：按分钟掷的话，35% 到早上就成了必然。"""
    qzone_right: int = 4
    """谁能看见：1 所有人、4 好友、16 部分好友、64 仅自己、128 部分好友不可见。
    默认 4——她的日记属于好友列表，不属于所有搜得到这个号的人。"""
    qzone_gap: float = 20.0
    """两条之间至少隔多少小时。只写「一天一条」的话，23:50 和 00:10 会被算成两天。"""
    qzone_web: bool = True
    """读好友动态、发评论走 QZone 的 Web 接口（用 NapCat 给的 Cookie），还是走 NapCat 上的
    QZone 扩展 action。

    默认走 Web：官方接口里根本没有读动态和评论这两件事，扩展又是要另装的东西。两条路的
    权限是同一份（都是这个号的 Cookie），但 Web 那条是逆向接口，腾讯改字段就得跟着改——
    真出问题时 `QQ_QZONE_WEB=0` 可以退回 action 探测。"""
    qzone_reply: bool = False
    """要不要回别人的说说（评论）。默认关：这是**在别人的空间里**说话，比自己的墙更难收回。

    需要 NapCat 那边有读动态和发评论的扩展（`get_qzone_msg_list` / `get_friend_feeds`、
    `send_qzone_comment` / `send_comment`）。没有的话插件会说不支持并停一天，不会刷日志。"""
    qzone_reply_hourly: int = 6
    """**滚动一小时**里最多回几条评论（0 = 不限）。

    按天算的问题：一晚上来十条，第七条要等到第二天早上，留言过夜就凉了。按小时算，
    最坏也只是等一小时。滚动窗口而不是整点重置——否则 21:59 回六条、22:00 又能回六条，
    限额等于没有。"""
    qzone_reply_gap: float = 5.0
    """两条评论之间至少隔多少分钟。

    五分钟，不是三十。三十是「回别人的动态」那一版的脾气：挨着回一串像机器在刷 feed。
    这里是别人在她自己墙上留的话——20:20 写的等到 20:50 才回，不叫克制，叫不理人。
    五分钟只够防「两条挤成一坨」。"""
    qzone_reply_age: float = 72.0
    """只回这么多小时以内的评论。评论是给她的留言，不是信息流，所以窗口比说说宽；但回上周的
    也不算聊天。0 = 不限。"""
    qzone_reply_posts: int = 3
    """看最近几条说说。第五新的那条下面的评论，已经是旧事了。"""
    qzone_reply_ignore: tuple[str, ...] = ()
    """不回这些人的评论（QQ 号，逗号分隔）。"""
    turn_timeout: float = 180.0
    """一轮对话最多等多久（秒）。没有上限的时候，一次卡住的请求会一直占着并发位，
    两个卡住就整条通道不说话——看起来就是「有时候不回，过一阵又好了」。"""
    digest: bool = True
    """把聊天写成记忆文件（`characters/memory/*.md`）。每次重写整份，旧的被新的盖掉：
    记忆是「现在还成立的」，不是日记。放在 `characters/` 底下是为了在宿主机上直接能看。"""
    digest_dm_days: float = 3.0
    """私聊多久重写一次。默认 3 天：那是一段对话，太勤会一直在磨同一件事。"""
    digest_group_days: float = 7.0
    """一个群多久重写一次。默认一周：五十个人话很多，事实很少。"""
    digest_prompt: bool = True
    """写好的记忆要不要进提示词。进，她才真的记得；不进，它就只是一份给你看的档案。"""
    world: bool = True
    meta: bool = True
    bracket: bool = True
    voice: bool = False
    admin_token: str = ""

    def bot_of(self, qq_id: str) -> str:
        """这个 QQ 号背后是哪个角色（不是机器人就返回空）。"""
        for qq, who in self.bots:
            if qq == str(qq_id):
                return who
        return ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "QQConfig":
        env = os.environ if env is None else env
        return cls(
            enabled=_flag(env, "QQ_ENABLED", False),
            ws=(env.get("QQ_ONEBOT_WS") or "ws://127.0.0.1:3001").strip(),
            token=(env.get("QQ_ONEBOT_TOKEN") or "").strip(),
            self_id=(env.get("QQ_SELF_ID") or "").strip(),
            groups=_list(env, "QQ_GROUP_IDS"),
            owners=_list(env, "QQ_OWNER_IDS"),
            private=_flag(env, "QQ_PRIVATE", False),
            private_ids=_list(env, "QQ_PRIVATE_IDS"),
            ignore=_list(env, "QQ_IGNORE_IDS"),
            bots=_pairs(env, "QQ_BOTS"),
            # 作息时区：默认日本。只写 `QQ_REST_TZ` 一件事，其余时间仍按 SCCORE_TIMEZONE。
            rest_tz=(env.get("QQ_REST_TZ", "Asia/Tokyo").strip() or "Asia/Tokyo"),
            holidays=_list(env, "QQ_HOLIDAYS"),
            workdays=_list(env, "QQ_WORKDAYS"),
            character=(env.get("QQ_CHARACTER") or "").strip(),
            merge_seconds=_float(env, "QQ_MERGE_SECONDS", 1.2),
            member_cooldown=_float(env, "QQ_MEMBER_COOLDOWN", 20.0),
            concurrency=_int(env, "QQ_GROUP_CONCURRENCY", 2),
            ambient=_flag(env, "QQ_AMBIENT", False),
            ambient_p=_float(env, "QQ_AMBIENT_P", 1.0),
            ambient_gap=_float(env, "QQ_AMBIENT_GAP", 240.0),
            ambient_heard=_int(env, "QQ_AMBIENT_HEARD", 8),
            ambient_sticker_p=_float(env, "QQ_AMBIENT_STICKER_P", 0.3),
            ambient_own_p=_float(env, "QQ_AMBIENT_OWN_P", 0.35),
            ambient_check=_float(env, "QQ_AMBIENT_CHECK", 300.0),
            ambient_daily=_int(env, "QQ_AMBIENT_DAILY", 10),
            ambient_idle=_float(env, "QQ_AMBIENT_IDLE", 0.0),
            called=_flag(env, "QQ_CALLED", True),
            stickers=_flag(env, "QQ_STICKERS", True),
            sticker_dir=(env.get("QQ_STICKER_DIR") or "").strip(),
            stickers_public=_flag(env, "QQ_STICKERS_PUBLIC", True),
            sticker_face=(env.get("QQ_STICKER_FACE", "无语") or "").strip(),
            sticker_face_p=_float(env, "QQ_STICKER_FACE_P", 0.2),
            shared_commands=_list(env, "QQ_SHARED_COMMANDS") or ("表情",),
            owner_stage=(env.get("QQ_OWNER_STAGE") or "close").strip(),
            owner_affinity=_float(env, "QQ_OWNER_AFFINITY", 0.0),
            commands=(env.get("QQ_COMMANDS") or "owner").strip().lower(),
            unknown_age_adult=_flag(env, "QQ_UNKNOWN_AGE_ADULT", False),
            owner_love=_float(env, "QQ_OWNER_LOVE", 0.0),
            owner_love_turns=_int(env, "QQ_OWNER_LOVE_TURNS", 20),
            owner_love_floor=_float(env, "QQ_OWNER_LOVE_FLOOR", 0.0),
            love_ping=_flag(env, "QQ_OWNER_LOVE_PING", False),
            love_ping_min=_float(env, "QQ_OWNER_LOVE_PING_MIN", 60.0),
            love_ping_max=_float(env, "QQ_OWNER_LOVE_PING_MAX", 150.0),
            monday_hour=_int(env, "QQ_MONDAY_HOUR", 9),
            monday_groups=_list(env, "QQ_MONDAY_GROUPS"),
            qzone=_flag(env, "QQ_QZONE", False),
            qzone_hour=_int(env, "QQ_QZONE_HOUR", 21),
            qzone_p=_float(env, "QQ_QZONE_P", 0.35),
            qzone_right=_int(env, "QQ_QZONE_RIGHT", 4),
            qzone_gap=_float(env, "QQ_QZONE_GAP", 20.0),
            qzone_web=_flag(env, "QQ_QZONE_WEB", True),
            qzone_reply=_flag(env, "QQ_QZONE_REPLY", False),
            qzone_reply_hourly=_int(env, "QQ_QZONE_REPLY_HOURLY", 6),
            qzone_reply_gap=_float(env, "QQ_QZONE_REPLY_GAP", 5.0),
            qzone_reply_age=_float(env, "QQ_QZONE_REPLY_AGE", 72.0),
            qzone_reply_posts=_int(env, "QQ_QZONE_REPLY_POSTS", 3),
            qzone_reply_ignore=_list(env, "QQ_QZONE_REPLY_IGNORE"),
            turn_timeout=_float(env, "QQ_TURN_TIMEOUT", 180.0),
            digest=_flag(env, "QQ_DIGEST", True),
            digest_dm_days=_float(env, "QQ_DIGEST_DM_DAYS", 3.0),
            digest_group_days=_float(env, "QQ_DIGEST_GROUP_DAYS", 7.0),
            digest_prompt=_flag(env, "QQ_DIGEST_PROMPT", True),
            world=_flag(env, "QQ_WORLD", True),
            meta=_flag(env, "QQ_META", True),
            bracket=_flag(env, "QQ_BRACKET", True),
            voice=_flag(env, "QQ_VOICE", False),
            admin_token=(env.get("QQ_ADMIN_TOKEN") or "").strip(),
        )

    def reason_off(self) -> str:
        """Why this channel cannot start, or `""` when it can. Said out loud at startup:
        a bot that silently does nothing is the hardest kind to debug."""
        if not self.enabled:
            return "QQ_ENABLED 不是 1"
        if not self.groups and not self.private:
            return "QQ_GROUP_IDS 和 QQ_PRIVATE 都是空的（不知道要待在哪个群、也不知道要不要接私聊）"
        if not self.self_id:
            return "QQ_SELF_ID 是空的（不知道哪个号是自己）"
        if not self.ws:
            return "QQ_ONEBOT_WS 是空的"
        if not self.character:
            return "QQ_CHARACTER 是空的（不知道用哪位角色的人格去说话）"
        return ""

    def where(self) -> str:
        """Where it sits, for the startup line: `群 123, 456 / 私聊（789）`.

        Cheap and only used once, but it is the difference between "it started" and "it
        started in the wrong room", which is the mistake that takes longest to notice.
        """
        parts: list[str] = []
        if self.groups:
            parts.append("群 " + ", ".join(self.groups))
        if self.private:
            who = self.private_ids or self.owners
            parts.append("私聊" + (f"（{', '.join(who)}）" if who else ""))
        return " / ".join(parts) or "无目标"

    def may_direct(self, qq_id: str) -> bool:
        """Whether this person is allowed to talk to her one to one.

        Private chat is opt-in and named: a bot that answers any stranger who adds it is a
        bot that answers spam, and the register it uses there is the one she only uses with
        the producer — so the list has to be short and deliberate.
        """
        if not self.private:
            return False
        allowed = self.private_ids or self.owners
        return str(qq_id) in allowed


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _pairs(env: Mapping[str, str], name: str) -> tuple[tuple[str, str], ...]:
    """`3824843517=luca, 1234=mano` → `(("3824843517", "luca"), …)`. 没有 `=` 的项丢掉：
    一个半截的映射比没有映射更糟——它会让人以为标好了。"""
    out: list[tuple[str, str]] = []
    for part in (env.get(name) or "").replace(";", ",").split(","):
        qq, _, who = part.strip().partition("=")
        qq, who = qq.strip(), who.strip()
        if qq and who:
            out.append((qq, who))
    return tuple(out)


def _list(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    raw = (env.get(name) or "").replace("，", ",")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float((env.get(name) or "").strip() or default)
    except ValueError:
        return default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(float((env.get(name) or "").strip() or default))
    except ValueError:
        return default
