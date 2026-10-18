"""Settings read from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self

DEFAULT_SUPERVISOR_MODEL = "claude-sonnet-5"
DEFAULT_WORKER_MODEL = "claude-haiku-4-5"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    Attributes:
        anthropic_api_key: key of the Anthropic API (never logged). Without it the API runs,
            but tasks answer 503.
        supervisor_model: Claude model of the Supervisor, which needs the most judgment.
        worker_model: Claude model of the workers.
        web_search: whether the Researcher and the Fact-checker may search the web.
        searches_per_call: web searches a worker may run in one call.
        daily_budget_usd: spending cap per UTC day, for every task together.
        run_budget_usd: spending cap of one task.
        runs_per_hour: tasks started (and feedback rounds) per client and hour.
        max_active_runs: tasks being worked on at once.
        max_parallel: delegations of one decision that run at the same time.
        max_feedback_rounds: times a user can send a result back with feedback.
        data_dir: where the memory database lives (``None``: in memory).
        cors_origins: origins allowed to call the API directly from a browser.
    """

    anthropic_api_key: str | None = None
    supervisor_model: str = DEFAULT_SUPERVISOR_MODEL
    worker_model: str = DEFAULT_WORKER_MODEL
    web_search: bool = True
    searches_per_call: int = 3
    daily_budget_usd: float = 1.0
    run_budget_usd: float = 0.25
    runs_per_hour: int = 10
    max_active_runs: int = 3
    max_parallel: int = 3
    max_feedback_rounds: int = 3
    data_dir: Path | None = None
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Self:
        def number(name: str, default: float, kind: type = int) -> float:
            raw = os.environ.get(name, "").strip()
            try:
                return kind(raw) if raw else default
            except ValueError:
                raise ValueError(f"{name} must be a number, not '{raw}'") from None

        defaults = cls()
        data_dir = os.environ.get("DATA_DIR", "").strip() or "data"
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip() or None,
            supervisor_model=os.environ.get("SUPERVISOR_MODEL", "").strip()
            or DEFAULT_SUPERVISOR_MODEL,
            worker_model=os.environ.get("WORKER_MODEL", "").strip() or DEFAULT_WORKER_MODEL,
            web_search=os.environ.get("WEB_SEARCH", "true").strip().lower() != "false",
            searches_per_call=int(number("SEARCHES_PER_CALL", defaults.searches_per_call)),
            daily_budget_usd=number("DAILY_BUDGET_USD", defaults.daily_budget_usd, float),
            run_budget_usd=number("RUN_BUDGET_USD", defaults.run_budget_usd, float),
            runs_per_hour=int(number("RUNS_PER_HOUR", defaults.runs_per_hour)),
            max_active_runs=int(number("MAX_ACTIVE_RUNS", defaults.max_active_runs)),
            max_parallel=int(number("MAX_PARALLEL", defaults.max_parallel)),
            max_feedback_rounds=int(number("MAX_FEEDBACK_ROUNDS", defaults.max_feedback_rounds)),
            data_dir=Path(data_dir),
            cors_origins=tuple(
                origin.strip()
                for origin in os.environ.get("CORS_ORIGINS", "").split(",")
                if origin.strip()
            ),
        )

    def __repr__(self) -> str:
        return (
            f"Settings(supervisor_model={self.supervisor_model!r}, "
            f"worker_model={self.worker_model!r}, web_search={self.web_search}, "
            f"api_key={'set' if self.anthropic_api_key else 'missing'}, "
            f"daily_budget_usd={self.daily_budget_usd}, run_budget_usd={self.run_budget_usd})"
        )
