import { describe, expect, it } from "vitest";

import {
  extractTraceError,
  formatTraceRunError,
} from "@/lib/activities";

const GROQ_TPM = `Error code: 413 - {'error': {'message': "Request too large for model 'openai/gpt-oss-120b' in organization 'org_01k9xyz' on tokens per minute (TPM): Limit 8000, Requested 9233, Needed 1233. Please reduce the size of your messages or tool schemas. Visit https://console.groq.com/settings/billing to upgrade.", 'type': 'tokens', 'code': 'rate_limit_exceeded'}}`;

describe("formatTraceRunError", () => {
  it("explains Groq TPM rate limits in plain language", () => {
    const result = formatTraceRunError(GROQ_TPM);
    expect(result.title).toBe("Model rate limit exceeded");
    expect(result.summary).toMatch(/TPM/i);
    expect(result.summary).toMatch(/8000/);
    expect(result.summary).toMatch(/9233/);
    expect(result.summary).toMatch(/openai\/gpt-oss-120b/);
    expect(result.raw).toBe(GROQ_TPM);
  });

  it("falls back to a shortened message for unknown errors", () => {
    const result = formatTraceRunError("Something broke in the sandbox");
    expect(result.title).toBe("Run failed");
    expect(result.summary).toBe("Something broke in the sandbox");
  });
});

describe("extractTraceError", () => {
  it("prefers trace output.error then span errors", () => {
    expect(
      extractTraceError({
        output: { error: "from output" },
        spans: [{ error: "from span" }],
      }),
    ).toBe("from output");
    expect(
      extractTraceError({
        output: {},
        spans: [{ error: null }, { error: "span boom" }],
      }),
    ).toBe("span boom");
  });
});
