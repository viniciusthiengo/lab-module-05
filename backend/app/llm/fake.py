"""Offline ``LLMClient`` for tests: a function decides each answer.

The answer is streamed to ``on_text`` in small pieces, like the real adapter does (without
its ``[cite:n]`` markers, which only the finished answer has), and every request is recorded
for assertions. A responder may raise ``LLMError`` to simulate a failure.
"""

import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.llm.base import Completion, Message, Source, TextCallback, Usage

FAKE_MODEL = "fake-model"
FAKE_USAGE = Usage(1000, 400)


@dataclass(frozen=True)
class FakeRequest:
    """What an agent sent in one call."""

    system: str
    messages: tuple[Message, ...]
    max_tokens: int
    web_searches: int

    @property
    def prompt(self) -> str:
        """The last user message."""
        return next(m.content for m in reversed(self.messages) if m.role == "user")


@dataclass(frozen=True)
class FakeAnswer:
    text: str
    sources: tuple[Source, ...] = ()
    stop_reason: str = "end_turn"
    usage: Usage | None = None
    searches: int = field(default=0)


Responder = Callable[[FakeRequest], FakeAnswer | str]


class FakeLLMClient:
    provider = "fake"

    def __init__(
        self, responder: Responder, model: str = FAKE_MODEL, usage: Usage = FAKE_USAGE
    ) -> None:
        self._responder = responder
        self._model = model
        self._usage = usage
        self._lock = threading.Lock()
        self.requests: list[FakeRequest] = []

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        max_tokens: int = 4096,
        web_searches: int = 0,
        on_text: TextCallback | None = None,
    ) -> Completion:
        request = FakeRequest(system, tuple(messages), max_tokens, web_searches)
        with self._lock:
            self.requests.append(request)
        answer = self._responder(request)
        if isinstance(answer, str):
            answer = FakeAnswer(answer)
        if on_text is not None:
            streamed = re.sub(r" ?\[cite:\d+\]", "", answer.text)
            for start in range(0, len(streamed), 40):
                on_text(streamed[start : start + 40])
        usage = answer.usage or self._usage
        if answer.searches:
            usage = usage + Usage(web_searches=answer.searches)
        return Completion(answer.text, usage, self._model, answer.stop_reason, answer.sources)
