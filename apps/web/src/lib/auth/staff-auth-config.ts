/**
 * Staff Auth.js / Keycloak configuration guards.
 * Development may use committed local defaults; production must fail closed.
 */

export const DEV_AUTH_SECRET = "dev-only-auth-secret-change-me-please";
export const DEV_KEYCLOAK_SECRET = "atlas-web-dev-secret-change-me";
export const DEV_KEYCLOAK_ISSUER = "http://localhost:8080/realms/atlas";
export const DEV_KEYCLOAK_ID = "atlas-web";

export function isWebDevelopment(): boolean {
  return (process.env.NODE_ENV ?? "development") !== "production";
}

function isProductionBuildPhase(): boolean {
  // `next build` sets NEXT_PHASE; secrets are injected at container runtime.
  return process.env.NEXT_PHASE === "phase-production-build";
}

function isInsecurePlaceholder(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  return (
    !normalized ||
    normalized.includes("change-me") ||
    normalized.includes("replace_me") ||
    normalized.includes("build-time-placeholder") ||
    normalized === DEV_AUTH_SECRET ||
    normalized === DEV_KEYCLOAK_SECRET
  );
}

/**
 * True when Auth.js can safely mint/verify session cookies for this environment.
 */
export function staffAuthConfigured(): boolean {
  const secret = process.env.AUTH_SECRET?.trim() ?? "";
  const keycloakSecret = process.env.AUTH_KEYCLOAK_SECRET?.trim() ?? "";
  const issuer = process.env.AUTH_KEYCLOAK_ISSUER?.trim() ?? "";
  const clientId = process.env.AUTH_KEYCLOAK_ID?.trim() ?? "";

  if (isWebDevelopment()) {
    // Local defaults are allowed; still require that something resolves.
    return Boolean(secret || DEV_AUTH_SECRET) && Boolean(keycloakSecret || DEV_KEYCLOAK_SECRET);
  }

  return (
    Boolean(secret) &&
    !isInsecurePlaceholder(secret) &&
    Boolean(keycloakSecret) &&
    !isInsecurePlaceholder(keycloakSecret) &&
    Boolean(issuer) &&
    Boolean(clientId)
  );
}

export function resolveAuthSecret(): string {
  const fromEnv = process.env.AUTH_SECRET?.trim() ?? "";
  if (fromEnv) {
    if (!isWebDevelopment() && isInsecurePlaceholder(fromEnv)) {
      throw new Error(
        "AUTH_SECRET must not use a development placeholder in production",
      );
    }
    return fromEnv;
  }
  if (isWebDevelopment()) {
    return DEV_AUTH_SECRET;
  }
  if (isProductionBuildPhase()) {
    return "build-time-placeholder-not-used-at-runtime";
  }
  throw new Error("AUTH_SECRET is required outside development");
}

export function resolveKeycloakClientId(): string {
  const fromEnv = process.env.AUTH_KEYCLOAK_ID?.trim() ?? "";
  if (fromEnv) return fromEnv;
  if (isWebDevelopment() || isProductionBuildPhase()) return DEV_KEYCLOAK_ID;
  throw new Error("AUTH_KEYCLOAK_ID is required outside development");
}

export function resolveKeycloakSecret(): string {
  const fromEnv = process.env.AUTH_KEYCLOAK_SECRET?.trim() ?? "";
  if (fromEnv) {
    if (!isWebDevelopment() && isInsecurePlaceholder(fromEnv)) {
      throw new Error(
        "AUTH_KEYCLOAK_SECRET must not use a development placeholder in production",
      );
    }
    return fromEnv;
  }
  if (isWebDevelopment()) return DEV_KEYCLOAK_SECRET;
  if (isProductionBuildPhase()) {
    return "build-time-placeholder-not-used-at-runtime";
  }
  throw new Error("AUTH_KEYCLOAK_SECRET is required outside development");
}

export function resolveKeycloakIssuer(): string {
  const fromEnv = process.env.AUTH_KEYCLOAK_ISSUER?.trim() ?? "";
  if (fromEnv) return fromEnv;
  if (isWebDevelopment() || isProductionBuildPhase()) return DEV_KEYCLOAK_ISSUER;
  throw new Error("AUTH_KEYCLOAK_ISSUER is required outside development");
}

/**
 * Server-side Keycloak base URL (Docker DNS). Defaults to the public issuer.
 * Browser redirects still use {@link resolveKeycloakIssuer}; token/userinfo/jwks
 * and refresh use this internal URL when set (compose split-horizon).
 */
export function resolveKeycloakInternalIssuer(): string {
  const fromEnv = process.env.AUTH_KEYCLOAK_INTERNAL_ISSUER?.trim() ?? "";
  if (fromEnv) return fromEnv;
  return resolveKeycloakIssuer();
}
