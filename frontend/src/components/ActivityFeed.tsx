"use client";

import { memo, useEffect, useRef, useState } from "react";

import { agentTitle } from "@/lib/agents";
import { cx } from "@/lib/dom";
import { listing, plural } from "@/lib/format";
import type { Activity, LiveRun } from "@/lib/run";

import styles from "./Activity.module.css";
import { AgentBadge } from "./AgentBadge";
import { Markdown } from "./Markdown";
import ui from "./ui.module.css";

/** The conversation of the team: the Supervisor's decisions and the workers' results, live. */
export function ActivityFeed({ run }: { run: LiveRun }) {
  return (
    <ol className={styles.feed} aria-label="Team activity">
      {run.activity.map((item) => (
        <li key={item.key} className={styles.item}>
          <ActivityItem item={item} />
        </li>
      ))}
      {run.thinking && (
        <li className={styles.item}>
          <div className={styles.thinking} data-agent="supervisor">
            <AgentBadge agent="supervisor" active />
            <span className={ui.hint}>
              {run.thinking.forced
                ? "Out of decisions: writing the final result…"
                : `Deciding (decision ${run.thinking.iteration})…`}
            </span>
            {run.thinking.text && <LiveText text={run.thinking.text} mono />}
          </div>
        </li>
      )}
    </ol>
  );
}

function ActivityItem({ item }: { item: Activity }) {
  switch (item.kind) {
    case "memory":
      return (
        <p className={styles.line}>
          <span className={ui.chip}>Memory</span> Recalled{" "}
          {plural(item.notes.length, "earlier task")}:{" "}
          {item.notes.map((note) => `“${note.task}”`).join(", ")}
        </p>
      );
    case "protocol_error":
      return (
        <p className={cx(styles.line, styles.warning)}>
          The Supervisor’s answer did not follow the format ({item.message}) — asked again.
        </p>
      );
    case "feedback":
      return (
        <p className={cx(styles.line, styles.feedback)}>
          <b>You sent the result back</b> (round {item.round}): {item.feedback}
        </p>
      );
    case "decision":
      return <DecisionItem item={item} />;
    case "step":
      return <StepItem item={item} />;
  }
}

function DecisionItem({ item }: { item: Extract<Activity, { kind: "decision" }> }) {
  const agents = item.steps.map((step) => `${agentTitle(step.agent)} (step ${step.number})`);
  return (
    <div className={styles.decision} data-agent="supervisor">
      <AgentBadge agent="supervisor" compact />
      <div>
        <p className={styles.decisionTitle}>
          Decision {item.iteration}:{" "}
          {item.decision === "final" ? (
            <>
              <b>FINAL</b> — the result is ready
              {item.forced && <span className={cx(ui.chip, ui.chipWarning)}>forced</span>}
            </>
          ) : (
            <>
              <b>DELEGATE</b> to {listing(agents)}
              {item.steps.length > 1 && <span className={ui.chip}>in parallel</span>}
            </>
          )}
        </p>
        {item.reason && <p className={styles.reason}>{item.reason}</p>}
      </div>
    </div>
  );
}

const STEP_STATUS = { pending: "waiting", running: "working", completed: "done", failed: "failed" };

/** A worker's step. Memoized: a step that did not change keeps its rendered result (an
 * event changes one step at most, and parsing Markdown again on every event adds up). */
const StepItem = memo(function StepItem({ item }: { item: Extract<Activity, { kind: "step" }> }) {
  const [open, setOpen] = useState(false);
  const done = item.status === "completed";
  return (
    <div
      className={cx(styles.step, item.status === "running" && styles.working)}
      data-agent={item.agent}
    >
      <div className={styles.stepHeader}>
        <AgentBadge agent={item.agent} active={item.status === "running"} />
        <span className={ui.hint}>
          Step {item.number} · {STEP_STATUS[item.status]}
          {item.context.length > 0 &&
            ` · uses ${plural(item.context.length, "step")} (${item.context.join(", ")})`}
        </span>
      </div>
      <p className={styles.task}>
        <span className={styles.taskLabel}>TASK</span> {item.task}
      </p>
      {item.status === "running" && item.text && <LiveText text={item.text} />}
      {done && (
        <div className={cx(styles.result, !open && styles.collapsed)}>
          <Markdown>{item.text}</Markdown>
        </div>
      )}
      {done && (
        <button type="button" className={ui.link} onClick={() => setOpen(!open)}>
          {open ? "Show less" : "Show the whole result"}
        </button>
      )}
      {item.note && <p className={cx(ui.hint, styles.note)}>{item.note}</p>}
      {item.error && <p className={ui.error}>{item.error}</p>}
    </div>
  );
});

/** Text that grows while an agent writes it, kept scrolled to its end. */
function LiveText({ text, mono = false }: { text: string; mono?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = box.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [text]);
  return (
    <div ref={box} className={cx(styles.live, mono && styles.mono)} aria-live="off">
      {text}
      <span className={styles.caret} aria-hidden />
    </div>
  );
}
