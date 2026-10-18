"""Memory across tasks: finished results kept in SQLite, recalled for related tasks.

When a run completes, its task and the start of its result are stored. A new task gets the
notes whose words overlap most with its own (cosine of the word sets, so long notes are not
favored), and the Supervisor decides whether they help: a follow-up can build on an earlier
result instead of researching again.
"""

import math
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SUMMARY_CHARS = 1_500  # of a result kept as a note
MAX_NOTES = 200
_WORD = re.compile(r"\w+", re.UNICODE)
# Common English and Portuguese words, which say nothing about a task's subject.
_STOP_WORDS_TEXT = """
a about an and are as at be but by can do does for from has have how i in into is it its me
my not of on or our that the their them then there these they this to was we what when where
which who why will with you your
à ao aos as até com como da das de do dos e é em entre essa esse esta este eu foi isso mais
mas me meu minha na nas no nos o os ou para pela pelo por qual quando que se sem ser seu sua
são também um uma umas uns
"""
_STOP_WORDS = frozenset(_STOP_WORDS_TEXT.split())


@dataclass(frozen=True)
class MemoryNote:
    id: str
    task: str
    summary: str
    created_at: float
    score: float = 0.0  # how related to the task it was recalled for


def words(text: str) -> set[str]:
    """The meaningful words of ``text``, lower-case."""
    return {
        word
        for word in _WORD.findall(text.lower())
        if len(word) > 2 and not word.isdigit() and word not in _STOP_WORDS
    }


class MemoryStore:
    """Notes in SQLite at ``path`` (in memory when ``None``)."""

    def __init__(self, path: Path | None) -> None:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS notes ("
                "id TEXT PRIMARY KEY, task TEXT NOT NULL, summary TEXT NOT NULL, "
                "created_at REAL NOT NULL)"
            )

    def remember(self, task: str, result: str) -> MemoryNote:
        """Keeps a finished task and the start of its result; the oldest notes go first."""
        summary = result.strip()
        if len(summary) > SUMMARY_CHARS:
            summary = summary[:SUMMARY_CHARS].rsplit(" ", 1)[0] + " …"
        note = MemoryNote(secrets.token_urlsafe(6), task.strip(), summary, time.time())
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO notes (id, task, summary, created_at) VALUES (?, ?, ?, ?)",
                (note.id, note.task, note.summary, note.created_at),
            )
            self._db.execute(
                "DELETE FROM notes WHERE id NOT IN "
                "(SELECT id FROM notes ORDER BY created_at DESC LIMIT ?)",
                (MAX_NOTES,),
            )
        return note

    def recall(self, task: str, limit: int = 3, threshold: float = 0.2) -> tuple[MemoryNote, ...]:
        """The notes most related to ``task``: at most ``limit``, each at least ``threshold``."""
        wanted = words(task)
        if not wanted:
            return ()
        scored = []
        for note in self.notes():
            # A matching earlier task counts even when its long result dilutes the overlap.
            task_words = words(note.task)
            score = max(
                _cosine(wanted, task_words), _cosine(wanted, task_words | words(note.summary))
            )
            if score >= threshold:
                scored.append(MemoryNote(note.id, note.task, note.summary, note.created_at, score))
        scored.sort(key=lambda note: (-note.score, -note.created_at))
        return tuple(scored[:limit])

    def notes(self, limit: int = MAX_NOTES) -> list[MemoryNote]:
        """The notes, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, task, summary, created_at FROM notes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [MemoryNote(*row) for row in rows]

    def forget(self, note_id: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount > 0

    def clear(self) -> int:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM notes").rowcount


def _cosine(a: set[str], b: set[str]) -> float:
    return len(a & b) / math.sqrt(len(a) * len(b)) if a and b else 0.0
