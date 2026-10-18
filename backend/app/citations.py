"""Source citations, numbered by the system rather than by the models.

The pages a worker's web search cites come from the API: the adapter marks each cited passage
``[cite:n]``, n counting that answer's pages. A run gives every page one number for the whole
task, the first time a worker cites it, and rewrites each worker's result with ``[3]``
markers of those numbers, ending it with the list of the pages it cites. Agents see only these
numbers and are told to keep them. A marker an agent writes stays only when the agent was
shown it, and a list of sources a model writes is dropped. The final result's list is built
here, renumbered 1, 2, 3... in the order the result cites the pages, so a model can misplace a
marker but cannot list a page that no search returned.
"""

import re
from collections.abc import Callable, Collection, Sequence

from app.llm.base import Source

# Markers an agent writes: [3], [3, 7], or several in a row ([3][7], [3] [7]); not an index
# (items[3]) nor a link ([3](url)).
_ONE = r"\[\d{1,4}(?:[ \t]*,[ \t]*\d{1,4})*\]"
_MARKERS = re.compile(rf"([ \t]?)(?<![\w\\])({_ONE}(?:[ \t]?{_ONE})*)(?!\()")
# A citation from the API, as the adapter marks it.
_CITATION = re.compile(r"([ \t]?)\[cite:(\d{1,4})\]")
# The heading of a list of sources: "Sources:", "## References", "**Fontes:**"...
_HEADING = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*|__)?"
    r"(?P<word>sources|references|fontes|referências|referencias|fuentes)"
    r"(?:\*\*|__)?[ \t]*:?[ \t]*(?:\*\*|__)?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_LIST_ITEM = re.compile(r"^(?:[-*+•]|\d+[.)]|\[)")
_RULES = re.compile(r"(?:\n[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*)+\Z")

DEFAULT_HEADING = "Sources"


def markers(text: str) -> list[int]:
    """The numbers of the markers in ``text``, in order of first appearance."""
    found: dict[int, None] = {}
    for match in _MARKERS.finditer(text):
        for number in re.findall(r"\d+", match.group(2)):
            found.setdefault(int(number), None)
    return list(found)


def renumber(text: str, number: Callable[[int], int | None]) -> str:
    """``text`` with the numbers of its markers mapped by ``number``. A number mapped to
    ``None`` is dropped, and so are markers left without numbers."""

    def replace(match: re.Match[str]) -> str:
        mapped: list[int] = []
        for old in re.findall(r"\d+", match.group(2)):
            new = number(int(old))
            if new is not None and new not in mapped:
                mapped.append(new)
        return match.group(1) + "".join(f"[{n}]" for n in mapped) if mapped else ""

    return _MARKERS.sub(replace, text)


def split_sources(text: str) -> tuple[str, str | None]:
    """``text`` without the list of sources it ends with, and the word that heads the list
    (``None`` when ``text`` ends with no such list)."""
    headings = list(_HEADING.finditer(text))
    if not headings:
        return text, None
    last = headings[-1]
    lines = [line.strip() for line in text[last.end() :].splitlines()]
    if not all(not line or _LIST_ITEM.match(line) or "://" in line for line in lines):
        return text, None
    return _RULES.sub("", text[: last.start()].rstrip()).rstrip(), last.group("word")


def strip_citations(text: str) -> str:
    """``text`` without markers or a list of sources, for where the numbers mean nothing."""
    return renumber(split_sources(text)[0], lambda _: None)


class SourceList:
    """The pages a run's workers cited, numbered from 1 in order of first citation."""

    def __init__(self) -> None:
        self._sources: list[Source] = []
        self._numbers: dict[str, int] = {}

    @property
    def sources(self) -> tuple[Source, ...]:
        return tuple(self._sources)

    def number(self, source: Source) -> int:
        """The number of ``source``, given the first time it is cited."""
        if source.url not in self._numbers:
            self._sources.append(source)
            self._numbers[source.url] = len(self._sources)
        return self._numbers[source.url]

    def cite(self, text: str, sources: Sequence[Source], shown: Collection[int]) -> str:
        """A worker's result in the run's numbers. Its citations of ``sources`` become markers,
        the markers it wrote stay when they were ``shown`` to it, a list of sources it wrote is
        dropped, and the list of the pages it cites ends it."""
        numbers = [self.number(source) for source in sources]

        def citation(match: re.Match[str]) -> str:
            index = int(match.group(2))
            return f"{match.group(1)}[{numbers[index - 1]}]" if 0 < index <= len(numbers) else ""

        body = renumber(split_sources(text)[0], lambda n: n if n in shown else None)
        body = _CITATION.sub(citation, body)
        cited = sorted(n for n in markers(body) if n <= len(self._sources))
        if not cited:
            return body
        listing = "\n".join(f"- [{n}] {self._label(n)}" for n in cited)
        return f"{body}\n\nSources:\n{listing}"

    def final(self, text: str) -> str:
        """The final result for the user: the markers of cited pages renumbered 1, 2, 3... in
        order of appearance (others dropped), then the list of those pages, under the heading
        of the list the model wrote when it wrote one."""
        body, heading = split_sources(text)
        order: dict[int, int] = {}

        def number(old: int) -> int | None:
            if not 1 <= old <= len(self._sources):
                return None
            return order.setdefault(old, len(order) + 1)

        body = renumber(body, number)
        if not order:
            return body
        listing = "\n".join(f"{new}. {self._label(old)}" for old, new in order.items())
        return f"{body}\n\n## {(heading or DEFAULT_HEADING).capitalize()}\n\n{listing}"

    def _label(self, number: int) -> str:
        source = self._sources[number - 1]
        title = " ".join(source.title.split())
        return f"{title} — {source.url}" if title and title != source.url else source.url
