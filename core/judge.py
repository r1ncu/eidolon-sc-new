"""One judge request per turn, however many plugins asked.

The judge is the model that reads "she said this" and answers a handful of small
questions: did that hurt? does she want to send a photo? is this about the user himself?
Upstream asks three in one request (ADR about the judge) for two reasons — latency and
cost — and both get worse with plugins, because each one would otherwise bring its own
call.

So the kernel owns the call and plugins only write the questions. A plugin that needs an
answer gets it in `TurnContext.facts` under its `key`, or gets nothing: a failed judge
must never fail a turn, and must never make a plugin guess.
"""

import json
import re
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from .llm import ChatMessage, LLM
from .plugin import Question

_JSON = re.compile(r"\{.*\}", re.S)


@runtime_checkable
class Judge(Protocol):
    async def ask(self, questions: Sequence[Question], *, said: str) -> dict[str, Any]: ...


class Judging:
    """A judge on top of any chat model: one prompt, one JSON object back.

    The instructions spell out every key and its range, because the alternative — asking
    the model to "answer these questions" and hoping — is how a judge starts returning
    prose that no parser survives.
    """

    def __init__(self, llm: LLM) -> None:
        self._llm = llm

    async def ask(self, questions: Sequence[Question], *, said: str) -> dict[str, Any]:
        if not questions:
            return {}
        messages = [
            ChatMessage("system", _instructions(questions)),
            ChatMessage("user", f"制作人说：{said}"),
        ]
        try:
            answer = await _text(self._llm, messages)
        except Exception:
            return {}
        return _read(answer, questions)


class ScriptedJudge:
    """A judge that answers from a dict, for tests and for running without a judge key."""

    def __init__(self, answers: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.answers = answers or {}
        self.fail = fail
        self.asked: list[list[Question]] = []

    async def ask(self, questions: Sequence[Question], *, said: str) -> dict[str, Any]:
        self.asked.append(list(questions))
        if self.fail:
            raise RuntimeError("the judge is down")
        return dict(self.answers)


def _instructions(questions: Sequence[Question]) -> str:
    lines = [
        "你在判断制作人刚说的一句话对角色有什么影响。只输出一个 JSON 对象，不要任何解释。",
        "键与取值范围：",
    ]
    for question in questions:
        if question.kind == "int":
            value = f"整数 {question.least} 到 {question.most}"
        elif question.kind == "bool":
            value = "true 或 false"
        else:
            value = "一段简短的文字"
        lines.append(f'- "{question.key}"：{question.ask}（{value}）')
    lines.append("拿不准时按最保守的值回答：bool 给 false，int 给 0。")
    return "\n".join(lines)


async def _text(llm: LLM, messages: Sequence[ChatMessage]) -> str:
    return "".join([chunk async for chunk in llm.stream(messages)])


def _read(answer: str, questions: Sequence[Question]) -> dict[str, Any]:
    """The answers that can be trusted, by key. Anything unreadable is simply absent: a
    plugin reading a key that is not there must do without, never guess."""
    found = _JSON.search(answer)
    if found is None:
        return {}
    try:
        parsed = json.loads(found.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, Any] = {}
    for question in questions:
        if question.key not in parsed:
            continue
        value = _coerce(parsed[question.key], question)
        if value is not None:
            out[question.key] = value
    return out


def _coerce(value: object, question: Question) -> Any:
    if question.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "yes", "是", "1"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"false", "no", "否", "0"}:
            return False
        if isinstance(value, (int, float)):
            return bool(value)
        return None
    if question.kind == "int":
        try:
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return max(question.least, min(question.most, number))
    return value if isinstance(value, str) else None
