import { afterEach, describe, expect, it, vi } from "vitest";

import {
  decodeRunEvent,
  extractTextContent,
  isPausedRunEvent,
  isTerminalRunEvent,
  parseSseChunk,
  parseSseFrame,
  streamAgentRun,
  SseError,
} from "@/lib/agentos/sse";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("parseSseFrame", () => {
  it("parses event, id, and multi-line data", () => {
    const frame = parseSseFrame(
      [
        "id: evt_1",
        "event: RunContent",
        "data: {\"event\":\"RunContent\",",
        'data: "content":"Hello"}',
      ].join("\n"),
    );
    expect(frame).toEqual({
      id: "evt_1",
      event: "RunContent",
      retry: undefined,
      data: '{"event":"RunContent",\n"content":"Hello"}',
    });
  });

  it("ignores comment-only blocks", () => {
    expect(parseSseFrame(": keepalive")).toBeNull();
  });
});

describe("parseSseChunk", () => {
  it("carries incomplete frames across chunks", () => {
    const first = parseSseChunk("event: RunStarted\ndata: {\"event\":\"RunSt");
    expect(first.frames).toHaveLength(0);
    const second = parseSseChunk('arted","run_id":"r1"}\n\n', first.remainder);
    expect(second.frames).toHaveLength(1);
    expect(decodeRunEvent(second.frames[0])?.run_id).toBe("r1");
  });
});

describe("decodeRunEvent", () => {
  it("prefers JSON event field and falls back to frame event", () => {
    const decoded = decodeRunEvent({
      event: "message",
      data: JSON.stringify({ event: "RunCompleted", run_id: "abc" }),
    });
    expect(decoded).toMatchObject({ event: "RunCompleted", run_id: "abc" });
  });
});

describe("event helpers", () => {
  it("detects terminal and paused events", () => {
    expect(isTerminalRunEvent({ event: "RunCompleted" })).toBe(true);
    expect(isTerminalRunEvent({ event: "RunError" })).toBe(true);
    expect(isTerminalRunEvent({ event: "RunContent" })).toBe(false);
    expect(isPausedRunEvent({ event: "RunPaused" })).toBe(true);
  });

  it("extracts nested text content", () => {
    expect(extractTextContent("hi")).toBe("hi");
    expect(extractTextContent({ text: "nested" })).toBe("nested");
    expect(extractTextContent(["a", { text: "b" }])).toBe("ab");
  });
});

describe("streamAgentRun", () => {
  it("rejects missing token and caller-supplied tenant_id", async () => {
    await expect(
      streamAgentRun({
        url: "http://localhost/runs",
        accessToken: "",
        body: { message: "x" },
        onEvent: () => undefined,
      }),
    ).rejects.toBeInstanceOf(SseError);

    await expect(
      streamAgentRun({
        url: "http://localhost/runs",
        accessToken: "tok",
        body: { message: "x", tenant_id: "t1" },
        onEvent: () => undefined,
      }),
    ).rejects.toThrow(/tenant_id must not be supplied/);
  });

  it("streams typed events with auth header and supports cancellation", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'id: 1\nevent: RunStarted\ndata: {"event":"RunStarted","run_id":"r1"}\n\n',
          ),
        );
        controller.enqueue(
          encoder.encode(
            'event: RunContent\ndata: {"event":"RunContent","content":"Hel"}\n\n',
          ),
        );
        controller.enqueue(
          encoder.encode(
            'event: RunContent\ndata: {"event":"RunContent","content":"lo"}\n\n',
          ),
        );
        controller.enqueue(
          encoder.encode(
            'event: RunCompleted\ndata: {"event":"RunCompleted","run_id":"r1"}\n\n',
          ),
        );
        controller.close();
      },
    });

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const events: string[] = [];
    const contents: string[] = [];
    const result = await streamAgentRun({
      url: "http://agentos/agents/runs",
      accessToken: "clerk_jwt",
      lastEventId: "0",
      body: {
        message: "hi",
        stream: true,
        factory_input: { agent_config_id: "agt_1", preview: true },
      },
      onEvent: (event) => {
        events.push(String(event.event));
        if (event.content) {
          contents.push(extractTextContent(event.content));
        }
      },
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toMatchObject({
      Authorization: "Bearer clerk_jwt",
      Accept: "text/event-stream",
      "Last-Event-ID": "0",
    });
    expect(events).toEqual([
      "RunStarted",
      "RunContent",
      "RunContent",
      "RunCompleted",
    ]);
    expect(contents.join("")).toBe("Hello");
    expect(result.lastEventId).toBe("1");
  });

  it("honors AbortSignal before the request starts reading", async () => {
    const controller = new AbortController();
    controller.abort();

    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      const signal = init?.signal;
      return new Promise((_resolve, reject) => {
        if (signal?.aborted) {
          reject(new DOMException("Aborted", "AbortError"));
          return;
        }
        signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      streamAgentRun({
        url: "http://agentos/agents/runs",
        accessToken: "tok",
        body: { message: "x" },
        signal: controller.signal,
        onEvent: () => undefined,
      }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  it("surfaces non-OK HTTP responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nope", { status: 401 })),
    );
    await expect(
      streamAgentRun({
        url: "http://agentos/agents/runs",
        accessToken: "tok",
        body: { message: "x" },
        onEvent: () => undefined,
      }),
    ).rejects.toMatchObject({ status: 401, name: "SseError" });
  });
});
