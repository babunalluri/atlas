import { afterEach, describe, expect, it, vi } from "vitest";

import { keycloakResetCredentialsUrl } from "@/lib/auth/keycloak-public";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("keycloakResetCredentialsUrl", () => {
  it("uses the public issuer reset-credentials action", () => {
    vi.stubEnv(
      "NEXT_PUBLIC_AUTH_KEYCLOAK_ISSUER",
      "http://localhost:8080/realms/atlas",
    );
    expect(keycloakResetCredentialsUrl()).toBe(
      "http://localhost:8080/realms/atlas/login-actions/reset-credentials",
    );
  });
});
