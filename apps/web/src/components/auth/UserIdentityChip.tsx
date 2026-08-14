import {
  userDisplayName,
  userIdentityTitle,
  userInitials,
  type IdentityUser,
} from "@/lib/auth/user-identity";
import { cn } from "@/lib/utils";

export function UserIdentityChip({
  user,
  compact = false,
  className,
}: {
  user?: IdentityUser | null;
  compact?: boolean;
  className?: string;
}) {
  const name = userDisplayName(user);
  const email = user?.email?.trim() || "";
  const initials = userInitials(user);
  const title = userIdentityTitle(user);
  const showEmail = Boolean(email && email !== name);

  return (
    <div
      className={cn("flex min-w-0 items-center gap-2", className)}
      title={title}
    >
      {user?.image ? (
        // eslint-disable-next-line @next/next/no-img-element -- OIDC avatars come from arbitrary issuer hosts
        <img
          src={user.image}
          alt=""
          className="size-7 shrink-0 rounded-full object-cover"
        />
      ) : (
        <span
          aria-hidden
          className="flex size-7 shrink-0 items-center justify-center rounded-full bg-teal/15 text-[11px] font-semibold text-teal"
        >
          {initials}
        </span>
      )}
      <span
        className={cn(
          "min-w-0",
          compact ? "max-w-[9rem] sm:max-w-[12rem]" : "max-w-[14rem]",
        )}
      >
        <span className="block truncate text-sm font-medium leading-tight text-ink">
          {name}
        </span>
        {showEmail && !compact ? (
          <span className="block truncate text-[11px] leading-tight text-slate-muted">
            {email}
          </span>
        ) : null}
      </span>
      <span className="sr-only">Signed in as {title}</span>
    </div>
  );
}
