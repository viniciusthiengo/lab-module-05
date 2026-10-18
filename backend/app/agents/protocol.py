"""The Supervisor's answer format, and its parser.

A decision delegates work::

    REASON: Facts first.
    DELEGATE: researcher
    TASK: Find the main causes of ...
    CONTEXT: none

(several DELEGATE/TASK/CONTEXT groups run at the same time), or finishes::

    REASON: The draft passed the review.
    FINAL:
    # The final result, in Markdown

The parser tolerates what models add around keywords (Markdown emphasis, list markers,
spaces, letter case) and refuses anything else with a message that tells the Supervisor how
to answer.
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal

_KEYWORD = re.compile(
    r"^[\s>*_#`-]*(REASON|DELEGATE|TASK|CONTEXT|FINAL)[\s*_`]*:[\s*_`]*(.*)$", re.IGNORECASE
)


class ProtocolError(ValueError):
    """The answer does not follow the format; the message says what is wrong."""


@dataclass(frozen=True)
class Delegation:
    agent: str
    task: str
    context: tuple[int, ...] | None = None  # numbers of earlier steps; None: all of them


@dataclass(frozen=True)
class Decision:
    reason: str
    delegations: tuple[Delegation, ...] = ()
    final: str | None = None

    @property
    def kind(self) -> Literal["delegate", "final"]:
        return "final" if self.final is not None else "delegate"


def agent_key(name: str) -> str:
    """``Fact-Checker`` → ``fact_checker``."""
    return re.sub(r"[\s-]+", "_", name.strip().strip("*`'\"").lower())


def parse_decision(text: str, agents: Collection[str], *, max_delegations: int = 3) -> Decision:
    """The decision in ``text``; raises ``ProtocolError`` when it does not follow the format."""
    reason: list[str] = []
    groups: list[dict[str, list[str]]] = []
    final: list[str] | None = None
    current: list[str] | None = None
    lines = text.strip().splitlines()
    for index, line in enumerate(lines):
        match = _KEYWORD.match(line)
        if match is None:
            if current is not None:
                current.append(line)
            continue
        keyword, value = match.group(1).upper(), match.group(2)
        if keyword == "FINAL":
            # Everything after FINAL is the result, whatever it contains.
            final = [value, *lines[index + 1 :]]
            break
        if keyword == "REASON":
            reason[:] = [value]
            current = reason
        elif keyword == "DELEGATE":
            groups.append({"agent": [value], "task": [], "context": []})
            current = None
        else:
            if not groups:
                raise ProtocolError(f"{keyword} must follow a DELEGATE line.")
            field = groups[-1][keyword.lower()]
            field[:] = [value]
            current = field
    reason_text = _join(reason)
    if final is not None:
        if groups:
            raise ProtocolError("Answer with DELEGATE lines or with FINAL, not both.")
        result = _join(final)
        if not result:
            raise ProtocolError("FINAL is empty: write the final result after FINAL:.")
        return Decision(reason_text, final=result)
    if not groups:
        raise ProtocolError(
            "Start with DELEGATE: <worker name> and TASK: <instructions>, or with FINAL: "
            "followed by the final result."
        )
    if len(groups) > max_delegations:
        raise ProtocolError(f"Give at most {max_delegations} delegations in one answer.")
    delegations = tuple(_delegation(group, agents) for group in groups)
    return Decision(reason_text, delegations)


def _delegation(group: dict[str, list[str]], agents: Collection[str]) -> Delegation:
    name = agent_key(_join(group["agent"]))
    if name not in agents:
        raise ProtocolError(
            f"There is no worker named '{name or '?'}'. The workers are: {', '.join(agents)}."
        )
    task = _join(group["task"])
    if not task:
        raise ProtocolError(f"The delegation to {name} has no TASK line.")
    return Delegation(name, task, _context(_join(group["context"])))


def _context(value: str) -> tuple[int, ...] | None:
    """Step numbers; ``()`` for none; ``None`` (every step) when omitted or "all"."""
    numbers = re.findall(r"\d+", value)
    if numbers:
        return tuple(dict.fromkeys(int(number) for number in numbers))
    return () if re.search(r"\bnone\b|\bno\b", value.lower()) else None


def _join(lines: Sequence[str]) -> str:
    return "\n".join(lines).strip()
