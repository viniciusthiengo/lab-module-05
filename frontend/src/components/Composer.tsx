"use client";

import { type KeyboardEvent, useId, useState } from "react";

import type { RunRequest } from "@/lib/types";

import styles from "./Chat.module.css";
import ui from "./ui.module.css";

const MAX_TASK = 4000;

interface Props {
  disabled: boolean;
  busy: boolean;
  webSearchAvailable: boolean;
  onSubmit: (request: RunRequest) => Promise<boolean>;
}

/** The task and how the team should work on it. */
export function Composer({ disabled, busy, webSearchAvailable, onSubmit }: Props) {
  const id = useId();
  const [task, setTask] = useState("");
  const [iterations, setIterations] = useState(5);
  const [webSearch, setWebSearch] = useState(true);
  const [approval, setApproval] = useState(false);
  const [memory, setMemory] = useState(true);

  async function submit() {
    const trimmed = task.trim();
    if (!trimmed || busy || disabled) return;
    const started = await onSubmit({
      task: trimmed,
      max_iterations: iterations,
      web_search: webSearch && webSearchAvailable,
      require_approval: approval,
      use_memory: memory,
      remember: memory,
    });
    if (started) setTask("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form
      className={styles.composer}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <label htmlFor={`${id}-task`} className="visually-hidden">
        Research task
      </label>
      <textarea
        id={`${id}-task`}
        className={styles.input}
        value={task}
        rows={3}
        maxLength={MAX_TASK}
        placeholder="What should the team research? For example: the state of fusion energy in 2026, as a one-page brief."
        disabled={disabled}
        onChange={(event) => setTask(event.currentTarget.value)}
        onKeyDown={onKeyDown}
      />
      <div className={styles.options}>
        <label className={styles.option} title="Decisions of the Supervisor before it must finish">
          Decisions
          <select
            value={iterations}
            onChange={(event) => setIterations(Number(event.currentTarget.value))}
          >
            {Array.from({ length: 10 }, (_, n) => n + 1).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.option} title="The Researcher and the Fact-checker search the web">
          <input
            type="checkbox"
            checked={webSearch && webSearchAvailable}
            disabled={!webSearchAvailable}
            onChange={(event) => setWebSearch(event.currentTarget.checked)}
          />
          Web search
        </label>
        <label className={styles.option} title="The result waits for your approval or feedback">
          <input
            type="checkbox"
            checked={approval}
            onChange={(event) => setApproval(event.currentTarget.checked)}
          />
          Approve before finishing
        </label>
        <label className={styles.option} title="Recall related earlier tasks, and keep this one">
          <input
            type="checkbox"
            checked={memory}
            onChange={(event) => setMemory(event.currentTarget.checked)}
          />
          Memory
        </label>
        <button
          type="submit"
          className={`${ui.primary} ${styles.send}`}
          disabled={disabled || busy || !task.trim()}
        >
          {busy ? (
            <>
              <span className={ui.spinner} aria-hidden /> Working…
            </>
          ) : (
            "Start"
          )}
        </button>
      </div>
      <span className={ui.hint}>Enter to start · Shift+Enter for a new line</span>
    </form>
  );
}
