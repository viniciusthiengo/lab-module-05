"""The API's view of the domain objects."""

from collections.abc import Mapping

from app.llm.base import Source
from app.memory import MemoryNote
from app.runs import RunSnapshot, Step
from app.schemas import (
    AgentUsageView,
    AgentView,
    DecisionView,
    FeedbackView,
    MemoryNoteView,
    OptionsView,
    RunView,
    SourceView,
    StepView,
    UsageView,
)

ACTIONS = {"running": ["cancel"], "awaiting_approval": ["approve", "reject", "cancel"]}


def run_view(run: RunSnapshot, roster: Mapping[str, AgentView]) -> RunView:
    usage = run.usage
    return RunView(
        id=run.id,
        task=run.task,
        status=run.status,
        options=OptionsView(
            max_iterations=run.options.max_iterations,
            web_search=run.options.web_search,
            require_approval=run.options.require_approval,
            use_memory=run.options.use_memory,
            remember=run.options.remember,
        ),
        final=run.final,
        forced=run.forced,
        note=run.note,
        error=run.error,
        sources=[_source(source) for source in run.sources],
        decisions=[
            DecisionView(
                round=d.round,
                iteration=d.iteration,
                kind=d.kind,
                reason=d.reason,
                steps=list(d.steps),
                forced=d.forced,
            )
            for d in run.decisions
        ],
        steps=[_step(step) for step in run.steps],
        iterations=len(run.decisions),
        round=run.round,
        memories=[note_view(note) for note in run.memories],
        feedback=[FeedbackView(round=f.round, feedback=f.text) for f in run.feedback],
        usage=UsageView(
            model_calls=sum(agent.calls for agent in run.agents.values()),
            input_tokens=usage.total_input_tokens,
            output_tokens=usage.output_tokens,
            web_searches=usage.web_searches,
            cost_usd=round(run.cost_usd, 6),
        ),
        agents=[
            AgentUsageView(
                agent=name,
                title=roster[name].title if name in roster else name,
                model=roster[name].model if name in roster else "",
                model_calls=spent.calls,
                input_tokens=spent.usage.total_input_tokens,
                output_tokens=spent.usage.output_tokens,
                web_searches=spent.usage.web_searches,
                cost_usd=round(spent.cost_usd, 6),
            )
            for name, spent in run.agents.items()
        ],
        cap_usd=run.cap_usd,
        actions=ACTIONS.get(run.status, []),  # type: ignore[arg-type]
        created_at=run.created_at,
        finished_at=run.finished_at,
        duration_ms=_ms(run.created_at, run.finished_at),
        version=run.version,
        last_event_id=run.last_event_id,
    )


def note_view(note: MemoryNote) -> MemoryNoteView:
    return MemoryNoteView(
        id=note.id,
        task=note.task,
        summary=note.summary,
        created_at=note.created_at,
        score=round(note.score, 3),
    )


def _step(step: Step) -> StepView:
    return StepView(
        number=step.number,
        agent=step.agent,
        task=step.task,
        context=list(step.context),
        round=step.round,
        iteration=step.iteration,
        status=step.status,
        result=step.result,
        sources=[_source(source) for source in step.sources],
        note=step.note,
        error=step.error,
        model=step.model,
        input_tokens=step.usage.total_input_tokens,
        output_tokens=step.usage.output_tokens,
        web_searches=step.usage.web_searches,
        cost_usd=round(step.cost_usd, 6),
        duration_ms=_ms(step.started_at, step.finished_at),
    )


def _source(source: Source) -> SourceView:
    return SourceView(url=source.url, title=source.title)


def _ms(start: float | None, end: float | None) -> int | None:
    return round((end - start) * 1000) if start is not None and end is not None else None
