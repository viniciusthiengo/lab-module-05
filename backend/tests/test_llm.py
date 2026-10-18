from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.llm.anthropic_client import AnthropicClient, web_search_tool
from app.llm.base import LLMError, Message, Source, Usage, cost_usd

URL = "https://example.org/batteries"


def text(value: str, *citations: tuple[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        type="text",
        text=value,
        citations=[
            SimpleNamespace(type="web_search_result_location", url=url, title=title, cited_text="")
            for url, title in citations
        ]
        or None,
    )


def message(
    *content: SimpleNamespace, stop_reason: str = "end_turn", searches: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        content=list(content),
        stop_reason=stop_reason,
        model="claude-haiku-4-5-20251001",
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=0,
            server_tool_use=SimpleNamespace(web_search_requests=searches) if searches else None,
        ),
    )


class FakeStream:
    def __init__(self, reply: SimpleNamespace) -> None:
        self._reply = reply

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @property
    def text_stream(self) -> Iterator[str]:
        for block in self._reply.content:
            if block.type == "text":
                yield from (block.text[i : i + 5] for i in range(0, len(block.text), 5))

    def get_final_message(self) -> SimpleNamespace:
        return self._reply


class FakeSDK:
    """Stands in for ``anthropic.Anthropic``: records each request and streams the next
    reply, or raises ``error``."""

    def __init__(self, *replies: SimpleNamespace, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(stream=self._stream)
        self._replies = list(replies)
        self._error = error

    def _stream(self, **params: Any) -> FakeStream:
        self.calls.append({**params, "messages": list(params["messages"])})
        if self._error is not None:
            raise self._error
        return FakeStream(self._replies.pop(0))


def complete(client: AnthropicClient, **overrides: Any):
    params: dict[str, Any] = {
        "system": "You are the Writer.",
        "messages": [Message("user", "Write it.")],
        **overrides,
    }
    return client.complete(**params)


class TestAnthropicAdapter:
    def test_streams_the_answer_and_reports_usage(self) -> None:
        sdk = FakeSDK(message(text("Solid-state batteries are safer.")))
        streamed: list[str] = []

        completion = complete(
            AnthropicClient("k", thinking="disabled", client=sdk),
            max_tokens=2048,
            on_text=streamed.append,
        )

        assert "".join(streamed) == "Solid-state batteries are safer."
        assert len(streamed) > 1
        assert completion.text == "Solid-state batteries are safer."
        assert (completion.usage, completion.model) == (
            Usage(1000, 200),
            "claude-haiku-4-5-20251001",
        )
        params = sdk.calls[0]
        assert (params["model"], params["max_tokens"], params["system"]) == (
            "claude-haiku-4-5",
            2048,
            "You are the Writer.",
        )
        assert params["thinking"] == {"type": "disabled"}
        assert params["messages"] == [{"role": "user", "content": "Write it."}]
        assert "tools" not in params

    @pytest.mark.parametrize(
        ("model", "version"),
        [
            ("claude-haiku-4-5", "web_search_20250305"),
            ("claude-sonnet-5", "web_search_20260209"),
            ("claude-opus-5", "web_search_20260209"),
        ],
    )
    def test_the_web_search_tool_follows_the_model(self, model: str, version: str) -> None:
        assert web_search_tool(model, 3) == {"type": version, "name": "web_search", "max_uses": 3}

    def test_cited_pages_become_markers_and_sources(self) -> None:
        reply = message(
            text("Let me search. "),
            SimpleNamespace(type="server_tool_use", name="web_search", input={"query": "x"}),
            SimpleNamespace(type="web_search_tool_result", content=[]),
            text("They use a solid electrolyte.", (URL, "Battery basics")),
            text(" Lithium-ion uses a liquid one.", (URL, "Battery basics"), ("https://b.org", "")),
            searches=2,
        )
        sdk = FakeSDK(reply)

        completion = complete(AnthropicClient("k", client=sdk), web_searches=3)

        assert sdk.calls[0]["tools"] == [web_search_tool("claude-haiku-4-5", 3)]
        # The words before the search end their own paragraph; the run numbers the sources.
        assert completion.text == (
            "Let me search.\n\nThey use a solid electrolyte. [cite:1] Lithium-ion uses a liquid"
            " one. [cite:1] [cite:2]"
        )
        assert completion.sources == (
            Source(URL, "Battery basics"),
            Source("https://b.org", "https://b.org"),
        )
        assert completion.usage.web_searches == 2

    def test_a_paused_search_turn_is_resumed(self) -> None:
        paused = message(text("Searching. "), stop_reason="pause_turn", searches=1)
        sdk = FakeSDK(paused, message(text("Done.", (URL, "Basics")), searches=1))

        completion = complete(AnthropicClient("k", client=sdk), web_searches=3)

        assert len(sdk.calls) == 2
        resumed = sdk.calls[1]["messages"]
        assert resumed[-1] == {"role": "assistant", "content": paused.content}
        assert completion.text == "Searching.\n\nDone. [cite:1]"
        assert completion.usage == Usage(2000, 400, web_searches=2)
        assert completion.stop_reason == "end_turn"

    def test_a_cut_answer_is_returned_as_truncated(self) -> None:
        sdk = FakeSDK(message(text("Half an ans"), stop_reason="max_tokens"))

        assert complete(AnthropicClient("k", client=sdk)).truncated

    def test_a_refusal_is_a_billed_error(self) -> None:
        sdk = FakeSDK(message(text(""), stop_reason="refusal"))

        with pytest.raises(LLMError, match="declined") as raised:
            complete(AnthropicClient("k", client=sdk))
        assert raised.value.usage == Usage(1000, 200)

    def test_an_error_of_the_text_callback_stops_the_call(self) -> None:
        class Stop(Exception):
            pass

        def stop(_: str) -> None:
            raise Stop

        sdk = FakeSDK(message(text("Some text")))

        with pytest.raises(Stop):
            complete(AnthropicClient("k", client=sdk), on_text=stop)

    @pytest.mark.parametrize(
        ("error", "status", "retryable", "words"),
        [
            (anthropic.AuthenticationError, 401, False, "rejected the API key"),
            (anthropic.RateLimitError, 429, True, "busy"),
            (anthropic.InternalServerError, 500, True, "HTTP 500"),
            (anthropic.BadRequestError, 400, False, "HTTP 400"),
        ],
    )
    def test_provider_errors(
        self, error: type[anthropic.APIStatusError], status: int, retryable: bool, words: str
    ) -> None:
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        failure = error("x", response=httpx2.Response(status, request=request), body=None)

        with pytest.raises(LLMError, match=words) as raised:
            complete(AnthropicClient("k", client=FakeSDK(error=failure)))
        assert (raised.value.status, raised.value.retryable) == (status, retryable)

    def test_connection_errors_are_retryable(self) -> None:
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        sdk = FakeSDK(error=anthropic.APIConnectionError(request=request))

        with pytest.raises(LLMError, match="Could not reach") as raised:
            complete(AnthropicClient("k", client=sdk))
        assert raised.value.retryable


def test_costs_follow_the_model_prices_and_searches() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000)

    assert cost_usd("claude-haiku-4-5", usage) == pytest.approx(1.5)
    assert cost_usd("claude-sonnet-5", usage) == pytest.approx(3.0)
    assert cost_usd("claude-haiku-4-5-20251001", Usage(web_searches=3)) == pytest.approx(0.03)
    assert cost_usd("unknown", Usage(input_tokens=1_000_000)) == pytest.approx(5.0)
