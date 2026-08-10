/** Shared admin route loading UI — shown while RSC pages fetch token + data. */

function Bone({ className }: { className?: string }) {
  return (
    <div
      className={`skeleton-bone rounded-md ${className ?? ""}`}
      aria-hidden
    />
  );
}

export function AdminPageSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      <header className="space-y-3">
        <Bone className="h-3 w-24" />
        <Bone className="h-9 w-56 max-w-full" />
        <Bone className="h-4 w-full max-w-md" />
      </header>
      <div className="grid gap-3 sm:grid-cols-3">
        <Bone className="h-20 rounded-xl" />
        <Bone className="h-20 rounded-xl" />
        <Bone className="h-20 rounded-xl" />
      </div>
      <div className="table-shell rounded-xl p-4 space-y-3">
        <Bone className="h-4 w-full" />
        <Bone className="h-4 w-[92%]" />
        <Bone className="h-4 w-[88%]" />
        <Bone className="h-4 w-[95%]" />
        <Bone className="h-4 w-[70%]" />
        <Bone className="h-4 w-[84%]" />
      </div>
    </div>
  );
}
