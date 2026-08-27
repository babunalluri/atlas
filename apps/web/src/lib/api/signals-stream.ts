import { agentOsUrl, formatApiError } from "@/lib/agentos/client";
import { parseSseChunk } from "@/lib/agentos/sse";
import type { SignalEngineState } from "@/lib/api/admin";
import { unpackAccessContext } from "@/lib/auth/access-context";
import { devTenantHeaders } from "@/lib/auth/token";

function streamHeaders(accessToken: string): Record<string, string> {
  const access = unpackAccessContext(accessToken);
  return {
    Accept: "text/event-stream",
    Authorization: `Bearer ${access.token}`,
    ...(access.platformTenantId
      ? { "X-Platform-Tenant-ID": access.platformTenantId }
      : {}),
    ...devTenantHeaders(),
  };
}

/** Live signal board via SSE (~8 Hz, coalesced server-side). */
export async function streamSignalState(options: {
  accessToken: string;
  signal?: AbortSignal;
  /** Matrix row to watch (warm switch without structural flush). */
  instrument?: string | null;
  onState: (state: SignalEngineState) => void;
}): Promise<void> {
  const { accessToken, signal, onState, instrument } = options;
  const qs =
    instrument && instrument.trim()
      ? `?instrument=${encodeURIComponent(instrument.trim())}`
      : "";
  let response: Response;
  try {
    response = await fetch(agentOsUrl(`/admin/signals/stream${qs}`), {
      method: "GET",
      headers: streamHeaders(accessToken),
      signal,
    });
  } catch (reason) {
    if (signal?.aborted) return;
    throw new Error(formatApiError(reason, "Signal stream failed"));
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      formatApiError(
        new Error(`Signal stream failed (${response.status}): ${text}`),
        "Signal stream failed",
      ),
    );
  }

  if (!response.body) {
    throw new Error("Signal stream missing body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let remainder = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const parsed = parseSseChunk(decoder.decode(value, { stream: true }), remainder);
      remainder = parsed.remainder;
      for (const frame of parsed.frames) {
        if (!frame.data?.trim()) continue;
        try {
          const payload = JSON.parse(frame.data) as SignalEngineState & {
            error?: string;
          };
          if (payload.error) {
            throw new Error(payload.error);
          }
          onState(payload);
        } catch {
          // Ignore malformed frames.
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
