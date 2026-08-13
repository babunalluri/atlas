"use client";

import { Button } from "@/components/ui/Button";
import { AdminFormDialog } from "@/components/ui/AdminFormDialog";

function deleteUserConfirmMessage(user: {
  displayName: string;
  email: string | null;
}) {
  const email = user.email?.trim();
  const who = email ? `${user.displayName} (${email})` : user.displayName;
  return `Remove ${who} from this organization? This cannot be undone.`;
}

export function DeleteUserDialog({
  user,
  busy = false,
  onClose,
  onConfirm,
}: {
  user: { displayName: string; email: string | null };
  busy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <AdminFormDialog
      title="Delete user"
      subtitle={deleteUserConfirmMessage(user)}
      titleId="delete-user-title"
      showCloseButton
      className="max-w-sm"
      onClose={busy ? () => undefined : onClose}
    >
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={busy}
          onClick={onClose}
        >
          Cancel
        </Button>
        <Button
          type="button"
          variant="danger"
          size="sm"
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? "Deleting…" : "Delete"}
        </Button>
      </div>
    </AdminFormDialog>
  );
}
