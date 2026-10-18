"use client";

import { useId, useState } from "react";

import styles from "./Chat.module.css";
import ui from "./ui.module.css";

interface Props {
  onApprove: () => void;
  onReject: (feedback: string) => void;
  onCancel: () => void;
}

/** Human in the loop: approve the result, or send it back with what to change. */
export function ApprovalBox({ onApprove, onReject, onCancel }: Props) {
  const id = useId();
  const [feedback, setFeedback] = useState("");

  return (
    <form
      className={styles.approval}
      onSubmit={(event) => {
        event.preventDefault();
        if (feedback.trim()) onReject(feedback.trim());
      }}
    >
      <p>
        <b>The team waits for you.</b> Approve the result, or tell the Supervisor what to change: it
        starts a new round with your feedback.
      </p>
      <label htmlFor={`${id}-feedback`} className="visually-hidden">
        What should change
      </label>
      <textarea
        id={`${id}-feedback`}
        className={styles.input}
        rows={2}
        maxLength={2000}
        placeholder="For example: make it shorter, and add a comparison table."
        value={feedback}
        onChange={(event) => setFeedback(event.currentTarget.value)}
      />
      <div className={styles.approvalActions}>
        <button type="button" className={ui.primary} onClick={onApprove}>
          Approve
        </button>
        <button type="submit" className={ui.secondary} disabled={!feedback.trim()}>
          Send back
        </button>
        <button type="button" className={ui.ghost} onClick={onCancel}>
          Cancel the task
        </button>
      </div>
    </form>
  );
}
