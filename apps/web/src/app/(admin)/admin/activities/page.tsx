import { redirect } from "next/navigation";

export default function ActivitiesRedirectPage() {
  redirect("/admin/traces");
}
