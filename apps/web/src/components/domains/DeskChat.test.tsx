import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DeskChatPills, deskChatEmptyCopy } from "@/components/domains/DeskChat";

describe("DeskChatPills", () => {
  it("renders one tab per assigned team using assignment names", () => {
    const html = renderToStaticMarkup(
      <DeskChatPills
        targets={[
          { id: "1", name: "Learning" },
          { id: "2", name: "Paper trading" },
        ]}
        selectedId="2"
        onSelect={() => undefined}
      />,
    );
    expect(html.match(/<button/g)?.length).toBe(2);
    expect(html).toContain("Learning");
    expect(html).toContain("Paper trading");
    expect(html).not.toContain("Live trading");
  });

  it("renders three or more assigned teams as tabs", () => {
    const html = renderToStaticMarkup(
      <DeskChatPills
        targets={[
          { id: "1", name: "Learning" },
          { id: "2", name: "Paper trading" },
          { id: "3", name: "Live trading" },
          { id: "4", name: "Options lab" },
        ]}
        selectedId="4"
        onSelect={() => undefined}
      />,
    );
    expect(html.match(/<button/g)?.length).toBe(4);
    expect(html).toContain("Options lab");
  });

  it("shows an empty message when nothing is assigned", () => {
    const html = renderToStaticMarkup(
      <DeskChatPills targets={[]} onSelect={() => undefined} />,
    );
    expect(html).toContain("No desk chats assigned.");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("Learning");
    expect(html).not.toContain("Paper trading");
    expect(html).not.toContain("Live trading");
  });
});

describe("deskChatEmptyCopy", () => {
  it("does not mention hardcoded Learning / Paper / Live tabs", () => {
    expect(deskChatEmptyCopy(true)).toContain("assigned");
    expect(deskChatEmptyCopy(true)).not.toContain("Learning");
    expect(deskChatEmptyCopy(false)).not.toContain("Paper trading");
  });
});
