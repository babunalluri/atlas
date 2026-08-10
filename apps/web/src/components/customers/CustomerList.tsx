"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { updateEndCustomer } from "@/lib/api/admin";
import type { EndCustomer } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

type StatusFilter = "all" | "active" | "inactive";

const PAGE_SIZE = 25;

export function CustomerList({
  initialCustomers,
}: {
  initialCustomers: EndCustomer[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [customers, setCustomers] = useState(initialCustomers);
  const [error, setError] = useState<string | null>(null);
  const [busyIds, setBusyIds] = useState<Set<string>>(() => new Set());
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return customers.filter((customer) => {
      if (status === "active" && !customer.isActive) return false;
      if (status === "inactive" && customer.isActive) return false;
      if (!query) return true;
      const haystack = [customer.displayName, customer.email]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [customers, q, status]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(start, start + PAGE_SIZE);

  async function toggleActive(customer: EndCustomer) {
    setBusyIds((current) => new Set(current).add(customer.id));
    setError(null);
    try {
      const updated = await updateEndCustomer(
        await getAccessToken(),
        customer.id,
        { isActive: !customer.isActive },
      );
      setCustomers((prev) =>
        prev.map((row) => (row.id === updated.id ? updated : row)),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to update customer",
      );
    } finally {
      setBusyIds((current) => {
        const next = new Set(current);
        next.delete(customer.id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
            Customers
          </p>
          <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight">
            Verified end users
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--muted)]">
            People who verified email in public chat or emailed a published
            team/workflow. Profile tools use this identity — not staff directory
            users.
          </p>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setPage(1);
          }}
          placeholder="Search email or name"
          className="max-w-xs"
        />
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as StatusFilter);
            setPage(1);
          }}
          className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm"
        >
          <option value="all">All</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </select>
      </div>

      {error ? (
        <p className="text-sm text-amber-700 dark:text-amber-300">{error}</p>
      ) : null}

      <div className="table-shell overflow-x-auto rounded-xl">
        <table className="min-w-full text-left text-sm">
          <thead className="border-b border-[var(--border)] text-xs uppercase tracking-wide text-[var(--muted)]">
            <tr>
              <th className="px-4 py-3 font-medium">Customer</th>
              <th className="px-4 py-3 font-medium">Verified</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Updated</th>
              <th className="px-4 py-3 font-medium" />
            </tr>
          </thead>
          <tbody>
            {pageItems.length === 0 ? (
              <tr>
                <td
                  colSpan={5}
                  className="px-4 py-8 text-center text-[var(--muted)]"
                >
                  No verified customers yet.
                </td>
              </tr>
            ) : (
              pageItems.map((customer) => (
                <tr
                  key={customer.id}
                  className="border-b border-[var(--border)]/70 last:border-0"
                >
                  <td className="px-4 py-3">
                    <p className="font-medium">
                      {customer.displayName || customer.email}
                    </p>
                    <p className="text-xs text-[var(--muted)]">
                      {customer.email}
                    </p>
                  </td>
                  <td className="px-4 py-3 text-[var(--muted)]">
                    {customer.emailVerifiedAt
                      ? formatRelative(customer.emailVerifiedAt)
                      : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={customer.isActive ? "success" : "neutral"}>
                      {customer.isActive ? "Active" : "Disabled"}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-[var(--muted)]">
                    {formatRelative(customer.updatedAt)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={busyIds.has(customer.id)}
                      onClick={() => void toggleActive(customer)}
                    >
                      {customer.isActive ? "Disable" : "Enable"}
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 ? (
        <div className="flex items-center justify-between text-sm text-[var(--muted)]">
          <p>
            Page {safePage} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={safePage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={safePage >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              Next
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
