export type IdentityUser = {
  name?: string | null;
  email?: string | null;
  image?: string | null;
};

/** Header chip shows given name only — first token of a display name. */
export function firstGivenName(name: string): string {
  return name.trim().split(/\s+/).filter(Boolean)[0] ?? "";
}

/**
 * Single-word Atlas names are stored with a copied family name in the IdP so
 * password login can succeed. Collapse "Babu Babu" back to "Babu" for session.
 */
export function collapseDuplicateNameParts(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (
    parts.length >= 2 &&
    parts.every((part) => part.toLowerCase() === parts[0]!.toLowerCase())
  ) {
    return parts[0]!;
  }
  return name.trim();
}

/** Prefer session/Atlas display name over joining given+family; never emit "Babu Babu". */
export function composeDisplayName(options: {
  name?: string | null;
  givenName?: string | null;
  familyName?: string | null;
  fallback?: string | null;
}): string | undefined {
  const fromName = options.name?.trim();
  if (fromName) return collapseDuplicateNameParts(fromName);
  const given = options.givenName?.trim() || "";
  const family = options.familyName?.trim() || "";
  if (given && family) {
    if (given.toLowerCase() === family.toLowerCase()) return given;
    return `${given} ${family}`;
  }
  const single = given || family || options.fallback?.trim() || "";
  return single || undefined;
}

export function userDisplayName(user?: IdentityUser | null): string {
  const name = firstGivenName(user?.name || "");
  if (name) return name;
  const email = user?.email?.trim();
  if (email) return email;
  return "Signed in";
}

export function userInitials(user?: IdentityUser | null): string {
  const name = firstGivenName(user?.name || "");
  if (name) return name.slice(0, 1).toUpperCase();
  const local = user?.email?.trim().split("@")[0];
  if (local) return local.slice(0, 1).toUpperCase();
  return "?";
}

export function userIdentityTitle(user?: IdentityUser | null): string {
  const name = userDisplayName(user);
  const email = user?.email?.trim() || "";
  if (email && email !== name) return `${name} · ${email}`;
  return name;
}
