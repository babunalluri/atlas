import { agentOsUrl, formatApiError } from "@/lib/agentos/client";
import { parseSseChunk } from "@/lib/agentos/sse";
import type { OptionsChainSnapshot } from "@/lib/api/admin";
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

/** Live Options Lab chain via SSE (~8 Hz, coalesced server-side). */
export async function streamOptionsChain(options: {
  accessToken: string;
  wings: number;
  signal?: AbortSignal;
  onState: (state: OptionsChainSnapshot) => void;
}): Promise<void> {
  const { accessToken, wings, signal, onState } = options;
  const params = new URLSearchParams({ wings: String(wings) });
  let response: Response;
  try {
    response = await fetch(agentOsUrl(`/admin/options-lab/stream?${params}`), {
      method: "GET",
      headers: streamHeaders(accessToken),
      signal,
    });
  } catch (reason) {
    if (signal?.aborted) return;
    throw new Error(formatApiError(reason, "Options Lab stream failed"));
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      formatApiError(
        new Error(`Options Lab stream failed (${response.status}): ${text}`),
        "Options Lab stream failed",
      ),
    );
  }

  if (!response.body) {
    throw new Error("Options Lab stream missing body");
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
          const payload = JSON.parse(frame.data) as OptionsChainSnapshot & {
            error?: string;
          };
          if (payload.error && !payload.ok) {
            onState(payload);
            continue;
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
