"""The run state, memory, limits and settings."""

from datetime import date
from pathlib import Path

import pytest

from app.agents.protocol import Decision, Delegation
from app.config import Settings
from app.limits import BudgetExceededError, DailyBudget, RateLimitedError, RateLimiter
from app.llm.base import Source, Usage
from app.memory import MAX_NOTES, SUMMARY_CHARS, MemoryStore, words
from app.runs import InvalidActionError, RunBudgetExceededError, RunStore
from tests.conftest import make_run

RESEARCH = Decision("Facts first.", (Delegation("researcher", "Find facts.", None),))


class TestRun:
    def test_steps_get_the_completed_steps_they_ask_for(self) -> None:
        run = make_run()
        [first] = run.delegate(RESEARCH, 1)
        run.step_started(first.number)
        run.step_finished(1, "Notes.", sources=(), model="m", usage=Usage())
        decision = Decision(
            "Write.",
            (Delegation("writer", "Write.", (1, 9)), Delegation("reviewer", "Check.", ())),
        )

        writer, reviewer = run.delegate(decision, 2)

        assert (writer.number, writer.context) == (2, (1,))  # step 9 does not exist
        assert reviewer.context == ()
        assert [step.number for step in run.steps((1, 2))] == [1]  # only completed ones

    def test_snapshots_do_not_change_afterwards(self) -> None:
        run = make_run()
        run.delegate(RESEARCH, 1)
        snapshot = run.snapshot()

        run.step_started(1)

        assert snapshot.steps[0].status == "pending"
        assert run.snapshot().steps[0].status == "running"
        assert run.snapshot().version > snapshot.version

    def test_events_after_an_id_and_whether_the_run_stopped(self) -> None:
        run = make_run()
        run.supervisor_started(1)

        events, stopped = run.events_after(1)
        assert ([e.type for e in events], stopped) == (["agent_started"], False)
        run.complete("# Done", forced=False)
        events, stopped = run.events_after(2)
        assert ([e.type for e in events], stopped) == (["run_finished"], True)
        assert events[0].data["final"] == "# Done"

    def test_cited_pages_get_the_runs_numbers_in_order_of_first_citation(self) -> None:
        run = make_run()
        decision = Decision("Two topics.", tuple(Delegation("researcher", t) for t in "AB"))
        run.delegate(decision, 1)
        a, b, c = Source("https://a", "A"), Source("https://b", "B"), Source("https://c", "C")
        run.step_finished(1, "x [cite:1] [cite:2]", sources=(a, b), model="m", usage=Usage())
        run.step_finished(2, "y [cite:1] [cite:2]", sources=(b, c), model="m", usage=Usage())

        snapshot = run.snapshot()
        assert snapshot.sources == (a, b, c)
        assert snapshot.steps[1].result == (
            "y [2] [3]\n\nSources:\n- [2] B — https://b\n- [3] C — https://c"
        )
        assert snapshot.steps[1].sources == (b, c)

    def test_the_user_gets_the_final_with_its_sources(self) -> None:
        run = make_run()
        run.delegate(RESEARCH, 1)
        run.step_finished(
            1, "x [cite:1]", sources=(Source("https://a", "A"),), model="m", usage=Usage()
        )

        run.await_approval("Result [1].", forced=False)

        assert run.snapshot().final == "Result [1].\n\n## Sources\n\n1. A — https://a"
        assert run.events_after(0)[0][-1].data["final"] == run.snapshot().final

    def test_spending_is_capped_per_run_and_per_day(self) -> None:
        budget = DailyBudget(1.0)
        run = make_run(cap_usd=0.01, budget=budget)

        run.ensure_budget()
        cost = run.charge("researcher", "claude-haiku-4-5", Usage(10_000, 0))

        assert cost == pytest.approx(0.01)
        assert budget.spent_usd == pytest.approx(0.01)
        assert run.snapshot().agents["researcher"].calls == 1
        with pytest.raises(RunBudgetExceededError):
            run.ensure_budget()

    def test_cancel_while_waiting_for_approval_ends_at_once(self) -> None:
        run = make_run(require_approval=True)
        run.await_approval("# Draft", forced=False)

        run.cancel()

        assert run.status == "cancelled"
        with pytest.raises(InvalidActionError, match="already ended"):
            run.cancel()


class TestRunStore:
    def test_finished_runs_expire_and_old_ones_are_dropped(self) -> None:
        store = RunStore(max_runs=2, ttl_seconds=0)
        finished, running = make_run(), make_run()
        finished.complete("x", forced=False)
        store.add(finished)
        store.add(running)

        store.add(make_run())  # expires the finished run

        with pytest.raises(Exception, match=finished.id):
            store.get(finished.id)
        assert store.get(running.id) is running
        assert store.active() == 2


class TestMemory:
    def test_recall_finds_related_tasks_only(self, tmp_path: Path) -> None:
        memory = MemoryStore(tmp_path / "memory.sqlite3")
        memory.remember("Solid-state batteries for electric cars", "A solid electrolyte ...")
        memory.remember("Comparação de baterias de lítio", "As baterias de lítio ...")
        memory.remember("A recipe for bread", "Flour, water, salt, yeast.")

        recalled = memory.recall("Are solid-state batteries safe in cars?")

        assert [note.task for note in recalled] == ["Solid-state batteries for electric cars"]
        assert recalled[0].score > 0.2
        assert [n.task for n in memory.recall("Quais são as baterias de lítio?")] == [
            "Comparação de baterias de lítio"
        ]
        assert memory.recall("the of and") == ()

    def test_notes_persist_and_can_be_forgotten(self, tmp_path: Path) -> None:
        path = tmp_path / "data" / "memory.sqlite3"
        note = MemoryStore(path).remember("Task", "Result " * 1000)

        reopened = MemoryStore(path)

        assert [n.id for n in reopened.notes()] == [note.id]
        assert len(note.summary) <= SUMMARY_CHARS + 2 and note.summary.endswith("…")
        assert reopened.forget(note.id) is True
        assert reopened.forget(note.id) is False

    def test_only_the_newest_notes_are_kept(self) -> None:
        memory = MemoryStore(None)
        for n in range(MAX_NOTES + 3):
            memory.remember(f"Task {n}", "Result")

        notes = memory.notes()

        assert len(notes) == MAX_NOTES
        assert notes[0].task == f"Task {MAX_NOTES + 2}"
        assert memory.clear() == MAX_NOTES

    def test_words_drop_stop_words_numbers_and_short_words(self) -> None:
        assert words("How do the 2 batteries of the EV work? O carro é elétrico.") == {
            "batteries",
            "work",
            "carro",
            "elétrico",
        }


class TestLimits:
    def test_rate_limiter_uses_a_sliding_window_per_client(self) -> None:
        now = [0.0]
        limiter = RateLimiter(2, 60, clock=lambda: now[0])
        limiter.check("a")
        limiter.check("a")

        with pytest.raises(RateLimitedError):
            limiter.check("a")
        limiter.check("b")
        now[0] = 60.0
        limiter.check("a")

    def test_daily_budget_resets_every_day(self) -> None:
        today = [date(2026, 10, 18)]
        budget = DailyBudget(1.0, today=lambda: today[0])
        budget.add(1.0)

        with pytest.raises(BudgetExceededError, match="used up"):
            budget.ensure_available()
        today[0] = date(2026, 10, 19)
        budget.ensure_available()


class TestSettings:
    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", " sk-test ")
        monkeypatch.setenv("WORKER_MODEL", "claude-sonnet-5")
        monkeypatch.setenv("WEB_SEARCH", "false")
        monkeypatch.setenv("RUN_BUDGET_USD", "0.5")
        monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
        monkeypatch.delenv("DATA_DIR", raising=False)

        settings = Settings.from_env()

        assert settings.anthropic_api_key == "sk-test"
        assert (settings.supervisor_model, settings.worker_model) == (
            "claude-sonnet-5",
            "claude-sonnet-5",
        )
        assert settings.web_search is False
        assert settings.run_budget_usd == 0.5
        assert settings.cors_origins == ("https://a.example", "https://b.example")
        assert str(settings.data_dir) == "data"

    def test_invalid_numbers_and_the_key_in_logs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNS_PER_HOUR", "many")

        with pytest.raises(ValueError, match="RUNS_PER_HOUR must be a number, not 'many'"):
            Settings.from_env()
        assert "sk-secret" not in repr(Settings(anthropic_api_key="sk-secret"))
