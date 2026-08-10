import NextAuth from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

const keycloakIssuer =
  process.env.AUTH_KEYCLOAK_ISSUER || "http://localhost:8080/realms/atlas";
const keycloakId = process.env.AUTH_KEYCLOAK_ID || "atlas-web";
const keycloakSecret =
  process.env.AUTH_KEYCLOAK_SECRET || "atlas-web-dev-secret-change-me";

type RefreshableToken = {
  accessToken?: string;
  refreshToken?: string;
  expiresAt?: number;
  orgId?: string;
  orgRole?: string;
  platformAdmin?: unknown;
  error?: string;
};

async function refreshAccessToken(
  token: RefreshableToken,
): Promise<RefreshableToken> {
  if (!token.refreshToken) {
    return { ...token, error: "RefreshAccessTokenError" };
  }
  try {
    const response = await fetch(
      `${keycloakIssuer}/protocol/openid-connect/token`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: keycloakId,
          client_secret: keycloakSecret,
          grant_type: "refresh_token",
          refresh_token: token.refreshToken,
        }),
      },
    );
    const refreshed = (await response.json()) as {
      access_token?: string;
      refresh_token?: string;
      expires_in?: number;
      error?: string;
    };
    if (!response.ok || !refreshed.access_token) {
      return { ...token, error: "RefreshAccessTokenError" };
    }
    return {
      ...token,
      accessToken: refreshed.access_token,
      refreshToken: refreshed.refresh_token ?? token.refreshToken,
      expiresAt:
        Math.floor(Date.now() / 1000) + Number(refreshed.expires_in ?? 300),
      error: undefined,
    };
  } catch {
    return { ...token, error: "RefreshAccessTokenError" };
  }
}

/**
 * Atlas staff auth via self-hosted Keycloak (OIDC).
 * Access token is forwarded to the Atlas API (same JWKS verify path as before).
 * Refresh keeps API calls working after the short-lived Keycloak access token expires.
 */
export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Keycloak({
      clientId: keycloakId,
      clientSecret: keycloakSecret,
      issuer: keycloakIssuer,
    }),
  ],
  session: { strategy: "jwt" },
  pages: {
    signIn: "/sign-in",
  },
  callbacks: {
    async jwt({ token, account, profile }) {
      const current = token as typeof token & RefreshableToken;

      if (account?.access_token) {
        current.accessToken = account.access_token;
        current.refreshToken = account.refresh_token;
        current.expiresAt = account.expires_at;
        current.error = undefined;
      }

      if (profile && typeof profile === "object") {
        const p = profile as Record<string, unknown>;
        if (typeof p.org_id === "string") current.orgId = p.org_id;
        if (typeof p.org_role === "string") current.orgRole = p.org_role;
        if (p.platform_admin != null) current.platformAdmin = p.platform_admin;
      }

      const expiresAt = current.expiresAt ?? 0;
      // Refresh 60s before expiry so API calls do not race the clock.
      if (current.accessToken && Date.now() < expiresAt * 1000 - 60_000) {
        return current;
      }
      if (current.refreshToken) {
        return await refreshAccessToken(current);
      }
      return current;
    },
    async session({ session, token }) {
      const t = token as RefreshableToken;
      const s = session as typeof session & {
        accessToken?: string;
        orgId?: string;
        error?: string;
      };
      s.accessToken = t.accessToken;
      s.orgId = t.orgId;
      s.error = t.error;
      return s;
    },
  },
  trustHost: true,
  secret: process.env.AUTH_SECRET || "dev-only-auth-secret-change-me-please",
});
