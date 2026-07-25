import { redirect } from "next/navigation";

export default function SessionsRedirectPage() {
  redirect("/admin/traces");
}
