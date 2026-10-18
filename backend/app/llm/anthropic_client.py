"""Anthropic (Claude) adapter of ``LLMClient``, with the official Python SDK.

Every call is streamed: the text goes to the caller as it is written. With web search, the
server-side ``web_search`` tool runs on Anthropic's servers; a long search turn can stop with
``pause_turn`` and is resumed by sending the paused turn back. Each passage that cites a page
is marked ``[cite:n]`` (``citation_marker``), n counting the answer's sources.
"""

from collections.abc import Sequence
from typing import Any, Literal

import anthropic

from app.llm.base import (
    Completion,
    LLMError,
    Message,
    Source,
    TextCallback,
    Usage,
    citation_marker,
)

DEFAULT_MODEL = "claude-haiku-4-5"
# Models with the web search version that filters results with code before they reach the
# context; the others use the basic version.
_DYNAMIC_SEARCH_MODELS = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
)


def web_search_tool(model: str, max_uses: int) -> dict[str, Any]:
    """The web search tool for ``model``."""
    dynamic = model.startswith(_DYNAMIC_SEARCH_MODELS)
    version = "web_search_20260209" if dynamic else "web_search_20250305"
    return {"type": version, "name": "web_search", "max_uses": max_uses}


class AnthropicClient:
    provider = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        thinking: Literal["disabled", "adaptive"] | None = None,
        client: Any = None,
        timeout: float = 300.0,
        max_continuations: int = 3,
    ) -> None:
        """``thinking`` is sent when set; otherwise each model uses its default (Sonnet 5
        thinks by default, and thinking tokens count toward ``max_tokens``)."""
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=2
        )
        self._model = model
        self._thinking = thinking
        self._max_continuations = max_continuations

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
        params: dict[str, Any] = {"model": self._model, "max_tokens": max_tokens, "system": system}
        if self._thinking:
            params["thinking"] = {"type": self._thinking}
        if web_searches > 0:
            params["tools"] = [web_search_tool(self._model, web_searches)]
        conversation: list[dict[str, Any]] = [
            {"role": message.role, "content": message.content} for message in messages
        ]
        answer = _Answer()
        for _ in range(self._max_continuations + 1):
            message = self._stream(params, conversation, on_text)
            answer.add(message)
            if message.stop_reason != "pause_turn":
                break
            # A long server-side turn paused: sending it back resumes it.
            conversation.append({"role": "assistant", "content": message.content})
        if answer.stop_reason == "refusal":
            raise LLMError(
                "The model declined this request.", usage=answer.usage, model=answer.model
            )
        return answer.completion()

    def _stream(
        self,
        params: dict[str, Any],
        conversation: list[dict[str, Any]],
        on_text: TextCallback | None,
    ) -> Any:
        try:
            with self._client.messages.stream(**params, messages=conversation) as stream:
                for text in stream.text_stream:
                    if on_text is not None:
                        on_text(text)
                return stream.get_final_message()
        except anthropic.AuthenticationError as error:
            raise LLMError("The AI provider rejected the API key.", status=401) from error
        except anthropic.RateLimitError as error:
            raise LLMError(
                "The AI provider is busy (rate limited); try again in a minute.",
                retryable=True,
                status=429,
            ) from error
        except anthropic.APIConnectionError as error:
            raise LLMError("Could not reach the AI provider.", retryable=True) from error
        except anthropic.APIStatusError as error:
            raise LLMError(
                f"The AI provider failed (HTTP {error.status_code}).",
                retryable=error.status_code >= 500,
                status=error.status_code,
            ) from error


class _Answer:
    """The text, sources and usage of an answer made of one message or more (pauses)."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._sources: dict[str, tuple[int, Source]] = {}
        self.usage = Usage()
        self.model = ""
        self.stop_reason = "end_turn"

    def add(self, message: Any) -> None:
        self.model = message.model
        self.stop_reason = message.stop_reason or "end_turn"
        self.usage = self.usage + _usage(message.usage)
        # Text after a search (or a pause) starts a paragraph: otherwise the words before a
        # search ("I'll look up...") run into the notes that follow it.
        gap = bool(self._parts)
        for block in message.content:
            if block.type != "text":
                gap = bool(self._parts)
                continue
            markers = []
            for citation in block.citations or []:
                if getattr(citation, "type", "") == "web_search_result_location":
                    number = self._number(citation.url, citation.title)
                    if number not in markers:
                        markers.append(number)
            if gap and not self._parts[-1].endswith("\n"):
                self._parts[-1] = self._parts[-1].rstrip() + "\n\n"
            gap = False
            self._parts.append(block.text + "".join(f" {citation_marker(n)}" for n in markers))

    def _number(self, url: str, title: str | None) -> int:
        if url not in self._sources:
            self._sources[url] = (len(self._sources) + 1, Source(url, (title or url).strip()))
        return self._sources[url][0]

    def completion(self) -> Completion:
        text = "".join(self._parts).strip()
        sources = tuple(source for _, source in sorted(self._sources.values()))
        return Completion(text, self.usage, self.model, self.stop_reason, sources)


def _usage(usage: Any) -> Usage:
    server = getattr(usage, "server_tool_use", None)
    return Usage(
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
        web_searches=(server.web_search_requests or 0) if server else 0,
    )
