import { describe, expect, it, vi } from "vitest";

import { ApiError, errorMessage, eventsUrl, getMemory, rejectRun, startRun } from "./api";
import { duration, fileName, listing, plural, usd } from "./format";

describe("formatting", () => {
  it("formats costs, durations and counts", () => {
    expect(usd(0)).toBe("$0");
    expect(usd(0.00421)).toBe("$0.0042");
    expect(usd(0.3456)).toBe("$0.346");
    expect(usd(1.2)).toBe("$1.20");
    expect(duration(850)).toBe("850 ms");
    expect(duration(12_300)).toBe("12.3 s");
    expect(duration(95_000)).toBe("1 min 35 s");
    expect(plural(1, "step")).toBe("1 step");
    expect(plural(3, "step")).toBe("3 steps");
  });

  it("lists names in words", () => {
    expect(listing([])).toBe("");
    expect(listing(["Researcher"])).toBe("Researcher");
    expect(listing(["Researcher", "Writer"])).toBe("Researcher and Writer");
    expect(listing(["A", "B", "C"])).toBe("A, B and C");
  });

  it("names a result's file after its task", () => {
    expect(fileName("Compare solid-state and lithium-ion batteries!")).toBe(
      "compare-solid-state-and-lithium-ion-batteries.md",
    );
    expect(fileName("Explique a fusão nuclear")).toBe("explique-a-fusao-nuclear.md");
    expect(fileName("???")).toBe("result.md");
  });
});

describe("the API client", () => {
  const request = {
    task: "Explain batteries.",
    max_iterations: 5,
    web_search: true,
    require_approval: false,
    use_memory: true,
    remember: true,
  };

  it("starts a task and builds its events URL", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ id: "abc" }));

    expect(await startRun(request, fetcher)).toEqual({ id: "abc" });
    const [path, init] = fetcher.mock.calls[0];
    expect(path).toBe("/api/runs");
    expect(JSON.parse(String(init?.body))).toEqual(request);
    expect(eventsUrl("abc", 12)).toBe("/api/runs/abc/events?after=12");
  });

  it("sends feedback and reads the memory", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json({ id: "abc" }, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ notes: [{ id: "m1" }] }));

    await rejectRun("abc", "Shorter.", fetcher);
    const notes = await getMemory(fetcher);

    expect(fetcher.mock.calls[0][0]).toBe("/api/runs/abc/reject");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ feedback: "Shorter." });
    expect(notes).toEqual([{ id: "m1" }]);
  });

  it("turns failures into messages", async () => {
    const busy = vi
      .fn<typeof fetch>()
      .mockResolvedValue(Response.json({ detail: "The team is busy." }, { status: 503 }));
    const offline = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(startRun(request, busy)).rejects.toEqual(new ApiError("The team is busy.", 503));
    await expect(startRun(request, offline)).rejects.toThrow("Could not reach the server");
    expect(errorMessage(504, null)).toBe("The server took too long to answer. Try again.");
    expect(errorMessage(502, "<html>")).toBe(
      "The research service is unavailable. Try again in a moment.",
    );
  });
});
