"use client";

import { Link } from "@/i18n/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PlayIcon } from "@/components/ui/icons";
import {
  runEval,
  type EvalDefinition,
  type EvalRun,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

function duration(value: number | null) {
  if (value === null) return "—";
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`;
}

export function EvalDetail({ definition }: { definition: EvalDefinition }) {
  const { getAccessToken } = useAgentOsToken();
  const [runs, setRuns] = useState(definition.runs);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runNow() {
    setBusy(true);
    setError(null);
    try {
      const run = await runEval(await getAccessToken(), definition.id);
      setRuns((current) => [run, ...current]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Eval run failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
        <div>
          <Link href="/admin/evals" className="text-xs font-semibold text-teal">
            ← All evals
          </Link>
          <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">
            {definition.name}
          </h1>
          <p className="mt-2 text-sm text-slate-muted">
            {definition.target_type} · pinned version{" "}
            <span className="font-mono">{definition.version_id}</span>
          </p>
        </div>
        <Button
          variant="accent"
          icon={<PlayIcon />}
          onClick={runNow}
          disabled={busy}
        >
          {busy ? "Running suite…" : "Run now"}
        </Button>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="grid gap-3 md:grid-cols-4">
        <div className="surface-panel rounded-xl p-4">
          <p className="th-label">Suite</p>
          <p className="mt-2 text-xl font-semibold capitalize">{definition.suite}</p>
        </div>
        <div className="surface-panel rounded-xl p-4">
          <p className="th-label">Cases</p>
          <p className="mt-2 text-xl font-semibold tnum">{definition.cases.length}</p>
        </div>
        <div className="surface-panel rounded-xl p-4">
          <p className="th-label">Pass threshold</p>
          <p className="mt-2 text-xl font-semibold tnum">
            {(definition.pass_threshold * 100).toFixed(0)}%
          </p>
        </div>
        <div className="surface-panel rounded-xl p-4">
          <p className="th-label">Publish signal</p>
          <p className="mt-2 text-sm font-semibold">
            {runs[0]?.passed === false ? "Review before publish" : "No active warning"}
          </p>
        </div>
      </section>

      <section className="table-shell rounded-xl">
        <div className="grid grid-cols-[.7fr_.55fr_.55fr_.55fr_1fr] gap-3 border-b border-line px-4 py-2.5">
          <span className="th-label">Result</span>
          <span className="th-label">Score</span>
          <span className="th-label">Latency</span>
          <span className="th-label">Tokens</span>
          <span className="th-label text-right">Run</span>
        </div>
        {runs.map((run: EvalRun) => (
          <div key={run.id} className="border-b border-line/60 px-4 py-3 last:border-0">
            <div className="grid grid-cols-[.7fr_.55fr_.55fr_.55fr_1fr] items-center gap-3">
              <Badge tone={run.passed ? "success" : "danger"} dot>
                {run.passed ? "passed" : "failed"}
              </Badge>
              <p className="text-sm tnum">
                {run.score === null ? "—" : `${(run.score * 100).toFixed(0)}%`}
              </p>
              <p className="mono-cell">{duration(run.latency_ms)}</p>
              <p className="mono-cell">
                {(run.input_tokens ?? 0) + (run.output_tokens ?? 0) || "—"}
              </p>
              <p className="mono-cell text-right text-slate-muted">
                {formatRelative(run.started_at)}
              </p>
            </div>
            {run.error ? <p className="mt-2 text-xs text-rose">{run.error}</p> : null}
            {run.case_results?.map((item) => (
              <div
                key={item.id}
                className="mt-3 grid gap-2 rounded-lg border border-line bg-raised p-3 text-xs md:grid-cols-[.8fr_1fr_1fr]"
              >
                <div>
                  <Badge tone={item.passed ? "success" : "danger"}>{item.name}</Badge>
                  <p className="mt-2 text-slate-muted">
                    {item.evaluator} · {duration(item.latency_ms)}
                    {item.details.mocked ? " · mocked grader" : ""}
                  </p>
                </div>
                <div>
                  <p className="th-label">Expected</p>
                  <p className="mt-1">{item.expected_output}</p>
                </div>
                <div>
                  <p className="th-label">Actual</p>
                  <p className="mt-1">{item.error ?? item.actual_output ?? "No output"}</p>
                </div>
              </div>
            ))}
          </div>
        ))}
        {runs.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-slate-muted">
            This pinned version has not been evaluated.
          </p>
        ) : null}
      </section>
    </div>
  );
}
