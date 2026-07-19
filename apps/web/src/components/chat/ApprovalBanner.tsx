export function ApprovalBanner({
  visible,
  message = "This run is paused until a tenant admin approves a tool action.",
}: {
  visible: boolean;
  message?: string;
}) {
  if (!visible) return null;
  return (
    <div className="border-b border-amber/40 bg-amber/15 px-4 py-2 text-sm text-ink">
      {message}
    </div>
  );
}
