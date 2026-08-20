/**
 * Desk → org admin. Tenant admins and Super Admin leave the customer /
 * trading desk for the admin app (Workflows, Agents, Configure).
 * End users never see this control.
 */

/** Org admin shell home — teams list (Signals ops, desk teams, etc.). */
export const ORG_ADMIN_HREF = "/admin/teams";

export function canOpenOrgAdmin(workspace: {
  can_administer?: boolean;
  role?: string | null;
} | null | undefined): boolean {
  if (!workspace || workspace.can_administer === false) return false;
  if (workspace.can_administer === true) return true;
  return workspace.role === "tenant_admin" || workspace.role === "platform_admin";
}
