import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMError
from app.llm.fake import FakeLLMClient
from tests.conftest import HeldExecutor
from tests.fakes import ARTICLE, NOTES, SOURCE, ScriptedTeam, delegate, final

TASK = "Explain solid-state batteries for a general audience."
WORKFLOW = [
    delegate(("researcher", "Find the key facts."), reason="Facts first."),
    delegate(("writer", "Write 200 words."), context="1"),
    delegate(
        ("reviewer", "Review the draft."), ("fact_checker", "Check the facts."), context="all"
    ),
    final("# Solid-state batteries\n\nThey use a solid electrolyte [1]."),
]


def events(client: TestClient, run_id: str, **params: Any) -> list[dict[str, Any]]:
    """The run's Server-Sent Events, the closing ``end`` event included."""
    received: list[dict[str, Any]] = []
    with client.stream("GET", f"/runs/{run_id}/events", params=params) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        kind = "message"
        for line in response.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
                received.append(data if kind == "message" else {"type": kind, **data})
                kind = "message"
    return received


class TestRun:
    def test_runs_the_whole_task(self, make_client: Callable[..., TestClient]) -> None:
        client = make_client(ScriptedTeam(WORKFLOW))

        response = client.post("/run", json={"task": TASK, "max_iterations": 5})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["final"] == ARTICLE
        assert body["forced"] is False
        assert [(d["iteration"], d["kind"], d["steps"]) for d in body["decisions"]] == [
            (1, "delegate", [1]),
            (2, "delegate", [2]),
            (3, "delegate", [3, 4]),
            (4, "final", []),
        ]
        assert [(s["number"], s["agent"], s["status"], s["context"]) for s in body["steps"]] == [
            (1, "researcher", "completed", []),
            (2, "writer", "completed", [1]),
            (3, "reviewer", "completed", [1, 2]),
            (4, "fact_checker", "completed", [1, 2]),
        ]
        assert body["steps"][0]["result"] == NOTES
        assert body["sources"] == [{"url": SOURCE.url, "title": SOURCE.title}]
        assert body["iterations"] == 4
        assert body["usage"]["model_calls"] == 8
        assert body["usage"]["web_searches"] == 2  # the Researcher's and the Fact-checker's
        assert {agent["agent"] for agent in body["agents"]} == {
            "supervisor",
            "researcher",
            "writer",
            "reviewer",
            "fact_checker",
        }
        assert body["actions"] == []

    def test_the_iteration_limit_forces_completion(
        self, make_client: Callable[..., TestClient]
    ) -> None:
        client = make_client(ScriptedTeam([WORKFLOW[0], WORKFLOW[0], final("# Forced")]))

        body = client.post("/run", json={"task": TASK, "max_iterations": 2}).json()

        assert (body["status"], body["final"], body["forced"]) == ("completed", "# Forced", True)
        assert body["iterations"] == 3
        assert body["decisions"][-1]["forced"] is True

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({}, "The field 'task' is required."),
            ({"task": "   "}, "The task is empty."),
            ({"task": "x" * 4001}, "'task' is too long (4000 characters at most)."),
            ({"task": "x", "max_iterations": 0}, "'max_iterations' input should be greater"),
            ({"task": "x", "max_iterations": 11}, "'max_iterations' input should be less"),
            ({"task": "x", "web_search": "maybe"}, "'web_search' must be true or false."),
            ({"task": "x", "agents": 3}, "Unknown field 'agents'."),
        ],
    )
    def test_invalid_requests(self, client: TestClient, body: dict[str, Any], message: str) -> None:
        response = client.post("/run", json=body)

        assert response.status_code == 422
        assert response.json()["detail"].startswith(message)

    def test_without_an_api_key(self, make_client: Callable[..., TestClient]) -> None:
        client = make_client(llm=None)

        response = client.post("/run", json={"task": TASK})

        assert (response.status_code, client.get("/health").json()["ready"]) == (503, False)

    def test_web_search_off_on_the_server_wins(
        self, make_client: Callable[..., TestClient]
    ) -> None:
        client = make_client(ScriptedTeam(WORKFLOW), web_search=False)

        body = client.post("/run", json={"task": TASK, "web_search": True}).json()

        assert body["options"]["web_search"] is False
        assert body["usage"]["web_searches"] == 0

    def test_a_failed_run_answers_its_error(self, make_client: Callable[..., TestClient]) -> None:
        def failing(_: object) -> str:
            raise LLMError("The AI provider rejected the API key.")

        client = make_client(llm=FakeLLMClient(failing))

        body = client.post("/run", json={"task": TASK}).json()

        assert (body["status"], body["error"]) == (
            "failed",
            "The AI provider rejected the API key.",
        )


class TestBackgroundRuns:
    def test_the_events_stream_shows_the_work_live(
        self, make_client: Callable[..., TestClient]
    ) -> None:
        client = make_client(ScriptedTeam(WORKFLOW))

        started = client.post("/runs", json={"task": TASK})

        assert started.status_code == 202
        run_id = started.json()["id"]
        received = events(client, run_id)
        kinds = [event["type"] for event in received]
        assert kinds[0] == "run_started"
        assert kinds[-2:] == ["run_finished", "end"]
        assert [e["id"] for e in received[:-1]] == list(range(1, len(received)))
        working = [(e["agent"], e.get("step")) for e in received if e["type"] == "agent_started"]
        assert working == [
            ("supervisor", None),
            ("researcher", 1),
            ("supervisor", None),
            ("writer", 2),
            ("supervisor", None),
            ("reviewer", 3),
            ("fact_checker", 4),
            ("supervisor", None),
        ]
        decision = next(e for e in received if e["type"] == "decision")
        assert (decision["kind"], decision["reason"]) == ("delegate", "Facts first.")
        finished = next(e for e in received if e["type"] == "agent_finished")
        assert finished["result"] == NOTES
        assert finished["sources"] == [{"url": SOURCE.url, "title": SOURCE.title}]
        assert received[-1] == {"type": "end", "status": "completed"}

    def test_a_stream_resumes_after_the_last_event(
        self, make_client: Callable[..., TestClient]
    ) -> None:
        client = make_client(ScriptedTeam(WORKFLOW))
        run_id = client.post("/runs", json={"task": TASK}).json()["id"]
        everything = events(client, run_id)

        resumed = events(client, run_id, after=10)
        with client.stream(
            "GET", f"/runs/{run_id}/events", headers={"Last-Event-ID": "12"}
        ) as response:
            first_id = next(
                json.loads(line[6:])["id"]
                for line in response.iter_lines()
                if line.startswith("data: ")
            )

        assert resumed[0]["id"] == 11
        assert len(resumed) == len(everything) - 10
        assert first_id == 13

    def test_long_polling(self, make_client: Callable[..., TestClient]) -> None:
        runner = HeldExecutor()
        client = make_client(ScriptedTeam(WORKFLOW), runner=runner)
        run = client.post("/runs", json={"task": TASK}).json()

        waited = client.get(f"/runs/{run['id']}", params={"since": run["version"], "wait": 0.2})
        runner.run_all()
        changed = client.get(f"/runs/{run['id']}", params={"since": run["version"], "wait": 5})

        assert waited.json()["status"] == "running"
        assert changed.json()["status"] == "completed"

    def test_too_many_running_tasks(self, make_client: Callable[..., TestClient]) -> None:
        client = make_client(runner=HeldExecutor(), max_active_runs=2)

        statuses = [client.post("/runs", json={"task": TASK}).status_code for _ in range(3)]

        assert statuses == [202, 202, 503]

    def test_cancel(self, make_client: Callable[..., TestClient]) -> None:
        runner = HeldExecutor()
        client = make_client(ScriptedTeam(WORKFLOW), runner=runner)
        run_id = client.post("/runs", json={"task": TASK}).json()["id"]

        cancelled = client.post(f"/runs/{run_id}/cancel")
        runner.run_all()

        assert cancelled.json()["actions"] == ["cancel"]  # it stops at the next check
        assert client.get(f"/runs/{run_id}").json()["status"] == "cancelled"
        again = client.post(f"/runs/{run_id}/cancel")
        assert (again.status_code, again.json()["detail"]) == (409, "The task has already ended.")

    def test_unknown_tasks(self, client: TestClient) -> None:
        missing = client.get("/runs/abcdefghijkl")

        assert (missing.status_code, missing.json()["detail"]) == (
            404,
            "Task not found: it never existed or it expired.",
        )
        assert client.get("/runs/bad!id").status_code == 422
        assert client.get("/runs/abcdefghijkl/events").status_code == 404


class TestApproval:
    def test_approve(self, make_client: Callable[..., TestClient]) -> None:
        client = make_client(ScriptedTeam([final("# Draft")]))

        waiting = client.post("/run", json={"task": TASK, "require_approval": True}).json()
        approved = client.post(f"/runs/{waiting['id']}/approve")

        assert (waiting["status"], waiting["final"]) == ("awaiting_approval", "# Draft")
        assert waiting["actions"] == ["approve", "reject", "cancel"]
        assert approved.json()["status"] == "completed"
        assert [note["task"] for note in client.get("/memory").json()["notes"]] == [TASK]

    def test_reject_with_feedback_starts_a_new_round(
        self, make_client: Callable[..., TestClient]
    ) -> None:
        client = make_client(ScriptedTeam([final("# Long"), final("# Short")]))
        waiting = client.post("/run", json={"task": TASK, "require_approval": True}).json()

        response = client.post(
            f"/runs/{waiting['id']}/reject", json={"feedback": "Make it shorter."}
        )

        assert response.status_code == 202
        body = client.get(f"/runs/{waiting['id']}").json()
        assert (body["status"], body["final"], body["round"]) == ("awaiting_approval", "# Short", 1)
        assert body["feedback"] == [{"round": 1, "feedback": "Make it shorter."}]
        stream = events(client, waiting["id"])
        assert [e["type"] for e in stream if e["type"] in ("feedback", "end")] == [
            "feedback",
            "end",
        ]

    def test_actions_that_do_not_fit_the_status(self, client: TestClient) -> None:
        run_id = client.post("/run", json={"task": TASK}).json()["id"]

        approve = client.post(f"/runs/{run_id}/approve")
        reject = client.post(f"/runs/{run_id}/reject", json={"feedback": "No."})

        assert approve.status_code == reject.status_code == 409
        assert approve.json()["detail"] == "Only a result that waits for approval can be approved."
        empty = client.post(f"/runs/{run_id}/reject", json={"feedback": " "})
        assert (empty.status_code, empty.json()["detail"]) == (422, "Say what to change.")

    def test_feedback_rounds_are_limited(self, make_client: Callable[..., TestClient]) -> None:
        client = make_client(ScriptedTeam([final("# Draft")]), max_feedback_rounds=1)
        run_id = client.post("/run", json={"task": TASK, "require_approval": True}).json()["id"]

        first = client.post(f"/runs/{run_id}/reject", json={"feedback": "Shorter."})
        second = client.post(f"/runs/{run_id}/reject", json={"feedback": "Even shorter."})

        assert (first.status_code, second.status_code) == (202, 409)
        assert second.json()["detail"] == "A task takes 1 rounds of feedback at most."


class TestMemory:
    def test_remember_recall_forget(self, make_client: Callable[..., TestClient]) -> None:
        client = make_client(ScriptedTeam([final("# Batteries")]))
        client.post("/run", json={"task": "How do solid-state batteries work?"})

        second = client.post("/run", json={"task": "Are solid-state batteries safer?"}).json()
        notes = client.get("/memory").json()["notes"]

        assert [m["task"] for m in second["memories"]] == ["How do solid-state batteries work?"]
        assert second["memories"][0]["score"] > 0
        assert [note["task"] for note in notes] == [
            "Are solid-state batteries safer?",
            "How do solid-state batteries work?",
        ]
        assert client.delete(f"/memory/{notes[0]['id']}").status_code == 204
        assert client.delete(f"/memory/{notes[0]['id']}").status_code == 404
        assert client.delete("/memory").json() == {"deleted": 1}
        assert client.get("/memory").json() == {"notes": []}


class TestLimits:
    def test_rate_limit_per_client(self, make_client: Callable[..., TestClient]) -> None:
        now = [0.0]
        client = make_client(runs_per_hour=2, rate_clock=lambda: now[0])
        headers = {"x-client-ip": "203.0.113.7"}

        statuses = [
            client.post("/run", json={"task": TASK}, headers=headers).status_code for _ in range(3)
        ]
        other = client.post("/run", json={"task": TASK}, headers={"x-client-ip": "198.51.100.1"})
        now[0] = 3600.0
        later = client.post("/run", json={"task": TASK}, headers=headers)

        assert statuses == [200, 200, 429]
        assert (other.status_code, later.status_code) == (200, 200)

    def test_the_daily_budget(self, make_client: Callable[..., TestClient]) -> None:
        client = make_client(ScriptedTeam(WORKFLOW), daily_budget_usd=0.05)

        first = client.post("/run", json={"task": TASK}).json()
        second = client.post("/run", json={"task": TASK})

        assert first["status"] == "completed"
        assert first["forced"] is True  # the budget ran out during the task
        assert (second.status_code, second.json()["detail"]) == (
            429,
            "Today's budget for AI calls is used up; try again tomorrow.",
        )


def test_health_and_agents(client: TestClient) -> None:
    health = client.get("/health").json()
    agents = client.get("/agents").json()

    assert health["status"] == "ok"
    assert (health["ready"], health["supervisor_model"], health["web_search"]) == (
        True,
        "fake-model",
        True,
    )
    assert "key" not in json.dumps(health).lower()
    assert [(a["name"], a["searches"]) for a in agents] == [
        ("supervisor", False),
        ("researcher", True),
        ("writer", False),
        ("reviewer", False),
        ("fact_checker", True),
    ]


def test_the_api_is_documented(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "Multi-Agent Research Assistant API"
    assert {"/run", "/runs", "/runs/{run_id}/events", "/runs/{run_id}/reject"} <= set(
        schema["paths"]
    )
