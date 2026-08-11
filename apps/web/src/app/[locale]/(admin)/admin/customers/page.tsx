import { redirect } from "@/i18n/navigation";

/**
 * Verified public/email customers are out of product scope (org-only access).
 * Keep the route so old bookmarks land somewhere useful.
 */
export default async function CustomersPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  redirect({ href: "/admin/users", locale });
}
