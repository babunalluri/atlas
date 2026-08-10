import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEV_AUTH_SECRET,
  DEV_KEYCLOAK_SECRET,
  resolveAuthSecret,
  resolveKeycloakSecret,
  staffAuthConfigured,
} from "@/lib/auth/staff-auth-config";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("staffAuthConfigured", () => {
  it("allows development defaults", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("AUTH_SECRET", "");
    vi.stubEnv("AUTH_KEYCLOAK_SECRET", "");
    expect(staffAuthConfigured()).toBe(true);
    expect(resolveAuthSecret()).toBe(DEV_AUTH_SECRET);
    expect(resolveKeycloakSecret()).toBe(DEV_KEYCLOAK_SECRET);
  });

  it("fails closed in production on missing or placeholder secrets", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("AUTH_SECRET", DEV_AUTH_SECRET);
    vi.stubEnv("AUTH_KEYCLOAK_SECRET", DEV_KEYCLOAK_SECRET);
    vi.stubEnv("AUTH_KEYCLOAK_ID", "atlas-web");
    vi.stubEnv("AUTH_KEYCLOAK_ISSUER", "https://idp.example/realms/atlas");
    expect(staffAuthConfigured()).toBe(false);
    expect(() => resolveAuthSecret()).toThrow(/placeholder/);

    vi.stubEnv("AUTH_SECRET", "prod-secret-not-a-placeholder-value");
    vi.stubEnv("AUTH_KEYCLOAK_SECRET", "prod-keycloak-client-secret-value");
    expect(staffAuthConfigured()).toBe(true);
    expect(resolveAuthSecret()).toBe("prod-secret-not-a-placeholder-value");
  });

  it("allows placeholders during next production build phase only", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PHASE", "phase-production-build");
    vi.stubEnv("AUTH_SECRET", DEV_AUTH_SECRET);
    vi.stubEnv("AUTH_KEYCLOAK_SECRET", DEV_KEYCLOAK_SECRET);
    expect(resolveAuthSecret()).toBe(DEV_AUTH_SECRET);
    expect(resolveKeycloakSecret()).toBe(DEV_KEYCLOAK_SECRET);
  });
});
