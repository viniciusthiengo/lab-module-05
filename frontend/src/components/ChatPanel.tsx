"use client";

import { useEffect, useRef } from "react";

import type { Health } from "@/lib/types";
import type { useRuns } from "@/lib/useRuns";

import styles from "./Chat.module.css";
import { Composer } from "./Composer";
import { RunCard } from "./RunCard";
import ui from "./ui.module.css";

/** Research tasks the team can try. */
const SUGGESTIONS = [
  "Compare solid-state and lithium-ion batteries for electric cars, in a short report.",
  "Explain how the James Webb Space Telescope sees in infrared, for a high-school class.",
  "What are the pros and cons of a four-day work week? Give the evidence.",
];

interface Props {
  health: Health | null;
  tasks: ReturnType<typeof useRuns>;
}

/** The conversation: each task, its live activity and its result, and the composer. */
export function ChatPanel({ health, tasks }: Props) {
  const list = useRef<HTMLOListElement>(null);
  const count = tasks.runs.length;
  const ready = health?.ready ?? false;

  useEffect(() => {
    // A new task scrolls into view; the page does not jump while agents write.
    list.current?.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [count]);

  return (
    <section className={`${ui.panel} ${styles.chat}`} aria-labelledby="chat-title">
      <div className={ui.panelHeader}>
        <h2 id="chat-title">Tasks</h2>
        {count > 0 && !tasks.busy && (
          <button type="button" className={ui.link} onClick={tasks.clear}>
            New conversation
          </button>
        )}
      </div>

      <ol ref={list} className={styles.conversation}>
        {count === 0 && (
          <li className={styles.welcome}>
            <p>
              Ask for research: a comparison, an explanation, a short report. The Supervisor plans
              the work and delegates it; you see each step live.
            </p>
            <div className={styles.suggestions}>
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  className={styles.suggestion}
                  disabled={!ready || tasks.busy}
                  onClick={() =>
                    void tasks.start({
                      task: suggestion,
                      max_iterations: 5,
                      web_search: health?.web_search ?? true,
                      require_approval: false,
                      use_memory: true,
                      remember: true,
                    })
                  }
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </li>
        )}
        {tasks.runs.map((run) => (
          <RunCard
            key={run.id}
            run={run}
            onApprove={() => void tasks.approve(run.id)}
            onReject={(feedback) => void tasks.reject(run.id, feedback)}
            onCancel={() => void tasks.cancel(run.id)}
          />
        ))}
      </ol>

      {tasks.error && (
        <p className={ui.alert} role="alert">
          {tasks.error}
        </p>
      )}
      <Composer
        disabled={!ready}
        busy={tasks.busy}
        webSearchAvailable={health?.web_search ?? false}
        onSubmit={tasks.start}
      />
      {health && !ready && (
        <p className={ui.hint}>The server has no Claude API key, so tasks cannot run.</p>
      )}
    </section>
  );
}
