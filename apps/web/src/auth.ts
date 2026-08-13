import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import Keycloak from "next-auth/providers/keycloak";

import {
  applyOAuthAccount,
  attachSessionFromToken,
  persistAuthorizedUser,
  type AtlasAuthToken,
} from "@/lib/auth/auth-session";
import {
  exchangePasswordGrant,
  firstStringClaim,
  PasswordGrantAuthError,
} from "@/lib/auth/keycloak-password";
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

type RefreshableToken = AtlasAuthToken;

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
 * Atlas staff auth via self-hosted Keycloak.
 *
 * Happy-path Sign in uses Resource Owner Password Credentials (modal form) so
 * the browser never leaves Atlas for Keycloak’s hosted login page. The OIDC
 * Keycloak provider remains for compatibility. Split-horizon: browser uses the
 * public issuer; the Next.js server uses AUTH_KEYCLOAK_INTERNAL_ISSUER for
 * token/userinfo/jwks when running in Docker Compose.
 */
export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Credentials({
      id: "credentials",
      name: "Atlas",
      credentials: {
        username: { label: "Username or email", type: "text" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const username =
          typeof credentials?.username === "string" ? credentials.username : "";
        const password =
          typeof credentials?.password === "string" ? credentials.password : "";
        try {
          const granted = await exchangePasswordGrant(username, password);
          return {
            id: granted.sub,
            name: granted.name,
            email: granted.email,
            accessToken: granted.accessToken,
            refreshToken: granted.refreshToken,
            idToken: granted.idToken,
            expiresAt: granted.expiresAt,
            orgId: granted.orgId,
            orgRole: granted.orgRole,
          };
        } catch (reason) {
          if (
            reason instanceof PasswordGrantAuthError &&
            reason.code === "invalid_credentials"
          ) {
            return null;
          }
          throw reason;
        }
      },
    }),
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
    async jwt({ token, user, account, profile }) {
      const current = token as typeof token & RefreshableToken;

      if (user) {
        persistAuthorizedUser(current, user);
      }

      if (account?.access_token) {
        applyOAuthAccount(current, account);
      }

      if (profile && typeof profile === "object") {
        const p = profile as Record<string, unknown>;
        const orgId = firstStringClaim(p.org_id);
        const orgRole = firstStringClaim(p.org_role);
        const email = firstStringClaim(p.email);
        const name = firstStringClaim(p.name);
        if (orgId) current.orgId = orgId;
        if (orgRole) current.orgRole = orgRole;
        if (email) current.email = email;
        if (name) current.name = name;
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
      return attachSessionFromToken(
        session,
        token as RefreshableToken,
        `${keycloakIssuer}/protocol/openid-connect/logout`,
      );
    },
  },
  trustHost: true,
  secret: resolveAuthSecret(),
});
