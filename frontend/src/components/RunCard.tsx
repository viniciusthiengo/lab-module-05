"use client";

import { cx } from "@/lib/dom";
import { duration, usd } from "@/lib/format";
import type { LiveRun } from "@/lib/run";
import type { RunStatus } from "@/lib/types";

import { ActivityFeed } from "./ActivityFeed";
import { ApprovalBox } from "./ApprovalBox";
import styles from "./Chat.module.css";
import { FinalOutput } from "./FinalOutput";
import ui from "./ui.module.css";

const STATUS: Record<RunStatus, { label: string; tone: string }> = {
  running: { label: "Working", tone: "" },
  awaiting_approval: { label: "Waiting for your approval", tone: ui.chipWarning },
  completed: { label: "Completed", tone: ui.chipSuccess },
  failed: { label: "Failed", tone: ui.chipDanger },
  cancelled: { label: "Cancelled", tone: "" },
};

interface Props {
  run: LiveRun;
  onApprove: () => void;
  onReject: (feedback: string) => void;
  onCancel: () => void;
}

/** One task: what was asked, the team's activity, and the result. */
export function RunCard({ run, onApprove, onReject, onCancel }: Props) {
  const status = STATUS[run.status];
  const decisions = run.activity.filter((item) => item.kind === "decision").length;
  const options = [
    `${run.options.max_iterations} decisions`,
    run.options.web_search ? "web search" : "no web search",
    ...(run.options.require_approval ? ["approval"] : []),
    run.options.use_memory ? "memory" : "no memory",
  ];

  return (
    <li className={styles.run}>
      <div className={styles.question}>
        <p>{run.task}</p>
        <span className={styles.questionOptions}>{options.join(" · ")}</span>
      </div>
      <div className={styles.reply}>
        <div className={styles.runHeader} aria-live="polite">
          <span className={cx(ui.chip, status.tone)}>
            {run.status === "running" && <span className={ui.spinner} aria-hidden />}
            {status.label}
          </span>
          <span className={ui.hint}>
            {decisions} of {run.options.max_iterations} decisions · {usd(run.costUsd)}
            {run.view?.duration_ms != null && ` · ${duration(run.view.duration_ms)}`}
          </span>
          {run.status === "running" && (
            <button type="button" className={cx(ui.link, styles.cancel)} onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
        <ActivityFeed run={run} />
        {run.final && (
          <FinalOutput
            task={run.task}
            final={run.final}
            forced={run.forced}
            note={run.note}
            sources={run.view?.sources ?? []}
            draft={run.status === "awaiting_approval"}
          />
        )}
        {run.status === "awaiting_approval" && (
          <ApprovalBox onApprove={onApprove} onReject={onReject} onCancel={onCancel} />
        )}
        {run.error && (
          <p className={ui.alert} role="alert">
            {run.error}
          </p>
        )}
      </div>
    </li>
  );
}
