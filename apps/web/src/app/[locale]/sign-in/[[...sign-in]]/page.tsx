import { redirect } from "next/navigation";

function firstQueryValue(
  value: string | string[] | undefined,
): string | undefined {
  if (typeof value === "string" && value) return value;
  if (Array.isArray(value) && typeof value[0] === "string" && value[0]) {
    return value[0];
  }
  return undefined;
}

/**
 * Auth.js and leftover /sign-in links still land here. Open the home layout
 * with the Atlas sign-in modal instead of Keycloak’s hosted page.
 */
export default async function SignInPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { locale } = await params;
  const query = await searchParams;
  const callbackUrl =
    firstQueryValue(query.callbackUrl) ??
    firstQueryValue(query.redirect_url) ??
    firstQueryValue(query.next);
  const dest = new URLSearchParams({ signin: "1" });
  if (callbackUrl) dest.set("callbackUrl", callbackUrl);
  redirect(`/${locale}?${dest.toString()}`);
}
