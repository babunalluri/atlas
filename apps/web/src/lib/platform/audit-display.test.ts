import { describe, expect, it } from "vitest";

import type { PlatformAuditEvent } from "@/lib/api/types";

import {
  describeAuditTenant,
  isEmptyAuditDetails,
  prettyAuditDetails,
  resolveAuditActor,
  shortActorId,
} from "./audit-display";

const event: PlatformAuditEvent = {
  id: "evt-1",
  actorId: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  actorEmail: null,
  actorName: null,
  action: "tenant.update",
  tenantId: "11111111-1111-1111-1111-111111111111",
  details: { name: { from: "Acme", to: "Acme Corp" } },
  createdAt: "2026-08-13T12:00:00.000Z",
};

describe("isEmptyAuditDetails", () => {
  it("treats missing and empty objects as empty", () => {
    expect(isEmptyAuditDetails(undefined)).toBe(true);
    expect(isEmptyAuditDetails({})).toBe(true);
    expect(isEmptyAuditDetails({ slug: "acme" })).toBe(false);
  });
});

describe("prettyAuditDetails", () => {
  it("pretty-prints stored payload JSON", () => {
    expect(prettyAuditDetails(event.details)).toContain('"from": "Acme"');
  });
});

describe("resolveAuditActor", () => {
  it("prefers stored name then email, else the full actor id", () => {
    expect(
      resolveAuditActor({
        ...event,
        actorName: "Ada Admin",
        actorEmail: "ada@atlas.test",
      }).label,
    ).toBe("Ada Admin");
    expect(
      resolveAuditActor({ ...event, actorEmail: "ada@atlas.test" }).label,
    ).toBe("ada@atlas.test");
    expect(resolveAuditActor(event).label).toBe(event.actorId);
  });

  it("fills name/email from the current viewer when ids match", () => {
    const resolved = resolveAuditActor(event, {
      id: event.actorId,
      name: "You",
      email: "you@atlas.test",
    });
    expect(resolved.label).toBe("You");
    expect(resolved.email).toBe("you@atlas.test");
  });
});

describe("shortActorId", () => {
  it("truncates long ids for the collapsed row", () => {
    expect(shortActorId(event.actorId)).toBe("aaaaaaaa-b…eeee");
    expect(shortActorId("platform-owner")).toBe("platform-owner");
  });
});

describe("describeAuditTenant", () => {
  it("joins name and slug from the loaded tenant list", () => {
    expect(
      describeAuditTenant(event, [
        {
          id: event.tenantId!,
          name: "Acme Corp",
          slug: "acme",
          authOrgId: "org_acme",
          domain: "generic",
          branding: {},
          timezone: "UTC",
          isActive: true,
          ownerEmail: null,
          createdAt: event.createdAt,
          updatedAt: event.createdAt,
        },
      ]),
    ).toEqual({
      name: "Acme Corp",
      slug: "acme",
      id: event.tenantId,
    });
  });
});
