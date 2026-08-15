import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import en from "../../../messages/en.json";

const dir = dirname(fileURLToPath(import.meta.url));

describe("workspace traces", () => {
  it("exposes traces next to the profile control", () => {
    const bar = readFileSync(join(dir, "ChatAccountBar.tsx"), "utf8");
    expect(bar).toContain("WorkspaceTracesButton");
    expect(bar).toContain("WorkspaceProfileMenu");
    expect(en.common.traces.open).toBe("Traces");
  });

  it("loads the current user's traces only (not admin monitor)", () => {
    const source = readFileSync(join(dir, "WorkspaceTracesPanel.tsx"), "utf8");
    expect(source).toContain("listMyTraces");
    expect(source).toContain("getMyTrace");
    expect(source).toContain("TraceSpanPanel");
    expect(source).not.toContain("/admin/traces");
    expect(source).not.toContain("/api/admin/traces");
    expect(source).not.toContain("listTraces(");
    expect(source).not.toContain("getTrace(");
    expect(source).not.toMatch(/Approvals|\/admin\/approvals|\/admin\/metrics/);
    expect(en.common.traces.hint).toMatch(/Only you can see these/);
    expect(en.common.traces.chat).toBe("Team / chat");
  });
});
