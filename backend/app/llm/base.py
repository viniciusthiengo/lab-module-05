"""Provider-neutral types of the LLM layer.

The agents only talk to ``LLMClient``: a text answer, streamed as it is written, optionally
with web searches. Adding a provider means writing one adapter.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Message:
    """One turn of a conversation."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class Usage:
    """Tokens and web searches of one or more calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_searches: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.web_searches + other.web_searches,
        )

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(frozen=True)
class Source:
    """A web page an answer cites."""

    url: str
    title: str


def citation_marker(number: int) -> str:
    """How an answer's text marks a passage that cites its ``number``-th source: ``[cite:2]``,
    which cannot be mistaken for a ``[2]`` the model wrote itself."""
    return f"[cite:{number}]"


@dataclass(frozen=True)
class Completion:
    """A model's answer. ``stop_reason`` is ``max_tokens`` when the answer was cut. The text
    marks the passages that cite a page of ``sources`` with ``citation_marker``."""

    text: str
    usage: Usage
    model: str
    stop_reason: str
    sources: tuple[Source, ...] = ()

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"


class LLMError(Exception):
    """A provider failure, with a message that can be shown to the user.

    ``usage`` and ``model`` are set when the model did answer (and the call was billed) but the
    answer is unusable; ``status`` is the HTTP status of a refused request.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        usage: Usage | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.usage = usage
        self.model = model


TextCallback = Callable[[str], None]
"""Receives the answer's text as it is written. Exceptions it raises stop the call."""


class LLMClient(Protocol):
    """What the agents need from a provider."""

    @property
    def model(self) -> str: ...

    def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        max_tokens: int = 4096,
        web_searches: int = 0,
        on_text: TextCallback | None = None,
    ) -> Completion:
        """One answer, streamed to ``on_text``. With ``web_searches`` > 0 the model may search
        the web that many times, and the answer lists the pages it cites."""
        ...


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: float
    output: float


PRICES: Mapping[str, Price] = {
    "claude-haiku-4-5": Price(1.0, 5.0),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-opus-5": Price(5.0, 25.0),
}
WEB_SEARCH_USD = 0.01  # US$ 10 per 1,000 searches
_MOST_EXPENSIVE = max(PRICES.values(), key=lambda price: price.output)


def cost_usd(model: str, usage: Usage) -> float:
    """Cost of ``usage`` on ``model`` (unknown models are priced as the most expensive).

    Cache writes cost 1.25x and cache reads 0.1x the input price; each web search costs
    US$ 0.01.
    """
    price = next((value for key, value in PRICES.items() if model.startswith(key)), _MOST_EXPENSIVE)
    tokens = (
        usage.input_tokens * price.input
        + usage.cache_write_tokens * price.input * 1.25
        + usage.cache_read_tokens * price.input * 0.1
        + usage.output_tokens * price.output
    ) / 1_000_000
    return tokens + usage.web_searches * WEB_SEARCH_USD
