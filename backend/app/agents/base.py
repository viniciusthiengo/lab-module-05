"""What every agent shares: the system prompts, and model calls that are metered."""

from collections.abc import Sequence
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Protocol

from app.llm.base import Completion, LLMClient, LLMError, Message, TextCallback, Usage

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@cache
def system_prompt(name: str) -> str:
    """The system prompt ``prompts/<name>.md``."""
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


def today() -> str:
    """Today's date in words, such as "Sunday, October 18, 2026": models do not know it, and
    research is often about what is current."""
    now = datetime.now(UTC)
    return f"{now:%A}, {now:%B} {now.day}, {now.year}"


class Meter(Protocol):
    """Where model calls are checked and paid: a run."""

    def ensure_budget(self) -> None: ...

    def charge(self, agent: str, model: str, usage: Usage) -> float: ...


def call_model(
    llm: LLMClient,
    meter: Meter,
    agent: str,
    *,
    system: str,
    messages: Sequence[Message],
    max_tokens: int,
    web_searches: int = 0,
    on_text: TextCallback | None = None,
) -> Completion:
    """A model call that the budget allows, charged to ``agent`` (unusable answers too)."""
    meter.ensure_budget()
    try:
        completion = llm.complete(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            web_searches=web_searches,
            on_text=on_text,
        )
    except LLMError as error:
        if error.usage is not None:
            meter.charge(agent, error.model or llm.model, error.usage)
        raise
    meter.charge(agent, completion.model, completion.usage)
    return completion


def cut(text: str, limit: int) -> str:
    """``text`` at most ``limit`` characters long, cut at a line or a word."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut_at = max(head.rfind("\n"), head.rfind(" "))
    return (head[:cut_at] if cut_at > limit // 2 else head).rstrip() + "\n[…cut]"
