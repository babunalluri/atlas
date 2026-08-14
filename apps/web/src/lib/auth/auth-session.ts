import {
  decodeJwtPayload,
  firstStringClaim,
  privilegedOrgRole,
} from "@/lib/auth/keycloak-password";
import { composeDisplayName } from "@/lib/auth/user-identity";

export type AtlasAuthToken = {
  accessToken?: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt?: number;
  orgId?: string;
  orgRole?: string;
  error?: string;
  name?: string | null;
  email?: string | null;
  sub?: string;
  picture?: string | null;
};

export type AtlasAuthorizedUser = {
  id?: string;
  name?: string | null;
  email?: string | null;
  image?: string | null;
  accessToken?: string;
  refreshToken?: string;
  idToken?: string;
  expiresAt?: number;
  orgId?: string;
  orgRole?: string;
};

export type AtlasClientSession = {
  user?: {
    id?: string;
    name?: string | null;
    email?: string | null;
    image?: string | null;
  };
  accessToken?: string;
  idToken?: string;
  orgId?: string;
  orgRole?: string;
  error?: string;
  endSessionUrl?: string;
};

/**
 * Persist Credentials / OIDC user identity on the Auth.js JWT.
 * Without name/email/sub on the token, `/api/auth/session` can look empty
 * to `useSession()` even though `auth()` still has access tokens.
 */
export function persistAuthorizedUser(
  token: AtlasAuthToken,
  user: AtlasAuthorizedUser,
): AtlasAuthToken {
  if (user.id) token.sub = String(user.id);
  if (user.name) token.name = user.name;
  if (user.email) token.email = user.email;
  if (user.image) token.picture = user.image;
  if (user.accessToken) {
    token.accessToken = user.accessToken;
    token.refreshToken = user.refreshToken;
    token.idToken = user.idToken;
    token.expiresAt = user.expiresAt;
    if (user.orgId) token.orgId = user.orgId;
    if (user.orgRole) token.orgRole = user.orgRole;
    token.error = undefined;
  }
  fillClaimsFromAccessToken(token);
  return token;
}

export function applyOAuthAccount(
  token: AtlasAuthToken,
  account: {
    access_token?: string;
    refresh_token?: string;
    id_token?: string;
    expires_at?: number;
  },
): AtlasAuthToken {
  if (!account.access_token) return token;
  token.accessToken = account.access_token;
  token.refreshToken = account.refresh_token;
  token.idToken = account.id_token;
  token.expiresAt = account.expires_at;
  token.error = undefined;
  fillClaimsFromAccessToken(token);
  return token;
}

export function fillClaimsFromAccessToken(token: AtlasAuthToken): void {
  if (!token.accessToken) return;
  try {
    const claims = decodeJwtPayload(token.accessToken);
    token.sub = token.sub ?? firstStringClaim(claims.sub);
    token.email = token.email ?? firstStringClaim(claims.email);
    token.name =
      composeDisplayName({
        name: token.name ?? firstStringClaim(claims.name),
        givenName: firstStringClaim(claims.given_name),
        familyName: firstStringClaim(claims.family_name),
        fallback:
          firstStringClaim(claims.preferred_username) ??
          firstStringClaim(claims.email),
      }) ?? token.name;
    token.orgId = token.orgId ?? firstStringClaim(claims.org_id);
    token.orgRole =
      privilegedOrgRole([token.orgRole, claims.org_role].flat()) ??
      token.orgRole;
  } catch {
    // Ignore malformed access tokens; session still carries whatever we have.
  }
}

export function attachSessionFromToken<S extends AtlasClientSession>(
  session: S,
  token: AtlasAuthToken,
  endSessionUrl: string,
): S {
  session.user = {
    ...session.user,
    id: token.sub ?? session.user?.id,
    name:
      composeDisplayName({ name: token.name ?? session.user?.name }) ?? null,
    email: token.email ?? session.user?.email ?? null,
    image: token.picture ?? session.user?.image ?? null,
  };
  session.accessToken = token.accessToken;
  session.idToken = token.idToken;
  session.orgId = token.orgId;
  session.orgRole = token.orgRole;
  session.error = token.error;
  session.endSessionUrl = endSessionUrl;
  return session;
}

export function sessionLooksSignedIn(
  session: AtlasClientSession | null | undefined,
): boolean {
  if (!session) return false;
  return Boolean(
    session.accessToken ||
      session.user?.email ||
      session.user?.name ||
      session.user?.id,
  );
}

export type AuthSessionStatus = "loading" | "authenticated" | "unauthenticated";

/**
 * First paint may use the server-hydrated session. After Sign out, Auth.js
 * reports `unauthenticated` while the layout prop still holds the old
 * session — never fall back to that hydrate or the header stays signed in.
 */
export function visibleAuthSession<S extends AtlasClientSession>(
  status: AuthSessionStatus,
  clientSession: S | null | undefined,
  serverSession: S | null | undefined,
): S | null {
  if (status === "unauthenticated") return null;
  if (clientSession) return clientSession;
  if (status === "loading") return serverSession ?? null;
  return clientSession ?? null;
}
