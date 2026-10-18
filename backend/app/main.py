"""FastAPI application of the multi-agent research assistant."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.agents.supervisor import Supervisor
from app.agents.workers import WORKERS, Worker
from app.config import Settings
from app.limits import BudgetExceededError, DailyBudget, RateLimitedError, RateLimiter
from app.llm.anthropic_client import AnthropicClient
from app.llm.base import LLMClient, LLMError
from app.memory import MemoryStore
from app.orchestrator import Orchestrator
from app.runs import (
    SUPERVISOR,
    Event,
    InvalidActionError,
    Run,
    RunNotFoundError,
    RunOptions,
    RunStore,
)
from app.schemas import (
    AgentView,
    ErrorResponse,
    FeedbackRequest,
    MemoryListView,
    RunRequest,
    RunView,
)
from app.views import note_view, run_view

log = logging.getLogger("agents")

MAX_WAIT_SECONDS = 25.0
POLL_SECONDS = 0.1
HEARTBEAT_SECONDS = 15.0

DESCRIPTION = """
A research assistant built with the **supervisor pattern**. The Supervisor reads the task and
decides, one step at a time, which worker does what: the **Researcher** finds and summarizes
information (searching the web), the **Writer** produces the polished result, the **Reviewer**
and the **Fact-checker** check it. The Supervisor delegates with `DELEGATE:`/`TASK:` lines
(several delegations run in parallel) and finishes with `FINAL:`, synthesizing the workers'
results; after `max_iterations` decisions it must finish.

`POST /run` answers with the finished task. `POST /runs` starts it in the background; follow
it live with `GET /runs/{id}/events` (Server-Sent Events) or with long polling on
`GET /runs/{id}?since=<version>&wait=20`.
"""

ERRORS = {
    404: {"model": ErrorResponse, "description": "Unknown or expired task, or memory note."},
    409: {"model": ErrorResponse, "description": "Not possible in the task's status."},
    422: {"model": ErrorResponse, "description": "Invalid request."},
    429: {"model": ErrorResponse, "description": "Rate limit or daily budget reached."},
    503: {"model": ErrorResponse, "description": "No AI provider configured, or busy."},
}

RunId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]{8,32}$")]
NoteId = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]{4,32}$")]


class ModelRequiredError(Exception):
    """Tasks need Claude, and no API key is configured."""


class ServerBusyError(Exception):
    """Too many tasks are being worked on."""


class NoteNotFoundError(Exception):
    """No memory note has this id."""


def build_llms(settings: Settings) -> tuple[LLMClient | None, LLMClient | None]:
    """Claude for the Supervisor and for the workers; ``None`` without an API key. Thinking
    is disabled: the budgets assume answers without thinking tokens."""
    key = settings.anthropic_api_key
    if not key:
        return None, None
    supervisor = AnthropicClient(key, settings.supervisor_model, thinking="disabled")
    if settings.worker_model == settings.supervisor_model:
        return supervisor, supervisor
    return supervisor, AnthropicClient(key, settings.worker_model, thinking="disabled")


def create_app(
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    *,
    worker_llm: LLMClient | None = None,
    runner: Executor | None = None,
    rate_clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    """Builds the application; tests pass their own models (``llm`` is also the workers'
    model by default), runner and clock."""
    settings = settings or Settings.from_env()
    if llm is None:
        llm, worker_llm = build_llms(settings)
    worker_llm = worker_llm or llm
    budget = DailyBudget(settings.daily_budget_usd)
    memory = MemoryStore(settings.data_dir / "memory.sqlite3" if settings.data_dir else None)
    runs = RunStore()
    orchestrator = (
        Orchestrator(
            Supervisor(llm, WORKERS, max_delegations=settings.max_parallel),
            {
                spec.name: Worker(spec, worker_llm, searches=settings.searches_per_call)
                for spec in WORKERS
            },
            memory,
            max_parallel=settings.max_parallel,
        )
        if llm is not None and worker_llm is not None
        else None
    )
    roster = {
        SUPERVISOR: AgentView(
            name=SUPERVISOR,
            title="Supervisor",
            description="Coordinates the workers and synthesizes the final result.",
            model=llm.model if llm else "",
            searches=False,
        ),
        **{
            spec.name: AgentView(
                name=spec.name,
                title=spec.title,
                description=spec.description,
                model=worker_llm.model if worker_llm else "",
                searches=spec.searches and settings.web_search,
            )
            for spec in WORKERS
        },
    }
    own_runner = runner is None
    runner = runner or ThreadPoolExecutor(settings.max_active_runs, thread_name_prefix="run")
    limiter = RateLimiter(settings.runs_per_hour, 3600, rate_clock)
    log.info("starting with %r", settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if own_runner:
            runner.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="Multi-Agent Research Assistant API",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "Last-Event-ID"],
        )
    _register_error_handlers(app)

    def rate_limited(request: Request) -> None:
        limiter.check(_client_id(request))

    RateLimited = Annotated[None, Depends(rate_limited)]

    def new_run(body: RunRequest) -> tuple[Run, Orchestrator]:
        """A run for ``body``, when the server can take it."""
        if orchestrator is None:
            raise ModelRequiredError
        budget.ensure_available()
        if runs.active() >= settings.max_active_runs:
            raise ServerBusyError
        options = RunOptions(
            max_iterations=body.max_iterations,
            web_search=body.web_search and settings.web_search,
            require_approval=body.require_approval,
            use_memory=body.use_memory,
            remember=body.remember,
        )
        run = Run(body.task, options, cap_usd=settings.run_budget_usd, budget=budget)
        runs.add(run)
        return run, orchestrator

    def view(run: Run) -> RunView:
        return run_view(run.snapshot(), roster)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, object]:
        """Liveness, models, web search, memory and today's spending (never the key)."""
        return {
            "status": "ok",
            "ready": orchestrator is not None,
            "supervisor_model": llm.model if llm else None,
            "worker_model": worker_llm.model if worker_llm else None,
            "web_search": settings.web_search,
            "active_runs": runs.active(),
            "memory_notes": len(memory.notes()),
            "daily_budget_usd": settings.daily_budget_usd,
            "spent_today_usd": round(budget.spent_usd, 4),
            "run_budget_usd": settings.run_budget_usd,
        }

    @app.get("/agents", response_model=list[AgentView], tags=["meta"])
    def agents() -> list[AgentView]:
        """The Supervisor and its workers, with their models."""
        return list(roster.values())

    # --- Tasks ----------------------------------------------------------------------------

    @app.post("/run", response_model=RunView, tags=["tasks"], responses=ERRORS)
    def run_task(body: RunRequest, _: RateLimited) -> RunView:
        """Runs the task and answers with the result: the `final` output, the Supervisor's
        `decisions` and the workers' `steps`. With `require_approval`, it answers when the
        result waits for approval."""
        run, team = new_run(body)
        team.run(run)
        return view(run)

    @app.post("/runs", status_code=202, response_model=RunView, tags=["tasks"], responses=ERRORS)
    def start_task(body: RunRequest, _: RateLimited) -> RunView:
        """Starts the task in the background and answers at once."""
        run, team = new_run(body)
        runner.submit(team.run, run)
        return view(run)

    @app.get("/runs/{run_id}", response_model=RunView, tags=["tasks"], responses=ERRORS)
    async def get_task(
        run_id: RunId,
        since: Annotated[int | None, Query(ge=0, description="Last version you saw.")] = None,
        wait: Annotated[
            float, Query(ge=0, le=MAX_WAIT_SECONDS, description="Seconds to wait for a change.")
        ] = 0,
    ) -> RunView:
        """The task. With `since` and `wait`, answers as soon as its version is newer than
        `since`, or after `wait` seconds (long polling)."""
        run = runs.get(run_id)
        if since is not None:
            deadline = time.monotonic() + wait
            while run.version <= since and time.monotonic() < deadline:
                await asyncio.sleep(POLL_SECONDS)
        return view(run)

    @app.get("/runs/{run_id}/events", tags=["tasks"], responses={404: ERRORS[404]})
    async def stream_events(
        run_id: RunId,
        request: Request,
        after: Annotated[int, Query(ge=0, description="Last event id you received.")] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        """The task's events as Server-Sent Events, from the event after `after` (or the
        `Last-Event-ID` header). The stream ends with an `end` event when the task stops:
        finished, or waiting for approval."""
        run = runs.get(run_id)
        if last_event_id and last_event_id.isdigit():
            after = max(after, int(last_event_id))
        return StreamingResponse(
            _events(run, after, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/runs/{run_id}/approve", response_model=RunView, tags=["approval"], responses=ERRORS)
    def approve(run_id: RunId) -> RunView:
        """Approves the result that waits for approval: it becomes final."""
        run = runs.get(run_id)
        run.approve()
        if orchestrator is not None:
            orchestrator.remember(run)
        return view(run)

    @app.post(
        "/runs/{run_id}/reject",
        status_code=202,
        response_model=RunView,
        tags=["approval"],
        responses=ERRORS,
    )
    def reject(run_id: RunId, body: FeedbackRequest, _: RateLimited) -> RunView:
        """Sends the result back with feedback: the Supervisor starts a new round."""
        run = runs.get(run_id)
        if orchestrator is None:
            raise ModelRequiredError
        run.add_feedback(body.feedback, settings.max_feedback_rounds)
        runner.submit(orchestrator.run, run)
        return view(run)

    @app.post("/runs/{run_id}/cancel", response_model=RunView, tags=["tasks"], responses=ERRORS)
    def cancel(run_id: RunId) -> RunView:
        """Cancels the task; a model call in progress stops at its next words."""
        run = runs.get(run_id)
        run.cancel()
        return view(run)

    # --- Memory ---------------------------------------------------------------------------

    @app.get("/memory", response_model=MemoryListView, tags=["memory"])
    def list_memory() -> MemoryListView:
        """The results kept from earlier tasks, newest first."""
        return MemoryListView(notes=[note_view(note) for note in memory.notes()])

    @app.delete("/memory/{note_id}", status_code=204, tags=["memory"], responses=ERRORS)
    def forget(note_id: NoteId) -> None:
        """Forgets one earlier task."""
        if not memory.forget(note_id):
            raise NoteNotFoundError(note_id)

    @app.delete("/memory", tags=["memory"])
    def clear_memory() -> dict[str, int]:
        """Forgets every earlier task."""
        return {"deleted": memory.clear()}

    return app


async def _events(run: Run, after: int, request: Request) -> AsyncIterator[str]:
    """Server-Sent Events of ``run`` after the event ``after``."""
    cursor = after
    last_write = time.monotonic()
    yield "retry: 3000\n\n"
    while True:
        batch, stopped = run.events_after(cursor)
        for event in batch:
            yield _sse(event)
            cursor = event.id
        now = time.monotonic()
        if batch:
            last_write = now
        elif stopped:
            yield f"event: end\ndata: {json.dumps({'status': run.status})}\n\n"
            return
        elif now - last_write >= HEARTBEAT_SECONDS:
            yield ": keep-alive\n\n"
            last_write = now
        if await request.is_disconnected():
            return
        await asyncio.sleep(POLL_SECONDS)


def _sse(event: Event) -> str:
    payload = {"id": event.id, "type": event.type, "at": event.at, **event.data}
    return f"id: {event.id}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _client_id(request: Request) -> str:
    """The client's address: from the frontend's proxy header, the platform's proxy, or the
    connection."""
    forwarded = request.headers.get("x-client-ip") or request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    return first or (request.client.host if request.client else "unknown")


def _register_error_handlers(app: FastAPI) -> None:
    """Every error answers ``{"detail": message}``."""

    def error(status: int, message: str) -> JSONResponse:
        return JSONResponse({"detail": message}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error(422, _validation_message(exc))

    @app.exception_handler(RateLimitedError)
    async def rate_limited(_: Request, __: RateLimitedError) -> JSONResponse:
        return error(429, "Too many tasks from this client; try again later.")

    @app.exception_handler(BudgetExceededError)
    async def over_budget(_: Request, exc: BudgetExceededError) -> JSONResponse:
        return error(429, str(exc))

    @app.exception_handler(ModelRequiredError)
    async def no_model(_: Request, __: ModelRequiredError) -> JSONResponse:
        return error(503, "The server has no AI provider configured (ANTHROPIC_API_KEY).")

    @app.exception_handler(ServerBusyError)
    async def busy(_: Request, __: ServerBusyError) -> JSONResponse:
        return error(503, "The team is busy with other tasks; try again in a minute.")

    @app.exception_handler(RunNotFoundError)
    async def run_not_found(_: Request, __: RunNotFoundError) -> JSONResponse:
        return error(404, "Task not found: it never existed or it expired.")

    @app.exception_handler(NoteNotFoundError)
    async def note_not_found(_: Request, __: NoteNotFoundError) -> JSONResponse:
        return error(404, "Memory note not found.")

    @app.exception_handler(InvalidActionError)
    async def invalid_action(_: Request, exc: InvalidActionError) -> JSONResponse:
        return error(409, str(exc))

    @app.exception_handler(LLMError)
    async def provider_failed(_: Request, exc: LLMError) -> JSONResponse:
        log.warning("provider error: %s", exc)
        return error(503 if exc.retryable else 502, str(exc))


def _validation_message(exc: RequestValidationError) -> str:
    """The first validation error, as a sentence."""
    first = exc.errors()[0] if exc.errors() else {}
    kind = first.get("type")
    location = [str(part) for part in first.get("loc", ())]
    field = ".".join(location[1:])
    context = first.get("ctx") or {}
    if kind == "missing":
        return f"The field '{field}' is required." if field else "Send a JSON body."
    if kind == "extra_forbidden":
        return f"Unknown field '{field}'."
    if kind == "string_pattern_mismatch" and location[:1] == ["path"]:
        return "Invalid id."
    if kind == "string_too_long":
        return f"'{field}' is too long ({context.get('max_length')} characters at most)."
    if kind == "string_too_short":
        return f"'{field}' is empty."
    if kind in ("less_than_equal", "greater_than_equal") and field:
        return f"'{field}' {str(first.get('msg', 'is out of range')).lower()}."
    if kind == "bool_parsing" and field:
        return f"'{field}' must be true or false."
    return str(first.get("msg", "Invalid request.")).removeprefix("Value error, ")
