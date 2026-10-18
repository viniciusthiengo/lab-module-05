import re

import pytest

from app.agents.base import call_model, cut, today
from app.agents.protocol import ProtocolError
from app.agents.supervisor import Situation, Supervisor
from app.agents.workers import WORKERS, ContextItem, Worker, worker_prompt
from app.llm.base import LLMError, Message, Usage
from app.llm.fake import FakeAnswer, FakeLLMClient, FakeRequest
from app.memory import MemoryNote
from app.runs import Feedback, Step
from tests.conftest import make_run
from tests.fakes import ScriptedTeam, delegate, final

SPECS = {spec.name: spec for spec in WORKERS}


class TestWorkers:
    def test_the_prompt_has_the_context_the_task_and_the_search_allowance(self) -> None:
        context = [ContextItem(1, "researcher", "Find facts.", "Notes.")]

        prompt = worker_prompt("Write it.", context, 3, 24_000)

        assert prompt == (
            '<context>\n<step number="1" agent="researcher">\n<task>\nFind facts.\n</task>\n'
            "<result>\nNotes.\n</result>\n</step>\n</context>\n\n<task>\nWrite it.\n</task>\n\n"
            "You can search the web: 3 searches at most."
        )
        assert worker_prompt("Write it.", [], 0, 24_000).endswith(
            "You cannot search the web for this task."
        )
        assert worker_prompt("Write it.", [], None, 24_000) == "<task>\nWrite it.\n</task>"

    def test_the_prompt_starts_with_the_date(self) -> None:
        prompt = worker_prompt("Write it.", [], None, 24_000, today="Sunday, October 18, 2026")

        assert prompt == "Today is Sunday, October 18, 2026.\n\n<task>\nWrite it.\n</task>"
        assert re.fullmatch(r"[A-Z][a-z]+day, [A-Z][a-z]+ \d{1,2}, \d{4}", today())

    def test_long_context_is_cut_to_its_share(self) -> None:
        context = [ContextItem(n, "researcher", "t", "word " * 3000) for n in (1, 2)]

        prompt = worker_prompt("Write.", context, None, 10_000)

        assert prompt.count("[…cut]") == 2
        assert len(prompt) < 11_000

    def test_only_researcher_and_fact_checker_search(self) -> None:
        llm = FakeLLMClient(ScriptedTeam())
        run = make_run()

        for name in ("researcher", "writer", "reviewer", "fact_checker"):
            Worker(SPECS[name], llm, searches=2).run("Do it.", [], meter=run, web_search=True)

        assert [r.web_searches for r in llm.requests] == [2, 0, 0, 2]
        assert "searches at most" not in llm.requests[1].prompt

    def test_without_web_search_on_the_provider_the_worker_still_works(self) -> None:
        def answer(request: FakeRequest) -> str:
            if request.web_searches:
                raise LLMError("The AI provider failed (HTTP 400).", status=400)
            return "Notes from what the model knows."

        llm = FakeLLMClient(answer)

        result = Worker(SPECS["researcher"], llm).run(
            "Find facts.", [], meter=make_run(), web_search=True
        )

        assert result.text == "Notes from what the model knows."
        assert (
            result.note
            == "Web search was not available, so this result comes from the model alone."
        )
        assert [r.web_searches for r in llm.requests] == [3, 0]

    def test_other_provider_errors_are_raised(self) -> None:
        def answer(_: FakeRequest) -> str:
            raise LLMError("The AI provider failed (HTTP 500).", status=500, retryable=True)

        with pytest.raises(LLMError):
            Worker(SPECS["writer"], FakeLLMClient(answer)).run(
                "Write.", [], meter=make_run(), web_search=True
            )

    def test_a_cut_result_has_a_note(self) -> None:
        llm = FakeLLMClient(lambda _: FakeAnswer("Half a draft", stop_reason="max_tokens"))

        result = Worker(SPECS["writer"], llm).run("Write.", [], meter=make_run(), web_search=False)

        assert result.note == "The result reached the output limit and may end abruptly."


class TestSupervisor:
    def situation(self, **overrides: object) -> Situation:
        step = Step(1, "researcher", "Find facts.", (), 0, 1, status="completed", result="Notes.")
        values: dict[str, object] = {
            "task": "Explain batteries.",
            "steps": [step],
            "iteration": 2,
            "max_iterations": 3,
        }
        return Situation(**{**values, **overrides})  # type: ignore[arg-type]

    def test_the_system_prompt_lists_the_workers_and_the_format(self) -> None:
        supervisor = Supervisor(FakeLLMClient(ScriptedTeam()), WORKERS, max_delegations=2)

        assert "- writer: Turns research notes into the polished deliverable" in supervisor.system
        assert "you may give up to 2 delegations in one answer" in supervisor.system
        assert "DELEGATE: <worker name>" in supervisor.system
        assert "FINAL:" in supervisor.system

    def test_the_prompt_has_memory_feedback_steps_and_the_iteration(self) -> None:
        supervisor = Supervisor(FakeLLMClient(ScriptedTeam()), WORKERS)
        memory = MemoryNote("m1", 'Old "battery" task', "Old summary [3].", 0.0, 0.5)

        prompt = supervisor.prompt(
            self.situation(memories=[memory], feedback=[Feedback(1, "# Draft", "Shorter, please.")])
        )

        assert prompt.startswith("<task>\nExplain batteries.\n</task>")
        dated = supervisor.prompt(self.situation(today="Sunday, October 18, 2026"))
        assert dated.startswith("Today is Sunday, October 18, 2026.\n\n<task>")
        assert '<note task="Old &quot;battery&quot; task">\nOld summary.\n</note>' in prompt
        assert '<rejected_result round="1">\n# Draft\n</rejected_result>' in prompt
        assert "<user_feedback>\nShorter, please.\n</user_feedback>" in prompt
        assert '<step number="1" agent="researcher" status="completed">' in prompt
        assert prompt.endswith("Decision 2 of 3.")
        last = supervisor.prompt(self.situation(iteration=3))
        assert last.endswith("This is your last decision: answer with FINAL.")

    def test_a_second_malformed_answer_raises(self) -> None:
        supervisor = Supervisor(FakeLLMClient(ScriptedTeam(["Hmm.", "Still thinking."])), WORKERS)
        errors: list[str] = []

        with pytest.raises(ProtocolError):
            supervisor.decide(self.situation(), meter=make_run(), on_protocol_error=errors.append)
        assert len(errors) == 1

    def test_finalize_needs_a_final(self) -> None:
        llm = FakeLLMClient(ScriptedTeam([delegate(("writer", "Write.")), final("# Result")]))
        supervisor = Supervisor(llm, WORKERS)

        with pytest.raises(ProtocolError, match="delegated again"):
            supervisor.finalize(self.situation(), meter=make_run())
        assert supervisor.finalize(self.situation(), meter=make_run()).final == "# Result"
        assert "You have used all your decisions" in llm.requests[-1].prompt


class TestMeteredCalls:
    def test_calls_are_charged_to_their_agent(self) -> None:
        run = make_run()
        llm = FakeLLMClient(lambda _: "ok", usage=Usage(1000, 100))

        call_model(llm, run, "writer", system="s", messages=[Message("user", "u")], max_tokens=10)

        assert run.snapshot().agents["writer"].calls == 1
        assert run.snapshot().cost_usd > 0

    def test_billed_failures_are_charged_too(self) -> None:
        def refused(_: FakeRequest) -> str:
            raise LLMError("declined", usage=Usage(500, 0), model="claude-haiku-4-5")

        run = make_run()

        with pytest.raises(LLMError):
            call_model(
                FakeLLMClient(refused), run, "writer", system="s", messages=[], max_tokens=10
            )
        assert run.snapshot().cost_usd == pytest.approx(0.0005)


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("short", 10, "short"),
        ("one two three four", 10, "one two\n[…cut]"),
        ("x" * 30, 10, "x" * 10 + "\n[…cut]"),
    ],
)
def test_cut(text: str, limit: int, expected: str) -> None:
    assert cut(text, limit) == expected
