"""The supervisor pattern: the Supervisor decides, the workers work, until FINAL.

Each round is a loop of at most ``max_iterations`` decisions. A decision either delegates
(several delegations run in parallel) or ends the round with FINAL. When the decisions run
out, the Supervisor must write the final result from what it has (forced completion); when it
cannot, or the spending cap stops the run, the best result so far is the final one. With
approval, a FINAL waits for the user, whose feedback starts a new round.
"""

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

from app.agents.base import today
from app.agents.protocol import Decision, ProtocolError
from app.agents.supervisor import Situation, Supervisor
from app.agents.workers import ContextItem, Worker
from app.citations import strip_citations
from app.limits import BudgetExceededError
from app.llm.base import LLMError
from app.memory import MemoryStore
from app.runs import SUPERVISOR, Run, RunBudgetExceededError, RunCancelled, Step

log = logging.getLogger("agents")

_STOPPING = (BudgetExceededError, RunBudgetExceededError, RunCancelled)


class Orchestrator:
    def __init__(
        self,
        supervisor: Supervisor,
        workers: Mapping[str, Worker],
        memory: MemoryStore | None = None,
        *,
        max_parallel: int = 3,
    ) -> None:
        self._supervisor = supervisor
        self._workers = workers
        self._memory = memory
        self._max_parallel = max_parallel

    def run(self, run: Run) -> None:
        """Works on ``run`` until its round ends (completed, failed, cancelled or waiting for
        approval). Starts a round again after feedback."""
        try:
            if not run.snapshot().steps and run.options.use_memory and self._memory is not None:
                run.set_memories(self._memory.recall(run.task))
            final, forced, note = self._round(run)
            self._finish(run, final, forced, note)
        except RunCancelled:
            run.cancelled()
        except (BudgetExceededError, RunBudgetExceededError) as error:
            fallback = best_result(run.snapshot().steps)
            if fallback is None:
                run.fail(f"{error} No result was produced.")
            else:
                self._finish(run, fallback, True, f"{error} This is the best result so far.")
        except LLMError as error:
            log.warning("run %s: provider error: %s", run.id, error)
            run.fail(str(error))
        except Exception:
            log.exception("run %s failed", run.id)
            run.fail("The task failed unexpectedly.")

    def _round(self, run: Run) -> tuple[str, bool, str | None]:
        """The final result of a round, whether it was forced, and a note."""
        iteration = 0
        for iteration in range(1, run.options.max_iterations + 1):
            run.check_cancelled()
            decision = self._decide(run, iteration)
            if decision is None:
                break  # the Supervisor cannot follow the format: finish with what there is
            if decision.final is not None:
                run.decide_final(decision.reason, iteration, forced=False)
                return decision.final, False, None
            self._delegate(run, decision, iteration)
        return self._force(run, iteration)

    def _decide(self, run: Run, iteration: int) -> Decision | None:
        run.supervisor_started(iteration)
        writer = DeltaWriter(run, SUPERVISOR, None)
        try:
            return self._supervisor.decide(
                self._situation(run, iteration),
                meter=run,
                on_text=writer,
                on_protocol_error=run.protocol_error,
            )
        except ProtocolError as error:
            run.protocol_error(str(error))
            return None
        finally:
            writer.flush()

    def _force(self, run: Run, iteration: int) -> tuple[str, bool, str | None]:
        """Forced completion: the Supervisor must answer FINAL from the steps so far."""
        run.check_cancelled()
        run.supervisor_started(iteration, forced=True)
        writer = DeltaWriter(run, SUPERVISOR, None)
        try:
            decision = self._supervisor.finalize(
                self._situation(run, iteration), meter=run, on_text=writer
            )
        except ProtocolError as error:
            run.protocol_error(str(error))
            fallback = best_result(run.snapshot().steps)
            if fallback is None:
                raise LLMError("The Supervisor could not produce a final result.") from error
            run.decide_final("No final answer from the Supervisor.", iteration, forced=True)
            return fallback, True, "The Supervisor did not finish, so this is the last result."
        finally:
            writer.flush()
        run.decide_final(decision.reason, iteration, forced=True)
        note = "The decision limit was reached, so the Supervisor finished with what it had."
        return decision.final or "", True, note

    def _delegate(self, run: Run, decision: Decision, iteration: int) -> None:
        """Runs the delegated steps, in parallel when there are several."""
        steps = run.delegate(decision, iteration)
        if len(steps) == 1:
            self._work(run, steps[0])
            return
        errors: list[BaseException] = []
        with ThreadPoolExecutor(min(len(steps), self._max_parallel), "worker") as pool:
            for future in [pool.submit(self._work, run, step) for step in steps]:
                if (error := future.exception()) is not None:
                    errors.append(error)
        if errors:
            raise errors[0]

    def _work(self, run: Run, step: Step) -> None:
        """One step. Its failure is recorded for the Supervisor, unless the run must stop."""
        run.check_cancelled()
        worker = self._workers[step.agent]
        context = [
            ContextItem(done.number, done.agent, done.task, done.result)
            for done in run.steps(step.context)
        ]
        run.step_started(step.number)
        writer = DeltaWriter(run, step.agent, step.number)
        try:
            result = worker.run(
                step.task, context, meter=run, web_search=run.options.web_search, on_text=writer
            )
        except _STOPPING:
            run.step_failed(step.number, "Stopped before the step finished.")
            raise
        except LLMError as error:
            run.step_failed(step.number, str(error))
            return
        except Exception:
            run.step_failed(step.number, "The step failed unexpectedly.")
            raise
        writer.flush()
        run.step_finished(
            step.number,
            result.text,
            sources=result.sources,
            model=result.model,
            usage=result.usage,
            note=result.note,
        )

    def _finish(self, run: Run, final: str, forced: bool, note: str | None) -> None:
        if run.options.require_approval:
            run.await_approval(final, forced, note)
            return
        run.complete(final, forced, note)
        self.remember(run)

    def remember(self, run: Run) -> None:
        """Keeps a completed run's result for later tasks, when the run allows it."""
        if self._memory is not None and run.options.remember:
            snapshot = run.snapshot()
            if snapshot.status == "completed" and snapshot.final:
                # Source numbers belong to this run: the note keeps the text only.
                self._memory.remember(run.task, strip_citations(snapshot.final))

    def _situation(self, run: Run, iteration: int) -> Situation:
        snapshot = run.snapshot()
        return Situation(
            task=run.task,
            steps=snapshot.steps,
            iteration=iteration,
            max_iterations=run.options.max_iterations,
            memories=snapshot.memories,
            feedback=snapshot.feedback,
            today=today(),
        )


def best_result(steps: Sequence[Step]) -> str | None:
    """The result to give when the Supervisor cannot finish: the latest draft of the Writer,
    or else the latest completed result."""
    done = [step for step in steps if step.status == "completed" and step.result.strip()]
    drafts = [step for step in done if step.agent == "writer"]
    chosen = (drafts or done)[-1:] if done else []
    return chosen[0].result if chosen else None


class DeltaWriter:
    """Streams an agent's text to the run in chunks (not one event per token), and stops the
    model call when the run is cancelled."""

    def __init__(
        self,
        run: Run,
        agent: str,
        step: int | None,
        *,
        min_chars: int = 120,
        max_wait: float = 0.15,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run = run
        self._agent = agent
        self._step = step
        self._min_chars = min_chars
        self._max_wait = max_wait
        self._clock = clock
        self._buffer: list[str] = []
        self._size = 0
        self._last = clock()
        self._lock = threading.Lock()

    def __call__(self, text: str) -> None:
        self._run.check_cancelled()
        with self._lock:
            self._buffer.append(text)
            self._size += len(text)
            due = self._size >= self._min_chars or self._clock() - self._last >= self._max_wait
        if due:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            text = "".join(self._buffer)
            self._buffer, self._size, self._last = [], 0, self._clock()
        if text:
            self._run.delta(self._agent, self._step, text)
