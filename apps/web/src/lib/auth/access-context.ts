export const PLATFORM_TENANT_COOKIE = "atlas_platform_tenant";
export const PLATFORM_TENANT_NAME_COOKIE = "atlas_platform_tenant_name";

const TENANT_MARKER = "::atlas-platform-tenant=";

export type AccessContext = {
  token: string;
  platformTenantId?: string;
};

export function packAccessContext(
  token: string,
  platformTenantId?: string | null,
): string {
  return platformTenantId
    ? `${token}${TENANT_MARKER}${platformTenantId}`
    : token;
}

export function unpackAccessContext(value: string): AccessContext {
  const markerIndex = value.lastIndexOf(TENANT_MARKER);
  if (markerIndex === -1) return { token: value };
  return {
    token: value.slice(0, markerIndex),
    platformTenantId: value.slice(markerIndex + TENANT_MARKER.length),
  };
}

export function browserSelectedTenantId(): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${PLATFORM_TENANT_COOKIE}=`;
  const value = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length);
  return value ? decodeURIComponent(value) : null;
}

export function tokenIsPlatformAdmin(value: string): boolean {
  const { token } = unpackAccessContext(value);
  if (
    process.env.NEXT_PUBLIC_DEV_AUTH === "true" &&
    process.env.NEXT_PUBLIC_DEV_ROLE === "platform_admin"
  ) {
    return true;
  }
  try {
    const segment = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = segment.padEnd(Math.ceil(segment.length / 4) * 4, "=");
    const payload = JSON.parse(
      atob(padded),
    ) as {
      platform_admin?: boolean | string;
      metadata?: { platform_admin?: boolean | string };
    };
    const flag = payload.platform_admin ?? payload.metadata?.platform_admin;
    return (
      flag === true ||
      ["true", "1", "yes"].includes(String(flag).toLowerCase())
    );
  } catch {
    return false;
  }
}
