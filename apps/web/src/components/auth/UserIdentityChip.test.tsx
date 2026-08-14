import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UserIdentityChip } from "@/components/auth/UserIdentityChip";

describe("UserIdentityChip", () => {
  it("shows initials, display name, and email for a desk user", () => {
    const html = renderToStaticMarkup(
      <UserIdentityChip user={{ name: "Babu", email: "babu@atlas.ai" }} />,
    );
    expect(html).toContain("Babu");
    expect(html).toContain("babu@atlas.ai");
    expect(html).toContain(">B<");
    expect(html).toContain("Signed in as Babu · babu@atlas.ai");
  });

  it("shows only the first name when the session name has a family name", () => {
    const html = renderToStaticMarkup(
      <UserIdentityChip
        user={{ name: "Babu Nalluri", email: "babu@atlas.ai" }}
      />,
    );
    expect(html).toContain(">Babu<");
    expect(html).not.toContain("Nalluri");
    expect(html).toContain(">B<");
    expect(html).not.toContain(">BN<");
    expect(html).toContain("Signed in as Babu · babu@atlas.ai");
  });

  it("does not render a duplicated given name", () => {
    const html = renderToStaticMarkup(
      <UserIdentityChip user={{ name: "Babu Babu", email: "babu@atlas.ai" }} />,
    );
    expect(html).toContain(">Babu<");
    expect(html).not.toContain("Babu Babu");
    expect(html).toContain(">B<");
  });
});
