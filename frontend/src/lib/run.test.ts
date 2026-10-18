import { describe, expect, it } from "vitest";

import { applyEvent, initialRun, type LiveRun, working } from "./run";
import type { RunEvent, RunView } from "./types";

const OPTIONS = {
  max_iterations: 5,
  web_search: true,
  require_approval: false,
  use_memory: true,
  remember: true,
};

function started(): LiveRun {
  return initialRun({
    id: "AbC123_def",
    task: "Explain batteries.",
    status: "running",
    options: OPTIONS,
  } as RunView);
}

/** An event without its id and time (`Omit` of each member of the union). */
type EventData = RunEvent extends infer E
  ? E extends unknown
    ? Omit<E, "id" | "at">
    : never
  : never;

/** Applies `events`, numbering them from 1. */
function play(events: EventData[], run = started()): LiveRun {
  return events.reduce(
    (state, event, index) => applyEvent(state, { ...event, id: index + 1, at: 0 } as RunEvent),
    run,
  );
}

const DELEGATE: EventData = {
  type: "decision",
  kind: "delegate",
  iteration: 1,
  reason: "Facts first.",
  steps: [
    { number: 1, agent: "researcher", task: "Topic A.", context: [] },
    { number: 2, agent: "researcher", task: "Topic B.", context: [] },
  ],
};

describe("the live state of a task", () => {
  it("follows the Supervisor while it decides", () => {
    const run = play([
      { type: "run_started", task: "Explain batteries.", options: OPTIONS, cap_usd: 0.25 },
      { type: "agent_started", agent: "supervisor", iteration: 1 },
      { type: "delta", agent: "supervisor", step: null, text: "REASON: Facts" },
      { type: "delta", agent: "supervisor", step: null, text: " first." },
    ]);

    expect(run.thinking).toEqual({ iteration: 1, forced: false, text: "REASON: Facts first." });
    expect(working(run)).toEqual([
      { agent: "supervisor", step: null, task: "Deciding the next step" },
    ]);
  });

  it("turns a decision into pending steps, then streams each worker's text", () => {
    const run = play([
      { type: "agent_started", agent: "supervisor", iteration: 1 },
      DELEGATE,
      { type: "agent_started", agent: "researcher", step: 1, task: "Topic A." },
      { type: "agent_started", agent: "researcher", step: 2, task: "Topic B." },
      { type: "delta", agent: "researcher", step: 1, text: "Notes on" },
      { type: "delta", agent: "researcher", step: 1, text: " A" },
    ]);

    expect(run.thinking).toBeNull();
    expect(run.activity.map((item) => item.kind)).toEqual(["decision", "step", "step"]);
    const [, first, second] = run.activity;
    expect(first).toMatchObject({ number: 1, status: "running", text: "Notes on A" });
    expect(second).toMatchObject({ number: 2, status: "running", text: "" });
    expect(working(run).map((entry) => entry.step)).toEqual([1, 2]);
  });

  it("replaces the streamed text with the result, and records failures", () => {
    const run = play([
      DELEGATE,
      { type: "agent_started", agent: "researcher", step: 1, task: "Topic A." },
      { type: "delta", agent: "researcher", step: 1, text: "Draft…" },
      {
        type: "agent_finished",
        agent: "researcher",
        step: 1,
        status: "completed",
        result: "Final notes [1].",
        sources: [{ url: "https://a.org", title: "A" }],
        note: null,
      },
      { type: "agent_finished", agent: "researcher", step: 2, status: "failed", error: "HTTP 500" },
      { type: "usage", agent: "researcher", cost_usd: 0.0123 },
    ]);

    expect(run.activity[1]).toMatchObject({
      status: "completed",
      text: "Final notes [1].",
      sources: [{ url: "https://a.org", title: "A" }],
    });
    expect(run.activity[2]).toMatchObject({ status: "failed", error: "HTTP 500" });
    expect(run.costUsd).toBe(0.0123);
  });

  it("ends with the final result", () => {
    const run = play([
      { type: "agent_started", agent: "supervisor", iteration: 3, forced: true },
      { type: "decision", kind: "final", iteration: 3, reason: "Out of decisions.", forced: true },
      {
        type: "run_finished",
        status: "completed",
        final: "# Result",
        forced: true,
        note: "The decision limit was reached.",
        error: null,
        cost_usd: 0.2,
      },
    ]);

    expect(run.status).toBe("completed");
    expect(run.final).toBe("# Result");
    expect(run.forced).toBe(true);
    expect(run.activity[0]).toMatchObject({ kind: "decision", decision: "final", forced: true });
    expect(working(run)).toEqual([]);
  });

  it("marks unfinished steps of a stopped task as failed", () => {
    const run = play([
      DELEGATE,
      { type: "agent_started", agent: "researcher", step: 1, task: "Topic A." },
      {
        type: "run_finished",
        status: "cancelled",
        final: null,
        forced: false,
        note: null,
        error: null,
        cost_usd: 0.01,
      },
    ]);

    expect(run.activity.slice(1).map((item) => item.kind === "step" && item.status)).toEqual([
      "failed",
      "failed",
    ]);
  });

  it("waits for approval, then starts a new round after feedback", () => {
    const waiting = play([
      { type: "awaiting_approval", final: "# Draft", forced: false, note: null },
    ]);
    const revising = applyEvent(waiting, {
      id: 2,
      at: 0,
      type: "feedback",
      round: 1,
      feedback: "Shorter.",
    });

    expect(waiting.status).toBe("awaiting_approval");
    expect(waiting.final).toBe("# Draft");
    expect(revising.status).toBe("running");
    expect(revising.final).toBeNull();
    expect(revising.activity.at(-1)).toMatchObject({
      kind: "feedback",
      round: 1,
      feedback: "Shorter.",
    });
  });

  it("shows recalled memories and protocol errors", () => {
    const run = play([
      { type: "memory", notes: [{ id: "m1", task: "Earlier task" }] },
      { type: "agent_started", agent: "supervisor", iteration: 1 },
      { type: "delta", agent: "supervisor", step: null, text: "Hmm" },
      { type: "protocol_error", agent: "supervisor", message: "Start with DELEGATE." },
    ]);

    expect(run.activity.map((item) => item.kind)).toEqual(["memory", "protocol_error"]);
    expect(run.thinking?.text).toBe("");
  });

  it("ignores events it already applied", () => {
    const once = play([{ type: "usage", agent: "writer", cost_usd: 0.5 }]);
    const again = applyEvent(once, { id: 1, at: 0, type: "usage", agent: "writer", cost_usd: 9 });

    expect(again).toBe(once);
    expect(again.lastEventId).toBe(1);
  });
});
