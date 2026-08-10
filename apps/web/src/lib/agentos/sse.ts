import {
  type ParsedSseFrame,
  type RunEventBase,
  type StreamPublicRunOptions,
  type StreamRunOptions,
  SseError,
} from "./types";
import { unpackAccessContext } from "@/lib/auth/access-context";

export type {
  ParsedSseFrame,
  RunEventBase,
  StreamPublicRunOptions,
  StreamRunOptions,
} from "./types";
export { SseError } from "./types";

function isAbortError(err: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      err instanceof DOMException &&
      err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

function abortError(err?: unknown): DOMException {
  if (
    typeof DOMException !== "undefined" &&
    err instanceof DOMException &&
    err.name === "AbortError"
  ) {
    return err;
  }
  return new DOMException("Aborted", "AbortError");
}

/**
 * Parse a complete SSE buffer chunk into frames.
 * Handles multi-line `data:` fields and ignores comments.
 */
export function parseSseChunk(
  chunk: string,
  carry = "",
): { frames: ParsedSseFrame[]; remainder: string } {
  const text = `${carry}${chunk}`;
  const parts = text.split(/\r?\n\r?\n/);
  const remainder = parts.pop() ?? "";
  const frames: ParsedSseFrame[] = [];

  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed || trimmed.startsWith(":")) {
      continue;
    }
    const frame = parseSseFrame(trimmed);
    if (frame) {
      frames.push(frame);
    }
  }

  return { frames, remainder };
}

export function parseSseFrame(block: string): ParsedSseFrame | null {
  const lines = block.split(/\r?\n/);
  let id: string | undefined;
  let event: string | undefined;
  let retry: number | undefined;
  const dataLines: string[] = [];

  for (const line of lines) {
    if (!line || line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    switch (field) {
      case "id":
        id = value;
        break;
      case "event":
        event = value;
        break;
      case "retry": {
        const n = Number.parseInt(value, 10);
        if (!Number.isNaN(n)) {
          retry = n;
        }
        break;
      }
      case "data":
        dataLines.push(value);
        break;
      default:
        break;
    }
  }

  if (dataLines.length === 0 && !event && !id) {
    return null;
  }

  return {
    id,
    event,
    retry,
    data: dataLines.join("\n"),
  };
}

export function decodeRunEvent(frame: ParsedSseFrame): RunEventBase | null {
  if (!frame.data) {
    return null;
  }
  try {
    const parsed = JSON.parse(frame.data) as RunEventBase;
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    const eventName =
      typeof parsed.event === "string"
        ? parsed.event
        : frame.event || "message";
    return { ...parsed, event: eventName };
  } catch {
    return {
      event: frame.event || "message",
      content: frame.data,
    };
  }
}

/**
 * Fetch-based SSE consumer for AgentOS runs.
 * Sends Clerk bearer token, supports AbortSignal, rejects caller-supplied tenant IDs.
 */
export async function streamAgentRun(
  options: StreamRunOptions,
): Promise<{ lastEventId?: string }> {
  const { accessToken, url, body, signal, lastEventId, onEvent, onRawFrame } =
    options;

  if (!accessToken) {
    throw new SseError("Missing access token for AgentOS stream");
  }
  const access = unpackAccessContext(accessToken);

  if (
    !(body instanceof FormData) &&
    ("tenant_id" in body || "tenantId" in body)
  ) {
    throw new SseError(
      "tenant_id must not be supplied by the client; it is derived from auth claims",
    );
  }

  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    Authorization: `Bearer ${access.token}`,
  };
  if (access.platformTenantId) {
    headers["X-Platform-Tenant-ID"] = access.platformTenantId;
  }
  if (process.env.NEXT_PUBLIC_DEV_AUTH === "true") {
    headers["X-Dev-Tenant-ID"] =
      process.env.NEXT_PUBLIC_DEV_TENANT_ID ??
      "11111111-1111-1111-1111-111111111111";
    headers["X-Dev-User-ID"] =
      process.env.NEXT_PUBLIC_DEV_USER_ID ?? "dev-admin";
    headers["X-Dev-Role"] =
      process.env.NEXT_PUBLIC_DEV_ROLE ?? "tenant_admin";
  }
  if (!(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (lastEventId) {
    headers["Last-Event-ID"] = lastEventId;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers,
      body: body instanceof FormData ? body : JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (signal?.aborted || isAbortError(err)) {
      throw abortError(err);
    }
    throw err;
  }

  if (!response.ok) {
    const bodyText = await response.text().catch(() => undefined);
    throw new SseError(
      `AgentOS stream failed (${response.status})`,
      response.status,
      bodyText,
    );
  }

  if (!response.body) {
    throw new SseError("AgentOS response missing body stream", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let remainder = "";
  let seenEventId: string | undefined = lastEventId;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      const chunk = decoder.decode(value, { stream: true });
      const parsed = parseSseChunk(chunk, remainder);
      remainder = parsed.remainder;

      for (const frame of parsed.frames) {
        onRawFrame?.(frame);
        if (frame.id) {
          seenEventId = frame.id;
        }
        const event = decodeRunEvent(frame);
        if (event) {
          onEvent(event, frame);
        }
      }
    }

    if (remainder.trim()) {
      const frame = parseSseFrame(remainder.trim());
      if (frame) {
        onRawFrame?.(frame);
        if (frame.id) {
          seenEventId = frame.id;
        }
        const event = decodeRunEvent(frame);
        if (event) {
          onEvent(event, frame);
        }
      }
    }
  } catch (err) {
    if (signal?.aborted || isAbortError(err)) {
      throw abortError(err);
    }
    throw err;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // already released
    }
  }

  return { lastEventId: seenEventId };
}

/**
 * Guest public chat stream — no Clerk bearer token. Tenant is resolved from
 * the URL path on the server; only X-Guest-Id identifies the conversation owner.
 */
export async function streamPublicRun(
  options: StreamPublicRunOptions,
): Promise<{ lastEventId?: string }> {
  const { guestId, url, body, signal, lastEventId, onEvent, onRawFrame } =
    options;

  if (!guestId) {
    throw new SseError("Missing guest id for public chat stream");
  }

  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "X-Guest-Id": guestId,
  };
  if (lastEventId) {
    headers["Last-Event-ID"] = lastEventId;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers,
      body,
      signal,
    });
  } catch (err) {
    if (signal?.aborted || isAbortError(err)) {
      throw abortError(err);
    }
    throw err;
  }

  if (!response.ok) {
    const bodyText = await response.text().catch(() => undefined);
    throw new SseError(
      `Public chat stream failed (${response.status})`,
      response.status,
      bodyText,
    );
  }

  if (!response.body) {
    throw new SseError("Public chat response missing body stream", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let remainder = "";
  let seenEventId: string | undefined = lastEventId;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      const chunk = decoder.decode(value, { stream: true });
      const parsed = parseSseChunk(chunk, remainder);
      remainder = parsed.remainder;

      for (const frame of parsed.frames) {
        onRawFrame?.(frame);
        if (frame.id) {
          seenEventId = frame.id;
        }
        const event = decodeRunEvent(frame);
        if (event) {
          onEvent(event, frame);
        }
      }
    }

    if (remainder.trim()) {
      const frame = parseSseFrame(remainder.trim());
      if (frame) {
        onRawFrame?.(frame);
        if (frame.id) {
          seenEventId = frame.id;
        }
        const event = decodeRunEvent(frame);
        if (event) {
          onEvent(event, frame);
        }
      }
    }
  } catch (err) {
    if (signal?.aborted || isAbortError(err)) {
      throw abortError(err);
    }
    throw err;
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // already released
    }
  }

  return { lastEventId: seenEventId };
}

export function extractTextContent(content: unknown): string {
  if (content == null) {
    return "";
  }
  if (typeof content === "string") {
    return content;
  }
  if (typeof content === "number" || typeof content === "boolean") {
    return String(content);
  }
  if (Array.isArray(content)) {
    return content.map(extractTextContent).join("");
  }
  if (typeof content === "object" && "text" in content) {
    return extractTextContent((content as { text: unknown }).text);
  }
  return "";
}

export function isTerminalRunEvent(event: RunEventBase): boolean {
  return (
    event.event === "RunCompleted" ||
    event.event === "RunError" ||
    event.event === "RunCancelled"
  );
}

export function isPausedRunEvent(event: RunEventBase): boolean {
  return event.event === "RunPaused";
}
