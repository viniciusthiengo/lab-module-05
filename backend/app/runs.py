"""A run: one task going through the team, with its state and its event log.

The agents' threads change a run only through its methods, under its lock. Every change
appends an event (what the live activity feed shows) and bumps the version; the API reads
consistent snapshots and streams the events. A run numbers the pages its workers cite (see
``app.citations``): worker results and the final result carry the run's numbers.
"""

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from app.agents.protocol import Decision
from app.citations import SourceList, markers
from app.limits import DailyBudget
from app.llm.base import Source, Usage, cost_usd
from app.memory import MemoryNote

RunStatus = Literal["running", "awaiting_approval", "completed", "failed", "cancelled"]
SUPERVISOR = "supervisor"


class RunCancelled(Exception):
    """The user cancelled the run."""


class RunBudgetExceededError(Exception):
    """The run spent its cap."""

    def __str__(self) -> str:
        return "This task reached its spending cap."


class RunNotFoundError(Exception):
    """No run has this id, or it expired."""


class InvalidActionError(Exception):
    """The action is not possible in the run's status."""


@dataclass(frozen=True)
class RunOptions:
    max_iterations: int = 5
    web_search: bool = True
    require_approval: bool = False
    use_memory: bool = True
    remember: bool = True


@dataclass(frozen=True)
class Event:
    """Something that happened in a run; ``id`` grows by one with each event."""

    id: int
    type: str
    at: float
    data: dict[str, Any]


@dataclass
class Step:
    """One delegation: a worker doing one task."""

    number: int
    agent: str
    task: str
    context: tuple[int, ...]  # the earlier steps the worker was given
    round: int
    iteration: int
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    result: str = ""  # with the run's source numbers
    sources: tuple[Source, ...] = ()  # the pages this step's searches cited
    note: str | None = None
    error: str | None = None
    model: str | None = None
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None


@dataclass(frozen=True)
class DecisionRecord:
    """A decision of the Supervisor: the steps it delegated, or the end."""

    round: int
    iteration: int
    kind: Literal["delegate", "final"]
    reason: str
    steps: tuple[int, ...]
    forced: bool
    at: float


@dataclass(frozen=True)
class Feedback:
    """A result the user sent back, and what they asked for."""

    round: int  # the round it starts
    draft: str  # with the run's source numbers
    text: str


@dataclass(frozen=True)
class AgentUsage:
    calls: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0


@dataclass(frozen=True)
class RunSnapshot:
    """A consistent copy of a run's state."""

    id: str
    task: str
    options: RunOptions
    status: RunStatus
    round: int
    iteration: int
    steps: tuple[Step, ...]
    decisions: tuple[DecisionRecord, ...]
    memories: tuple[MemoryNote, ...]
    feedback: tuple[Feedback, ...]
    final: str | None
    sources: tuple[Source, ...]  # the pages the workers cited, numbered from 1
    forced: bool
    note: str | None
    error: str | None
    agents: dict[str, AgentUsage]
    cost_usd: float
    cap_usd: float
    created_at: float
    finished_at: float | None
    version: int
    last_event_id: int

    @property
    def usage(self) -> Usage:
        total = Usage()
        for agent in self.agents.values():
            total = total + agent.usage
        return total


class Run:
    """One task and everything that happened to it."""

    def __init__(
        self,
        task: str,
        options: RunOptions,
        *,
        cap_usd: float,
        budget: DailyBudget,
        run_id: str | None = None,
    ) -> None:
        self.id = run_id or secrets.token_urlsafe(9)
        self.task = task
        self.options = options
        self._cap_usd = cap_usd
        self._budget = budget
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._events: list[Event] = []
        self._status: RunStatus = "running"
        self._round = 0
        self._iteration = 0
        self._steps: list[Step] = []
        self._decisions: list[DecisionRecord] = []
        self._memories: tuple[MemoryNote, ...] = ()
        self._feedback: list[Feedback] = []
        self._citations = SourceList()
        self._draft: str | None = None  # the final result with the run's source numbers
        self._final: str | None = None
        self._forced = False
        self._note: str | None = None
        self._error: str | None = None
        self._agents: dict[str, AgentUsage] = {}
        self._cost = 0.0
        self._created_at = time.time()
        self._finished_at: float | None = None
        self._version = 0
        self._emit("run_started", task=task, options=_options(options), cap_usd=cap_usd)

    # --- Reading ------------------------------------------------------------------------

    @property
    def status(self) -> RunStatus:
        with self._lock:
            return self._status

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def finished_at(self) -> float | None:
        with self._lock:
            return self._finished_at

    def snapshot(self) -> RunSnapshot:
        with self._lock:
            return RunSnapshot(
                id=self.id,
                task=self.task,
                options=self.options,
                status=self._status,
                round=self._round,
                iteration=self._iteration,
                steps=tuple(replace(step) for step in self._steps),
                decisions=tuple(self._decisions),
                memories=self._memories,
                feedback=tuple(self._feedback),
                final=self._final,
                sources=self._citations.sources,
                forced=self._forced,
                note=self._note,
                error=self._error,
                agents=dict(self._agents),
                cost_usd=self._cost,
                cap_usd=self._cap_usd,
                created_at=self._created_at,
                finished_at=self._finished_at,
                version=self._version,
                last_event_id=len(self._events),
            )

    def events_after(self, event_id: int) -> tuple[list[Event], bool]:
        """The events after ``event_id``, and whether the run has stopped (it waits for a
        human or ended), so that no event follows until something resumes it."""
        with self._lock:
            return self._events[event_id:], self._status != "running"

    def steps(self, numbers: tuple[int, ...] | None = None) -> list[Step]:
        """Copies of the completed steps (all of them, or those numbered ``numbers``)."""
        with self._lock:
            done = [replace(step) for step in self._steps if step.status == "completed"]
        return done if numbers is None else [step for step in done if step.number in numbers]

    # --- Cancellation and spending ------------------------------------------------------

    def cancel(self) -> None:
        """Stops the run: at once when it waits for approval, at the next check (between
        steps, or while a model writes) when it runs."""
        with self._lock:
            if self._status not in ("running", "awaiting_approval"):
                raise InvalidActionError("The task has already ended.")
            self._cancel.set()
            if self._status == "awaiting_approval":
                self._finish("cancelled")

    def check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise RunCancelled

    def ensure_budget(self) -> None:
        """Before a model call: today's budget and the run's cap must allow it."""
        self._budget.ensure_available()
        with self._lock:
            if self._cost >= self._cap_usd:
                raise RunBudgetExceededError

    def charge(self, agent: str, model: str, usage: Usage) -> float:
        """After a model call (billed even when its answer is unusable)."""
        cost = cost_usd(model, usage)
        self._budget.add(cost)
        with self._lock:
            spent = self._agents.get(agent, AgentUsage())
            self._agents[agent] = AgentUsage(
                spent.calls + 1, spent.usage + usage, spent.cost_usd + cost
            )
            self._cost += cost
            self._emit("usage", agent=agent, cost_usd=round(self._cost, 6))
        return cost

    # --- Changes ------------------------------------------------------------------------

    def set_memories(self, notes: tuple[MemoryNote, ...]) -> None:
        with self._lock:
            self._memories = notes
            self._emit("memory", notes=[{"id": n.id, "task": n.task} for n in notes])

    def supervisor_started(self, iteration: int, forced: bool = False) -> None:
        with self._lock:
            self._iteration = iteration
            self._emit("agent_started", agent=SUPERVISOR, iteration=iteration, forced=forced)

    def delta(self, agent: str, step: int | None, text: str) -> None:
        with self._lock:
            self._emit("delta", agent=agent, step=step, text=text)

    def protocol_error(self, message: str) -> None:
        with self._lock:
            self._emit("protocol_error", agent=SUPERVISOR, message=message)

    def delegate(self, decision: Decision, iteration: int) -> list[Step]:
        """Records a delegating decision and its steps (pending)."""
        with self._lock:
            done = {step.number for step in self._steps if step.status == "completed"}
            steps = []
            for delegation in decision.delegations:
                wanted = delegation.context
                context = tuple(sorted(done if wanted is None else done & set(wanted)))
                step = Step(
                    number=len(self._steps) + 1,
                    agent=delegation.agent,
                    task=delegation.task,
                    context=context,
                    round=self._round,
                    iteration=iteration,
                )
                self._steps.append(step)
                steps.append(replace(step))
            numbers = tuple(step.number for step in steps)
            self._decisions.append(
                DecisionRecord(
                    self._round, iteration, "delegate", decision.reason, numbers, False, time.time()
                )
            )
            self._emit(
                "decision",
                kind="delegate",
                iteration=iteration,
                reason=decision.reason,
                steps=[
                    {
                        "number": s.number,
                        "agent": s.agent,
                        "task": s.task,
                        "context": list(s.context),
                    }
                    for s in steps
                ],
            )
            return steps

    def decide_final(self, reason: str, iteration: int, forced: bool) -> None:
        with self._lock:
            self._decisions.append(
                DecisionRecord(self._round, iteration, "final", reason, (), forced, time.time())
            )
            self._emit("decision", kind="final", iteration=iteration, reason=reason, forced=forced)

    def step_started(self, number: int) -> None:
        with self._lock:
            step = self._step(number)
            step.status, step.started_at = "running", time.time()
            self._emit("agent_started", agent=step.agent, step=number, task=step.task)

    def step_finished(
        self,
        number: int,
        result: str,
        *,
        sources: tuple[Source, ...],
        model: str,
        usage: Usage,
        note: str | None = None,
    ) -> None:
        """Records a step's result, in the run's numbers: ``result`` cites ``sources`` with
        ``[cite:n]``, and may repeat the markers of the steps it was given."""
        with self._lock:
            step = self._step(number)
            shown = {n for done in step.context for n in markers(self._step(done).result)}
            result = self._citations.cite(result, sources, shown)
            step.status, step.finished_at = "completed", time.time()
            step.result, step.sources, step.model, step.usage, step.note = (
                result,
                sources,
                model,
                usage,
                note,
            )
            step.cost_usd = cost_usd(model, usage)
            self._emit(
                "agent_finished",
                agent=step.agent,
                step=number,
                status="completed",
                result=result,
                sources=[{"url": s.url, "title": s.title} for s in sources],
                note=note,
            )

    def step_failed(self, number: int, message: str) -> None:
        with self._lock:
            step = self._step(number)
            step.status, step.finished_at, step.error = "failed", time.time(), message
            self._emit(
                "agent_finished", agent=step.agent, step=number, status="failed", error=message
            )

    def await_approval(self, final: str, forced: bool, note: str | None = None) -> None:
        """``final`` waits for the user (it uses the run's source numbers)."""
        with self._lock:
            self._set_final(final, forced, note)
            self._status = "awaiting_approval"
            self._emit("awaiting_approval", final=self._final, forced=forced, note=note)

    def complete(self, final: str, forced: bool, note: str | None = None) -> None:
        """``final`` is the result (it uses the run's source numbers)."""
        with self._lock:
            self._set_final(final, forced, note)
            self._finish("completed")

    def approve(self) -> str:
        """Approves the final result that waits for approval; answers it."""
        with self._lock:
            if self._status != "awaiting_approval" or self._final is None:
                raise InvalidActionError("Only a result that waits for approval can be approved.")
            self._emit("approved")
            self._finish("completed")
            return self._final

    def add_feedback(self, feedback: str, max_rounds: int) -> None:
        """Sends the result back with the user's feedback: a new round starts."""
        with self._lock:
            if self._status != "awaiting_approval":
                raise InvalidActionError("Feedback is for a result that waits for approval.")
            if self._round + 1 > max_rounds:
                raise InvalidActionError(f"A task takes {max_rounds} rounds of feedback at most.")
            self._round += 1
            self._feedback.append(Feedback(self._round, self._draft or "", feedback))
            self._status, self._draft, self._final = "running", None, None
            self._forced, self._note = False, None
            self._emit("feedback", round=self._round, feedback=feedback)

    def fail(self, message: str) -> None:
        with self._lock:
            self._error = message
            self._finish("failed")

    def cancelled(self) -> None:
        with self._lock:
            if self._status == "running":
                self._finish("cancelled")

    # --- Internals (under the lock) -----------------------------------------------------

    def _step(self, number: int) -> Step:
        return self._steps[number - 1]

    def _set_final(self, final: str, forced: bool, note: str | None) -> None:
        """The user sees the final result with its own source list, numbered 1, 2, 3..."""
        self._draft, self._final = final, self._citations.final(final)
        self._forced, self._note = forced, note

    def _finish(self, status: RunStatus) -> None:
        self._status, self._finished_at = status, time.time()
        self._emit(
            "run_finished",
            status=status,
            final=self._final,
            forced=self._forced,
            note=self._note,
            error=self._error,
            cost_usd=round(self._cost, 6),
        )

    def _emit(self, event_type: str, /, **data: Any) -> None:
        self._events.append(Event(len(self._events) + 1, event_type, time.time(), data))
        self._version += 1


class RunStore:
    """The runs of this process: at most ``max_runs``; finished ones expire after
    ``ttl_seconds``."""

    def __init__(self, max_runs: int = 100, ttl_seconds: float = 6 * 3600) -> None:
        self._runs: OrderedDict[str, Run] = OrderedDict()
        self._max = max_runs
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def add(self, run: Run) -> None:
        with self._lock:
            self._expire()
            self._runs[run.id] = run
            while len(self._runs) > self._max:
                self._runs.popitem(last=False)

    def get(self, run_id: str) -> Run:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run

    def active(self) -> int:
        """Runs being worked on (not waiting for a human, not ended)."""
        with self._lock:
            return sum(1 for run in self._runs.values() if run.status == "running")

    def _expire(self) -> None:
        now = time.time()
        for run_id in [
            run_id
            for run_id, run in self._runs.items()
            if (finished := run.finished_at) is not None and now - finished > self._ttl
        ]:
            del self._runs[run_id]


def _options(options: RunOptions) -> dict[str, Any]:
    return {
        "max_iterations": options.max_iterations,
        "web_search": options.web_search,
        "require_approval": options.require_approval,
        "use_memory": options.use_memory,
        "remember": options.remember,
    }
