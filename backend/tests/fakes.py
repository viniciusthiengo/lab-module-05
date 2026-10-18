"""A scripted team for the tests: the Supervisor follows a script of decisions, and each
worker answers a canned result.

``FakeLLMClient`` calls ``ScriptedTeam`` for every request; the system prompt tells which
agent is asking. Helpers write decisions in the Supervisor's format.
"""

import threading
from collections.abc import Callable, Sequence

from app.llm.base import LLMError, Source
from app.llm.fake import FakeAnswer, FakeRequest

SOURCE = Source("https://example.org/batteries", "Battery basics")

# The Researcher's citation is marked as the adapter marks it; the Writer repeats the marker
# it was shown and, like models sometimes do, writes a list of sources of its own.
RESULTS = {
    "researcher": "## Notes\n- Solid-state cells use a solid electrolyte. [cite:1]",
    "writer": "# Solid-state batteries\n\nThey use a solid electrolyte [1].\n\n"
    "## Sources\n1. Made-up page — https://made-up.example/page",
    "reviewer": "VERDICT: APPROVE",
    "fact_checker": "1. Solid electrolyte — SUPPORTED.\n1 supported, 0 unsupported, 0 wrong.",
}

# What a run makes of them (see ``app.citations``): the citation becomes the run's [1] and
# the notes end with their sources; the Writer's list is replaced by the pages really cited.
NOTES = (
    "## Notes\n- Solid-state cells use a solid electrolyte. [1]\n\n"
    f"Sources:\n- [1] {SOURCE.title} — {SOURCE.url}"
)
ARTICLE = (
    "# Solid-state batteries\n\nThey use a solid electrolyte [1].\n\n"
    f"## Sources\n\n1. {SOURCE.title} — {SOURCE.url}"
)

Decision = str | Callable[[FakeRequest], str]


def delegate(*pairs: tuple[str, str], reason: str = "Next step.", context: str = "") -> str:
    """A delegating decision: one DELEGATE/TASK group per (worker, task)."""
    lines = [f"REASON: {reason}"]
    for agent, task in pairs:
        lines += [f"DELEGATE: {agent}", f"TASK: {task}"]
        if context:
            lines.append(f"CONTEXT: {context}")
    return "\n".join(lines)


def final(text: str = "# Final\n\nThe answer.", reason: str = "Done.") -> str:
    return f"REASON: {reason}\nFINAL:\n{text}"


def agent_of(request: FakeRequest) -> str:
    """The agent that sends ``request``, from the first line of its system prompt."""
    first = request.system.split(".", 1)[0]
    for name, title in (
        ("supervisor", "Supervisor"),
        ("researcher", "Researcher"),
        ("writer", "Writer"),
        ("reviewer", "Reviewer"),
        ("fact_checker", "Fact-checker"),
    ):
        if title in first:
            return name
    raise AssertionError(f"unknown agent: {first}")


class ScriptedTeam:
    """Responder of ``FakeLLMClient``.

    The Supervisor answers ``decisions`` in order (the last one repeats); a decision may be a
    function of the request. Workers answer ``results`` (default ``RESULTS``), which may also be
    functions of the request; the Researcher cites ``SOURCE``. ``failures`` maps a worker to
    the error its calls raise.
    """

    def __init__(
        self,
        decisions: Sequence[Decision] = (),
        *,
        results: dict[str, Decision] | None = None,
        failures: dict[str, LLMError] | None = None,
    ) -> None:
        self._decisions = list(decisions) or [final()]
        self._results = {**RESULTS, **(results or {})}
        self._failures = failures or {}
        self._next = 0
        self._lock = threading.Lock()

    def __call__(self, request: FakeRequest) -> FakeAnswer:
        agent = agent_of(request)
        if agent == "supervisor":
            with self._lock:
                decision = self._decisions[min(self._next, len(self._decisions) - 1)]
                self._next += 1
            return FakeAnswer(decision(request) if callable(decision) else decision)
        if agent in self._failures:
            raise self._failures[agent]
        result = self._results[agent]
        sources = (SOURCE,) if agent == "researcher" else ()
        searches = 1 if request.web_searches else 0
        text = result(request) if callable(result) else result
        return FakeAnswer(text, sources=sources, searches=searches)
