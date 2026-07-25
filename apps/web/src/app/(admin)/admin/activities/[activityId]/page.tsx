import { redirect } from "next/navigation";

export default async function ActivityDetailRedirectPage({
  params,
}: {
  params: Promise<{ activityId: string }>;
}) {
  const { activityId } = await params;
  redirect(`/admin/traces/${encodeURIComponent(activityId)}`);
}
