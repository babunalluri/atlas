import { redirect } from "@/i18n/navigation";

/** Legacy entry — the trader workspace landing lives at /workspace. */
export default async function LabHome({
  params,
}: {
  params: Promise<{ tenantSlug: string; locale: string }>;
}) {
  const { tenantSlug, locale } = await params;
  redirect({ href: `/t/${tenantSlug}/workspace`, locale });
}
