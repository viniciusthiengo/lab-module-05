"""The worker agents: each one a specialized system prompt over a model.

A worker gets a task from the Supervisor and the results of the earlier steps the Supervisor
chose (the context), and returns its result. The Researcher and the Fact-checker may search
the web when the run allows it.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.base import Meter, call_model, cut, system_prompt, today
from app.llm.base import Completion, LLMClient, LLMError, Message, Source, TextCallback, Usage


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    title: str
    description: str  # what the Supervisor reads about the worker
    searches: bool = False  # may search the web
    max_tokens: int = 4096


WORKERS: tuple[WorkerSpec, ...] = (
    WorkerSpec(
        "researcher",
        "Researcher",
        "Finds and summarizes information: research notes with the facts, figures and "
        "viewpoints a task needs, citing web sources when it can search the web.",
        searches=True,
    ),
    WorkerSpec(
        "writer",
        "Writer",
        "Turns research notes into the polished deliverable, in Markdown, in the format, "
        "length and tone you ask for; revises a draft with review findings.",
        max_tokens=6144,
    ),
    WorkerSpec(
        "reviewer",
        "Reviewer",
        "Checks a draft against the task and the notes (scope, support, clarity, sources) "
        "and answers APPROVE or REVISE with concrete fixes.",
        max_tokens=2048,
    ),
    WorkerSpec(
        "fact_checker",
        "Fact-checker",
        "Verifies a draft's factual claims one by one against the notes, and against the "
        "web when it can search the web.",
        searches=True,
        max_tokens=3072,
    ),
)


@dataclass(frozen=True)
class ContextItem:
    """The result of an earlier step, given to a worker."""

    number: int
    agent: str
    task: str
    result: str


@dataclass(frozen=True)
class WorkerResult:
    text: str
    sources: tuple[Source, ...]
    usage: Usage
    model: str
    note: str | None = None


class Worker:
    def __init__(
        self, spec: WorkerSpec, llm: LLMClient, *, searches: int = 3, context_chars: int = 24_000
    ) -> None:
        self.spec = spec
        self._llm = llm
        self._searches = searches
        self._context_chars = context_chars

    @property
    def model(self) -> str:
        return self._llm.model

    def run(
        self,
        task: str,
        context: Sequence[ContextItem],
        *,
        meter: Meter,
        web_search: bool,
        on_text: TextCallback | None = None,
    ) -> WorkerResult:
        """Does ``task`` with ``context``. When the provider refuses the web search (it can be
        turned off for an organization), the worker does the task without it."""
        searches = self._searches if self.spec.searches and web_search else 0
        note = None
        try:
            completion = self._complete(task, context, searches, meter, on_text)
        except LLMError as error:
            if not searches or error.status != 400:
                raise
            completion = self._complete(task, context, 0, meter, on_text)
            note = "Web search was not available, so this result comes from the model alone."
        if completion.truncated:
            note = "The result reached the output limit and may end abruptly."
        return WorkerResult(
            completion.text, completion.sources, completion.usage, completion.model, note
        )

    def _complete(
        self,
        task: str,
        context: Sequence[ContextItem],
        searches: int,
        meter: Meter,
        on_text: TextCallback | None,
    ) -> Completion:
        # Only the workers that may search are told whether they can, this time.
        allowed = searches if self.spec.searches else None
        prompt = worker_prompt(task, context, allowed, self._context_chars, today=today())
        return call_model(
            self._llm,
            meter,
            self.spec.name,
            system=system_prompt(self.spec.name),
            messages=[Message("user", prompt)],
            max_tokens=self.spec.max_tokens,
            web_searches=searches,
            on_text=on_text,
        )


def worker_prompt(
    task: str,
    context: Sequence[ContextItem],
    searches: int | None,
    context_chars: int,
    *,
    today: str | None = None,
) -> str:
    """The date, the context (each result cut to its share of ``context_chars``), the task
    and, for a worker that may search, whether it can this time."""
    parts = [f"Today is {today}."] if today else []
    if context:
        share = max(2_000, context_chars // len(context))
        steps = "\n\n".join(
            f'<step number="{item.number}" agent="{item.agent}">\n'
            f"<task>\n{item.task}\n</task>\n<result>\n{cut(item.result, share)}\n</result>\n</step>"
            for item in context
        )
        parts.append(f"<context>\n{steps}\n</context>")
    parts.append(f"<task>\n{task}\n</task>")
    if searches:
        parts.append(f"You can search the web: {searches} searches at most.")
    elif searches == 0:
        parts.append("You cannot search the web for this task.")
    return "\n\n".join(parts)
