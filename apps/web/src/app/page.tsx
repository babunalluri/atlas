import Link from "next/link";

import { AuthControls } from "@/components/auth/AuthControls";
import { BrandMark } from "@/components/layout/BrandMark";
import { Button } from "@/components/ui/Button";

export default function HomePage() {
  return (
    <div className="relative min-h-screen overflow-hidden">
      <div className="pointer-events-none absolute inset-0 grid-noise opacity-50" />
      <div className="relative mx-auto flex min-h-screen max-w-5xl flex-col justify-center px-6 py-16">
        <div className="flex items-center justify-between gap-4">
          <BrandMark />
          <AuthControls />
        </div>
        <h1 className="mt-10 max-w-3xl font-display text-5xl font-semibold tracking-tight text-ink md:text-6xl">
          Ship tenant-owned agents with a control surface that feels on-brand.
        </h1>
        <p className="mt-5 max-w-xl text-base leading-relaxed text-slate-muted">
          Configure instructions, tools, knowledge, and memory — then publish to a
          customer chat experience that carries each tenant&apos;s identity.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link href="/admin/agents">
            <Button variant="accent">Open admin</Button>
          </Link>
          <Link href="/t/northwind/chat/support-concierge">
            <Button variant="secondary">Preview customer chat</Button>
          </Link>
        </div>
      </div>
    </div>
  );
}
