"""The Supervisor: reads the task and the results so far, and decides the next step.

Each decision is one model call with the whole picture rebuilt from the run: the task, the
notes recalled from earlier tasks, the user's feedback, and every step with its result. An
answer that does not follow the DELEGATE/TASK/FINAL format is sent back once with what is
wrong. When the decisions run out, ``finalize`` asks for FINAL (forced completion).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

from app.agents.base import Meter, call_model, cut, system_prompt
from app.agents.protocol import Decision, ProtocolError, parse_decision
from app.agents.workers import WorkerSpec
from app.citations import split_sources, strip_citations
from app.llm.base import LLMClient, Message, TextCallback
from app.memory import MemoryNote
from app.runs import SUPERVISOR, Feedback, Step


@dataclass(frozen=True)
class Situation:
    """What the Supervisor knows when it decides."""

    task: str
    steps: Sequence[Step]
    iteration: int
    max_iterations: int
    memories: Sequence[MemoryNote] = ()
    feedback: Sequence[Feedback] = ()
    today: str | None = None  # the date, in words


class Supervisor:
    name = SUPERVISOR

    def __init__(
        self,
        llm: LLMClient,
        workers: Sequence[WorkerSpec],
        *,
        max_delegations: int = 3,
        result_chars: int = 6_000,
        max_tokens: int = 8_192,
    ) -> None:
        self._llm = llm
        self._agents = [worker.name for worker in workers]
        self._max_delegations = max_delegations
        self._result_chars = result_chars
        self._max_tokens = max_tokens
        roster = "\n".join(f"- {worker.name}: {worker.description}" for worker in workers)
        self._system = system_prompt("supervisor").format(
            workers=roster, max_delegations=max_delegations
        )

    @property
    def model(self) -> str:
        return self._llm.model

    @property
    def system(self) -> str:
        return self._system

    def decide(
        self,
        situation: Situation,
        *,
        meter: Meter,
        on_text: TextCallback | None = None,
        on_protocol_error: Callable[[str], None] | None = None,
    ) -> Decision:
        """The next decision. A malformed answer is sent back once; a second one raises
        ``ProtocolError``."""
        messages = [Message("user", self.prompt(situation))]
        for attempt in range(2):
            completion = call_model(
                self._llm,
                meter,
                self.name,
                system=self._system,
                messages=messages,
                max_tokens=self._max_tokens,
                on_text=on_text,
            )
            try:
                return self._parse(completion.text)
            except ProtocolError as error:
                if attempt == 1:
                    raise
                if on_protocol_error is not None:
                    on_protocol_error(str(error))
                messages += [
                    Message("assistant", completion.text),
                    Message(
                        "user",
                        f"That answer does not follow the format: {error} Answer again, "
                        "starting with REASON:, then DELEGATE/TASK/CONTEXT lines or FINAL:.",
                    ),
                ]
        raise AssertionError("unreachable")

    def finalize(
        self, situation: Situation, *, meter: Meter, on_text: TextCallback | None = None
    ) -> Decision:
        """Forced completion: the final result from the steps so far. Raises
        ``ProtocolError`` when the answer is not a FINAL."""
        completion = call_model(
            self._llm,
            meter,
            self.name,
            system=self._system,
            messages=[Message("user", self.prompt(situation, forced=True))],
            max_tokens=self._max_tokens,
            on_text=on_text,
        )
        decision = self._parse(completion.text)
        if decision.final is None:
            raise ProtocolError("The Supervisor delegated again instead of finishing.")
        return decision

    def prompt(self, situation: Situation, *, forced: bool = False) -> str:
        """The user message of a decision."""
        parts = [f"Today is {situation.today}."] if situation.today else []
        parts.append(f"<task>\n{situation.task}\n</task>")
        if situation.memories:
            # Source numbers of earlier tasks would point at this task's sources: text only.
            notes = "\n".join(
                f'<note task="{escape(note.task)}">\n{strip_citations(note.summary)}\n</note>'
                for note in situation.memories
            )
            parts.append(
                "Notes from earlier tasks of this team, which may help or may not be related:\n"
                f"<earlier_tasks>\n{notes}\n</earlier_tasks>"
            )
        for feedback in situation.feedback:
            parts.append(
                f'<rejected_result round="{feedback.round}">\n'
                f"{self._cut(feedback.draft)}\n</rejected_result>\n"
                f"<user_feedback>\n{feedback.text}\n</user_feedback>"
            )
        if situation.feedback:
            parts.append(
                "The user sent the final result back with the feedback above: address it, "
                "with your workers when needed, and answer with a new FINAL."
            )
        parts.append(self._steps(situation.steps))
        if forced:
            parts.append(
                "You have used all your decisions. Answer now with FINAL: the best final "
                "result you can write from the task and the steps above."
            )
        else:
            last = situation.iteration == situation.max_iterations
            parts.append(
                f"Decision {situation.iteration} of {situation.max_iterations}."
                + (" This is your last decision: answer with FINAL." if last else "")
            )
        return "\n\n".join(parts)

    def _steps(self, steps: Sequence[Step]) -> str:
        if not steps:
            return "No step has run yet."
        blocks = []
        for step in steps:
            body = (
                f"<result>\n{self._cut(step.result)}\n</result>"
                if step.status == "completed"
                else f"<error>\n{step.error or 'The step did not complete.'}\n</error>"
            )
            blocks.append(
                f'<step number="{step.number}" agent="{step.agent}" status="{step.status}">\n'
                f"<task>\n{step.task}\n</task>\n{body}\n</step>"
            )
        return "<steps>\n" + "\n\n".join(blocks) + "\n</steps>"

    def _cut(self, result: str) -> str:
        """A result as the Supervisor reads it: without its list of sources (the system
        writes the final one), cut to ``result_chars``."""
        return cut(split_sources(result)[0], self._result_chars)

    def _parse(self, text: str) -> Decision:
        return parse_decision(text, self._agents, max_delegations=self._max_delegations)
