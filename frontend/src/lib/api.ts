// The browser's calls to this app's /api proxy, which forwards them to the backend.

import type { Agent, Health, MemoryNote, RunRequest, RunView } from "./types";

/** A failed call, with a message that can be shown to the user. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Fetch = typeof fetch;

export function getHealth(fetcher: Fetch = fetch) {
  return send<Health>("/api/health", { method: "GET" }, fetcher);
}

export function getAgents(fetcher: Fetch = fetch) {
  return send<Agent[]>("/api/agents", { method: "GET" }, fetcher);
}

/** Starts a task in the background; follow it with `eventsUrl`. */
export function startRun(request: RunRequest, fetcher: Fetch = fetch) {
  return send<RunView>("/api/runs", { method: "POST", body: request }, fetcher);
}

export function getRun(id: string, fetcher: Fetch = fetch) {
  return send<RunView>(`/api/runs/${encodeURIComponent(id)}`, { method: "GET" }, fetcher);
}

/** The task's Server-Sent Events, after the event `after`. */
export function eventsUrl(id: string, after: number): string {
  return `/api/runs/${encodeURIComponent(id)}/events?after=${after}`;
}

export function approveRun(id: string, fetcher: Fetch = fetch) {
  return send<RunView>(
    `/api/runs/${encodeURIComponent(id)}/approve`,
    { method: "POST", body: {} },
    fetcher,
  );
}

/** Sends the result back with feedback: a new round starts. */
export function rejectRun(id: string, feedback: string, fetcher: Fetch = fetch) {
  return send<RunView>(
    `/api/runs/${encodeURIComponent(id)}/reject`,
    { method: "POST", body: { feedback } },
    fetcher,
  );
}

export function cancelRun(id: string, fetcher: Fetch = fetch) {
  return send<RunView>(
    `/api/runs/${encodeURIComponent(id)}/cancel`,
    { method: "POST", body: {} },
    fetcher,
  );
}

export async function getMemory(fetcher: Fetch = fetch): Promise<MemoryNote[]> {
  return (await send<{ notes: MemoryNote[] }>("/api/memory", { method: "GET" }, fetcher)).notes;
}

export function forgetNote(id: string, fetcher: Fetch = fetch) {
  return send<null>(`/api/memory/${encodeURIComponent(id)}`, { method: "DELETE" }, fetcher);
}

export function clearMemory(fetcher: Fetch = fetch) {
  return send<{ deleted: number }>("/api/memory", { method: "DELETE" }, fetcher);
}

/** The message to show for a failed answer with `status` and `body`. */
export function errorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
    return body.detail;
  }
  if (status === 404) return "Not found on the server.";
  if (status === 504) return "The server took too long to answer. Try again.";
  if (status >= 500) return "The research service is unavailable. Try again in a moment.";
  return "Something went wrong. Try again.";
}

async function send<T>(
  path: string,
  { method, body }: { method: "GET" | "POST" | "DELETE"; body?: unknown },
  fetcher: Fetch,
): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError("Could not reach the server. Check your connection and try again.", 0);
  }
  if (response.status === 204) return null as T;
  const answer: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(errorMessage(response.status, answer), response.status);
  return answer as T;
}
