"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api";
import type { MemoryNote } from "@/lib/types";

import styles from "./Team.module.css";
import ui from "./ui.module.css";

interface Props {
  notes: MemoryNote[];
  onForget: (id: string) => Promise<void>;
  onForgetAll: () => Promise<void>;
}

/** What the team remembers from earlier tasks, which the Supervisor recalls when related. */
export function MemoryPanel({ notes, onForget, onForgetAll }: Props) {
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<void>) {
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Could not change the memory.");
    }
  }

  return (
    <section className={ui.panel} aria-labelledby="memory-title">
      <div className={ui.panelHeader}>
        <h2 id="memory-title">Memory</h2>
        {notes.length > 0 && (
          <button type="button" className={ui.link} onClick={() => void run(onForgetAll)}>
            Forget all
          </button>
        )}
      </div>
      <p className={ui.hint}>
        Finished tasks are remembered; the Supervisor gets the related ones with each new task.
      </p>
      {notes.length === 0 ? (
        <p className={ui.empty}>Nothing remembered yet.</p>
      ) : (
        <ul className={styles.notes}>
          {notes.slice(0, 20).map((note) => (
            <li key={note.id}>
              <details>
                <summary>{note.task}</summary>
                <p className={styles.summary}>{note.summary}</p>
              </details>
              <button
                type="button"
                className={ui.link}
                aria-label={`Forget “${note.task}”`}
                onClick={() => void run(() => onForget(note.id))}
              >
                Forget
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className={ui.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
