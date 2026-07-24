import Link from "next/link";

import { ChevronLeftIcon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export function BackLink({
  href,
  label,
  className,
}: {
  href: string;
  label: string;
  className?: string;
}) {
  return (
    <Link
      href={href}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-md text-ink hover:bg-mist",
        className,
      )}
    >
      <ChevronLeftIcon className="size-5" />
    </Link>
  );
}
