import { describe, expect, it } from "vitest";

import { readableToolName } from "@/components/metrics/MetricsDashboard";

describe("readableToolName", () => {
  it("extracts tool_name from a Python dict blob", () => {
    const blob =
      "{'tool_call_id': 'call_qrGpwPPJXXBChAk3Nwuz2vbc', 'tool_name': 'search_web'}";
    expect(readableToolName(blob)).toBe("search_web");
  });

  it("extracts nested tool objects and ignores event names", () => {
    expect(
      readableToolName({
        tool: { tool_call_id: "call_1", tool_name: "get_quote" },
      }),
    ).toBe("get_quote");
    expect(readableToolName("ToolCallStarted")).toBe("Unknown tool");
    expect(readableToolName("list_orders")).toBe("list_orders");
  });
});
