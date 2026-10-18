// Server-side calls from the /api route to the FastAPI backend (BACKEND_URL). The browser only
// talks to this app, so the backend needs no CORS and its address stays private. Only the
// routes below are forwarded; the event stream of a task is passed through as it comes.

type Method = "GET" | "POST" | "DELETE";

interface Route {
  method: Method;
  /** The path after /api/. */
  path: RegExp;
  /** Seconds the backend may take to answer (streams: how long the stream may stay open). */
  timeout: number;
  /** Accepts `?since=<version>&wait=<seconds>` (long polling). */
  longPoll?: boolean;
  /** A Server-Sent Events stream, which accepts `?after=<event id>`. */
  stream?: boolean;
}

const RUN = "runs/[A-Za-z0-9_-]{8,32}";
export const MAX_WAIT_SECONDS = 25;

export const ROUTES: readonly Route[] = [
  { method: "GET", path: /^health$/, timeout: 15 },
  { method: "GET", path: /^agents$/, timeout: 15 },
  { method: "POST", path: /^runs$/, timeout: 30 },
  { method: "GET", path: new RegExp(`^${RUN}$`), timeout: 15, longPoll: true },
  { method: "GET", path: new RegExp(`^${RUN}/events$`), timeout: 280, stream: true },
  { method: "POST", path: new RegExp(`^${RUN}/(approve|reject|cancel)$`), timeout: 30 },
  { method: "GET", path: /^memory$/, timeout: 15 },
  { method: "DELETE", path: /^memory$/, timeout: 15 },
  { method: "DELETE", path: /^memory\/[A-Za-z0-9_-]{4,32}$/, timeout: 15 },
];

/** The backend's base URL without a trailing slash, or `null` when it is not configured. */
export function backendUrl(): string | null {
  const value = process.env.BACKEND_URL?.trim();
  return value ? value.replace(/\/+$/, "") : null;
}

/** The visitor's address, forwarded so that the backend can rate-limit per person. */
export function clientIp(request: Request): string | null {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return forwarded || request.headers.get("x-real-ip");
}

/** A `{ detail }` error answer, like the backend's. */
export function failure(status: number, detail: string): Response {
  return Response.json({ detail }, { status });
}

/** Forwards `request` for the API path `segments` when it is an allowed route. */
export async function proxy(request: Request, segments: string[]): Promise<Response> {
  const path = segments.join("/");
  const method = request.method as Method;
  const matching = ROUTES.filter((route) => route.path.test(path));
  if (matching.length === 0) return failure(404, "Unknown API route.");
  const route = matching.find((candidate) => candidate.method === method);
  if (!route) return failure(405, `${request.method} is not allowed here.`);
  const params = new URL(request.url).searchParams;

  if (route.stream) {
    const after = params.get("after");
    if (after !== null && !/^\d+$/.test(after)) {
      return failure(400, "'after' must be an event id.");
    }
    return stream(`/${path}${after === null ? "" : `?after=${after}`}`, request, route.timeout);
  }

  let query = "";
  let timeout = route.timeout;
  if (route.longPoll) {
    const since = params.get("since");
    const wait = Number(params.get("wait") ?? 0);
    if (since !== null && !/^\d+$/.test(since)) {
      return failure(400, "'since' must be a version number.");
    }
    if (!Number.isFinite(wait) || wait < 0 || wait > MAX_WAIT_SECONDS) {
      return failure(400, `'wait' must be between 0 and ${MAX_WAIT_SECONDS} seconds.`);
    }
    if (since !== null) {
      query = `?since=${since}&wait=${wait}`;
      timeout += wait;
    }
  }

  let body: unknown;
  if (method === "POST") {
    body = await jsonBody(request);
    if (body === undefined) return failure(400, "Send a JSON body.");
  }
  return forward(`/${path}${query}`, request, { method, body, timeoutMs: timeout * 1000 });
}

/** The JSON body of `request`, or `undefined` when it is not JSON. */
async function jsonBody(request: Request): Promise<unknown> {
  try {
    return await request.json();
  } catch {
    return undefined;
  }
}

function headers(request: Request, extra: Record<string, string>): Record<string, string> {
  const ip = clientIp(request);
  return { ...extra, ...(ip ? { "X-Client-IP": ip } : {}) };
}

/**
 * Calls `path` on the backend and returns its JSON answer with the same status. Configuration,
 * network, timeout and format problems become `{ detail }` errors.
 */
async function forward(
  path: string,
  request: Request,
  { method, body, timeoutMs }: { method: Method; body?: unknown; timeoutMs: number },
): Promise<Response> {
  const base = backendUrl();
  if (!base) return failure(500, "The server is not configured: BACKEND_URL is missing.");
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      method,
      headers: headers(request, {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      }),
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    return unreachable(error);
  }
  if (response.status === 204) return new Response(null, { status: 204 });
  const answer: unknown = await response.json().catch(() => null);
  if (answer === null) return failure(502, "The research service sent an unexpected answer.");
  return Response.json(answer, { status: response.status });
}

/**
 * Passes the backend's event stream through as it comes. The browser's `Last-Event-ID` (sent
 * when it reconnects) is forwarded, and closing the page closes the backend's stream.
 */
async function stream(path: string, request: Request, seconds: number): Promise<Response> {
  const base = backendUrl();
  if (!base) return failure(500, "The server is not configured: BACKEND_URL is missing.");
  const lastEventId = request.headers.get("last-event-id");
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      headers: headers(request, {
        Accept: "text/event-stream",
        ...(lastEventId && /^\d+$/.test(lastEventId) ? { "Last-Event-ID": lastEventId } : {}),
      }),
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(seconds * 1000)]),
    });
  } catch (error) {
    return unreachable(error);
  }
  const type = response.headers.get("content-type") ?? "";
  if (!response.ok || !type.startsWith("text/event-stream") || !response.body) {
    const answer: unknown = await response.json().catch(() => null);
    if (answer === null) return failure(502, "The research service sent an unexpected answer.");
    return Response.json(answer, { status: response.status });
  }
  return new Response(response.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}

function unreachable(error: unknown): Response {
  const timedOut = error instanceof DOMException && error.name === "TimeoutError";
  return timedOut
    ? failure(504, "The research service took too long to answer. Try again.")
    : failure(502, "The research service is unreachable. Try again in a moment.");
}
