import { redirect } from "next/navigation";

/**
 * Verified public/email customers are out of product scope (org-only access).
 * Keep the route so old bookmarks land somewhere useful.
 */
export default function CustomersPage() {
  redirect("/admin/users");
}
