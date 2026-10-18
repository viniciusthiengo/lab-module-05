from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.supervisor import Supervisor
from app.agents.workers import WORKERS, Worker
from app.config import Settings
from app.limits import DailyBudget
from app.llm.base import LLMClient
from app.llm.fake import FakeLLMClient
from app.main import create_app
from app.memory import MemoryStore
from app.orchestrator import Orchestrator
from app.runs import Run, RunOptions
from tests.fakes import ScriptedTeam


class InlineExecutor(Executor):
    """Runs submitted work at once, in the caller's thread: a background task has ended
    when the request that started it returns."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:  # pragma: no cover - runs record their errors
            future.set_exception(error)
        return future


class HeldExecutor(Executor):
    """Keeps submitted work without running it: tasks stay running."""

    def __init__(self) -> None:
        self.held: list[Callable[[], Any]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.held.append(lambda: fn(*args, **kwargs))
        return Future()

    def run_all(self) -> None:
        while self.held:
            self.held.pop(0)()


def make_run(
    task: str = "Explain solid-state batteries.",
    *,
    cap_usd: float = 1.0,
    budget: DailyBudget | None = None,
    **options: Any,
) -> Run:
    return Run(task, RunOptions(**options), cap_usd=cap_usd, budget=budget or DailyBudget(10.0))


def make_team(
    team: ScriptedTeam | None = None,
    *,
    memory: MemoryStore | None = None,
    max_parallel: int = 3,
) -> tuple[Orchestrator, FakeLLMClient]:
    """An orchestrator over the scripted team (one fake model for every agent)."""
    llm = FakeLLMClient(team or ScriptedTeam())
    orchestrator = Orchestrator(
        Supervisor(llm, WORKERS, max_delegations=max_parallel),
        {spec.name: Worker(spec, llm) for spec in WORKERS},
        memory,
        max_parallel=max_parallel,
    )
    return orchestrator, llm


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    """A client of the app over the scripted team (or ``llm``; ``None`` is a server without
    an API key). Background tasks run inline unless ``runner`` says otherwise; keywords
    override settings."""
    clients: list[TestClient] = []

    def make(
        team: ScriptedTeam | None = None,
        *,
        llm: LLMClient | str | None = "fake",
        runner: Executor | None = None,
        rate_clock: Callable[[], float] | None = None,
        **overrides: Any,
    ) -> TestClient:
        model = FakeLLMClient(team or ScriptedTeam()) if llm == "fake" else llm
        app = create_app(
            Settings(**overrides),
            llm=model,  # type: ignore[arg-type]
            runner=runner or InlineExecutor(),
            **({"rate_clock": rate_clock} if rate_clock else {}),
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
