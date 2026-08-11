import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChatMarkdown, isBlockCode } from "@/components/chat/ChatMarkdown";

describe("isBlockCode", () => {
  it("treats language fences and multiline text as block code", () => {
    expect(isBlockCode("language-ts", "x")).toBe(true);
    expect(isBlockCode(undefined, "line one\nline two")).toBe(true);
    expect(isBlockCode(undefined, "inline")).toBe(false);
  });
});

describe("ChatMarkdown", () => {
  it("renders tables with logical start alignment for RTL", () => {
    const html = renderToStaticMarkup(
      <ChatMarkdown content={"| Col | Val |\n| --- | --- |\n| a | 1 |"} />,
    );
    expect(html).toContain("text-start");
    expect(html).not.toContain("text-left");
  });

  it("styles inline and block code differently", () => {
    const html = renderToStaticMarkup(
      <ChatMarkdown content={"`inline`\n\n```\nblock\n```"} />,
    );
    expect(html).toContain("rounded bg-fog/80");
    expect(html).toContain("whitespace-pre");
  });

  it("constrains markdown images", () => {
    const html = renderToStaticMarkup(
      <ChatMarkdown content={"![alt](https://example.com/x.png)"} />,
    );
    expect(html).toContain('src="https://example.com/x.png"');
    expect(html).toContain("max-w-full");
  });
});
