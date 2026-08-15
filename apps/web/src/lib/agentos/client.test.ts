import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, formatApiError } from "@/lib/agentos/client";
import { SseError } from "@/lib/agentos/types";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("formatApiError", () => {
  it("includes HTTP status and body detail", () => {
    expect(
      formatApiError(
        new ApiError(
          "API GET /admin/tools/1/capabilities failed (422): ignored",
          422,
          "MCP server at mcp.groww.in requires authentication (401: Authentication required). No credential is bound.",
        ),
      ),
    ).toContain("422:");
    expect(
      formatApiError(
        new ApiError("x", 422, "MCP server at mcp.groww.in requires authentication"),
      ),
    ).toContain("requires authentication");
  });

  it("surfaces AgentOS stream 400 body instead of a generic status", () => {
    const message = formatApiError(
      new SseError(
        "AgentOS stream failed (400)",
        400,
        JSON.stringify({ detail: "Select at least one reviewed MCP tool" }),
      ),
    );
    expect(message).not.toMatch(/^AgentOS stream failed \(400\)$/);
    expect(message).toMatch(/Detach that MCP tool|reviewed tools/i);
  });

  it("does not surface a bare Failed to fetch", () => {
    const message = formatApiError(new TypeError("Failed to fetch"));
    expect(message).not.toMatch(/^Failed to fetch$/);
    expect(message).toMatch(/Atlas API|backend down|CORS/i);
  });
});

describe("apiFetch", () => {
  it("maps browser network failures instead of Failed to fetch", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(
      apiFetch("/admin/tools/abc/capabilities", { accessToken: "tok" }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
    });
    try {
      await apiFetch("/admin/tools/abc/capabilities", { accessToken: "tok" });
    } catch (reason) {
      const message = formatApiError(reason, "Provider check failed");
      expect(message).not.toMatch(/^Failed to fetch$/);
      expect(message).toMatch(/admin\/tools\/abc\/capabilities/);
      expect(message).toMatch(/not called from the browser/i);
    }
  });

  it("keeps status and body from Atlas API errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail:
              "MCP server at mcp.groww.in requires authentication (401: Authentication required). No credential is bound.",
          }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    try {
      await apiFetch("/admin/tools/abc/capabilities", { accessToken: "tok" });
      throw new Error("expected failure");
    } catch (reason) {
      expect(formatApiError(reason)).toContain("422:");
      expect(formatApiError(reason)).toContain("401");
      expect(formatApiError(reason)).toContain("No credential is bound");
    }
  });
});
