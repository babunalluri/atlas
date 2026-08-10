import { redirect } from "next/navigation";

/** Secrets & Variables live on each user form under Users. */
export default function AdminUserVaultRedirect() {
  redirect("/admin/users");
}
