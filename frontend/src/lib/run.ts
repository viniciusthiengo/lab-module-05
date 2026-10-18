// The live state of a task, built from its events: the timeline of the Supervisor's
// decisions and the workers' steps, the text being written right now, and the result.

import type {
  AgentName,
  DelegatedStep,
  RunEvent,
  RunOptions,
  RunStatus,
  RunView,
  Source,
  StepStatus,
} from "./types";

export type Activity =
  | { kind: "memory"; key: string; notes: { id: string; task: string }[] }
  | {
      kind: "decision";
      key: string;
      iteration: number;
      decision: "delegate" | "final";
      reason: string;
      steps: DelegatedStep[];
      forced: boolean;
    }
  | {
      kind: "step";
      key: string;
      number: number;
      agent: AgentName;
      task: string;
      context: number[];
      status: StepStatus;
      /** The result, or what the worker has written so far. */
      text: string;
      sources: Source[];
      note: string | null;
      error: string | null;
    }
  | { kind: "protocol_error"; key: string; message: string }
  | { kind: "feedback"; key: string; round: number; feedback: string };

export interface LiveRun {
  id: string;
  task: string;
  options: RunOptions;
  status: RunStatus;
  activity: Activity[];
  /** What the Supervisor is writing while it decides. */
  thinking: { iteration: number; forced: boolean; text: string } | null;
  final: string | null;
  forced: boolean;
  note: string | null;
  error: string | null;
  costUsd: number;
  /** The last event applied: the stream resumes after it. */
  lastEventId: number;
  /** The task's full view, fetched when it stops (per-agent usage, durations). */
  view: RunView | null;
}

/** A task that just started: its events will fill it. */
export function initialRun(view: RunView): LiveRun {
  return {
    id: view.id,
    task: view.task,
    options: view.options,
    status: view.status,
    activity: [],
    thinking: null,
    final: null,
    forced: false,
    note: null,
    error: null,
    costUsd: 0,
    lastEventId: 0,
    view: null,
  };
}

/** `run` after `event`. Events already applied (a reconnecting stream) change nothing. */
export function applyEvent(run: LiveRun, event: RunEvent): LiveRun {
  if (event.id <= run.lastEventId) return run;
  const next = { ...run, lastEventId: event.id };
  const key = String(event.id);
  switch (event.type) {
    case "run_started":
      return next;
    case "memory":
      return event.notes.length
        ? { ...next, activity: [...run.activity, { kind: "memory", key, notes: event.notes }] }
        : next;
    case "agent_started":
      if (event.agent === "supervisor") {
        return {
          ...next,
          thinking: { iteration: event.iteration ?? 0, forced: event.forced ?? false, text: "" },
        };
      }
      return updateStep(next, event.step, (step) => ({ ...step, status: "running", text: "" }));
    case "delta":
      if (event.step === null) {
        return run.thinking
          ? { ...next, thinking: { ...run.thinking, text: run.thinking.text + event.text } }
          : next;
      }
      return updateStep(next, event.step, (step) =>
        step.status === "running" ? { ...step, text: step.text + event.text } : step,
      );
    case "protocol_error":
      return {
        ...next,
        thinking: run.thinking && { ...run.thinking, text: "" },
        activity: [...run.activity, { kind: "protocol_error", key, message: event.message }],
      };
    case "decision": {
      const steps = event.steps ?? [];
      const decision: Activity = {
        kind: "decision",
        key,
        iteration: event.iteration,
        decision: event.kind,
        reason: event.reason,
        steps,
        forced: event.forced ?? false,
      };
      const pending: Activity[] = steps.map((step) => ({
        kind: "step",
        key: `${key}-${step.number}`,
        number: step.number,
        agent: step.agent,
        task: step.task,
        context: step.context,
        status: "pending",
        text: "",
        sources: [],
        note: null,
        error: null,
      }));
      return { ...next, thinking: null, activity: [...run.activity, decision, ...pending] };
    }
    case "agent_finished":
      return updateStep(next, event.step, (step) => ({
        ...step,
        status: event.status,
        text: event.status === "completed" ? (event.result ?? step.text) : step.text,
        sources: event.sources ?? [],
        note: event.note ?? null,
        error: event.error ?? null,
      }));
    case "usage":
      return { ...next, costUsd: event.cost_usd };
    case "awaiting_approval":
      return {
        ...next,
        status: "awaiting_approval",
        thinking: null,
        final: event.final,
        forced: event.forced,
        note: event.note,
      };
    case "approved":
      return next;
    case "feedback":
      return {
        ...next,
        status: "running",
        final: null,
        forced: false,
        note: null,
        activity: [
          ...run.activity,
          { kind: "feedback", key, round: event.round, feedback: event.feedback },
        ],
      };
    case "run_finished":
      return {
        ...next,
        status: event.status,
        thinking: null,
        final: event.final,
        forced: event.forced,
        note: event.note,
        error: event.error,
        costUsd: event.cost_usd,
        activity: run.activity.map((item) =>
          item.kind === "step" && (item.status === "running" || item.status === "pending")
            ? { ...item, status: "failed", error: item.error ?? "Stopped." }
            : item,
        ),
      };
  }
}

function updateStep(
  run: LiveRun,
  number: number | undefined,
  change: (step: Extract<Activity, { kind: "step" }>) => Activity,
): LiveRun {
  return {
    ...run,
    activity: run.activity.map((item) =>
      item.kind === "step" && item.number === number ? change(item) : item,
    ),
  };
}

/** The agents working right now, and on what. */
export function working(run: LiveRun): { agent: AgentName; step: number | null; task: string }[] {
  const busy: { agent: AgentName; step: number | null; task: string }[] = [];
  if (run.thinking) {
    busy.push({
      agent: "supervisor",
      step: null,
      task: run.thinking.forced ? "Writing the final result" : "Deciding the next step",
    });
  }
  for (const item of run.activity) {
    if (item.kind === "step" && item.status === "running") {
      busy.push({ agent: item.agent, step: item.number, task: item.task });
    }
  }
  return busy;
}

/** Whether the task still changes without the user. */
export function isActive(run: LiveRun): boolean {
  return run.status === "running";
}
