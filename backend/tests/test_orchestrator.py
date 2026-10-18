import threading

import pytest

from app.limits import DailyBudget
from app.llm.base import LLMError
from app.llm.fake import FakeLLMClient, FakeRequest
from app.memory import MemoryStore
from app.orchestrator import DeltaWriter, best_result
from app.runs import Run, RunCancelled, Step
from tests.conftest import make_run, make_team
from tests.fakes import ARTICLE, NOTES, SOURCE, ScriptedTeam, agent_of, delegate, final

RESEARCH = delegate(("researcher", "Find the key facts."), reason="Facts first.")
WRITE = delegate(("writer", "Write a short article."), reason="Turn the notes into prose.")


def kinds(run: Run) -> list[str]:
    return [event.type for event in run.events_after(0)[0]]


def calls(llm: FakeLLMClient) -> list[str]:
    return [agent_of(request) for request in llm.requests]


class TestWorkflow:
    def test_research_write_then_final(self) -> None:
        orchestrator, llm = make_team(ScriptedTeam([RESEARCH, WRITE, final("# Article")]))
        run = make_run()

        orchestrator.run(run)

        snapshot = run.snapshot()
        assert (snapshot.status, snapshot.final, snapshot.forced) == (
            "completed",
            "# Article",
            False,
        )
        assert [(s.number, s.agent, s.status) for s in snapshot.steps] == [
            (1, "researcher", "completed"),
            (2, "writer", "completed"),
        ]
        assert snapshot.steps[0].result == NOTES
        assert snapshot.steps[0].sources == (SOURCE,)
        assert [(d.iteration, d.kind, d.reason) for d in snapshot.decisions] == [
            (1, "delegate", "Facts first."),
            (2, "delegate", "Turn the notes into prose."),
            (3, "final", "Done."),
        ]
        assert calls(llm) == ["supervisor", "researcher", "supervisor", "writer", "supervisor"]
        assert snapshot.sources == (SOURCE,)
        assert set(snapshot.agents) == {"supervisor", "researcher", "writer"}
        assert snapshot.agents["supervisor"].calls == 3

    def test_events_tell_who_works_and_what_was_decided(self) -> None:
        orchestrator, _ = make_team(ScriptedTeam([RESEARCH, final()]))
        run = make_run()

        orchestrator.run(run)

        events = run.events_after(0)[0]
        assert events[0].type == "run_started"
        started = [
            (e.data["agent"], e.data.get("step")) for e in events if e.type == "agent_started"
        ]
        assert started == [("supervisor", None), ("researcher", 1), ("supervisor", None)]
        decision = next(e for e in events if e.type == "decision")
        assert decision.data["steps"] == [
            {"number": 1, "agent": "researcher", "task": "Find the key facts.", "context": []}
        ]
        streamed = "".join(
            e.data["text"] for e in events if e.type == "delta" and e.data["agent"] == "researcher"
        )
        # The live text has no markers: the citations come with the finished answer.
        assert streamed == "## Notes\n- Solid-state cells use a solid electrolyte."
        assert events[-1].type == "run_finished"
        assert [e.id for e in events] == list(range(1, len(events) + 1))

    def test_workers_get_the_task_and_the_context_the_supervisor_chose(self) -> None:
        team = ScriptedTeam(
            [
                RESEARCH,
                delegate(("researcher", "Find the costs.")),
                delegate(("writer", "Write it."), context="2"),
                final(),
            ]
        )
        orchestrator, llm = make_team(team)

        orchestrator.run(make_run())

        writer = next(r for r in llm.requests if agent_of(r) == "writer")
        assert '<step number="2" agent="researcher">' in writer.prompt
        assert '<step number="1"' not in writer.prompt
        assert writer.prompt.rstrip().endswith("<task>\nWrite it.\n</task>")
        first_research = next(r for r in llm.requests if agent_of(r) == "researcher")
        assert all(r.prompt.startswith("Today is ") for r in llm.requests)
        assert "<context>" not in first_research.prompt
        assert "You can search the web: 3 searches at most." in first_research.prompt

    def test_the_supervisor_sees_every_step_and_its_iteration(self) -> None:
        orchestrator, llm = make_team(ScriptedTeam([RESEARCH, final()]))

        orchestrator.run(make_run("Explain solid-state batteries.", max_iterations=4))

        first, second = [r for r in llm.requests if agent_of(r) == "supervisor"]
        assert "<task>\nExplain solid-state batteries.\n</task>" in first.prompt
        assert "No step has run yet." in first.prompt
        assert first.prompt.endswith("Decision 1 of 4.")
        assert '<step number="1" agent="researcher" status="completed">' in second.prompt
        # The result without its list of sources: the system writes the final one.
        assert "- Solid-state cells use a solid electrolyte. [1]\n</result>" in second.prompt
        assert SOURCE.url not in second.prompt
        assert second.prompt.endswith("Decision 2 of 4.")
        assert "researcher: Finds and summarizes information" in first.system


class TestParallelWorkers:
    def test_delegations_of_one_decision_run_at_the_same_time(self) -> None:
        barrier = threading.Barrier(2, timeout=5)

        def research(request: FakeRequest) -> str:
            barrier.wait()  # both researchers must be working at once to get past this
            return "notes"

        team = ScriptedTeam(
            [delegate(("researcher", "Topic A."), ("researcher", "Topic B.")), final()],
            results={"researcher": research},
        )
        orchestrator, _ = make_team(team)
        run = make_run()
        orchestrator.run(run)

        snapshot = run.snapshot()
        assert [(s.number, s.task, s.status) for s in snapshot.steps] == [
            (1, "Topic A.", "completed"),
            (2, "Topic B.", "completed"),
        ]
        assert snapshot.decisions[0].steps == (1, 2)

    def test_a_failed_step_is_reported_to_the_supervisor(self) -> None:
        team = ScriptedTeam(
            [delegate(("researcher", "A."), ("writer", "B.")), final()],
            failures={"writer": LLMError("The AI provider failed (HTTP 500).", retryable=True)},
        )
        orchestrator, llm = make_team(team)
        run = make_run()

        orchestrator.run(run)

        steps = run.snapshot().steps
        assert [(s.agent, s.status, s.error) for s in steps] == [
            ("researcher", "completed", None),
            ("writer", "failed", "The AI provider failed (HTTP 500)."),
        ]
        last = [r for r in llm.requests if agent_of(r) == "supervisor"][-1]
        assert '<step number="2" agent="writer" status="failed">' in last.prompt
        assert "<error>\nThe AI provider failed (HTTP 500).\n</error>" in last.prompt
        assert run.status == "completed"


class TestIterationLimit:
    def test_the_supervisor_is_forced_to_finish(self) -> None:
        team = ScriptedTeam([RESEARCH, RESEARCH, final("# Forced result")])
        orchestrator, llm = make_team(team)
        run = make_run(max_iterations=2)

        orchestrator.run(run)

        snapshot = run.snapshot()
        assert (snapshot.final, snapshot.forced) == ("# Forced result", True)
        assert "decision limit was reached" in (snapshot.note or "")
        assert [d.forced for d in snapshot.decisions] == [False, False, True]
        prompts = [r.prompt for r in llm.requests if agent_of(r) == "supervisor"]
        assert prompts[1].endswith(
            "Decision 2 of 2. This is your last decision: answer with FINAL."
        )
        assert "You have used all your decisions. Answer now with FINAL" in prompts[2]
        assert len(snapshot.steps) == 2

    def test_when_even_the_forced_answer_delegates_the_last_draft_is_final(self) -> None:
        team = ScriptedTeam([RESEARCH, WRITE])
        orchestrator, _ = make_team(team)
        run = make_run(max_iterations=2)

        orchestrator.run(run)

        snapshot = run.snapshot()
        assert (snapshot.status, snapshot.final, snapshot.forced) == ("completed", ARTICLE, True)
        assert snapshot.note == "The Supervisor did not finish, so this is the last result."
        assert "protocol_error" in kinds(run)

    def test_without_any_result_the_run_fails(self) -> None:
        team = ScriptedTeam(["I am not sure what to do."])
        orchestrator, llm = make_team(team)
        run = make_run(max_iterations=3)

        orchestrator.run(run)

        snapshot = run.snapshot()
        assert snapshot.status == "failed"
        assert snapshot.error == "The Supervisor could not produce a final result."
        # A malformed answer is sent back once; two in a row end the decisions.
        assert calls(llm) == ["supervisor", "supervisor", "supervisor"]


class TestProtocolErrors:
    def test_a_malformed_answer_is_sent_back_once(self) -> None:
        team = ScriptedTeam(["Let me think about it.", RESEARCH, final()])
        orchestrator, llm = make_team(team)
        run = make_run()

        orchestrator.run(run)

        retry = llm.requests[1]
        assert [m.role for m in retry.messages] == ["user", "assistant", "user"]
        assert retry.messages[1].content == "Let me think about it."
        assert retry.prompt.startswith(
            "That answer does not follow the format: Start with DELEGATE"
        )
        assert run.snapshot().steps[0].agent == "researcher"
        assert kinds(run).count("protocol_error") == 1


class TestStopping:
    def test_the_run_cap_ends_with_the_best_result_so_far(self) -> None:
        orchestrator, _ = make_team(ScriptedTeam([RESEARCH, WRITE, final()]))
        # A fake call costs US$ 0.015 (an unknown model has the highest price), plus US$ 0.01
        # per web search: the Supervisor and the Researcher spend the US$ 0.04.
        run = make_run(cap_usd=0.04)

        orchestrator.run(run)

        snapshot = run.snapshot()
        assert (snapshot.status, snapshot.forced) == ("completed", True)
        assert snapshot.final == (
            "## Notes\n- Solid-state cells use a solid electrolyte. [1]\n\n"
            f"## Sources\n\n1. {SOURCE.title} — {SOURCE.url}"
        )
        assert snapshot.note == (
            "This task reached its spending cap. This is the best result so far."
        )
        assert snapshot.cost_usd == pytest.approx(0.04)

    def test_the_daily_budget_stops_a_run_without_results(self) -> None:
        budget = DailyBudget(0.01)
        budget.add(0.01)
        orchestrator, llm = make_team(ScriptedTeam([RESEARCH]))
        run = make_run(budget=budget)

        orchestrator.run(run)

        assert run.snapshot().status == "failed"
        assert run.snapshot().error == (
            "Today's budget for AI calls is used up; try again tomorrow. No result was produced."
        )
        assert llm.requests == []

    def test_a_cancelled_run_stops_while_a_worker_writes(self) -> None:
        run = make_run()

        def cancel_while_writing(request: FakeRequest) -> str:
            if agent_of(request) == "researcher":
                run.cancel()
                return "notes " * 100
            return RESEARCH

        llm = FakeLLMClient(cancel_while_writing)
        orchestrator, _ = make_team()
        orchestrator._supervisor._llm = llm
        orchestrator._workers["researcher"]._llm = llm

        orchestrator.run(run)

        snapshot = run.snapshot()
        assert snapshot.status == "cancelled"
        assert snapshot.steps[0].status == "failed"

    def test_provider_failures_of_the_supervisor_fail_the_run(self) -> None:
        def failing(request: FakeRequest) -> str:
            raise LLMError("The AI provider rejected the API key.")

        orchestrator, _ = make_team()
        orchestrator._supervisor._llm = FakeLLMClient(failing)
        run = make_run()

        orchestrator.run(run)

        assert (run.status, run.snapshot().error) == (
            "failed",
            "The AI provider rejected the API key.",
        )


class TestApproval:
    def test_the_final_result_waits_for_approval(self) -> None:
        memory = MemoryStore(None)
        orchestrator, _ = make_team(ScriptedTeam([final("# Draft")]), memory=memory)
        run = make_run(require_approval=True)

        orchestrator.run(run)

        assert (run.status, run.snapshot().final) == ("awaiting_approval", "# Draft")
        assert memory.notes() == []
        assert run.events_after(0)[1] is True  # the stream can end: it waits for a human
        run.approve()
        orchestrator.remember(run)
        assert run.status == "completed"
        assert [note.task for note in memory.notes()] == ["Explain solid-state batteries."]

    def test_feedback_starts_a_new_round(self) -> None:
        def decide(request: FakeRequest) -> str:
            if "<user_feedback>" in request.prompt:
                return final("# Shorter draft")
            return final("# Long draft")

        orchestrator, llm = make_team(ScriptedTeam([decide]))
        run = make_run(require_approval=True)
        orchestrator.run(run)

        run.add_feedback("Make it shorter.", max_rounds=3)
        orchestrator.run(run)

        snapshot = run.snapshot()
        assert (snapshot.status, snapshot.final, snapshot.round) == (
            "awaiting_approval",
            "# Shorter draft",
            1,
        )
        prompt = llm.requests[-1].prompt
        assert '<rejected_result round="1">\n# Long draft\n</rejected_result>' in prompt
        assert "<user_feedback>\nMake it shorter.\n</user_feedback>" in prompt
        assert [d.round for d in snapshot.decisions] == [0, 1]


class TestMemory:
    def test_related_earlier_tasks_are_recalled_for_the_supervisor(self) -> None:
        memory = MemoryStore(None)
        memory.remember("History of solid-state batteries", "Solid-state batteries began ...")
        memory.remember("Best pasta recipes", "Carbonara, cacio e pepe ...")
        orchestrator, llm = make_team(ScriptedTeam([final()]), memory=memory)
        run = make_run("How do solid-state batteries work?")

        orchestrator.run(run)

        assert [note.task for note in run.snapshot().memories] == [
            "History of solid-state batteries"
        ]
        prompt = llm.requests[0].prompt
        assert '<note task="History of solid-state batteries">' in prompt
        assert "pasta" not in prompt
        assert memory.notes()[0].task == "How do solid-state batteries work?"

    def test_memory_can_be_turned_off_per_task(self) -> None:
        memory = MemoryStore(None)
        memory.remember("Solid-state batteries", "Notes.")
        orchestrator, llm = make_team(ScriptedTeam([final()]), memory=memory)

        orchestrator.run(make_run("Solid-state batteries?", use_memory=False, remember=False))

        assert "<earlier_tasks>" not in llm.requests[0].prompt
        assert len(memory.notes()) == 1


class TestCitations:
    def test_the_final_lists_only_the_pages_the_searches_returned(self) -> None:
        answer = (
            "# Article\n\nSolid electrolytes [1], and a figure from nowhere [5].\n\n"
            "## References\n1. Made-up page — https://made-up.example/page"
        )
        orchestrator, _ = make_team(ScriptedTeam([RESEARCH, WRITE, final(answer)]))
        run = make_run()

        orchestrator.run(run)

        snapshot = run.snapshot()
        assert snapshot.final == (
            "# Article\n\nSolid electrolytes [1], and a figure from nowhere.\n\n"
            f"## References\n\n1. {SOURCE.title} — {SOURCE.url}"
        )
        draft = snapshot.steps[1].result  # the Writer's list is replaced by the real one
        assert draft.endswith(f"[1].\n\nSources:\n- [1] {SOURCE.title} — {SOURCE.url}")
        assert "made-up" not in draft

    def test_a_worker_keeps_no_marker_it_was_not_shown(self) -> None:
        blind = delegate(("writer", "Write without notes."), context="none")
        orchestrator, _ = make_team(ScriptedTeam([RESEARCH, blind, final()]))
        run = make_run()

        orchestrator.run(run)

        assert run.snapshot().steps[1].result == (
            "# Solid-state batteries\n\nThey use a solid electrolyte."
        )

    def test_feedback_shows_the_supervisor_the_draft_in_the_runs_numbers(self) -> None:
        def decide(request: FakeRequest) -> str:
            if "No step has run yet." in request.prompt:
                return RESEARCH
            return final("Short [1]." if "<user_feedback>" in request.prompt else "Long [1].")

        orchestrator, llm = make_team(ScriptedTeam([decide]))
        run = make_run(require_approval=True)
        orchestrator.run(run)
        shown = run.snapshot().final

        run.add_feedback("Shorter.", max_rounds=3)
        orchestrator.run(run)

        assert shown == f"Long [1].\n\n## Sources\n\n1. {SOURCE.title} — {SOURCE.url}"
        prompt = llm.requests[-1].prompt
        assert '<rejected_result round="1">\nLong [1].\n</rejected_result>' in prompt
        assert SOURCE.url not in prompt
        assert run.snapshot().final.startswith("Short [1].")

    def test_the_memory_keeps_the_text_without_citations(self) -> None:
        memory = MemoryStore(None)
        orchestrator, _ = make_team(
            ScriptedTeam([RESEARCH, final("Solid electrolytes are safer [1].")]), memory=memory
        )

        orchestrator.run(make_run())

        assert memory.notes()[0].summary == "Solid electrolytes are safer."


def test_best_result_prefers_the_latest_draft() -> None:
    def step(number: int, agent: str, result: str, status: str = "completed") -> Step:
        return Step(number, agent, "t", (), 0, 1, status=status, result=result)  # type: ignore[arg-type]

    assert best_result([step(1, "researcher", "notes"), step(2, "writer", "draft")]) == "draft"
    assert best_result([step(1, "writer", "draft"), step(2, "reviewer", "ok")]) == "draft"
    assert best_result([step(1, "researcher", "notes")]) == "notes"
    assert best_result([step(1, "writer", "x", status="failed")]) is None
    assert best_result([]) is None


class TestDeltaWriter:
    def test_text_is_sent_in_chunks(self) -> None:
        now = [0.0]
        run = make_run()
        writer = DeltaWriter(run, "writer", 1, min_chars=10, max_wait=1.0, clock=lambda: now[0])

        writer("abc")
        writer("def")
        writer("ghijk")  # 11 characters: sent
        writer("l")
        now[0] = 2.0
        writer("m")  # waited long enough: sent
        writer("n")
        writer.flush()

        deltas = [e.data["text"] for e in run.events_after(0)[0] if e.type == "delta"]
        assert deltas == ["abcdefghijk", "lm", "n"]

    def test_a_cancelled_run_stops_the_writer(self) -> None:
        run = make_run()
        writer = DeltaWriter(run, "writer", 1)
        run.cancel()

        with pytest.raises(RunCancelled):
            writer("text")
