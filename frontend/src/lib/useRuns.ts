"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, approveRun, cancelRun, eventsUrl, getRun, rejectRun, startRun } from "./api";
import { applyEvent, initialRun, type LiveRun } from "./run";
import type { RunEvent, RunRequest } from "./types";

/**
 * The tasks of this page. Each one is followed live through its Server-Sent Events (the
 * browser's EventSource reconnects by itself and resumes after the last event); when a task
 * stops, its full view is fetched and `onStopped` runs.
 */
export function useRuns(onStopped: () => void) {
  const [runs, setRuns] = useState<LiveRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const streams = useRef(new Map<string, EventSource>());
  const cursors = useRef(new Map<string, number>());

  const update = useCallback((id: string, change: (run: LiveRun) => LiveRun) => {
    setRuns((current) => current.map((run) => (run.id === id ? change(run) : run)));
  }, []);

  const follow = useCallback(
    (id: string) => {
      streams.current.get(id)?.close();
      const source = new EventSource(eventsUrl(id, cursors.current.get(id) ?? 0));
      streams.current.set(id, source);
      source.onmessage = (message) => {
        const event = JSON.parse(message.data) as RunEvent;
        cursors.current.set(id, Math.max(cursors.current.get(id) ?? 0, event.id));
        update(id, (run) => applyEvent(run, event));
      };
      source.addEventListener("end", () => {
        source.close();
        streams.current.delete(id);
        getRun(id)
          .then((view) => update(id, (run) => ({ ...run, view })))
          .catch(() => undefined);
        onStopped();
      });
      source.onerror = () => {
        // A dropped connection reconnects by itself; a refused one (an expired task) is over.
        if (source.readyState === EventSource.CLOSED) {
          streams.current.delete(id);
          update(id, (run) => ({ ...run, error: run.error ?? "Lost the live feed of this task." }));
        }
      };
    },
    [onStopped, update],
  );

  useEffect(() => {
    const open = streams.current;
    return () => {
      for (const source of open.values()) source.close();
    };
  }, []);

  const start = useCallback(
    async (request: RunRequest) => {
      setError(null);
      try {
        const view = await startRun(request);
        setRuns((current) => [...current, initialRun(view)]);
        follow(view.id);
        return true;
      } catch (failure) {
        setError(messageOf(failure, "Could not start the task."));
        return false;
      }
    },
    [follow],
  );

  /** Runs an action on a task, then follows the events it causes. */
  const act = useCallback(
    async (id: string, action: () => Promise<unknown>) => {
      try {
        await action();
        follow(id);
      } catch (failure) {
        update(id, (run) => ({ ...run, error: messageOf(failure, "The action failed.") }));
      }
    },
    [follow, update],
  );

  const approve = useCallback((id: string) => act(id, () => approveRun(id)), [act]);
  const reject = useCallback(
    (id: string, feedback: string) => act(id, () => rejectRun(id, feedback)),
    [act],
  );
  const cancel = useCallback((id: string) => act(id, () => cancelRun(id)), [act]);
  const clear = useCallback(() => {
    for (const source of streams.current.values()) source.close();
    streams.current.clear();
    cursors.current.clear();
    setRuns([]);
  }, []);

  const busy = runs.some((run) => run.status === "running");
  return { runs, busy, error, start, approve, reject, cancel, clear };
}

function messageOf(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}
