import { beforeEach, describe, expect, it, vi } from "vitest";

import { DELETE, GET, POST } from "./[...path]/route";

const backend = vi.fn<typeof fetch>();
const RUN = "AbC123_def";

beforeEach(() => {
  vi.stubEnv("BACKEND_URL", "https://api.example.com/");
  vi.stubGlobal("fetch", backend);
  backend.mockReset();
});

function call(handler: typeof GET, path: string, init: RequestInit = {}): Promise<Response> {
  const url = new URL(`http://localhost/api/${path}`);
  const segments = url.pathname.slice("/api/".length).split("/");
  return handler(new Request(url, init), { params: Promise.resolve({ path: segments }) });
}

function sent(index = 0): { url: string; init: RequestInit; headers: Record<string, string> } {
  const [url, init = {}] = backend.mock.calls[index];
  return { url: String(url), init, headers: init.headers as Record<string, string> };
}

function eventStream(text: string): Response {
  return new Response(new Blob([text]).stream(), {
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("the /api proxy", () => {
  it("starts a task with the visitor's address", async () => {
    backend.mockResolvedValue(Response.json({ id: RUN, status: "running" }, { status: 202 }));

    const response = await call(POST, "runs", {
      method: "POST",
      body: '{"task":"Explain batteries."}',
      headers: { "x-forwarded-for": "203.0.113.7, 10.0.0.1" },
    });

    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({ id: RUN, status: "running" });
    const { url, init, headers } = sent();
    expect(url).toBe("https://api.example.com/runs");
    expect(init.body).toBe('{"task":"Explain batteries."}');
    expect(headers["X-Client-IP"]).toBe("203.0.113.7");
  });

  it("passes a task's event stream through", async () => {
    backend.mockResolvedValue(eventStream('id: 1\ndata: {"id":1,"type":"run_started"}\n\n'));

    const response = await call(GET, `runs/${RUN}/events?after=0`, {
      headers: { "Last-Event-ID": "7" },
    });

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(await response.text()).toBe('id: 1\ndata: {"id":1,"type":"run_started"}\n\n');
    const { url, headers, init } = sent();
    expect(url).toBe(`https://api.example.com/runs/${RUN}/events?after=0`);
    expect(headers["Last-Event-ID"]).toBe("7");
    expect(headers.Accept).toBe("text/event-stream");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("answers the backend's error when there is no stream", async () => {
    backend.mockResolvedValue(Response.json({ detail: "Task not found." }, { status: 404 }));

    const response = await call(GET, `runs/${RUN}/events`);

    expect(response.status).toBe(404);
    expect((await response.json()).detail).toBe("Task not found.");
  });

  it("forwards approvals, feedback, cancellations and memory", async () => {
    backend.mockImplementation(async () => Response.json({ ok: true }));

    await call(POST, `runs/${RUN}/approve`, { method: "POST", body: "{}" });
    await call(POST, `runs/${RUN}/reject`, { method: "POST", body: '{"feedback":"Shorter."}' });
    await call(POST, `runs/${RUN}/cancel`, { method: "POST", body: "{}" });
    await call(GET, "memory");
    await call(DELETE, "memory/note_1", { method: "DELETE" });

    expect(backend.mock.calls.map(([url]) => String(url))).toEqual([
      `https://api.example.com/runs/${RUN}/approve`,
      `https://api.example.com/runs/${RUN}/reject`,
      `https://api.example.com/runs/${RUN}/cancel`,
      "https://api.example.com/memory",
      "https://api.example.com/memory/note_1",
    ]);
  });

  it("only forwards the allowed routes and methods", async () => {
    const unknown = await call(GET, "admin");
    const sync = await call(POST, "run", { method: "POST", body: "{}" });
    const wrongMethod = await call(GET, "runs");
    const badId = await call(GET, "runs/bad!id");

    expect([unknown.status, sync.status, wrongMethod.status, badId.status]).toEqual([
      404, 404, 405, 404,
    ]);
    expect(backend).not.toHaveBeenCalled();
  });

  it.each([
    [`runs/${RUN}/events?after=x`, "'after' must be an event id."],
    [`runs/${RUN}?since=abc`, "'since' must be a version number."],
    [`runs/${RUN}?since=1&wait=99`, "'wait' must be between 0 and 25 seconds."],
  ])("refuses invalid parameters (%s)", async (path, message) => {
    const response = await call(GET, path);

    expect(response.status).toBe(400);
    expect((await response.json()).detail).toBe(message);
  });

  it("refuses a body that is not JSON", async () => {
    const response = await call(POST, "runs", { method: "POST", body: "nope" });

    expect(response.status).toBe(400);
    expect(backend).not.toHaveBeenCalled();
  });

  it("explains timeouts, an unreachable backend and a missing configuration", async () => {
    const start = () => call(POST, "runs", { method: "POST", body: "{}" });

    backend.mockRejectedValueOnce(new DOMException("The operation timed out.", "TimeoutError"));
    expect((await start()).status).toBe(504);

    backend.mockRejectedValueOnce(new TypeError("fetch failed"));
    expect((await start()).status).toBe(502);

    backend.mockResolvedValueOnce(new Response("<html>oops</html>", { status: 500 }));
    expect((await start()).status).toBe(502);

    vi.stubEnv("BACKEND_URL", "");
    const missing = await start();
    expect(missing.status).toBe(500);
    expect((await missing.json()).detail).toContain("BACKEND_URL");
  });
});
