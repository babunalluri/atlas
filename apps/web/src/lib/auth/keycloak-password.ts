import {
  resolveKeycloakClientId,
  resolveKeycloakInternalIssuer,
  resolveKeycloakSecret,
} from "@/lib/auth/staff-auth-config";

export type PasswordGrantSession = {
  accessToken: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt: number;
  sub: string;
  email?: string;
  name?: string;
  orgId?: string;
  orgRole?: string;
};

export class PasswordGrantAuthError extends Error {
  readonly code: "invalid_credentials" | "unavailable";

  constructor(code: "invalid_credentials" | "unavailable") {
    super(code === "invalid_credentials" ? "invalid_credentials" : "unavailable");
    this.name = "PasswordGrantAuthError";
    this.code = code;
  }
}

export function firstStringClaim(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value;
  if (Array.isArray(value) && typeof value[0] === "string" && value[0].trim()) {
    return value[0];
  }
  return undefined;
}

export function decodeJwtPayload(token: string): Record<string, unknown> {
  const segment = token.split(".")[1];
  if (!segment) {
    throw new Error("invalid token");
  }
  const padded = segment
    .replace(/-/g, "+")
    .replace(/_/g, "/")
    .padEnd(Math.ceil(segment.length / 4) * 4, "=");
  const binary = atob(padded);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  const json = new TextDecoder().decode(bytes);
  const payload = JSON.parse(json) as unknown;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("invalid token");
  }
  return payload as Record<string, unknown>;
}

export function sessionFromPasswordGrant(tokens: {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  expires_in?: number;
}): PasswordGrantSession {
  const claims = decodeJwtPayload(tokens.access_token);
  const sub = firstStringClaim(claims.sub);
  if (!sub) {
    throw new PasswordGrantAuthError("unavailable");
  }
  const given = firstStringClaim(claims.given_name);
  const family = firstStringClaim(claims.family_name);
  const composed =
    given && family ? `${given} ${family}` : given || family || undefined;
  return {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
    idToken: tokens.id_token,
    expiresAt: Math.floor(Date.now() / 1000) + Number(tokens.expires_in ?? 300),
    sub,
    email: firstStringClaim(claims.email),
    name:
      firstStringClaim(claims.name) ||
      composed ||
      firstStringClaim(claims.preferred_username) ||
      firstStringClaim(claims.email),
    orgId: firstStringClaim(claims.org_id),
    orgRole: firstStringClaim(claims.org_role),
  };
}

type TokenErrorBody = {
  error?: unknown;
};

/**
 * Resource Owner Password Credentials against Keycloak.
 * Never logs username or password.
 */
export async function exchangePasswordGrant(
  username: string,
  password: string,
): Promise<PasswordGrantSession> {
  const trimmedUser = username.trim();
  if (!trimmedUser || !password) {
    throw new PasswordGrantAuthError("invalid_credentials");
  }

  let response: Response;
  try {
    response = await fetch(
      `${resolveKeycloakInternalIssuer()}/protocol/openid-connect/token`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "password",
          client_id: resolveKeycloakClientId(),
          client_secret: resolveKeycloakSecret(),
          username: trimmedUser,
          password,
          scope: "openid",
        }),
      },
    );
  } catch {
    throw new PasswordGrantAuthError("unavailable");
  }

  if (!response.ok) {
    let errorCode = "";
    try {
      const body = (await response.json()) as TokenErrorBody;
      if (typeof body.error === "string") errorCode = body.error;
    } catch {
      // ignore malformed error bodies
    }
    if (errorCode === "invalid_grant" || response.status === 401) {
      throw new PasswordGrantAuthError("invalid_credentials");
    }
    throw new PasswordGrantAuthError("unavailable");
  }

  const tokens = (await response.json()) as {
    access_token?: string;
    refresh_token?: string;
    id_token?: string;
    expires_in?: number;
  };
  if (!tokens.access_token) {
    throw new PasswordGrantAuthError("unavailable");
  }
  return sessionFromPasswordGrant({
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    id_token: tokens.id_token,
    expires_in: tokens.expires_in,
  });
}
