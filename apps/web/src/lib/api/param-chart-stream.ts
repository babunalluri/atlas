import { agentOsUrl, formatApiError } from "@/lib/agentos/client";
import { parseSseChunk } from "@/lib/agentos/sse";
import type { ParamChartMonthSnapshot } from "@/lib/api/admin";
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

/** Live Param Chart today overlay via SSE. */
export async function streamParamChart(options: {
  accessToken: string;
  signal?: AbortSignal;
  onState: (state: ParamChartMonthSnapshot) => void;
}): Promise<void> {
  const { accessToken, signal, onState } = options;
  let response: Response;
  try {
    response = await fetch(agentOsUrl("/admin/param-chart/stream"), {
      method: "GET",
      headers: streamHeaders(accessToken),
      signal,
    });
  } catch (reason) {
    if (signal?.aborted) return;
    throw new Error(formatApiError(reason, "Param Chart stream failed"));
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(
      formatApiError(
        new Error(`Param Chart stream failed (${response.status}): ${text}`),
        "Param Chart stream failed",
      ),
    );
  }

  if (!response.body) {
    throw new Error("Param Chart stream missing body");
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
          const payload = JSON.parse(frame.data) as ParamChartMonthSnapshot & {
            error?: string;
          };
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
