"""Protection of a public deployment: a per-client rate limit and a daily spending cap."""

import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime
from threading import Lock


class RateLimitedError(Exception):
    """The client used up its quota."""


class BudgetExceededError(Exception):
    """Today's spending cap is reached."""

    def __str__(self) -> str:
        return "Today's budget for AI calls is used up; try again tomorrow."


class RateLimiter:
    """At most ``limit`` requests per client in a sliding window of ``window_seconds``."""

    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check(self, client: str) -> None:
        """Counts a request of ``client``; raises ``RateLimitedError`` over the limit."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(client, deque())
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                raise RateLimitedError(client)
            hits.append(now)


class DailyBudget:
    """Spending cap in US dollars per UTC day, shared by every task."""

    def __init__(
        self, cap_usd: float, today: Callable[[], date] = lambda: datetime.now(UTC).date()
    ) -> None:
        self.cap_usd = cap_usd
        self._today = today
        self._day = today()
        self._spent = 0.0
        self._lock = Lock()

    @property
    def spent_usd(self) -> float:
        with self._lock:
            self._roll()
            return self._spent

    def ensure_available(self) -> None:
        """Raises ``BudgetExceededError`` when today's cap is used up."""
        with self._lock:
            self._roll()
            if self._spent >= self.cap_usd:
                raise BudgetExceededError(self.cap_usd)

    def add(self, cost_usd: float) -> None:
        with self._lock:
            self._roll()
            self._spent += cost_usd

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._spent = today, 0.0
