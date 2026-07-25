import { redirect } from "next/navigation";

/** Catch-all for legacy `/admin/sessions/*` → unified Traces. */
export default async function SessionCatchAllRedirectPage({
  params,
}: {
  params: Promise<{ path?: string[] }>;
}) {
  const { path } = await params;
  const id = path?.[0];
  if (id) {
    redirect(`/admin/traces/${encodeURIComponent(id)}`);
  }
  redirect("/admin/traces");
}
