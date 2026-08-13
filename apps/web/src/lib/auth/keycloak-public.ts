const DEFAULT_ISSUER = "http://localhost:8080/realms/atlas";

export function keycloakResetCredentialsUrl(): string {
  const issuer = (
    process.env.NEXT_PUBLIC_AUTH_KEYCLOAK_ISSUER || DEFAULT_ISSUER
  ).replace(/\/$/, "");
  return `${issuer}/login-actions/reset-credentials`;
}
