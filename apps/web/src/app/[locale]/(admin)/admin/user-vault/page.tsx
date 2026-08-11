import { redirect } from "@/i18n/navigation";

/** Secrets & Variables live on each user form under Users. */
export default async function AdminUserVaultRedirect({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect({ href: "/admin/users", locale });
}
