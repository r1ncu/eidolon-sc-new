"""The assembly: one store, one host, one engine per character, and nothing else.

This is the only file that knows both the kernel and the features, and it knows them only
as a list. Everything a character needs to be herself — the sheet — goes in as a persona
section; everything else is a plugin in `host`, in the order `requires` puts them:

    characters   the roster and the avatars (surface)
    recall       her facts: whole, or looked up by meaning (enrich, surface)
    stories      the scenes her words call up (judge, enrich)
    quota        how many turns an hour        (gate)
    photos       pictures and their allowance  (gate, surface)
    affinity     how she feels, carried        (judge, gate, enrich, observe, surface)
    memory       what she remembers            (enrich, observe, surface)
    gifts        draws, a backpack, reset cards (surface)
    voice        her voice, when there is one  (surface)

Turn one plugin off and the page still talks: that is the boundary the split was for.
"""

from collections import Counter
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import uuid

from core.events import KernelEvent
from core.host import Host
from core.judge import Judge, Judging
from core.llm import LLM, OpenAICompat
from core.messages import Conversation
from core.prompt import Section, Slot
from core.protocol.cues import without_cues
from core.settings import Settings
from core.store import Store
from core.turn import Engine
from plugins.affinity import TIERS, Affinity
from plugins.characters import Character, Characters
from plugins.gifts import GiftCatalogError, Gifts
from plugins.memory import Memory
from plugins.photos import Photos
from plugins.profile import Profile
from plugins.quota import Quota
from plugins.recall import DEFAULT_MODEL, OpenAIEmbeddings, Recall
from plugins.status import ServiceStatus
from plugins.stories import Stories
from plugins.voice import Voice

USER_NAME = "制作人"

HEATMAP_WEEKS = 52
"""热力图往回看多少个整周。"""

RANGE_DAYS: dict[str, int | None] = {"30d": 30, "1y": 365, "all": None}
"""每个跨度取多少天，今天算最后一天；「全部」是 None。"""


class App:
    """Everything the handlers need, built once."""

    def __init__(self, settings: Settings, llm: LLM | None = None, *, judge: Judge | None = None) -> None:
        self.settings = settings
        self.store = Store(settings.db)
        self.characters = Characters(settings.characters, config=settings.characters_config)
        chat = llm or OpenAICompat(
            base_url=settings.base_url, api_key=settings.api_key, model=settings.model
        )
        self.llm = chat
        self.affinity = Affinity(
            self.store, labels=self.characters.name_of, start=settings.affinity_start
        )
        self.memory = Memory(
            self.store, llm=chat, names=self.characters.name_of, timezone_name=settings.timezone
        )
        self.profile = Profile(self.store)
        self.photos = Photos(self.store, settings.media, per_hour=settings.photos_per_hour)
        self.voice = Voice(settings.fish_key, base_url=settings.fish_base_url or "https://api.fish.audio")
        self.quota = Quota(
            self.store,
            turns_per_hour=settings.turns_per_hour,
            # 自带密钥的那一轮不计入限额（原版：带上自己的 LLM 就不计入）。
            exempt=lambda turn: bool(turn.facts.get("quota.exempt")),
        )
        self.status = ServiceStatus(self.store)
        # 资料有两种用法（原版只有后者）：`whole` 是整份 `facts/<id>.md` 跟着人设一起进提示词、
        # 一起缓存；`vector` 是切段嵌入、每轮只带最近的几段。向量版没有嵌入服务就退回整份，
        # 因为「话照说」比「说了但少了资料」重要。
        mode = settings.recall
        embedder = _embedder(settings)
        if mode == "vector" and embedder is None:
            print(
                "向量版需要 SCCORE_EMBEDDING_BASE_URL 与 SCCORE_EMBEDDING_API_KEY，"
                "这次改用整份资料（SCCORE_RECALL=whole）"
            )
            mode = "whole"
        self.recall = Recall(
            self.store,
            settings.characters,
            mode=mode,
            embedder=embedder,
            model=settings.embedding_model or DEFAULT_MODEL,
            top=settings.recall_top,
            threshold=settings.recall_min,
            gate=settings.recall_gate == "judge",
            status=self.status,
        )
        self.gift_problem = ""
        try:
            self.gifts: Gifts | None = Gifts(
                self.store, self.affinity, characters=lambda: self.characters.roster,
                directory=settings.characters,
            )
        except GiftCatalogError as exc:
            # A deployment without a gift catalog still talks: the page shows 「礼物暂时没有
            # 开放」, which is exactly what it shows when the plugin is off.
            self.gift_problem = str(exc)
            self.gifts = None

        # 剧情原文检索：话题命中了才把那一话找出来（仿 world.py 的同事档案，确定性匹配）。
        # 整份 1.1MB 的剧情塞不进提示词，资料里也只有提炼后的一句话——要原话的时候，
        # 就按这句话说到了什么去查。没有语料库的部署它什么也不做。
        self.stories = Stories(
            settings.characters,
            on=settings.stories == "on",
        )

        # The judge reads the producer's words, not the reply, so one model is enough:
        # a second one is a cost decision, not a correctness one.
        self.judge = judge or Judging(_judge_llm(settings, chat))

        plugins = [
            self.characters,
            self.recall,
            self.stories,
            self.quota,
            self.photos,
            self.affinity,
            self.memory,
            self.profile,
            self.voice,
            self.status,
        ]
        if self.gifts is not None:
            plugins.append(self.gifts)
        # QQ 通道是唯一一个自己开线程、自己连 socket 的插件。它属于「可以整个拿走」的那一层：
        # 目录不在，`_qq_plugin` 返回 None，下面照常起一个网页版。装上它不需要改这个文件，
        # 也不需要改 serve.py——`Host` 会按 `requires` 把它排好，`start()` 会把它拉起来。
        self.qq = _qq_plugin(self)
        if self.qq is not None:
            plugins.append(self.qq)
        self.host = Host(plugins, judge=self.judge)
        self.host.migrate(self.store)

        self.engines: dict[str, Engine] = {}
        self.generation = 0
        """Bumped every time the sheets are re-read. Anything that caches a prompt alongside
        an engine (the QQ channel does, one per scene) keys on it, so an edited sheet can
        never be served from a cache built before the edit."""
        self._build_engines()
        self.user_id = self._user()

    # ---- edits on disk

    def refresh(self, *, force: bool = False) -> bool:
        """Pick up edited sheets and the roster table, without a restart.

        Cheap to call every turn: `stale()` is a handful of `stat` calls, and it returns
        False immediately when nothing changed. `force` re-reads even when the mtimes look
        the same, for an explicit reload.
        """
        if not force and not self.characters.stale():
            return False
        if not self.characters.reload():
            return False
        self._build_engines()
        self.generation += 1
        return True

    def _build_engines(self) -> None:
        chat = self.llm
        self.engines = {
            character.id: Engine(
                self.store,
                chat,
                base=_base_sections(character, self.recall),
                host=self.host,
                history_turns=self.settings.history_turns,
                voiced_by=self.settings.voiced_by,
            )
            for character in self.characters.roster
        }

    # ---- who and where

    def _user(self) -> str:
        users = self.store.query("SELECT id FROM users ORDER BY rowid LIMIT 1")
        return str(users[0]["id"]) if users else self.store.create_user(USER_NAME)

    def conversation(
        self,
        character_id: str,
        conversation_id: str | None = None,
        user_id: str | None = None,
    ) -> Conversation:
        """The producer's conversation with this character — the one asked for, else the
        latest, else a new one. Made on first use: one per pair is the whole model, and
        「新对话」 just starts another row.

        `user_id` is for a caller that is not the page: a group channel serves several
        people, each with their own history with the same character. The page passes
        nothing and gets the one producer this deployment has always had.
        """
        user = user_id or self.user_id
        if conversation_id:
            conversation = self.store.conversation(conversation_id)
            if conversation is not None and conversation.character_id == character_id:
                return conversation
        found = self.store.query(
            "SELECT id FROM conversations WHERE user_id = ? AND character_id = ? ORDER BY rowid DESC LIMIT 1",
            (user, character_id),
        )
        if found:
            conversation = self.store.conversation(str(found[0]["id"]))
            if conversation is not None:
                return conversation
        return self.store.create_conversation(user, character_id)

    def character(self, character_id: str | None = None) -> Character:
        return self.characters.character(character_id)

    # ---- what the page reads

    def call_ready(self) -> bool:
        """Whether a phone call is a thing that can actually happen on this deployment.

        One answer, used by two places that must never disagree: the page (draw the key or not)
        and her turn (may she ask him to call, or would that be a promise she cannot keep).
        Written into the persona it would be wrong the day `FISH_AUDIO_API_KEY` is filled in;
        written here it is right again on the next turn.
        """
        if self.settings.call == "off":
            return False
        if self.settings.call == "on":
            return True
        voice = getattr(self, "voice", None)
        return bool(voice is not None and getattr(voice, "ready", False))

    def state(self, character_id: str | None = None, conversation_id: str | None = None) -> dict:
        character = self.character(character_id)
        conversation = self.conversation(character.id, conversation_id)
        capabilities = self.host.capabilities()
        if self.gifts is None:
            capabilities["gifts"] = {"enabled": False, "why": self.gift_problem or "礼物没有开放"}
        # The phone key is drawn from this, not from whether a call backend answers. There
        # is no call backend in this port (see `Settings.call`), so the key would only ever
        # reply 「这一版还没有开放」 — a button whose only behaviour is an apology.
        calls = self.call_ready()
        capabilities["call"] = {
            "enabled": calls,
            "why": ""
            if calls
            else (
                "SCCORE_CALL=off：通话被手关掉了"
                if self.settings.call == "off"
                else "还没有配语音合成模型（FISH_AUDIO_API_KEY），电话打不通"
            ),
        }
        return {
            "user_id": self.user_id,
            "character": _brief(character),
            "conversation_id": conversation.id,
            "characters": [_brief(c) for c in self.characters.roster],
            "capabilities": capabilities,
            "affinity": self.affinity.read(self.user_id, character.id).as_dict(),
            "memories": self.memory.everything(self.user_id),
            "messages": [_message(m) for m in self.store.messages(conversation.id)],
        }

    def messages(self, character_id: str | None = None, conversation_id: str | None = None) -> list[dict]:
        character = self.character(character_id)
        conversation = self.conversation(character.id, conversation_id)
        return [_message(m) for m in self.store.messages(conversation.id)]

    def roster(self) -> list[dict]:
        """One line per character for the list: her last words (or his), and when.

        Three states, the way the original list shows them: never talked at all
        (「还没有对话」), a conversation opened but nothing said in it
        (「新的对话，还没有说话」, stamped with when it was opened), and the last words
        of the latest one.
        """
        import json as _json

        out = []
        for character in self.characters.roster:
            row = self.store.query(
                "SELECT m.role AS role, m.text AS text, m.segments AS segments, m.created_at AS at"
                " FROM messages m JOIN conversations c ON c.id = m.conversation_id"
                " WHERE c.user_id = ? AND c.character_id = ?"
                " ORDER BY m.seq DESC LIMIT 1",
                (self.user_id, character.id),
            )
            opened = self.store.query(
                "SELECT created_at FROM conversations WHERE user_id = ? AND character_id = ?"
                " ORDER BY rowid DESC LIMIT 1",
                (self.user_id, character.id),
            )

            preview, at, fresh = "", "", False
            if row:
                last = row[0]
                at = str(last["at"])
                if last["role"] == "user":
                    preview = "你：" + str(last["text"])[:40]
                else:
                    segments = _json.loads(last["segments"] or "[]")
                    # 原版显示她的字幕（中文），没有字幕才退回日语原文；语气提示不显示。
                    head = segments[0] if segments else []
                    preview = str(without_cues(str(head[1] or head[0] or "")))[:40]
            elif opened:
                fresh = True
                at = str(opened[0]["created_at"])
            out.append({"id": character.id, "preview": preview, "at": at, "fresh": fresh})
        # 原版 arrange()：置顶不谈（这台设备没有置顶），其余按最近往来时间降序；从没聊过的
        # （at 为空）排最后，并保持 characters 原来的顺序；时间相同的也保持原来的顺序。
        # out 本来就是 roster 顺序，sort 是稳定的，所以只按时间排就够了——同时间的自动保持原顺序。
        out.sort(key=lambda line: (1 if line["at"] else 0, line["at"] or ""), reverse=True)
        return out

    def new_conversation(self, character_id: str) -> Conversation:
        """「新对话」: another row for the same pair; the old one stays in 历史对话."""
        return self.store.create_conversation(self.user_id, character_id)

    def delete_conversation(self, conversation_id: str) -> bool:
        """「历史对话」面板里的删除：这段对话的消息一并彻底删除；当前对话不删（返回 False）。"""
        row = self.store.query(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, self.user_id)
        )
        if not row:
            return False
        latest = self.store.query(
            "SELECT id FROM conversations WHERE user_id = ? AND character_id ="
            " (SELECT character_id FROM conversations WHERE id = ?) ORDER BY rowid DESC LIMIT 1",
            (self.user_id, conversation_id),
        )
        if latest and str(latest[0]["id"]) == conversation_id:
            return False
        self.store.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        self.store.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return True

    def conversations(self, character_id: str) -> list[dict]:
        """Every conversation with this character, newest first, each with its last words —
        the 「历史对话」 panel, and the list's time stamps."""
        rows = self.store.query(
            "SELECT id, created_at FROM conversations WHERE user_id = ? AND character_id = ?"
            " ORDER BY rowid DESC",
            (self.user_id, character_id),
        )
        out = []
        for row in rows:
            id = str(row["id"])
            last = self.store.query(
                "SELECT role, text, segments FROM messages WHERE conversation_id = ? ORDER BY seq DESC LIMIT 1",
                (id,),
            )
            preview = ""
            fresh = True
            if last:
                fresh = False
                row_last = last[0]
                if row_last["role"] == "user":
                    preview = "你：" + str(row_last["text"])[:40]
                else:
                    import json as _json

                    segments = _json.loads(row_last["segments"] or "[]")
                    head = segments[0] if segments else []
                    preview = str(without_cues(str(head[1] or head[0] or "")))[:40]
            out.append(
                {"id": id, "created_at": row["created_at"], "preview": preview, "fresh": fresh}
            )
        return out

    def user_created_at(self) -> str:
        row = self.store.query("SELECT created_at FROM users WHERE id = ?", (self.user_id,))
        return str(row[0]["created_at"]) if row else ""

    # ---- 统计

    def stats(self, span: str = "all", device_timezone: str | None = None) -> dict:
        """和原版 `/api/stats` 一样的形状：认识天数、连续来访、每个钟点、过去一年的热力图、
        好感度分组，以及随跨度变化的那一半（回合数、聊得最多的角色、分时段、送礼）。

        原版数的是「还留在对话里的东西」：一回合（你说的话，按它说的时候的时刻），或者一次送礼。
        新内核的送礼记在礼物插件自己的表里，也算一次「来过」。通话还没有记录，所以通话时长恒为 0。
        """
        zone = _zone(self.profile.get(self.user_id).get("timezone") or device_timezone)
        today = datetime.now(timezone.utc).astimezone(zone).date()

        contacts, gifts = self._contacts(zone)
        per_day = Counter(at.date() for _, at in contacts)
        signed_up = self._signed_up_date(zone)
        first = self._first_records(zone)
        start = today - timedelta(days=today.weekday() + 7 * HEATMAP_WEEKS)

        def days_since(day: date) -> int:
            return max(1, (today - day).days + 1)

        days = RANGE_DAYS.get(span, None)
        since = None if days is None else today - timedelta(days=days - 1)
        within = [(c, at) for c, at in contacts if since is None or at.date() >= since]
        given_gifts = [g for g in gifts if since is None or g["at"].date() >= since]
        gifts_given = Counter(g["character_id"] for g in given_gifts)
        most = max(gifts_given.values(), default=0)
        idols = list(self.characters.roster)

        return {
            "days_known": days_since(min([signed_up, *first.values()])),
            "visits": _visits(per_day.keys(), today),
            "all_hours": _by_hour(at for _, at in contacts),
            "heatmap": {
                "from": start.isoformat(),
                "counts": [per_day[start + timedelta(days=i)] for i in range((today - start).days + 1)],
            },
            "affinity": self._tiers(idols),
            "range": {
                "span": span if span in RANGE_DAYS else "all",
                "turns": sum(1 for _, _ in within),
                "by_hour": _by_hour(at for _, at in within),
                "characters": [
                    {
                        "character_id": character.id,
                        "turns": sum(1 for c, _ in within if c == character.id),
                        "days_known": days_since(first[character.id]),
                        "call_seconds": 0,
                    }
                    for character in idols
                    if character.id in first
                ],
                "calls": {"count": 0, "seconds": 0},
                "gifts": {
                    "given": len(given_gifts),
                    "liked": sum(1 for g in given_gifts if g["liked"]),
                    "most": next(
                        (
                            {"character_id": character.id, "given": most}
                            for character in idols
                            if most and gifts_given[character.id] == most
                        ),
                        None,
                    ),
                },
            },
        }

    def _contacts(self, zone: ZoneInfo) -> tuple[list[tuple[str, datetime]], list[dict]]:
        """每一回合和每一次送礼：谁、在什么时候。按制作人所在时区算时刻。"""
        rows = self.store.query(
            "SELECT c.character_id AS character_id, m.created_at AS at"
            " FROM messages m JOIN conversations c ON c.id = m.conversation_id"
            " WHERE c.user_id = ? AND m.role = 'user' ORDER BY m.seq",
            (self.user_id,),
        )
        out = []
        for row in rows:
            at = _parse(row["at"])
            if at is None:
                continue
            out.append((str(row["character_id"]), at.astimezone(zone)))
        gifts = []
        if self.gifts is not None:
            for record in self.gifts.records(self.user_id):
                at = _parse(record["at"])
                if at is None:
                    continue
                gifts.append({**record, "at": at.astimezone(zone)})
        out.extend((str(g["character_id"]), g["at"]) for g in gifts)
        return out, gifts

    def _signed_up_date(self, zone: ZoneInfo) -> date:
        at = _parse(self.user_created_at())
        return at.astimezone(zone).date() if at else datetime.now(timezone.utc).date()

    def _first_records(self, zone: ZoneInfo) -> dict[str, date]:
        """和每位角色第一次说上话（或第一次送她礼物）的那天。"""
        out: dict[str, date] = {}
        for character_id, at in self._contacts(zone)[0]:
            day = at.date()
            if character_id not in out or day < out[character_id]:
                out[character_id] = day
        return out

    def _tiers(self, idols: Sequence) -> list[dict]:
        """好感度分组：从冷淡到亲密，每一档里的角色按偶像的顺序。"""
        tiers: dict[str, list[str]] = {key: [] for key, _, _ in TIERS}
        for character in idols:
            tier = self.affinity.standing(self.user_id, character.id).tier
            tiers.setdefault(tier, []).append(character.id)
        return [{"tier": tier, "characters": tiers[tier]} for tier, _, _ in TIERS]

    def export(self) -> dict:
        """The data package: everything that is hers to keep, as plain JSON."""
        conversations = self.store.query(
            "SELECT id, character_id, title, created_at FROM conversations WHERE user_id = ? ORDER BY rowid",
            (self.user_id,),
        )
        turns = 0
        packed = []
        for row in conversations:
            id = str(row["id"])
            messages = self.store.messages(id)
            turns += sum(1 for m in messages if m.role == "user")
            packed.append(
                {
                    "id": id,
                    "character_id": row["character_id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "messages": [_message(m) for m in messages],
                }
            )
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user": {"id": self.user_id, "name": USER_NAME, "created_at": self.user_created_at()},
            "profile": self.profile.get(self.user_id),
            "memories": self.memory.everything(self.user_id),
            "conversations": packed,
            "turns": turns,
        }

    def import_package(self, package: dict) -> dict:
        """Bring a data package back: its conversations, memories and profile, folded into
        what is already here. What the package does not name is left alone; a conversation
        whose character is not on this deployment is skipped, as upstream does."""
        if not isinstance(package, dict):
            raise ValueError("not_a_package")
        conversations = 0
        for packed in package.get("conversations") or []:
            if not isinstance(packed, dict):
                continue
            character_id = str(packed.get("character_id") or "")
            if not character_id or character_id not in {c.id for c in self.characters.roster}:
                continue
            conversation = self.store.create_conversation(self.user_id, character_id)
            for message in packed.get("messages") or []:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "")
                if role not in ("user", "assistant", "character"):
                    continue
                segments = message.get("segments") or []
                if role == "user":
                    self.store.execute(
                        "INSERT INTO messages (id, conversation_id, role, text, created_at)"
                        " VALUES (?, ?, 'user', ?, ?)",
                        (_mid(), conversation.id, str(message.get("text") or ""), _at(message.get("created_at"))),
                    )
                else:
                    pairs = [[str(s[0]), str(s[1] or "")] for s in segments if isinstance(s, (list, tuple)) and s]
                    self.store.execute(
                        "INSERT INTO messages (id, conversation_id, role, text, segments, created_at)"
                        " VALUES (?, ?, 'assistant', '', ?, ?)",
                        (_mid(), conversation.id, json.dumps(pairs, ensure_ascii=False), _at(message.get("created_at"))),
                    )
            conversations += 1
        memories = 0
        for memory in package.get("memories") or []:
            if not isinstance(memory, dict):
                continue
            text = str(memory.get("text") or "").strip()
            if not text:
                continue
            self.store.execute(
                "INSERT INTO memories (user_id, character_id, text, at) VALUES (?, ?, ?, ?)",
                (self.user_id, str(memory.get("character_id") or ""), text, _at(memory.get("at"))),
            )
            memories += 1
        profile = package.get("profile")
        if isinstance(profile, dict) and profile:
            self.profile.set(
                self.user_id,
                address=str(profile.get("address") or ""),
                gender=str(profile.get("gender") or "default"),
                month=profile.get("birth_month"),
                day=profile.get("birth_day"),
                timezone_name=str(profile.get("timezone") or ""),
            )
        return {"conversations": conversations, "memories": memories, "profile": bool(profile)}

    # ---- one turn

    async def turn(
        self,
        character_id: str | None,
        text: str,
        photos: Sequence[str] = (),
        conversation_id: str | None = None,
        own_keys: dict | None = None,
        *,
        user_id: str | None = None,
        sections: Sequence[Section] = (),
        engine: Engine | None = None,
        surface: str = "page",
        extra_facts: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[KernelEvent]:
        """One turn for one person. `user_id` and `sections` exist for the group channel:
        it serves people the page has never heard of, and it brings its own section (the
        register, the roster). Both default to the page's behaviour, unchanged.

        `extra_facts` is what the channel knows and the plugins cannot ask it: the QQ channel
        puts `affinity.loving` here, because a relationship being official is a fact about the
        person, not something the affinity table can read off a number."""
        self.refresh()
        character = self.character(character_id)
        conversation = self.conversation(character.id, conversation_id, user_id)
        engine = engine or self.engines[character.id]
        facts: dict[str, Any] = {"photos.ids": list(photos)} if photos else {}
        if extra_facts:
            facts.update(extra_facts)
        if own := _own_llm(own_keys, self.settings):
            # 自带密钥（原版 own_keys）：这一轮的对话请求用制作人自己的 key，服务商地址与
            # 模型名也照他填的来，没填的用这台部署配好的；判断模型与记忆抽取还在部署的 key 上。
            engine = replace(engine, llm=own)
            facts["own.llm"] = True
            facts["quota.exempt"] = True
        parts = self.photos.parts(photos)
        async for event in engine.turn(
            conversation.id,
            text,
            sections=sections,
            parts=parts,
            facts=facts,
            surface=surface,
        ):
            yield event

    def close(self) -> None:
        self.store.close()


def _qq_plugin(app: "App") -> Any | None:
    """The QQ channel, when this checkout has one.

    A deployment that never heard of QQ has no `channels/` directory, and that is not an
    error: the import fails, this returns `None`, and the page still talks. That is what
    "drop-in" means here — the plugin is found by being present, not by being registered.

    Constructing it opens nothing (the socket is in `start()`), so a test that builds an
    `App` to read a table does not reach for NapCat in the background.
    """
    try:
        from channels.plugin_qq import QQService
    except ImportError:
        return None
    return QQService(app)


def _zone(name: str | None) -> timezone:
    """时区：档案里的，浏览器的，都没有就用机器的。认不出来就当 UTC。

    `ZoneInfo` 在没有时区数据库的机器上会失败（Windows 上就是），这时退回 UTC 偏移，
    统计的日期仍然算得出来，只是没有本地区分。
    """
    for candidate in (name, None):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            continue
    return timezone.utc


def _parse(at: str | None) -> datetime | None:
    if not at:
        return None
    try:
        parsed = datetime.fromisoformat(str(at))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _by_hour(times) -> list[int]:
    """每个钟点各有几次，0 到 23。"""
    per_hour = Counter(at.hour for at in times)
    return [per_hour[hour] for hour in range(24)]


def _visits(days, today: date) -> dict:
    """连续来了几天：到今天为止的这一串，和最长的一串。"""
    longest = run = 0
    previous: date | None = None
    for day in sorted(d for d in days if d <= today):
        run = run + 1 if previous == day - timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    current = 0
    day = today if today in days else today - timedelta(days=1)
    while day in days:
        current += 1
        day -= timedelta(days=1)
    return {"current": current, "longest": longest}


def _base_sections(character: Character, recall: Recall) -> tuple[Section, ...]:
    """人设，和（选了整份资料时）她的资料。两者都在 `persona` 槽：对同一个角色不变，
    跟着人设一起被服务商缓存，所以整份资料的代价只是第一次的提示词长度。"""
    sections = [Section(Slot.PERSONA, character.sheet, title=character.id)]
    facts = recall.persona(character.id)
    if facts.strip():
        sections.append(Section(Slot.PERSONA, facts, title=f"{character.id}:facts"))
    return tuple(sections)


def _embedder(settings: Settings) -> OpenAIEmbeddings | None:
    """向量版要一个 OpenAI 兼容的 `/embeddings`；没配就 None，由调用方退回整份资料。"""
    if not settings.embedding_base_url or not settings.embedding_api_key:
        return None
    return OpenAIEmbeddings(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model or DEFAULT_MODEL,
    )


def _judge_llm(settings: Settings, chat: LLM) -> LLM:
    """The judge's own model when one is named, otherwise the chat model — which is the
    deployment's whole point: three variables and nothing else. A judge handed in by a
    caller (a test) is used as it is: it already knows what it is."""
    if not isinstance(chat, OpenAICompat):
        return chat
    return OpenAICompat(
        base_url=settings.judge_base_url or settings.base_url,
        api_key=settings.judge_api_key or settings.api_key,
        model=settings.judge_model or settings.model,
        temperature=0.0,
    )


def _own_llm(own_keys: object, settings: Settings) -> LLM | None:
    """The producer's own model from a turn's body, or None for none: `{"llm": {"key": "sk-…",
    "base_url": "…", "model": "…"}}`, the shape the settings page sends. What he did not
    fill in comes from the deployment; anything of the wrong type is ignored, not guessed at.
    Only `http`/`https` addresses are accepted, since the server is what would fetch them."""
    if not isinstance(own_keys, dict):
        return None
    llm = own_keys.get("llm")
    if not isinstance(llm, dict):
        return None
    key = llm.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    base_url = llm.get("base_url")
    base_url = base_url.strip() if isinstance(base_url, str) else ""
    if base_url and not base_url.startswith(("http://", "https://")):
        base_url = ""
    model = llm.get("model")
    model = model.strip() if isinstance(model, str) else ""
    return OpenAICompat(
        base_url=base_url or settings.base_url,
        api_key=key.strip(),
        model=model or settings.model,
    )


def _mid() -> str:
    return uuid.uuid4().hex[:16]


def _at(value: object) -> str:
    """A stored timestamp from a package: kept as it is when it is text, else now."""
    text = str(value or "").strip()
    return text or datetime.now(timezone.utc).isoformat(timespec="seconds")


def _brief(character: Character) -> dict:
    return {
        **character.brief(),
        "avatar_url": f"/media/avatar/{character.id}" if character.avatar else "",
        "scsp_avatar_url": f"/media/avatar/{character.id}?set=scsp" if character.scsp_avatar else "",
        "voice_id": character.voice_id,
    }


def _message(message) -> dict:  # noqa: ANN001 - core.messages.Message
    return {
        "id": message.id,
        "role": message.role,
        "text": message.text,
        "segments": [[s.ja, s.zh] for s in message.segments],
        "photo_tag": message.photo_tag,
        "photo_failed": message.photo_failed,
        "at": message.created_at,
    }
