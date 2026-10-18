"""Request bodies and answers of the API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TASK_CHARS = 4_000
MAX_FEEDBACK_CHARS = 2_000

# --- Requests -----------------------------------------------------------------------------


class RunRequest(BaseModel):
    """Body of ``POST /run`` and ``POST /runs``."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(
        min_length=1,
        max_length=MAX_TASK_CHARS,
        examples=["Compare solid-state and lithium-ion batteries for electric cars."],
    )
    max_iterations: int = Field(
        default=5, ge=1, le=10, description="Decisions of the Supervisor before it must finish."
    )
    web_search: bool = Field(
        default=True, description="Let the Researcher and the Fact-checker search the web."
    )
    require_approval: bool = Field(
        default=False, description="Wait for the user's approval before the result is final."
    )
    use_memory: bool = Field(default=True, description="Recall related earlier tasks.")
    remember: bool = Field(default=True, description="Keep this task's result for later tasks.")

    @field_validator("task")
    @classmethod
    def _check_task(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The task is empty.")
        return value.strip()


class FeedbackRequest(BaseModel):
    """Body of ``POST /runs/{id}/reject``."""

    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(min_length=1, max_length=MAX_FEEDBACK_CHARS)

    @field_validator("feedback")
    @classmethod
    def _check_feedback(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Say what to change.")
        return value.strip()


# --- Answers ------------------------------------------------------------------------------


class SourceView(BaseModel):
    url: str
    title: str


class UsageView(BaseModel):
    model_calls: int
    input_tokens: int
    output_tokens: int
    web_searches: int
    cost_usd: float


class AgentUsageView(UsageView):
    agent: str
    title: str
    model: str


class StepView(BaseModel):
    number: int
    agent: str
    task: str
    context: list[int] = Field(description="The earlier steps the worker was given.")
    round: int
    iteration: int
    status: Literal["pending", "running", "completed", "failed"]
    result: str
    sources: list[SourceView]
    note: str | None
    error: str | None
    model: str | None
    input_tokens: int
    output_tokens: int
    web_searches: int
    cost_usd: float
    duration_ms: int | None


class DecisionView(BaseModel):
    round: int
    iteration: int
    kind: Literal["delegate", "final"]
    reason: str
    steps: list[int] = Field(description="The steps it delegated.")
    forced: bool = Field(description="The decision limit forced the final result.")


class MemoryNoteView(BaseModel):
    id: str
    task: str
    summary: str
    created_at: float
    score: float


class FeedbackView(BaseModel):
    round: int
    feedback: str


class OptionsView(BaseModel):
    max_iterations: int
    web_search: bool
    require_approval: bool
    use_memory: bool
    remember: bool


class RunView(BaseModel):
    id: str
    task: str
    status: Literal["running", "awaiting_approval", "completed", "failed", "cancelled"]
    options: OptionsView
    final: str | None = Field(description="The final result, in Markdown.")
    forced: bool
    note: str | None
    error: str | None
    sources: list[SourceView]
    decisions: list[DecisionView] = Field(description="The Supervisor's decisions, in order.")
    steps: list[StepView] = Field(description="The workers' steps, in order.")
    iterations: int = Field(description="Decisions made, every round together.")
    round: int = Field(description="Feedback rounds so far.")
    memories: list[MemoryNoteView]
    feedback: list[FeedbackView]
    usage: UsageView
    agents: list[AgentUsageView]
    cap_usd: float
    actions: list[Literal["approve", "reject", "cancel"]]
    created_at: float
    finished_at: float | None
    duration_ms: int | None
    version: int
    last_event_id: int


class AgentView(BaseModel):
    name: str
    title: str
    description: str
    model: str
    searches: bool


class MemoryListView(BaseModel):
    notes: list[MemoryNoteView]


class ErrorResponse(BaseModel):
    detail: str
