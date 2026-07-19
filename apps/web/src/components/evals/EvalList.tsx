"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Field";
import {
  createEval,
  type EvalDefinition,
  type EvalTarget,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";
import { slugifyName } from "@/lib/validation/agent-form";

export function EvalList({
  evals,
  targets,
}: {
  evals: EvalDefinition[];
  targets: EvalTarget[];
}) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [name, setName] = useState("");
  const [targetIndex, setTargetIndex] = useState(0);
  const [prompt, setPrompt] = useState("");
  const [expected, setExpected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    const target = targets[targetIndex];
    if (!target || !name.trim() || !prompt.trim() || !expected.trim()) {
      setError("Name, target, prompt, and expected answer are required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const definition = await createEval(await getAccessToken(), {
        name: name.trim(),
        slug: slugifyName(name),
        target_type: target.target_type,
        target_id: target.target_id,
        version_id: target.version_id,
        cases: [
          {
            key: "smoke-1",
            name: "Smoke answer",
            input: prompt.trim(),
            expected_output: expected.trim(),
            evaluator: "contains",
          },
        ],
      });
      router.push(`/admin/evals/${definition.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Eval creation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <section className="surface-panel relative overflow-hidden rounded-2xl p-6 md:p-8">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-60" />
        <div className="relative grid gap-6 lg:grid-cols-[1.15fr_1fr]">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              Atlas quality
            </p>
            <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">
              Evals that ship with the agent.
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-muted">
              Run tenant-isolated correctness suites against pinned agent, team,
              and workflow versions. Latency and token usage are captured when
              the runtime provides them.
            </p>
          </div>
          <div className="grid gap-3 rounded-xl border border-line bg-raised/90 p-4">
            <div>
              <Label htmlFor="eval-name">New smoke eval</Label>
              <Input
                id="eval-name"
                value={name}
                placeholder="Support policy smoke"
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="eval-target">Pinned target</Label>
              <Select
                id="eval-target"
                value={targetIndex}
                onChange={(event) => setTargetIndex(Number(event.target.value))}
              >
                {targets.map((target, index) => (
                  <option key={`${target.target_type}:${target.version_id}`} value={index}>
                    {target.name} · {target.target_type} · {target.version_status}
                  </option>
                ))}
              </Select>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <Input
                aria-label="Eval prompt"
                value={prompt}
                placeholder="Prompt"
                onChange={(event) => setPrompt(event.target.value)}
              />
              <Input
                aria-label="Expected answer"
                value={expected}
                placeholder="Expected answer"
                onChange={(event) => setExpected(event.target.value)}
              />
            </div>
            <div className="flex items-center justify-between">
              <p className="text-xs text-rose">{error}</p>
              <Button variant="accent" disabled={busy || targets.length === 0} onClick={create}>
                {busy ? "Creating…" : "Create eval"}
              </Button>
            </div>
          </div>
        </div>
      </section>

      <section className="table-shell rounded-xl">
        <div className="grid grid-cols-[1.5fr_.65fr_.7fr_.7fr] gap-3 border-b border-line px-4 py-2.5">
          <span className="th-label">Evaluation</span>
          <span className="th-label">Suite</span>
          <span className="th-label">Latest</span>
          <span className="th-label text-right">Updated</span>
        </div>
        {evals.map((definition) => (
          <Link
            key={definition.id}
            href={`/admin/evals/${definition.id}`}
            className="grid grid-cols-[1.5fr_.65fr_.7fr_.7fr] items-center gap-3 border-b border-line/60 px-4 py-3 transition last:border-0 hover:bg-mist/70"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{definition.name}</p>
              <p className="mono-cell truncate text-slate-muted">
                {definition.target_type} · {definition.cases.length} cases
              </p>
            </div>
            <Badge tone="info">{definition.suite}</Badge>
            {definition.latest_run ? (
              <Badge tone={definition.latest_run.passed ? "success" : "danger"} dot>
                {definition.latest_run.passed ? "passed" : "failed"}
              </Badge>
            ) : (
              <Badge>not run</Badge>
            )}
            <p className="mono-cell text-right text-slate-muted">
              {formatRelative(definition.updated_at)}
            </p>
          </Link>
        ))}
        {evals.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-slate-muted">
            No evals yet. Create a smoke check above.
          </p>
        ) : null}
      </section>
    </div>
  );
}
