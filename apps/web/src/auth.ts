import NextAuth from "next-auth";
import Keycloak from "next-auth/providers/keycloak";

import {
  resolveAuthSecret,
  resolveKeycloakClientId,
  resolveKeycloakInternalIssuer,
  resolveKeycloakIssuer,
  resolveKeycloakSecret,
} from "@/lib/auth/staff-auth-config";

const keycloakIssuer = resolveKeycloakIssuer();
const keycloakInternalIssuer = resolveKeycloakInternalIssuer();
const keycloakId = resolveKeycloakClientId();
const keycloakSecret = resolveKeycloakSecret();

type RefreshableToken = {
  accessToken?: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt?: number;
  orgId?: string;
  orgRole?: string;
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
      `${keycloakInternalIssuer}/protocol/openid-connect/token`,
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
      id_token?: string;
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
      idToken: refreshed.id_token ?? token.idToken,
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
 *
 * Split-horizon: browser uses the public issuer (authorization redirect + iss);
 * the Next.js server uses AUTH_KEYCLOAK_INTERNAL_ISSUER for token/userinfo/jwks
 * when running in Docker Compose.
 */
export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Keycloak({
      clientId: keycloakId,
      clientSecret: keycloakSecret,
      issuer: keycloakIssuer,
      // Avoid discovery returning unreachable localhost endpoints inside Compose.
      authorization: `${keycloakIssuer}/protocol/openid-connect/auth`,
      token: `${keycloakInternalIssuer}/protocol/openid-connect/token`,
      userinfo: `${keycloakInternalIssuer}/protocol/openid-connect/userinfo`,
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
        current.idToken = account.id_token;
        current.expiresAt = account.expires_at;
        current.error = undefined;
      }

      if (profile && typeof profile === "object") {
        const p = profile as Record<string, unknown>;
        if (typeof p.org_id === "string") current.orgId = p.org_id;
        else if (Array.isArray(p.org_id) && typeof p.org_id[0] === "string") {
          current.orgId = p.org_id[0];
        }
        if (typeof p.org_role === "string") current.orgRole = p.org_role;
        else if (Array.isArray(p.org_role) && typeof p.org_role[0] === "string") {
          current.orgRole = p.org_role[0];
        }
      }

      const expiresAt = current.expiresAt ?? 0;
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
        idToken?: string;
        orgId?: string;
        error?: string;
        endSessionUrl?: string;
      };
      s.accessToken = t.accessToken;
      s.idToken = t.idToken;
      s.orgId = t.orgId;
      s.error = t.error;
      s.endSessionUrl = `${keycloakIssuer}/protocol/openid-connect/logout`;
      return s;
    },
  },
  trustHost: true,
  secret: resolveAuthSecret(),
});
